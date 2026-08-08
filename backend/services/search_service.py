"""
文档检索业务逻辑 — 调用检索引擎 + 分页组装。
"""
from typing import Any

from core.retrieval import retrieve
from utils.logging import get_logger

logger = get_logger(__name__)


async def search(
    kb_id: str,
    query: str,
    top_k: int = 10,
    similarity_threshold: float = 0.6,
    offset: int = 0,
    limit: int = 20,
) -> dict[str, Any]:
    """在知识库中执行语义检索。

    Args:
        kb_id: 知识库 ID
        query: 检索关键词或自然语言描述
        top_k: 向量检索候选数量
        similarity_threshold: 相似度阈值
        offset: 分页偏移
        limit: 每页条数

    Returns:
        {query, total, results: [{rank, score, text, doc_id, file_name, page_number, chunk_index}]}
    """
    # 1. 向量检索（多取一些用于分页）
    fetch_count = offset + limit
    hits = await retrieve(
        query=query,
        kb_id=kb_id,
        top_k=max(top_k, fetch_count),
        similarity_threshold=similarity_threshold,
    )

    # 2. 分页
    total = len(hits)
    page = hits[offset : offset + limit]

    # 3. 组装响应
    results = []
    for i, hit in enumerate(page):
        results.append({
            "rank": offset + i + 1,
            "score": hit["score"],
            "text": hit["text"],
            "doc_id": hit["doc_id"],
            "file_name": hit["file_name"],
            "page_number": hit["page_number"],
            "chunk_index": hit["chunk_index"],
        })

    return {
        "query": query,
        "total": total,
        "results": results,
    }
