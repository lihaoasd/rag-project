"""
MilvusVectorStore 工厂 — 统一创建/获取知识库 Collection 实例。

每个知识库一个 Collection（kb_{kb_id}），使用 IVF_FLAT 索引。
Schema、Collection、Index 均由 MilvusVectorStore 自动管理（overwrite=False）。

开启 retrieval_hybrid_enabled 时，启用 Milvus 内置 BM25 稀疏字段
（SPARSE_FLOAT_VECTOR 字段 + BM25 函数 + jieba 中文分词），供 sparse 检索策略使用。
"""
from llama_index.vector_stores.milvus import MilvusVectorStore
from llama_index.vector_stores.milvus.utils import BM25BuiltInFunction
from pymilvus import DataType

from config import settings

# 标量字段（VARCHAR/INT64）需与写入时的 metadata 字段一一对应，
# indexing 与 retrieval 共用，避免 schema 不一致。
SCALAR_FIELD_NAMES = [
    "file_name",
    "page_number",
    "chunk_index",
    "heading",
    "heading_path",
    "chunk_type",
    "source_chunk_index",
]
SCALAR_FIELD_TYPES = [
    DataType.VARCHAR,
    DataType.INT64,
    DataType.INT64,
    DataType.VARCHAR,
    DataType.VARCHAR,
    DataType.VARCHAR,
    DataType.INT64,
]


def get_vector_store(
    kb_id: str,
    dim: int | None = None,
) -> MilvusVectorStore:
    """为指定知识库创建或获取 MilvusVectorStore 实例。

    dim 为空时回退 settings.embedding_dim（删除/删除集合等只读操作可不传）。
    """
    safe = kb_id.replace("-", "_")
    kwargs = dict(
        uri=settings.milvus_uri,
        collection_name=f"kb_{safe}",
        dim=dim or settings.embedding_dim,
        similarity_metric="IP",
        index_config={"index_type": "IVF_FLAT", "params": {"nlist": 128}},
        search_config={"nprobe": 16},
        scalar_field_names=SCALAR_FIELD_NAMES,
        scalar_field_types=SCALAR_FIELD_TYPES,
        text_key="text",
        overwrite=False,
    )
    if settings.retrieval_hybrid_enabled:
        kwargs["enable_sparse"] = True
        kwargs["sparse_embedding_function"] = BM25BuiltInFunction(
            input_field_names="text",
            output_field_names="sparse_embedding",
            analyzer_params={"tokenizer": "jieba"},
        )
    return MilvusVectorStore(**kwargs)
