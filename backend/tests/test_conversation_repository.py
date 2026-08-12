"""
Conversation Repository 单元测试。
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from repositories.conversation_repository import ConversationRepository
from tests.helpers import MockConversation, MockMessage, KB_ID, CONV_ID


class TestConversationRepository:
    """测试会话 Repository CRUD 操作。"""

    @pytest.fixture
    def repo(self, mock_session):
        return ConversationRepository(mock_session)

    # ---- get_or_create ----
    @pytest.mark.asyncio
    async def test_get_existing_session(self, repo, mock_session):
        """传入有效 session_id，应返回已有会话。"""
        conv = MockConversation()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = conv
        mock_session.execute.return_value = mock_result

        result = await repo.get_or_create(CONV_ID, KB_ID)
        assert result is not None
        assert result.title == "测试会话"
        # 不应调用 add（不是新建）
        mock_session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_new_session_when_none(self, repo, mock_session):
        """不传 session_id，应创建新会话。"""
        mock_session.refresh = AsyncMock()
        result = await repo.get_or_create(None, KB_ID)
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        assert result is not None

    @pytest.mark.asyncio
    async def test_create_new_session_when_invalid_id(self, repo, mock_session):
        """传入无效 session_id，应创建新会话。"""
        mock_session.refresh = AsyncMock()
        result = await repo.get_or_create("invalid-uuid", KB_ID)
        mock_session.add.assert_called_once()
        assert result is not None

    @pytest.mark.asyncio
    async def test_create_new_session_when_not_found(self, repo, mock_session):
        """传入不存在的 session_id，应创建新会话。"""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result
        mock_session.refresh = AsyncMock()

        result = await repo.get_or_create(str(uuid.uuid4()), KB_ID)
        mock_session.add.assert_called_once()
        assert result is not None

    # ---- create_message ----
    @pytest.mark.asyncio
    async def test_create_message(self, repo, mock_session):
        mock_session.refresh = AsyncMock()
        mock_session.execute = AsyncMock()

        result = await repo.create_message(
            CONV_ID,
            role="user",
            content="你好",
        )
        mock_session.add.assert_called_once()
        assert result.role == "user"

    @pytest.mark.asyncio
    async def test_create_message_with_sources(self, repo, mock_session):
        mock_session.refresh = AsyncMock()
        mock_session.execute = AsyncMock()
        sources = [{"doc_id": "abc", "text": "test"}]

        result = await repo.create_message(
            CONV_ID,
            role="assistant",
            content="回复",
            sources=sources,
            token_count=100,
        )
        mock_session.add.assert_called_once()
        assert result.role == "assistant"

    # ---- get_history ----
    @pytest.mark.asyncio
    async def test_get_history(self, repo, mock_session):
        msg1 = MockMessage(role="user", content="问题1")
        msg2 = MockMessage(role="assistant", content="回答1")
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [msg1, msg2]
        mock_session.execute.return_value = mock_result

        result = await repo.get_history(CONV_ID, limit=10)
        assert len(result) == 2
        assert result[0].role == "user"
        assert result[1].role == "assistant"

    @pytest.mark.asyncio
    async def test_get_history_invalid_uuid(self, repo):
        result = await repo.get_history("invalid-uuid")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_history_default_limit(self, repo, mock_session):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        result = await repo.get_history(CONV_ID)
        assert result == []

    # ---- delete_messages_by_conv_id ----
    @pytest.mark.asyncio
    async def test_delete_messages_by_conv_id(self, repo, mock_session):
        mock_session.execute = AsyncMock()
        await repo.delete_messages_by_conv_id(CONV_ID)
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_messages_by_conv_id_invalid_uuid(self, repo):
        # 不应抛异常
        await repo.delete_messages_by_conv_id("not-a-uuid")

    # ---- delete_messages_by_kb_id ----
    @pytest.mark.asyncio
    async def test_delete_messages_by_kb_id(self, repo, mock_session):
        # 模拟有会话
        mock_result1 = MagicMock()
        mock_result1.all.return_value = [(CONV_ID,)]
        mock_session.execute = AsyncMock(return_value=mock_result1)

        await repo.delete_messages_by_kb_id(KB_ID)

    # ---- delete_conversations_by_kb_id ----
    @pytest.mark.asyncio
    async def test_delete_conversations_by_kb_id(self, repo, mock_session):
        mock_session.execute = AsyncMock()
        await repo.delete_conversations_by_kb_id(KB_ID)
        mock_session.execute.assert_called_once()
