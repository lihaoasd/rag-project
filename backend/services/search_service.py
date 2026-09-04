"""
文档检索业务逻辑 — 多路独立召回（Milvus 稠密 / Milvus 稀疏 / ES 全文）+ 可选 RRF 融合 + 可选重排。

检索策略（retrieval_methods，可多选）：
- dense    : Milvus 稠密向量
- sparse   : Milvus 内置 BM25 稀疏向量（jieba 分词）
- fulltext : ES + IK 全文检索（关键词精确命中 + 后端高亮）

单选一个策略时直接返回该路结果（保留原始分数语义）；
多选时各路独立召回，Python 端 RRF 融合。
"""
import asyncio
import time
from typing import Any

from langfuse import get_client

from config import settings
from core import es_store
from core.database import async_session
from core.model_factory import resolve_default_model_config
from core.observability import format_retrieval_observation, traced_span
from core.query_rewrite import rewrite_query
from core.retrieval import merge_intent_hits, retrieve, rerank, rrf_merge
from repositories.kb_repository import KnowledgeBaseRepository
from services import lexicon_service, model_service
from utils.logging import get_logger

logger = get_logger(__name__)

VALID_METHODS = ("dense", "sparse", "fulltext", "graph")


@traced_span("recall_graph")
async def _recall_graph(query: str, kb_ids: list[str], top_k: int) -> list[dict]:
    """知识图谱证据召回（graph 通道）：逐 kb 子图检索，实体证据切片转结果项。

    图证据无向量相似度：score 取固定 1.0（RRF 只看排序位置；展示时以 graph_entity 标记来源）。
    图谱未启用/检索失败时降级为空列表，不阻断整体检索。
    """
    t0 = time.perf_counter()
    if not settings.graph_enabled:
        get_client().update_current_span(
            input=query,
            metadata={"method": "graph", "kb_ids": kb_ids, "hit_count": 0, "skipped": "graph_disabled"},
        )
        return []

    from services import graph_service

    async def _one(kb_id: str):
        try:
            result = await graph_service.search_subgraph(
                kb_id, query, max_hops=1, limit=max(top_k * 2, 20)
            )
            hits: list[dict] = []
            for evg in result.get("evidence", []):
                entity = evg.get("entity", "")
                for chunk in evg.get("chunks", []):
                    hits.append({
                        "score": 1.0,
                        "text": chunk.get("text", ""),
                        "doc_id": chunk.get("doc_id", ""),
                        "file_name": chunk.get("file_name", ""),
                        "page_number": chunk.get("page_number", 1),
                        "chunk_index": chunk.get("chunk_index", 0),
                        "chunk_type": "text",
                        "source_chunk_index": -1,
                        "kb_id": kb_id,
                        "graph_entity": entity,
                    })
            return hits
        except Exception as e:
            logger.warning("知识库 %s graph 检索失败（降级为空）: %s", kb_id, e)
            return []

    per_kb = await asyncio.gather(*[_one(kid) for kid in kb_ids])
    all_hits = [h for hits in per_kb for h in hits]

    # 去重：同一切片被多个实体提到时保留一条，graph_entity 合并
    merged: dict[tuple, dict] = {}
    for h in all_hits:
        key = (h["doc_id"], h["chunk_index"])
        if key not in merged:
            merged[key] = h
        else:
            prev = merged[key]["graph_entity"]
            if h["graph_entity"] and h["graph_entity"] not in prev:
                merged[key]["graph_entity"] = f"{prev}、{h['graph_entity']}"
    all_hits = list(merged.values())

    get_client().update_current_span(
        input=query,
        metadata={
            "method": "graph",
            "kb_ids": kb_ids,
            "hit_count": len(all_hits),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        },
        output=format_retrieval_observation(all_hits),
    )
    return all_hits


@traced_span("rrf_merge")
def _rrf_merge_traced(
    ranked_lists: list[list[dict]],
    k: int = 60,
    method_hits: dict[str, int] | None = None,
) -> list[dict]:
    """RRF 融合（独立 span，SPAN）：记录各路召回命中数与融合结果。

    Langfuse v4 无手动 start_span，用 @observe 嵌套函数自动成为 search 的子 span。
    """
    get_client().update_current_span(
        input={"per_method_hits": method_hits or {}},
        metadata={"method": "rrf", "k": k},
    )
    merged = rrf_merge(ranked_lists, k)
    get_client().update_current_span(output=format_retrieval_observation(merged))
    return merged


async def _get_kb_name(kb_id: str) -> str:
    """查询知识库名称，失败返回 kb_id。"""
    try:
        async with async_session() as session:
            repo = KnowledgeBaseRepository(session)
            kb = await repo.get_by_id(kb_id)
            return kb.name if kb else kb_id
    except Exception:
        return kb_id


async def _apply_rerank(query: str, hits: list[dict], rerank_model_id: str) -> list[dict]:
    """对候选结果做重排（委托公用 rerank），用 relevance_score 覆盖 score 并降序返回。"""
    config = await model_service.get_model(rerank_model_id)
    if config is None:
        raise ValueError("排序模型配置不存在")
    if config.get("model_type") != "rerank":
        raise ValueError("所选模型不是排序模型")

    return await rerank(query, hits, config)


@traced_span(lambda query, kb_ids, method, top_k, similarity_threshold: f"recall_{method}")
async def _recall_milvus(
    query: str,
    kb_ids: list[str],
    method: str,
    top_k: int,
    similarity_threshold: float | None,
) -> list[dict]:
    """Milvus 单路召回（dense/sparse），逐 kb 并发，失败降级为空。

    独立 retriever span：记录 method / 命中数 / 耗时（嵌套的逐 kb retrieve span 保留）。
    """
    t0 = time.perf_counter()
    async def _one(kb_id: str):
        try:
            kwargs: dict[str, Any] = dict(
                query=query,
                kb_id=kb_id,
                top_k=top_k,
                retrieval_method=method,
            )
            if similarity_threshold is not None:
                kwargs["similarity_threshold"] = similarity_threshold
            hits = await retrieve(**kwargs)
            for h in hits:
                h["kb_id"] = kb_id
            return hits
        except Exception as e:
            logger.warning("知识库 %s %s 检索失败: %s", kb_id, method, e)
            return []

    per_kb = await asyncio.gather(*[_one(kid) for kid in kb_ids])
    all_hits = [h for hits in per_kb for h in hits]
    get_client().update_current_span(
        input=query,
        metadata={
            "method": method,
            "kb_ids": kb_ids,
            "top_k": top_k,
            "hit_count": len(all_hits),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        },
        output=format_retrieval_observation(all_hits),
    )
    return all_hits


@traced_span("recall_fulltext")
async def _recall_fulltext(
    query: str, kb_ids: list[str], top_k: int, highlight: bool
) -> list[dict]:
    """ES 全文检索（词库查询扩展，OR 语义）。

    独立 retriever span：记录 method=fulltext / 命中数 / 耗时。
    """
    t0 = time.perf_counter()
    await lexicon_service.ensure_cache()
    expanded = lexicon_service.expand_query(query)
    all_hits = await es_store.es_search(
        expanded, kb_ids, top_k=top_k, highlight=highlight
    )
    get_client().update_current_span(
        input=query,
        metadata={
            "method": "fulltext",
            "kb_ids": kb_ids,
            "top_k": top_k,
            "hit_count": len(all_hits),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        },
        output=format_retrieval_observation(all_hits),
    )
    return all_hits


@traced_span("search", "chain")
async def search(
    query: str,
    kb_ids: list[str],
    top_k: int = 10,
    similarity_threshold: float = 0.6,
    retrieval_methods: list[str] | None = None,
    rerank_model_id: str | None = None,
    rerank_top_n: int = 5,
    highlight: bool = True,
    query_rewrite: bool | None = None,
) -> dict[str, Any]:
    """在多个知识库中执行检索：单选直返，多选 RRF 融合，可再重排。

    Args:
        query: 检索关键词或自然语言描述
        kb_ids: 知识库 ID 列表
        top_k: 每个知识库的检索候选数量（也是无重排时的返回条数上限）
        similarity_threshold: 最终阈值（rerank 时按 rerank 分数过滤；sparse/fulltext 无阈值语义）
        retrieval_methods: 检索策略列表（dense/sparse/fulltext 可多选），为空则沿用服务端默认
        rerank_model_id: 排序模型配置 ID，为空则跳过重排
        rerank_top_n: 进入排序模型的候选数量
        highlight: 是否返回 ES 高亮片段（fulltext 生效）
        query_rewrite: 是否启用 LLM Query 改写；为空则沿用服务端默认 query_rewrite_enabled
            （改写只服务召回，rerank 与响应仍用原查询；无全局默认 LLM 或改写失败自动回退原词；
            多意图输入改写输出 list 时按子查询分别召回/重排，各取 top_k 后跨意图去重合并）

    Returns:
        {query, total, results: [{rank, score, text, doc_id, file_name, page_number,
                                  chunk_index, kb_id, kb_name, highlight}, ...]}
    """
    original_query = query
    if retrieval_methods is None:
        methods = list(settings.retrieval_default_methods)
    else:
        methods = list(dict.fromkeys(retrieval_methods))
    if not methods:
        raise ValueError("未选择任何检索方式")
    invalid = set(methods) - set(VALID_METHODS)
    if invalid:
        raise ValueError(f"不支持的检索方式: {sorted(invalid)}")

    if not kb_ids:
        get_client().update_current_span(output={"total": 0})
        return {"query": original_query, "total": 0, "results": []}

    # ===== 0. 可选 Query 改写（只服务召回；失败/无默认 LLM 回退原词） =====
    query_rewritten = False
    sub_queries: list[str] | None = None
    rewrite_on = settings.query_rewrite_enabled if query_rewrite is None else query_rewrite
    if rewrite_on:
        rewrite_config = await resolve_default_model_config("llm")  # 无全局默认 LLM → None → 跳过
        if rewrite_config:
            rewritten = await rewrite_query(original_query, rewrite_config)
            if isinstance(rewritten, list) and len(rewritten) >= 2:
                sub_queries = rewritten
                query_rewritten = True
            else:
                # 单意图（str；改写失败/空输出时 rewrite_query 已回退原词）
                query = rewritten
                query_rewritten = query != original_query

    get_client().update_current_span(
        input=original_query,
        metadata={
            "kb_ids": kb_ids,
            "top_k": top_k,
            "similarity_threshold": similarity_threshold,
            "retrieval_methods": methods,
            "rerank_model_id": rerank_model_id,
            "highlight": highlight,
            "query_rewritten": query_rewritten,
            "sub_queries": sub_queries,
        },
    )

    # 向量门槛：有排序模型时默认 0.0（全召回交给 rerank）；否则 similarity_threshold
    vector_gate = 0.0 if rerank_model_id else similarity_threshold

    # 召回数：rerank 时需覆盖 rerank_top_n（全局前 N 个进排序）；无 rerank 时即 top_k
    fetch_top_k = max(top_k, rerank_top_n) if rerank_model_id else top_k

    # ===== 1b. 多意图（≥2 条子查询）：逐子查询召回 → 按子查询 rerank → 各取 top_k → 跨意图合并去重 =====
    if sub_queries:
        intent_hits: list[list[dict]] = []
        for sub in sub_queries:
            per_sub: list[list[dict]] = []
            per_sub_hits: dict[str, int] = {}
            if "dense" in methods:
                hits = await _recall_milvus(sub, kb_ids, "dense", fetch_top_k, vector_gate)
                per_sub_hits["dense"] = len(hits)
                per_sub.append(hits)
            if "sparse" in methods:
                hits = await _recall_milvus(sub, kb_ids, "sparse", fetch_top_k, None)
                per_sub_hits["sparse"] = len(hits)
                per_sub.append(hits)
            if "fulltext" in methods:
                hits = await _recall_fulltext(
                    sub, kb_ids, fetch_top_k * settings.retrieval_es_top_k_factor, highlight,
                )
                per_sub_hits["fulltext"] = len(hits)
                per_sub.append(hits)
            if "graph" in methods:
                hits = await _recall_graph(sub, kb_ids, fetch_top_k)
                per_sub_hits["graph"] = len(hits)
                per_sub.append(hits)

            if len(per_sub) == 1:
                sub_hits = per_sub[0]
                sub_hits.sort(key=lambda x: x["score"], reverse=True)
            else:
                sub_hits = _rrf_merge_traced(
                    per_sub, k=settings.retrieval_rrf_k, method_hits=per_sub_hits
                )

            if rerank_model_id:
                candidates = sub_hits[:rerank_top_n]
                try:
                    # 多意图按各自子查询判断相关性（跨意图 rerank 分数不可比）
                    sub_hits = await _apply_rerank(sub, candidates, rerank_model_id)
                except Exception as e:
                    logger.warning("rerank 失败，回退原排序: %s", e)
                sub_hits = [h for h in sub_hits if h["score"] >= similarity_threshold]

            intent_hits.append(sub_hits[:top_k])  # 每个意图各取 top_k
        all_hits = merge_intent_hits(intent_hits)

    # ===== 1. 各路独立召回（单意图） =====
    else:
        ranked_lists = []
        per_method_hits: dict[str, int] = {}
        if "dense" in methods:
            hits = await _recall_milvus(query, kb_ids, "dense", fetch_top_k, vector_gate)
            per_method_hits["dense"] = len(hits)
            ranked_lists.append(hits)
        if "sparse" in methods:
            hits = await _recall_milvus(query, kb_ids, "sparse", fetch_top_k, None)
            per_method_hits["sparse"] = len(hits)
            ranked_lists.append(hits)
        if "fulltext" in methods:
            hits = await _recall_fulltext(
                query, kb_ids, fetch_top_k * settings.retrieval_es_top_k_factor, highlight,
            )
            per_method_hits["fulltext"] = len(hits)
            ranked_lists.append(hits)
        if "graph" in methods:
            hits = await _recall_graph(query, kb_ids, fetch_top_k)
            per_method_hits["graph"] = len(hits)
            ranked_lists.append(hits)
        get_client().update_current_span(metadata={"per_method_hits": per_method_hits})

        # ===== 2. 单选直返 / 多选 RRF 融合 =====
        if len(ranked_lists) == 1:
            all_hits = ranked_lists[0]
            # 单路召回逐 kb 拼接后需全局按分数降序（fulltext 的 ES 已有序，dense/sparse 跨 kb 需排序）
            all_hits.sort(key=lambda x: x["score"], reverse=True)
        else:
            all_hits = _rrf_merge_traced(
                ranked_lists, k=settings.retrieval_rrf_k, method_hits=per_method_hits
            )

        # ===== 3. 可选 rerank（只取前 rerank_top_n 个候选，失败回退原排序；重排用原查询判断相关性） =====
        if rerank_model_id:
            candidates = all_hits[:rerank_top_n]
            try:
                all_hits = await _apply_rerank(original_query, candidates, rerank_model_id)
            except Exception as e:
                logger.warning("rerank 失败，回退原排序: %s", e)
            # 最终阈值：rerank 成功按 rerank 分数过滤，失败按原分数过滤
            all_hits = [h for h in all_hits if h["score"] >= similarity_threshold]

    # ===== 4. 查询知识库名称（并发） =====
    kb_names: dict[str, str] = {}
    unique_kb_ids = set(h.get("kb_id", "") for h in all_hits if h.get("kb_id"))
    name_results = await asyncio.gather(*[_get_kb_name(kid) for kid in unique_kb_ids])
    for kid, name in zip(unique_kb_ids, name_results):
        kb_names[kid] = name

    # ===== 5. 组装响应 =====
    results = []
    for i, hit in enumerate(all_hits):
        kid = hit.get("kb_id", "")
        results.append({
            "rank": i + 1,
            "score": hit["score"],
            "text": hit["text"],
            "doc_id": hit["doc_id"],
            "file_name": hit["file_name"],
            "page_number": hit["page_number"],
            "chunk_index": hit["chunk_index"],
            "chunk_type": hit.get("chunk_type", "text"),
            "source_chunk_index": hit.get("source_chunk_index", -1),
            "kb_id": kid,
            "kb_name": kb_names.get(kid, kid),
            "highlight": hit.get("highlight"),
            "graph_entity": hit.get("graph_entity"),
        })

    get_client().update_current_span(output={"total": len(results)})
    return {
        "query": original_query,
        "total": len(results),
        "results": results,
    }
