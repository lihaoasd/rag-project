"""
KB Service 业务逻辑单元测试。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import kb_service
from tests.helpers import MockKB, KB_ID


class TestCreateKnowledgeBase:
    @pytest.mark.asyncio
    async def test_create_returns_dict(self, mock_async_session, mock_session):
        kb = MockKB(name="新知识库")
        mock_session.refresh = AsyncMock()

        result = await kb_service.create_knowledge_base(name="新知识库", description="描述")
        assert result["name"] == "新知识库"
        assert "id" in result

    @pytest.mark.asyncio
    async def test_create_without_description(self, mock_async_session, mock_session):
        kb = MockKB(name="新知识库", description=None)
        mock_session.refresh = AsyncMock()

        result = await kb_service.create_knowledge_base(name="新知识库")
        assert result["name"] == "新知识库"


class TestListKnowledgeBases:
    @pytest.mark.asyncio
    async def test_list_returns_list(self, mock_async_session, mock_session):
        kb1 = MockKB(name="KB1")
        kb2 = MockKB(name="KB2")
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [kb1, kb2]
        mock_session.execute.return_value = mock_result

        result = await kb_service.list_knowledge_bases()
        assert len(result) == 2
        assert result[0]["name"] == "KB1"

    @pytest.mark.asyncio
    async def test_list_empty(self, mock_async_session, mock_session):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        result = await kb_service.list_knowledge_bases()
        assert result == []


class TestGetKnowledgeBase:
    @pytest.mark.asyncio
    async def test_get_returns_dict(self, mock_async_session, mock_session):
        kb = MockKB()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = kb
        mock_session.execute.return_value = mock_result

        result = await kb_service.get_knowledge_base(KB_ID)
        assert result is not None
        assert result["name"] == "测试知识库"

    @pytest.mark.asyncio
    async def test_get_not_found(self, mock_async_session, mock_session):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await kb_service.get_knowledge_base(KB_ID)
        assert result is None


class TestDeleteKnowledgeBase:
    @pytest.mark.asyncio
    async def test_delete_existing(self, mock_async_session, mock_session):
        """删除已有知识库 — 验证级联删除流程。"""
        kb = MockKB()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = kb
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.delete = AsyncMock()

        result = await kb_service.delete_knowledge_base(KB_ID)
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, mock_async_session, mock_session):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await kb_service.delete_knowledge_base(KB_ID)
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_clears_graph_when_enabled(self, mock_async_session, mock_session):
        """v2 联动：图谱启用时删除 KB 一并清空 Neo4j 数据（best-effort）。"""
        kb = MockKB()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = kb
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.delete = AsyncMock()

        with patch("services.kb_service.settings.graph_enabled", True), \
             patch("services.kb_service.Neo4jStore.execute_write", new_callable=AsyncMock) as mock_graph:
            result = await kb_service.delete_knowledge_base(KB_ID)

        assert result is True
        mock_graph.assert_awaited_once()
        assert "DETACH DELETE n" in mock_graph.await_args.args[0]
        assert mock_graph.await_args.args[1]["kb_id"] == KB_ID


class TestKBToDict:
    def test_kb_to_dict_fields(self):
        kb = MockKB(name="测试", description="描述")
        result = kb_service._kb_to_dict(kb)
        assert "id" in result
        assert "name" in result
        assert "description" in result
        assert "document_count" in result
        assert "created_at" in result
        assert "updated_at" in result
        assert result["name"] == "测试"
        assert result["description"] == "描述"
