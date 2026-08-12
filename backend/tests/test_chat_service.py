"""
Chat Service 业务逻辑单元测试。
"""
from unittest.mock import MagicMock

import pytest

from services.chat_service import (
    _build_context,
    _build_history,
    _sse_event,
    _extract_sources,
    RAG_SYSTEM_PROMPT,
)
from tests.helpers import MockMessage


# ============================================================
# 纯函数测试
# ============================================================
class TestBuildContext:
    def test_build_context_with_results(self):
        retrieved = [
            {
                "file_name": "test.pdf",
                "page_number": 1,
                "text": "文档内容第一段",
            },
            {
                "file_name": "test2.pdf",
                "page_number": 3,
                "text": "文档内容第二段",
            },
        ]
        result = _build_context(retrieved)
        assert "[1] 来源：test.pdf 第1页" in result
        assert "[2] 来源：test2.pdf 第3页" in result
        assert "文档内容第一段" in result
        assert "文档内容第二段" in result
        assert "---" in result  # 分隔符

    def test_build_context_empty(self):
        result = _build_context([])
        assert result == ""


class TestBuildHistory:
    def test_build_history_with_messages(self):
        msg1 = MockMessage(role="user", content="问题1")
        msg2 = MockMessage(role="assistant", content="回答1")
        result = _build_history([msg1, msg2])
        assert "用户: 问题1" in result
        assert "AI助手: 回答1" in result

    def test_build_history_empty(self):
        result = _build_history([])
        assert "无历史对话" in result


class TestSSEEvent:
    def test_token_event(self):
        result = _sse_event("token", content="你好")
        assert result.startswith("data: ")
        assert '"token"' in result
        assert '你好' in result
        assert result.endswith("\n\n")

    def test_sources_event(self):
        sources = [{"doc_id": "abc", "file_name": "test.pdf"}]
        result = _sse_event("sources", sources=sources)
        assert 'sources' in result

    def test_error_event(self):
        result = _sse_event("error", message="出错了")
        assert 'error' in result
        assert '出错了' in result

    def test_done_event(self):
        result = _sse_event("done", session_id="session123")
        assert 'done' in result
        assert 'session123' in result


class TestExtractSources:
    def test_extract_sources(self):
        retrieved = [
            {
                "doc_id": "doc-123",
                "file_name": "test.pdf",
                "page_number": 5,
                "chunk_index": 2,
                "score": 0.95,
                "text": "完整的文档文本内容" * 50,  # 长文本
            }
        ]
        result = _extract_sources(retrieved)
        assert len(result) == 1
        assert result[0]["doc_id"] == "doc-123"
        assert result[0]["file_name"] == "test.pdf"
        assert result[0]["score"] == 0.95
        assert len(result[0]["text"]) <= 500  # 截断

    def test_extract_sources_empty(self):
        result = _extract_sources([])
        assert result == []

    def test_extract_sources_short_text(self):
        retrieved = [
            {
                "doc_id": "doc-123",
                "file_name": "test.pdf",
                "page_number": 1,
                "chunk_index": 0,
                "score": 0.8,
                "text": "短文本",
            }
        ]
        result = _extract_sources(retrieved)
        assert len(result[0]["text"]) == 3  # 不截断


class TestRAGPrompt:
    def test_prompt_contains_context(self):
        prompt = RAG_SYSTEM_PROMPT.format(context_str="测试上下文")
        assert "测试上下文" in prompt
        assert "投标文档分析助手" in prompt


# ============================================================
# chat_stream 行为测试（纯函数 + 简单的 SSE 格式验证）
# ============================================================
class TestChatStreamBehavior:
    """测试 chat_stream 相关纯函数的行为，避免深度嵌套 mock。"""

    def test_build_context_formats_correctly(self):
        """验证上下文字段正确拼接。"""
        retrieved = [
            {"file_name": "a.pdf", "page_number": 1, "text": "内容A"},
            {"file_name": "b.pdf", "page_number": 5, "text": "内容B"},
        ]
        ctx = _build_context(retrieved)
        assert "内容A" in ctx
        assert "内容B" in ctx
        assert "[1]" in ctx
        assert "[2]" in ctx
        assert "a.pdf" in ctx
        assert "b.pdf" in ctx

    def test_build_history_with_messages(self):
        msg1 = MockMessage(role="user", content="问题")
        msg2 = MockMessage(role="assistant", content="回答")
        result = _build_history([msg1, msg2])
        assert "用户: 问题" in result
        assert "AI助手: 回答" in result

    def test_sse_event_format(self):
        """验证 SSE 事件格式。"""
        event = _sse_event("token", content="你好")
        assert event.startswith("data: ")
        assert event.endswith("\n\n")
        assert "你好" in event

    def test_sse_event_types(self):
        """验证不同事件类型。"""
        done = _sse_event("done", session_id="s1")
        assert "done" in done
        assert "s1" in done

        error = _sse_event("error", message="失败")
        assert "error" in error
        assert "失败" in error

    def test_extract_sources_truncation(self):
        """验证来源提取截断长文本。"""
        retrieved = [{
            "doc_id": "d1", "file_name": "f.pdf",
            "page_number": 1, "chunk_index": 0,
            "score": 0.9, "text": "x" * 1000,
        }]
        sources = _extract_sources(retrieved)
        assert len(sources[0]["text"]) == 500  # 截断为 500

    def test_rag_prompt_template(self):
        """验证 RAG prompt 模板。"""
        prompt = RAG_SYSTEM_PROMPT.format(context_str="TEST_CONTEXT")
        assert "TEST_CONTEXT" in prompt
        assert "投标文档分析助手" in prompt
