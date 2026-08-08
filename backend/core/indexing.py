"""
索引引擎 — LlamaIndex 文本分割 + Embedding 向量化 + Milvus 写入。
每个知识库一个 Collection（kb_{kb_id}），使用 IVF_FLAT 索引。
"""
from typing import Any

from llama_index.core import Document as LlamaDocument
from llama_index.core.node_parser import SentenceSplitter
from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    connections,
    utility,
)

from config import settings
from core.llm_factory import create_embedding
from utils.logging import get_logger

logger = get_logger(__name__)

# Milvus 连接状态
_connected = False

# 集合过期时间（秒），默认 7 天清理
TTL_SECONDS = 7 * 24 * 3600


def _ensure_connection() -> None:
    """确保 Milvus 已连接（幂等）。"""
    global _connected
    if not _connected:
        connections.connect(
            alias="default",
            host=settings.milvus_host,
            port=settings.milvus_port,
        )
        _connected = True
        logger.info("Milvus 连接成功: %s:%d", settings.milvus_host, settings.milvus_port)


def _collection_name(kb_id: str) -> str:
    """生成 Collection 名称。Milvus 不支持连字符，替换为下划线。"""
    safe = kb_id.replace("-", "_")
    return f"kb_{safe}"


def _create_schema(embedding_dim: int) -> CollectionSchema:
    """定义 Collection Schema：主键 + 向量 + 标量元数据。"""
    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=embedding_dim),
        FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="file_name", dtype=DataType.VARCHAR, max_length=512),
        FieldSchema(name="page_number", dtype=DataType.INT64),
        FieldSchema(name="chunk_index", dtype=DataType.INT64),
        FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535),
    ]
    return CollectionSchema(fields, description="Bid Document RAG — Vector Index")


def _get_or_create_collection(kb_id: str, embedding_dim: int) -> Collection:
    """获取已有 Collection 或创建新 Collection。"""
    _ensure_connection()
    col_name = _collection_name(kb_id)

    if utility.has_collection(col_name):
        coll = Collection(col_name)
        logger.debug("Milvus Collection 已存在: %s", col_name)
        return coll

    schema = _create_schema(embedding_dim)
    coll = Collection(col_name, schema)
    logger.info("Milvus Collection 已创建: %s (dim=%d)", col_name, embedding_dim)
    return coll


def _ensure_index(coll: Collection) -> None:
    """确保 Collection 有索引。没索引就创建 IVF_FLAT。"""
    if coll.has_index():
        return
    index_params = {
        "index_type": "IVF_FLAT",
        "metric_type": "IP",
        "params": {"nlist": 128},
    }
    coll.create_index("embedding", index_params)
    logger.info("Milvus 索引已创建: %s (IVF_FLAT)", coll.name)


async def build_index(
    chunks: list[str],
    metadata_list: list[dict[str, Any]],
    kb_id: str,
    embedding_dim: int | None = None,
) -> list[int]:
    """为文档切片构建向量索引并写入 Milvus。

    Args:
        chunks: 文本切片列表
        metadata_list: 每个切片对应的元数据（doc_id, file_name, page_number, chunk_index）
        kb_id: 知识库 ID
        embedding_dim: 向量维度，默认从 settings 读取

    Returns:
        Milvus 自动生成的主键 ID 列表
    """
    if not chunks:
        logger.warning("build_index: 空切片列表，跳过")
        return []

    dim = embedding_dim or settings.embedding_dim
    logger.info(
        "build_index 开始: kb_id=%s, chunks=%d, dim=%d",
        kb_id,
        len(chunks),
        dim,
    )

    # 1. 获取 Embedding 模型
    embed_model = create_embedding(settings)

    # 2. 批量向量化
    logger.debug("正在向量化 %d 个切片...", len(chunks))
    embeddings = []
    batch_size = 32
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        batch_embeddings = await embed_model.aget_text_embedding_batch(batch)
        embeddings.extend(batch_embeddings)
    logger.debug("向量化完成: %d 个向量", len(embeddings))

    # 3. 获取或创建 Collection
    coll = _get_or_create_collection(kb_id, dim)

    # 4. 准备插入数据
    insert_data = {
        "embedding": embeddings,
        "doc_id": [m["doc_id"] for m in metadata_list],
        "file_name": [m.get("file_name", "") for m in metadata_list],
        "page_number": [m.get("page_number", 1) for m in metadata_list],
        "chunk_index": [m.get("chunk_index", 0) for m in metadata_list],
        "text": [chunks[i] if i < len(chunks) else "" for i in range(len(metadata_list))],
    }

    # 5. 插入
    mr = coll.insert(list(insert_data.values()))
    coll.flush()
    _ensure_index(coll)

    pk_list = list(mr.primary_keys)
    logger.info(
        "build_index 完成: kb_id=%s, collection=%s, inserted=%d",
        kb_id,
        coll.name,
        len(pk_list),
    )
    return pk_list


async def delete_index(kb_id: str, doc_id: str) -> None:
    """从 Milvus 删除指定文档的所有向量。

    Args:
        kb_id: 知识库 ID
        doc_id: 文档 ID（UUID 字符串）
    """
    _ensure_connection()
    col_name = _collection_name(kb_id)

    if not utility.has_collection(col_name):
        logger.warning("delete_index: Collection 不存在 %s", col_name)
        return

    coll = Collection(col_name)
    coll.load()
    expr = f'doc_id == "{doc_id}"'
    coll.delete(expr)
    coll.flush()
    logger.info("delete_index 完成: kb_id=%s, doc_id=%s", kb_id, doc_id)


def drop_kb_collection(kb_id: str) -> None:
    """删除知识库对应的整个 Milvus Collection（删除知识库时使用）。

    Args:
        kb_id: 知识库 ID
    """
    _ensure_connection()
    col_name = _collection_name(kb_id)

    if utility.has_collection(col_name):
        utility.drop_collection(col_name)
        logger.info("Milvus Collection 已删除: %s", col_name)
    else:
        logger.debug("drop_kb_collection: Collection 不存在 %s", col_name)


def chunk_documents(
    documents: list[LlamaDocument],
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """将 LlamaIndex Document 列表分割为文本切片。

    Args:
        documents: 解析好的 LlamaIndex Document 列表
        chunk_size: 切片大小（tokens），默认从 settings 读取
        chunk_overlap: 切片重叠（tokens），默认从 settings 读取

    Returns:
        (chunks_text_list, metadata_dict_list)
    """
    size = chunk_size or settings.chunk_size
    overlap = chunk_overlap or settings.chunk_overlap

    splitter = SentenceSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
    )

    nodes = splitter.get_nodes_from_documents(documents)
    logger.info("文档切片完成: documents=%d → chunks=%d", len(documents), len(nodes))

    chunks: list[str] = []
    metadatas: list[dict[str, Any]] = []

    for node in nodes:
        chunks.append(node.text)
        meta = dict(node.metadata)
        # 确保必要字段存在
        meta.setdefault("doc_id", "unknown")
        meta.setdefault("file_name", "unknown")
        meta.setdefault("page_number", 1)
        meta.setdefault("chunk_index", 0)
        metadatas.append(meta)

    return chunks, metadatas
