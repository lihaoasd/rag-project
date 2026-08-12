"""
KB Repository 单元测试。
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from repositories.kb_repository import KnowledgeBaseRepository
from tests.helpers import MockKB, KB_ID


class TestKnowledgeBaseRepository:
    """测试知识库 Repository CRUD 操作。"""

    @pytest.fixture
    def repo(self, mock_session):
        return KnowledgeBaseRepository(mock_session)

    # ---- create ----
    @pytest.mark.asyncio
    async def test_create_returns_kb(self, repo, mock_session):
        mock_session.refresh = AsyncMock()
        result = await repo.create(name="测试知识库")

        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        assert result.name == "测试知识库"

    @pytest.mark.asyncio
    async def test_create_with_description(self, repo, mock_session):
        mock_session.refresh = AsyncMock()
        result = await repo.create(name="KB1", description="我的描述")

        mock_session.add.assert_called_once()
        assert result.description == "我的描述"

    # ---- list_all ----
    @pytest.mark.asyncio
    async def test_list_all_returns_list(self, repo, mock_session):
        kb1 = MockKB(name="KB1")
        kb2 = MockKB(name="KB2")
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [kb1, kb2]
        mock_session.execute.return_value = mock_result

        result = await repo.list_all()
        assert len(result) == 2
        assert result[0].name == "KB1"
        assert result[1].name == "KB2"

    @pytest.mark.asyncio
    async def test_list_all_empty(self, repo, mock_session):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        result = await repo.list_all()
        assert result == []

    # ---- get_by_id ----
    @pytest.mark.asyncio
    async def test_get_by_id_returns_kb(self, repo, mock_session):
        kb = MockKB()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = kb
        mock_session.execute.return_value = mock_result

        result = await repo.get_by_id(KB_ID)
        assert result is not None
        assert result.name == "测试知识库"

    @pytest.mark.asyncio
    async def test_get_by_id_not_found(self, repo, mock_session):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await repo.get_by_id(str(uuid.uuid4()))
        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_id_invalid_uuid(self, repo):
        result = await repo.get_by_id("not-a-valid-uuid")
        assert result is None

    # ---- delete ----
    @pytest.mark.asyncio
    async def test_delete_existing_kb(self, repo, mock_session):
        kb = MockKB()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = kb
        mock_session.execute.return_value = mock_result
        mock_session.delete = AsyncMock()

        result = await repo.delete(KB_ID)
        assert result is True
        mock_session.delete.assert_called_once_with(kb)
        mock_session.commit.assert_called()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_kb(self, repo, mock_session):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await repo.delete(str(uuid.uuid4()))
        assert result is False

    # ---- increment_doc_count ----
    @pytest.mark.asyncio
    async def test_increment_doc_count(self, repo, mock_session):
        mock_session.execute = AsyncMock()
        await repo.increment_doc_count(KB_ID)
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    # ---- decrement_doc_count ----
    @pytest.mark.asyncio
    async def test_decrement_doc_count(self, repo, mock_session):
        mock_session.execute = AsyncMock()
        await repo.decrement_doc_count(KB_ID)
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()
