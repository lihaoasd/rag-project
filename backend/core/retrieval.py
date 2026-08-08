"""
检索引擎 — Milvus 向量相似度搜索 + Embedding 查询向量化。
"""
from typing import Any

from pymilvus import Collection, connections, utility

from config import settings
from core.llm_factory import create_embedding
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
        similarity_threshold: 相似度阈值（IP 分数），低于此分数丢弃

    Returns:
        按分数降序排列的结果列表:
        [{score, text, doc_id, file_name, page_number, chunk_index}, ...]
    """
    # 1. 查询向量化
    embed_model = create_embedding(settings)
    query_embedding = await embed_model.aget_query_embedding(query)

    # 2. 定位 Milvus Collection
    safe_kb = kb_id.replace("-", "_")
    col_name = f"kb_{safe_kb}"

    if not utility.has_collection(col_name):
        logger.warning("retrieve: Collection 不存在 %s", col_name)
        return []

    coll = Collection(col_name)
    coll.load()

    # 3. 向量搜索
    search_params = {
        "metric_type": "IP",
        "params": {"nprobe": 16},
    }
    output_fields = ["doc_id", "file_name", "page_number", "chunk_index", "text"]

    results = coll.search(
        data=[query_embedding],
        anns_field="embedding",
        param=search_params,
        limit=top_k,
        output_fields=output_fields,
    )

    # 4. 过滤 + 组装结果
    hits = []
    for hit in results[0]:
        if hit.score < similarity_threshold:
            continue
        hits.append({
            "score": round(hit.score, 4),
            "text": hit.entity.get("text", ""),
            "doc_id": hit.entity.get("doc_id", ""),
            "file_name": hit.entity.get("file_name", ""),
            "page_number": hit.entity.get("page_number", 1),
            "chunk_index": hit.entity.get("chunk_index", 0),
        })

    logger.info(
        "retrieve 完成: kb_id=%s, query=%s, top_k=%d, found=%d",
        kb_id,
        query[:80],
        top_k,
        len(hits),
    )
    return hits
