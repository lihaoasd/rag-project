"""
文档检索业务逻辑。
Phase 1: 返回占位数据，完整实现后续补全。
"""
from typing import Any


async def search(
    kb_id: str,
    query: str,
    top_k: int = 10,
    similarity_threshold: float = 0.6,
    offset: int = 0,
    limit: int = 20,
) -> dict[str, Any]:
    """在知识库中执行语义检索。Phase 1 stub。"""
    return {
        "query": query,
        "total": 0,
        "results": [],
    }
