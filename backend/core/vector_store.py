"""
MilvusVectorStore 工厂 — 统一创建/获取知识库 Collection 实例。

每个知识库一个 Collection（kb_{kb_id}），使用 IVF_FLAT 索引。
Schema、Collection、Index 均由 MilvusVectorStore 自动管理（overwrite=False）。
"""
from llama_index.vector_stores.milvus import MilvusVectorStore
from pymilvus import DataType

from config import settings

# 标量字段（VARCHAR/INT64）需与写入时的 metadata 字段一一对应，
# indexing 与 retrieval 共用，避免 schema 不一致。
SCALAR_FIELD_NAMES = ["file_name", "page_number", "chunk_index", "heading", "heading_path"]
SCALAR_FIELD_TYPES = [DataType.VARCHAR, DataType.INT64, DataType.INT64, DataType.VARCHAR, DataType.VARCHAR]


def get_vector_store(kb_id: str, dim: int | None = None) -> MilvusVectorStore:
    """为指定知识库创建或获取 MilvusVectorStore 实例。

    dim 为空时回退 settings.embedding_dim（删除/删除集合等只读操作可不传）。
    """
    safe = kb_id.replace("-", "_")
    return MilvusVectorStore(
        uri=settings.milvus_uri,
        collection_name=f"kb_{safe}",
        dim=dim or settings.embedding_dim,
        similarity_metric="IP",
        index_config={"index_type": "IVF_FLAT", "params": {"nlist": 128}},
        search_config={"nprobe": 16},
        scalar_field_names=SCALAR_FIELD_NAMES,
        scalar_field_types=SCALAR_FIELD_TYPES,
        text_key="text",
        overwrite=False
    )
