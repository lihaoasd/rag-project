"""
智能问答 API (SSE) 端点测试。
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.helpers import KB_ID


@pytest.fixture
def client():
    with patch("core.parse_queue.start_worker", new_callable=AsyncMock), \
         patch("core.parse_queue.stop_worker", new_callable=AsyncMock):
        from main import app
        app.router.lifespan_context = None
        return TestClient(app)


def make_sse_events():
    """生成模拟的 SSE 事件流。"""
    events = [
        'data: {"type": "token", "content": "你好"}\n\n',
        'data: {"type": "token", "content": "，这是测试回复"}\n\n',
        'data: {"type": "sources", "sources": [{"doc_id": "abc", "file_name": "test.pdf"}]}\n\n',
        'data: {"type": "done", "session_id": "session-123"}\n\n',
    ]
    for e in events:
        yield e


class TestChat:
    def test_chat_streaming(self, client):
        """测试 SSE 流式问答。"""
        with patch("routers.chat.chat_service.chat_stream",
                   new_callable=MagicMock) as mock_chat:

            async def async_gen():
                for e in make_sse_events():
                    yield e

            mock_chat.return_value = async_gen()

            response = client.post(
                f"/api/knowledge-bases/{KB_ID}/chat",
                json={"message": "你好"},
            )
            assert response.status_code == 200
            assert response.headers["content-type"] == "text/event-stream; charset=utf-8"

            # 读取流式响应
            content = response.text
            assert "token" in content
            assert "done" in content

    def test_chat_with_session(self, client):
        """传入 session_id 的聊天请求。"""
        with patch("routers.chat.chat_service.chat_stream",
                   new_callable=MagicMock) as mock_chat:

            async def async_gen():
                yield 'data: {"type": "done", "session_id": "existing-session"}\n\n'

            mock_chat.return_value = async_gen()

            response = client.post(
                f"/api/knowledge-bases/{KB_ID}/chat",
                json={
                    "message": "继续对话",
                    "session_id": "existing-session",
                },
            )
            assert response.status_code == 200

    def test_chat_missing_message(self, client):
        """缺少 message 字段应返回 422。"""
        response = client.post(
            f"/api/knowledge-bases/{KB_ID}/chat",
            json={},
        )
        assert response.status_code == 422

    def test_chat_new_session(self, client):
        """不传 session_id 自动创建新会话。"""
        with patch("routers.chat.chat_service.chat_stream",
                   new_callable=MagicMock) as mock_chat:

            async def async_gen():
                yield 'data: {"type": "done", "session_id": "new-session-id"}\n\n'

            mock_chat.return_value = async_gen()

            response = client.post(
                f"/api/knowledge-bases/{KB_ID}/chat",
                json={"message": "新建会话"},
            )
            assert response.status_code == 200
