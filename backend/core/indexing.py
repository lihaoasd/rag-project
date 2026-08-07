"""
LlamaIndex 索引引擎 — 文本切片、Embedding、写入 Milvus。
Phase 1: 仅定义接口签名，具体实现后续补全。
"""
from typing import Any


async def build_index(
    chunks: list[str],
    metadata_list: list[dict[str, Any]],
    kb_id: str,
    embedding_dim: int = 1024,
) -> list[int]:
    """为文档切片构建向量索引并写入 Milvus。

    Args:
        chunks: 文本切片列表
        metadata_list: 每个切片对应的元数据
        kb_id: 知识库 ID（决定 Collection 名称 kb_{kb_id}）
        embedding_dim: 向量维度

    Returns:
        Milvus 主键 ID 列表
    """
    raise NotImplementedError("Indexing engine not yet implemented — Phase 2")


async def delete_index(kb_id: str, doc_id: str) -> None:
    """从 Milvus 删除指定文档的所有向量。

    Args:
        kb_id: 知识库 ID
        doc_id: 文档 ID
    """
    raise NotImplementedError("Index deletion not yet implemented — Phase 2")
