"""
检索引擎 — Milvus 向量相似度搜索 + LlamaIndex Retrieval。
Phase 1: 仅定义接口签名，具体实现后续补全。
"""
from typing import Any

from utils.logging import get_logger

logger = get_logger(__name__)


async def retrieve(
    query: str,
    kb_id: str,
    top_k: int = 5,
    similarity_threshold: float = 0.6,
) -> list[dict[str, Any]]:
    """在指定知识库中执行语义检索。

    Args:
        query: 检索关键词或自然语言描述
        kb_id: 目标知识库 ID
        top_k: 返回的最相关结果数
        similarity_threshold: 相似度阈值，低于此分数丢弃

    Returns:
        [{score, text, doc_id, file_name, page_number, chunk_index}, ...]
    """
    logger.warning(
        "retrieve 被调用但尚未实现 (Phase 2): kb_id=%s, query=%s",
        kb_id,
        query[:80],
    )
    raise NotImplementedError("Retrieval engine not yet implemented — Phase 2")
