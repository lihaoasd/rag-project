"""
Document Repository 单元测试。
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from repositories.document_repository import DocumentRepository
from tests.helpers import MockDocument, MockChunk, KB_ID, DOC_ID


class TestDocumentRepository:
    """测试文档 Repository CRUD 操作。"""

    @pytest.fixture
    def repo(self, mock_session):
        return DocumentRepository(mock_session)

    # ---- create ----
    @pytest.mark.asyncio
    async def test_create_document(self, repo, mock_session):
        mock_session.refresh = AsyncMock()
        result = await repo.create(
            kb_id=KB_ID,
            file_name="test.pdf",
            file_size=1024,
            file_type="pdf",
            object_key=f"{KB_ID}/{DOC_ID}.pdf",
        )
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        assert result.file_name == "test.pdf"
        assert result.status == "uploaded"

    # ---- list_by_kb ----
    @pytest.mark.asyncio
    async def test_list_by_kb(self, repo, mock_session):
        doc1 = MockDocument(file_name="doc1.pdf")
        doc2 = MockDocument(file_name="doc2.pdf")
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [doc1, doc2]
        mock_session.execute.return_value = mock_result

        result = await repo.list_by_kb(KB_ID)
        assert len(result) == 2

    # ---- list_chunks_by_kb / count_chunks_by_kb（ES 对账/重建用）----
    @pytest.mark.asyncio
    async def test_list_chunks_by_kb(self, repo, mock_session):
        c1 = MockChunk(chunk_index=0)
        c2 = MockChunk(chunk_index=1)
        mock_result = MagicMock()
        mock_result.all.return_value = [c1, c2]
        mock_session.scalars.return_value = mock_result

        result = await repo.list_chunks_by_kb(KB_ID)
        assert len(result) == 2
        assert result[0].chunk_index == 0

    @pytest.mark.asyncio
    async def test_count_chunks_by_kb(self, repo, mock_session):
        mock_result = MagicMock()
        mock_result.all.return_value = [(KB_ID, 3), ("kb2", 5)]
        mock_session.execute.return_value = mock_result

        result = await repo.count_chunks_by_kb()
        assert result == {KB_ID: 3, "kb2": 5}

    @pytest.mark.asyncio
    async def test_count_chunks_by_doc(self, repo, mock_session):
        mock_result = MagicMock()
        mock_result.all.return_value = [(DOC_ID, 3), ("doc2", 5)]
        mock_session.execute.return_value = mock_result

        result = await repo.count_chunks_by_doc()
        assert result == {DOC_ID: 3, "doc2": 5}

    @pytest.mark.asyncio
    async def test_count_docs_by_kb(self, repo, mock_session):
        mock_result = MagicMock()
        mock_result.all.return_value = [(KB_ID, 2), ("kb2", 1)]
        mock_session.execute.return_value = mock_result

        result = await repo.count_docs_by_kb()
        assert result == {KB_ID: 2, "kb2": 1}

    @pytest.mark.asyncio
    async def test_list_doc_meta(self, repo, mock_session):
        mock_result = MagicMock()
        mock_result.all.return_value = [
            (DOC_ID, KB_ID, "a.pdf"),
            ("doc2", "kb2", "b.pdf"),
        ]
        mock_session.execute.return_value = mock_result

        result = await repo.list_doc_meta()
        assert result == {
            DOC_ID: {"kb_id": KB_ID, "file_name": "a.pdf"},
            "doc2": {"kb_id": "kb2", "file_name": "b.pdf"},
        }

    @pytest.mark.asyncio
    async def test_list_by_kb_invalid_uuid(self, repo):
        result = await repo.list_by_kb("not-a-uuid")
        assert result == []

    # ---- get_by_id ----
    @pytest.mark.asyncio
    async def test_get_by_id_found(self, repo, mock_session):
        doc = MockDocument()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = doc
        mock_session.execute.return_value = mock_result

        result = await repo.get_by_id(DOC_ID)
        assert result is not None
        assert result.file_name == "test.pdf"

    @pytest.mark.asyncio
    async def test_get_by_id_invalid_uuid(self, repo):
        result = await repo.get_by_id("not-a-uuid")
        assert result is None

    # ---- update_status ----
    @pytest.mark.asyncio
    async def test_update_status(self, repo, mock_session):
        mock_session.execute = AsyncMock()
        await repo.update_status(DOC_ID, "processing")
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_status_with_error(self, repo, mock_session):
        mock_session.execute = AsyncMock()
        await repo.update_status(DOC_ID, "failed", error_message="解析错误")
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    # ---- delete ----
    @pytest.mark.asyncio
    async def test_delete_existing_doc(self, repo, mock_session):
        doc = MockDocument()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = doc
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.delete = AsyncMock()

        result = await repo.delete(DOC_ID)
        assert result is not None
        mock_session.delete.assert_called()

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, repo, mock_session):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await repo.delete(str(uuid.uuid4()))
        assert result is None

    # ---- batch_create_chunks ----
    @pytest.mark.asyncio
    async def test_batch_create_chunks(self, repo, mock_session):
        mock_session.add_all = MagicMock()
        metadatas = [
            {"chunk_index": 0, "page_number": 1, "text": "第一段文本"},
            {"chunk_index": 1, "page_number": 1, "text": "第二段文本"},
        ]
        milvus_pks = ["pk_1", "pk_2"]

        await repo.batch_create_chunks(DOC_ID, KB_ID, metadatas, milvus_pks)
        mock_session.add_all.assert_called_once()
        # 验证传入了 2 个 chunk
        call_args = mock_session.add_all.call_args[0][0]
        assert len(call_args) == 2


class TestDocumentChunkRepository:
    """测试切片 Repository CRUD 操作。"""

    @pytest.fixture
    def repo(self, mock_session):
        return DocumentRepository(mock_session)

    # ---- list_chunks_by_doc_id ----
    @pytest.mark.asyncio
    async def test_list_chunks(self, repo, mock_session):
        chunk1 = MockChunk()
        chunk2 = MockChunk(chunk_index=1, text_content="第二段")
        mock_exec = MagicMock()
        mock_exec.scalars.return_value.all.side_effect = [[chunk1, chunk2], [chunk1, chunk2]]
        mock_session.execute = AsyncMock(return_value=mock_exec)

        chunks, total = await repo.list_chunks_by_doc_id(DOC_ID, offset=0, limit=20)
        assert total == 2
        assert len(chunks) == 2

    @pytest.mark.asyncio
    async def test_list_chunks_invalid_uuid(self, repo):
        chunks, total = await repo.list_chunks_by_doc_id("not-a-uuid")
        assert chunks == []
        assert total == 0

    # ---- get_chunk_by_id ----
    @pytest.mark.asyncio
    async def test_get_chunk_by_id_found(self, repo, mock_session):
        chunk = MockChunk()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = chunk
        mock_session.execute.return_value = mock_result

        result = await repo.get_chunk_by_id(str(uuid.uuid4()))
        assert result is not None
        assert result.text_content == "测试切片文本内容"

    @pytest.mark.asyncio
    async def test_get_chunk_by_id_invalid_uuid(self, repo):
        result = await repo.get_chunk_by_id("not-a-uuid")
        assert result is None

    # ---- delete_chunk_by_id ----
    @pytest.mark.asyncio
    async def test_delete_chunk_existing(self, repo, mock_session):
        chunk = MockChunk()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = chunk
        mock_session.execute.return_value = mock_result
        mock_session.delete = AsyncMock()

        result = await repo.delete_chunk_by_id(str(uuid.uuid4()))
        assert result is not None
        mock_session.delete.assert_called_once()

    # ---- update_chunk_text ----
    @pytest.mark.asyncio
    async def test_update_chunk_text(self, repo, mock_session):
        mock_session.execute = AsyncMock()
        await repo.update_chunk_text(str(uuid.uuid4()), "新文本", 3)
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    # ---- update_doc_chunk_count ----
    @pytest.mark.asyncio
    async def test_update_doc_chunk_count_increment(self, repo, mock_session):
        mock_session.execute = AsyncMock()
        await repo.update_doc_chunk_count(DOC_ID, delta=1)
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_doc_chunk_count_decrement(self, repo, mock_session):
        mock_session.execute = AsyncMock()
        await repo.update_doc_chunk_count(DOC_ID, delta=-1)
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    # ---- delete methods ----
    @pytest.mark.asyncio
    async def test_delete_chunks_by_doc_id(self, repo, mock_session):
        mock_session.execute = AsyncMock()
        await repo.delete_chunks_by_doc_id(DOC_ID)
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_chunks_by_kb_id(self, repo, mock_session):
        mock_session.execute = AsyncMock()
        await repo.delete_chunks_by_kb_id(KB_ID)
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_chunks_by_kb_id_invalid_uuid(self, repo):
        # 不应抛异常
        await repo.delete_chunks_by_kb_id("not-a-uuid")
