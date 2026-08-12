"""
索引引擎 — LlamaIndex 文本分割 + MilvusVectorStore 向量写入。
每个知识库一个 Collection（kb_{kb_id}），使用 IVF_FLAT 索引。

重要：所有同步 Milvus 操作（add/delete）均通过 asyncio.to_thread()
放到线程池执行，embedding 批次间添加 yield 点，避免阻塞事件循环。
"""
import asyncio
from typing import Any

from llama_index.core import Document as LlamaDocument
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import NodeRelationship, RelatedNodeInfo, TextNode
from llama_index.vector_stores.milvus import MilvusVectorStore
from pymilvus import DataType

from config import settings
from core.llm_factory import create_embedding
from utils.logging import get_logger

logger = get_logger(__name__)

# 集合过期时间（秒），默认 7 天清理
TTL_SECONDS = 7 * 24 * 3600


def _get_vector_store(kb_id: str) -> MilvusVectorStore:
    """为指定知识库创建或获取 MilvusVectorStore 实例。

    Schema、Collection、Index 均由 MilvusVectorStore 自动管理。
    """
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
        collection_properties={"collection.ttl.seconds": TTL_SECONDS},
    )


async def build_index(
    chunks: list[str],
    metadata_list: list[dict[str, Any]],
    kb_id: str,
    embedding_dim: int | None = None,
) -> list[str]:
    """为文档切片构建向量索引并写入 Milvus。

    Args:
        chunks: 文本切片列表
        metadata_list: 每个切片对应的元数据（doc_id, file_name, page_number, chunk_index）
        kb_id: 知识库 ID
        embedding_dim: 向量维度，默认从 settings 读取

    Returns:
        llama_index TextNode ID 列表（VARCHAR 格式，用于 PG 存储）
    """
    if not chunks:
        logger.warning("build_index: 空切片列表，跳过")
        return []

    logger.info(
        "build_index 开始: kb_id=%s, chunks=%d, dim=%d",
        kb_id,
        len(chunks),
        embedding_dim or settings.embedding_dim,
    )

    # 1. 获取 Embedding 模型
    embed_model = create_embedding(settings)

    # 2. 创建 TextNode 列表（含 SOURCE 关系，确保 ref_doc_id 正确映射到 doc_id 字段）
    text_nodes: list[TextNode] = []
    for i, (text, meta) in enumerate(zip(chunks, metadata_list)):
        doc_id = meta.get("doc_id", "")
        node = TextNode(
            text=text,
            metadata={
                "doc_id": doc_id,
                "file_name": meta.get("file_name", ""),
                "page_number": meta.get("page_number", 1),
                "chunk_index": meta.get("chunk_index", i),
            },
            relationships={
                NodeRelationship.SOURCE: RelatedNodeInfo(node_id=doc_id),
            },
        )
        text_nodes.append(node)

    # 3. 批量向量化并设置到 node.embedding
    logger.debug("正在向量化 %d 个切片...", len(chunks))
    embeddings = []
    batch_size = 32
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        batch_embeddings = await embed_model.aget_text_embedding_batch(batch)
        embeddings.extend(batch_embeddings)
        # 每批次之间让出事件循环，避免长时间占据
        await asyncio.sleep(0)
    logger.debug("向量化完成: %d 个向量", len(embeddings))

    for node, emb in zip(text_nodes, embeddings):
        node.embedding = emb

    # 4. 通过 MilvusVectorStore 写入（同步 I/O，放到线程池）
    vector_store = _get_vector_store(kb_id)
    node_ids = await asyncio.to_thread(vector_store.add, text_nodes)
    logger.info(
        "build_index 完成: kb_id=%s, collection=%s, inserted=%d",
        kb_id,
        vector_store.collection_name,
        len(node_ids),
    )
    return node_ids


async def delete_index(kb_id: str, doc_id: str) -> None:
    """从 Milvus 删除指定文档的所有向量。

    Args:
        kb_id: 知识库 ID
        doc_id: 文档 ID（UUID 字符串）— 对应 TextNode 的 ref_doc_id
    """
    vector_store = _get_vector_store(kb_id)
    await asyncio.to_thread(vector_store.delete, ref_doc_id=doc_id)
    logger.info("delete_index 完成: kb_id=%s, doc_id=%s", kb_id, doc_id)


def drop_kb_collection(kb_id: str) -> None:
    """删除知识库对应的整个 Milvus Collection（删除知识库时使用）。

    Args:
        kb_id: 知识库 ID
    """
    vector_store = _get_vector_store(kb_id)
    col_name = vector_store.collection_name
    if col_name in vector_store.client.list_collections():
        vector_store.client.drop_collection(col_name)
        logger.info("Milvus Collection 已删除: %s", col_name)
    else:
        logger.debug("drop_kb_collection: Collection 不存在 %s", col_name)


def chunk_documents(
    documents: list[LlamaDocument],
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> tuple[list[str], list[dict[str, Any]], list[TextNode]]:
    """将 LlamaIndex Document 列表分割为文本切片和 TextNode。

    SentenceSplitter 生成的 raw_nodes 已携带 SOURCE 关系，
    此处仅增补 metadata 默认值，不创建新节点以保持关系链完整。

    Args:
        documents: 解析好的 LlamaIndex Document 列表
        chunk_size: 切片大小（tokens），默认从 settings 读取
        chunk_overlap: 切片重叠（tokens），默认从 settings 读取

    Returns:
        (chunks_text_list, metadata_dict_list, text_node_list)
    """
    size = chunk_size or settings.chunk_size
    overlap = chunk_overlap or settings.chunk_overlap

    splitter = SentenceSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
    )

    raw_nodes = splitter.get_nodes_from_documents(documents)
    logger.info("文档切片完成: documents=%d → chunks=%d", len(documents), len(raw_nodes))

    chunks: list[str] = []
    metadatas: list[dict[str, Any]] = []
    text_nodes: list[TextNode] = []

    for i, node in enumerate(raw_nodes):
        chunks.append(node.text)

        # 在 raw node 的 metadata 上增补必要字段
        node.metadata.setdefault("doc_id", "unknown")
        node.metadata.setdefault("file_name", "unknown")
        node.metadata.setdefault("page_number", 1)
        node.metadata.setdefault("chunk_index", i)
        # 将 text 加入 metadata，供 batch_create_chunks 使用
        node.metadata["text"] = node.text

        meta = dict(node.metadata)
        metadatas.append(meta)
        text_nodes.append(node)

    return chunks, metadatas, text_nodes


# ============================================================
# 单向量操作（切片管理用）
# ============================================================

async def delete_single_vector(kb_id: str, node_id: str) -> None:
    """从 Milvus Collection 中删除单个向量（按 llama_index node_id）。

    Args:
        kb_id: 知识库 ID
        node_id: llama_index TextNode ID（VARCHAR）
    """
    vector_store = _get_vector_store(kb_id)
    await asyncio.to_thread(
        vector_store.client.delete,
        collection_name=vector_store.collection_name,
        pks=[node_id],
    )
    logger.info("delete_single_vector 完成: kb_id=%s, node_id=%s", kb_id, node_id)


async def insert_single_vector(kb_id: str, text: str, metadata: dict) -> str:
    """对单段文本向量化并插入 Milvus Collection。

    Args:
        kb_id: 知识库 ID
        text: 文本内容
        metadata: 元数据字典（doc_id, file_name, page_number, chunk_index）

    Returns:
        新插入向量的 llama_index node_id
    """
    if len(text) > 65535:
        raise ValueError(f"文本超过 Milvus VARCHAR 最大长度限制 (65535)，当前 {len(text)} 字符")

    embed_model = create_embedding(settings)

    doc_id = metadata.get("doc_id", "")

    # 创建 TextNode，设置 SOURCE 关系使 ref_doc_id 正确映射到 Milvus doc_id 字段
    node = TextNode(
        text=text,
        metadata={
            "doc_id": doc_id,
            "file_name": metadata.get("file_name", ""),
            "page_number": metadata.get("page_number", 1),
            "chunk_index": metadata.get("chunk_index", 0),
        },
        relationships={
            NodeRelationship.SOURCE: RelatedNodeInfo(node_id=doc_id),
        },
    )

    # 向量化
    embedding = await embed_model.aget_text_embedding(text)
    node.embedding = embedding

    # 写入 Milvus（同步 I/O，放到线程池）
    vector_store = _get_vector_store(kb_id)
    node_ids = await asyncio.to_thread(vector_store.add, [node])
    new_node_id = node_ids[0]

    logger.info("insert_single_vector 完成: kb_id=%s, new_node_id=%s", kb_id, new_node_id)
    return new_node_id
