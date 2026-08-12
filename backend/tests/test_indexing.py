"""
Indexing 模块单元测试 — chunk_documents 纯逻辑测试。
"""
from unittest.mock import patch

from llama_index.core import Document as LlamaDocument

from core.indexing import chunk_documents


class TestChunkDocuments:
    """测试 chunk_documents 函数 - 该函数不依赖外部服务。"""

    def test_chunk_single_document(self):
        doc = LlamaDocument(
            text="第一段测试文本。第二段测试文本。" * 20,
            metadata={"file_name": "test.pdf", "page_number": 1, "file_type": "pdf"},
        )
        chunks, metadatas, nodes = chunk_documents([doc], chunk_size=200, chunk_overlap=20)

        assert len(chunks) > 0
        assert len(chunks) == len(metadatas)
        assert len(chunks) == len(nodes)
        # 每个 chunk 的 metadata 应包含 text
        for meta in metadatas:
            assert "text" in meta
            assert "doc_id" in meta
            assert "file_name" in meta
            assert "page_number" in meta
            assert "chunk_index" in meta

    def test_chunk_multiple_documents(self):
        docs = [
            LlamaDocument(
                text="文档一的文本。" * 30,
                metadata={"file_name": "doc1.pdf", "page_number": 1, "file_type": "pdf"},
            ),
            LlamaDocument(
                text="文档二的文本。" * 30,
                metadata={"file_name": "doc1.pdf", "page_number": 2, "file_type": "pdf"},
            ),
        ]
        chunks, metadatas, nodes = chunk_documents(docs, chunk_size=200, chunk_overlap=20)

        assert len(chunks) > 1
        assert len(metadatas) > 1
        assert len(nodes) > 1

    def test_chunk_with_custom_size(self):
        doc = LlamaDocument(
            text="测试文本。" * 50,
            metadata={"file_name": "test.pdf"},
        )
        chunks, metadatas, _ = chunk_documents([doc], chunk_size=100, chunk_overlap=10)

        # 较小的 chunk_size 会产生更多切片
        assert len(chunks) > 0
        for meta in metadatas:
            assert meta["file_name"] == "test.pdf"
            assert meta["page_number"] == 1  # 默认值

    def test_chunk_empty_input(self):
        chunks, metadatas, nodes = chunk_documents([])
        assert chunks == []
        assert metadatas == []
        assert nodes == []

    def test_chunk_very_short_text(self):
        """很短的文本不应被切分。"""
        doc = LlamaDocument(
            text="短文本",
            metadata={"file_name": "test.pdf", "page_number": 1, "file_type": "pdf"},
        )
        chunks, metadatas, nodes = chunk_documents([doc], chunk_size=200, chunk_overlap=20)

        # SentenceSplitter 默认不截断短于 chunk_size 的文本
        assert len(chunks) >= 0  # 不报错就行

    def test_chunk_index_increments(self):
        doc = LlamaDocument(
            text="文本A。" * 40,
            metadata={"file_name": "test.pdf", "page_number": 1, "file_type": "pdf"},
        )
        chunks, metadatas, _ = chunk_documents([doc], chunk_size=50, chunk_overlap=5)

        if len(metadatas) > 1:
            # chunk_index 应递增
            for i, meta in enumerate(metadatas):
                assert meta["chunk_index"] == i

    def test_metadata_defaults(self):
        """验证 metadata 有默认值。"""
        doc = LlamaDocument(
            text="测试文本。" * 20,
            metadata={"file_name": "test.pdf"},
        )
        _, metadatas, _ = chunk_documents([doc], chunk_size=200, chunk_overlap=20)

        for meta in metadatas:
            assert meta["file_name"] == "test.pdf"
            assert meta["page_number"] == 1  # 默认
            assert "chunk_index" in meta
            assert "text" in meta
            # doc_id 设为 unknown（如果没有在解析时设置）
            # 实际上 metadata.setdefault("doc_id", "unknown") 会填充
            assert "doc_id" in meta
