"""
文档检索业务逻辑 — 调用检索引擎 + 合并排序 + 可选重排。
"""
import asyncio
from typing import Any

from langfuse import get_client, observe

from core.database import async_session
from core.retrieval import retrieve, rerank
from repositories.kb_repository import KnowledgeBaseRepository
from services import model_service
from utils.logging import get_logger

logger = get_logger(__name__)


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
    rerank_model_id: str | None = None,
    rerank_top_n: int = 20,
    rerank_vector_threshold: float | None = None,
) -> dict[str, Any]:
    """在多个知识库中执行语义检索，按分数合并排序。

    Args:
        query: 检索关键词或自然语言描述
        kb_ids: 知识库 ID 列表
        top_k: 每个知识库的向量检索候选数量（也是无重排时的返回条数上限）
        similarity_threshold: 最终相似度阈值（rerank 时按 rerank 分数过滤，否则按向量分数）
        rerank_model_id: 排序模型配置 ID，为空则跳过重排
        rerank_top_n: 进入排序模型的向量候选数量
        rerank_vector_threshold: 进入排序模型的最低向量相似度，为空则等于 similarity_threshold

    Returns:
        {query, total, results: [{rank, score, text, doc_id, file_name, page_number, chunk_index, kb_id, kb_name}]}
    """
    get_client().update_current_span(
        input=query,
        metadata={
            "kb_ids": kb_ids,
            "top_k": top_k,
            "similarity_threshold": similarity_threshold,
            "rerank_model_id": rerank_model_id,
        },
    )

    if not kb_ids:
        get_client().update_current_span(output={"total": 0})
        return {"query": query, "total": 0, "results": []}

    # 向量门槛：有排序模型 → rerank_vector_threshold（缺省=similarity_threshold）；否则 similarity_threshold
    vector_gate = rerank_vector_threshold if rerank_vector_threshold is not None else similarity_threshold

    # 召回数：rerank 时需覆盖 rerank_top_n（全局前 N 个进排序）；无 rerank 时即 top_k
    fetch_top_k = max(top_k, rerank_top_n) if rerank_model_id else top_k

    # 1. 并发检索所有知识库（向量门槛过滤）
    async def _retrieve_one(kb_id: str):
        try:
            hits = await retrieve(
                query=query,
                kb_id=kb_id,
                top_k=fetch_top_k,
                similarity_threshold=vector_gate,
            )
            # 打上知识库标签
            for h in hits:
                h["kb_id"] = kb_id
            return hits
        except Exception as e:
            logger.warning("知识库 %s 检索失败: %s", kb_id, e)
            return []

    results_per_kb = await asyncio.gather(*[_retrieve_one(kid) for kid in kb_ids])

    # 2. 合并所有结果，按分数降序排序
    all_hits = [h for hits in results_per_kb for h in hits]
    all_hits.sort(key=lambda x: x["score"], reverse=True)

    # 2.5 可选 rerank（只取前 rerank_top_n 个候选，失败回退相似度排序）
    if rerank_model_id:
        candidates = all_hits[:rerank_top_n]
        try:
            all_hits = await _apply_rerank(query, candidates, rerank_model_id)
        except Exception as e:
            logger.warning("rerank 失败，回退相似度排序: %s", e)
        # 最终阈值：rerank 成功按 rerank 分数过滤，失败按向量分数过滤
        all_hits = [h for h in all_hits if h["score"] >= similarity_threshold]

    # 3. 查询知识库名称（并发）
    kb_names: dict[str, str] = {}
    unique_kb_ids = set(h["kb_id"] for h in all_hits)
    name_results = await asyncio.gather(*[_get_kb_name(kid) for kid in unique_kb_ids])
    for kid, name in zip(unique_kb_ids, name_results):
        kb_names[kid] = name

    # 4. 组装响应
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
            "kb_id": kid,
            "kb_name": kb_names.get(kid, kid),
        })

    get_client().update_current_span(output={"total": len(results)})
    return {
        "query": query,
        "total": len(results),
        "results": results,
    }
