"""
检索引擎 — llama_index VectorStoreIndex + Retriever + SimilarityPostprocessor。
"""
from typing import Any

from llama_index.core import VectorStoreIndex
from llama_index.core.postprocessor import SimilarityPostprocessor
from llama_index.core.schema import QueryBundle
from llama_index.vector_stores.milvus import MilvusVectorStore
from pymilvus import DataType

from config import settings
from core.llm_factory import create_embedding
from utils.logging import get_logger

logger = get_logger(__name__)


def _get_vector_store(kb_id: str) -> MilvusVectorStore:
    """为指定知识库创建 MilvusVectorStore 实例（与 indexing.py 参数一致）。"""
    safe = kb_id.replace("-", "_")
    return MilvusVectorStore(
        uri=settings.milvus_uri,
        collection_name=f"kb_{safe}",
        dim=settings.embedding_dim,
        similarity_metric="IP",
        index_config={"index_type": "IVF_FLAT", "params": {"nlist": 128}},
        search_config={"nprobe": 16},
        scalar_field_names=["file_name", "page_number", "chunk_index"],
        scalar_field_types=[DataType.VARCHAR, DataType.INT64, DataType.INT64],
        text_key="text",
        overwrite=False,
    )


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
    # 1. 创建 vector_store + index
    vector_store = _get_vector_store(kb_id)

    # 检查 Collection 是否存在
    if vector_store.collection_name not in vector_store.client.list_collections():
        logger.warning("retrieve: Collection 不存在 %s", vector_store.collection_name)
        return []

    # 2. 构建 Embedding 模型
    embed_model = create_embedding(settings)

    # 3. 构建 VectorStoreIndex + Retriever
    index = VectorStoreIndex.from_vector_store(vector_store, embed_model=embed_model)
    retriever = index.as_retriever(similarity_top_k=top_k)

    # 4. 查询向量化
    query_embedding = await embed_model.aget_query_embedding(query)
    query_bundle = QueryBundle(query_str=query, embedding=query_embedding)

    # 5. 检索
    nodes = await retriever.aretrieve(query_bundle)

    # 6. 后处理：相似度阈值过滤
    processor = SimilarityPostprocessor(similarity_cutoff=similarity_threshold)
    filtered_nodes = processor.postprocess_nodes(nodes)

    # 7. 组装为 service 层兼容的 dict 格式
    hits = []
    for node_with_score in filtered_nodes:
        node = node_with_score.node
        hits.append({
            "score": round(node_with_score.score, 4) if node_with_score.score else 0.0,
            "text": node.text,
            "doc_id": node.metadata.get("doc_id", ""),
            "file_name": node.metadata.get("file_name", ""),
            "page_number": node.metadata.get("page_number", 1),
            "chunk_index": node.metadata.get("chunk_index", 0),
        })

    logger.info(
        "retrieve 完成: kb_id=%s, query=%s, top_k=%d, found=%d",
        kb_id,
        query[:80],
        top_k,
        len(hits),
    )
    return hits
