"""
文档检索业务逻辑 — 调用检索引擎 + 分页组装。
"""
import asyncio
from typing import Any

from core.database import async_session
from core.retrieval import retrieve
from repositories.kb_repository import KnowledgeBaseRepository
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


async def search(
    query: str,
    kb_ids: list[str],
    top_k: int = 10,
    similarity_threshold: float = 0.6,
    offset: int = 0,
    limit: int = 20,
) -> dict[str, Any]:
    """在多个知识库中执行语义检索，按分数合并排序。

    Args:
        query: 检索关键词或自然语言描述
        kb_ids: 知识库 ID 列表
        top_k: 每个知识库的向量检索候选数量
        similarity_threshold: 相似度阈值
        offset: 分页偏移
        limit: 每页条数

    Returns:
        {query, total, results: [{rank, score, text, doc_id, file_name, page_number, chunk_index, kb_id, kb_name}]}
    """
    if not kb_ids:
        return {"query": query, "total": 0, "results": []}

    # 分页需保证候选数足以覆盖 offset+limit（多库并发各取 fetch_top_k）
    fetch_top_k = max(top_k, offset + limit)

    # 1. 并发检索所有知识库
    async def _retrieve_one(kb_id: str):
        try:
            hits = await retrieve(
                query=query,
                kb_id=kb_id,
                top_k=fetch_top_k,
                similarity_threshold=similarity_threshold,
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

    # 3. 分页
    total = len(all_hits)
    page = all_hits[offset : offset + limit]

    # 4. 查询知识库名称（并发）
    kb_names: dict[str, str] = {}
    unique_kb_ids = set(h["kb_id"] for h in page)
    name_results = await asyncio.gather(*[_get_kb_name(kid) for kid in unique_kb_ids])
    for kid, name in zip(unique_kb_ids, name_results):
        kb_names[kid] = name

    # 5. 组装响应
    results = []
    for i, hit in enumerate(page):
        kid = hit.get("kb_id", "")
        results.append({
            "rank": offset + i + 1,
            "score": hit["score"],
            "text": hit["text"],
            "doc_id": hit["doc_id"],
            "file_name": hit["file_name"],
            "page_number": hit["page_number"],
            "chunk_index": hit["chunk_index"],
            "kb_id": kid,
            "kb_name": kb_names.get(kid, kid),
        })

    return {
        "query": query,
        "total": total,
        "results": results,
    }
