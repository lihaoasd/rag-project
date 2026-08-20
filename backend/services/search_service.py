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

from langfuse import get_client, observe

from config import settings
from core import es_store
from core.database import async_session
from core.observability import format_retrieval_observation
from core.retrieval import retrieve, rerank
from repositories.kb_repository import KnowledgeBaseRepository
from services import lexicon_service, model_service
from utils.logging import get_logger

logger = get_logger(__name__)

VALID_METHODS = ("dense", "sparse", "fulltext")


def _rrf_merge(ranked_lists: list[list[dict]], k: int = 60) -> list[dict]:
    """RRF 融合多路召回（按 (doc_id, chunk_index) 对齐 rank 分数）。"""
    scores: dict[tuple, float] = {}
    hits_by_key: dict[tuple, dict] = {}
    for ranked in ranked_lists:
        for i, h in enumerate(ranked):
            key = (h.get("doc_id", ""), h.get("chunk_index", -1))
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + i + 1)
            hits_by_key[key] = h
    merged = []
    for key, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
        h = dict(hits_by_key[key])
        h["score"] = round(score, 4)
        h.setdefault("highlight", None)
        h.setdefault("kb_id", "")
        merged.append(h)
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


@observe(as_type="retriever", capture_input=False, capture_output=False)
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


@observe(as_type="retriever", capture_input=False, capture_output=False)
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


@observe(name="search", capture_input=False, capture_output=False)
async def search(
    query: str,
    kb_ids: list[str],
    top_k: int = 10,
    similarity_threshold: float = 0.6,
    retrieval_methods: list[str] | None = None,
    rerank_model_id: str | None = None,
    rerank_top_n: int = 5,
    highlight: bool = True,
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

    Returns:
        {query, total, results: [{rank, score, text, doc_id, file_name, page_number,
                                  chunk_index, kb_id, kb_name, highlight}, ...]}
    """
    if retrieval_methods is None:
        methods = list(settings.retrieval_default_methods)
    else:
        methods = list(dict.fromkeys(retrieval_methods))
    if not methods:
        raise ValueError("未选择任何检索方式")
    invalid = set(methods) - set(VALID_METHODS)
    if invalid:
        raise ValueError(f"不支持的检索方式: {sorted(invalid)}")

    get_client().update_current_span(
        input=query,
        metadata={
            "kb_ids": kb_ids,
            "top_k": top_k,
            "similarity_threshold": similarity_threshold,
            "retrieval_methods": methods,
            "rerank_model_id": rerank_model_id,
            "highlight": highlight,
        },
    )

    if not kb_ids:
        get_client().update_current_span(output={"total": 0})
        return {"query": query, "total": 0, "results": []}

    # 向量门槛：有排序模型时默认 0.0（全召回交给 rerank）；否则 similarity_threshold
    vector_gate = 0.0 if rerank_model_id else similarity_threshold

    # 召回数：rerank 时需覆盖 rerank_top_n（全局前 N 个进排序）；无 rerank 时即 top_k
    fetch_top_k = max(top_k, rerank_top_n) if rerank_model_id else top_k

    # ===== 1. 各路独立召回 =====
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
    get_client().update_current_span(metadata={"per_method_hits": per_method_hits})

    # ===== 2. 单选直返 / 多选 RRF 融合 =====
    if len(ranked_lists) == 1:
        all_hits = ranked_lists[0]
        # 单路召回逐 kb 拼接后需全局按分数降序（fulltext 的 ES 已有序，dense/sparse 跨 kb 需排序）
        all_hits.sort(key=lambda x: x["score"], reverse=True)
    else:
        all_hits = _rrf_merge(ranked_lists, k=settings.retrieval_rrf_k)

    # ===== 3. 可选 rerank（只取前 rerank_top_n 个候选，失败回退原排序） =====
    if rerank_model_id:
        candidates = all_hits[:rerank_top_n]
        try:
            all_hits = await _apply_rerank(query, candidates, rerank_model_id)
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
        })

    get_client().update_current_span(output={"total": len(results)})
    return {
        "query": query,
        "total": len(results),
        "results": results,
    }
