"""
文档检索业务逻辑 — 多路召回（Milvus 向量 / ES 全文）+ 合并排序 + 可选重排。

检索方式（retrieval_method）：
- vector    : Milvus 稠密向量（现状）
- hybrid    : Milvus 稠密 + Milvus 内置 BM25 稀疏，RRF/Weighted（现状）
- fulltext  : ES + IK 全文检索（关键词精确命中 + 后端高亮）
- hybrid_es : Milvus 稠密 + ES 全文，Python 端 RRF 融合
"""
import asyncio
from typing import Any

from langfuse import get_client, observe

from config import settings
from core import es_store
from core.database import async_session
from core.retrieval import retrieve, rerank
from repositories.kb_repository import KnowledgeBaseRepository
from services import lexicon_service, model_service
from utils.logging import get_logger

logger = get_logger(__name__)

VALID_METHODS = ("vector", "fulltext", "hybrid", "hybrid_es")


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


@observe(name="search", capture_input=False, capture_output=False)
async def search(
    query: str,
    kb_ids: list[str],
    top_k: int = 10,
    similarity_threshold: float = 0.6,
    retrieval_method: str | None = None,
    hybrid_ranker: str | None = None,
    hybrid_weights: list[float] | None = None,
    rerank_model_id: str | None = None,
    rerank_top_n: int = 5,
    rerank_vector_threshold: float | None = None,
    highlight: bool = True,
) -> dict[str, Any]:
    """在多个知识库中执行检索，按分数合并排序（可融合多路召回）。

    Args:
        query: 检索关键词或自然语言描述
        kb_ids: 知识库 ID 列表
        top_k: 每个知识库的检索候选数量（也是无重排时的返回条数上限）
        similarity_threshold: 最终阈值（rerank 时按 rerank 分数过滤；fulltext 无阈值语义，仅 rerank 生效）
        retrieval_method: 检索方式：vector/fulltext/hybrid/hybrid_es，为空则沿用服务端默认
        hybrid_ranker: 混合检索融合策略：rrf/weighted，仅 hybrid 生效
        hybrid_weights: WeightedRanker 的 [向量权重, 关键词权重]
        rerank_model_id: 排序模型配置 ID，为空则跳过重排
        rerank_top_n: 进入排序模型的候选数量
        rerank_vector_threshold: 进入排序模型的最低向量相似度，为空则等于 similarity_threshold
        highlight: 是否返回 ES 高亮片段（fulltext/hybrid_es 生效）

    Returns:
        {query, total, results: [{rank, score, text, doc_id, file_name, page_number,
                                  chunk_index, kb_id, kb_name, highlight}, ...]}
    """
    method = retrieval_method or settings.retrieval_default_method
    if method not in VALID_METHODS:
        raise ValueError(f"不支持的检索方式: {method}")

    get_client().update_current_span(
        input=query,
        metadata={
            "kb_ids": kb_ids,
            "top_k": top_k,
            "similarity_threshold": similarity_threshold,
            "retrieval_method": method,
            "hybrid_ranker": hybrid_ranker,
            "rerank_model_id": rerank_model_id,
            "highlight": highlight,
        },
    )

    if not kb_ids:
        get_client().update_current_span(output={"total": 0})
        return {"query": query, "total": 0, "results": []}

    # 向量门槛：显式 rerank_vector_threshold 优先；有排序模型时默认 0.0（全召回交给 rerank）；否则 similarity_threshold
    if rerank_vector_threshold is not None:
        vector_gate = rerank_vector_threshold
    elif rerank_model_id:
        vector_gate = 0.0
    else:
        vector_gate = similarity_threshold

    # 召回数：rerank 时需覆盖 rerank_top_n（全局前 N 个进排序）；无 rerank 时即 top_k
    fetch_top_k = max(top_k, rerank_top_n) if rerank_model_id else top_k

    # ===== 1. 多路召回 =====
    if method == "fulltext":
        # ES 全文检索（词库查询扩展，OR 语义）
        await lexicon_service.ensure_cache()
        expanded = lexicon_service.expand_query(query)
        all_hits = await es_store.es_search(
            expanded,
            kb_ids,
            top_k=fetch_top_k * settings.retrieval_es_top_k_factor,
            highlight=highlight,
        )

    elif method == "hybrid_es":
        # 向量路（逐 kb 并发，纯稠密）+ ES 路（词库扩展）→ RRF 融合
        await lexicon_service.ensure_cache()
        expanded = lexicon_service.expand_query(query)

        async def _vector_one(kb_id: str):
            try:
                hits = await retrieve(
                    query=query,
                    kb_id=kb_id,
                    top_k=fetch_top_k,
                    similarity_threshold=vector_gate,
                    retrieval_method="vector",
                )
                for h in hits:
                    h["kb_id"] = kb_id
                return hits
            except Exception as e:
                logger.warning("知识库 %s 向量检索失败: %s", kb_id, e)
                return []

        vector_per_kb = await asyncio.gather(*[_vector_one(kid) for kid in kb_ids])
        vector_hits = [h for hits in vector_per_kb for h in hits]
        es_hits = await es_store.es_search(
            expanded,
            kb_ids,
            top_k=fetch_top_k * settings.retrieval_es_top_k_factor,
            highlight=highlight,
        )
        all_hits = _rrf_merge([vector_hits, es_hits], k=settings.retrieval_rrf_k)

    else:
        # vector / hybrid：沿用现有 Milvus 路径
        async def _retrieve_one(kb_id: str):
            try:
                hits = await retrieve(
                    query=query,
                    kb_id=kb_id,
                    top_k=fetch_top_k,
                    similarity_threshold=vector_gate,
                    retrieval_method=method,
                    hybrid_ranker=hybrid_ranker,
                    hybrid_weights=hybrid_weights,
                )
                # 打上知识库标签
                for h in hits:
                    h["kb_id"] = kb_id
                return hits
            except Exception as e:
                logger.warning("知识库 %s 检索失败: %s", kb_id, e)
                return []

        results_per_kb = await asyncio.gather(*[_retrieve_one(kid) for kid in kb_ids])
        all_hits = [h for hits in results_per_kb for h in hits]
        all_hits.sort(key=lambda x: x["score"], reverse=True)

    # ===== 2. 可选 rerank（只取前 rerank_top_n 个候选，失败回退原排序） =====
    if rerank_model_id:
        candidates = all_hits[:rerank_top_n]
        try:
            all_hits = await _apply_rerank(query, candidates, rerank_model_id)
        except Exception as e:
            logger.warning("rerank 失败，回退原排序: %s", e)
        # 最终阈值：rerank 成功按 rerank 分数过滤，失败按原分数过滤
        all_hits = [h for h in all_hits if h["score"] >= similarity_threshold]

    # ===== 3. 查询知识库名称（并发） =====
    kb_names: dict[str, str] = {}
    unique_kb_ids = set(h.get("kb_id", "") for h in all_hits if h.get("kb_id"))
    name_results = await asyncio.gather(*[_get_kb_name(kid) for kid in unique_kb_ids])
    for kid, name in zip(unique_kb_ids, name_results):
        kb_names[kid] = name

    # ===== 4. 组装响应 =====
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
