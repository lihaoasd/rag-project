"""
Chat Service 业务逻辑单元测试。
"""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.chat_service import (
    _build_context,
    _build_history,
    _sse_event,
    _extract_sources,
    FALLBACK_RAG_PROMPT,
)
from tests.helpers import KB_ID, MockMessage


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
        prompt = FALLBACK_RAG_PROMPT.format(
            context_str="测试上下文",
            query="测试问题",
            history="无历史对话",
            kb_name="测试知识库",
        )
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
        prompt = FALLBACK_RAG_PROMPT.format(
            context_str="TEST_CONTEXT",
            query="TEST_QUERY",
            history="TEST_HISTORY",
            kb_name="TEST_KB",
        )
        assert "TEST_CONTEXT" in prompt
        assert "TEST_QUERY" in prompt
        assert "投标文档分析助手" in prompt


# ============================================================
# _run_rag Query 改写接线：开关开启时解析 LLM 配置并传给 retrieve_and_rank
# ============================================================
class TestRunRagRewriteWiring:
    """验证 _run_rag 按 chat_query_rewrite_enabled 决定是否传 query_rewrite_config。"""

    def _stream_llm(self):
        """构造 astream_chat 返回流式响应的 mock LLM（含 usage 尾部 chunk）。"""
        llm = MagicMock()

        async def _astream_chat(**kwargs):
            async def _gen():
                yield SimpleNamespace(delta="你好", additional_kwargs={})
                yield SimpleNamespace(delta="", additional_kwargs={
                    "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
                })

            return _gen()

        llm.astream_chat = _astream_chat
        return llm

    async def _run(self, history_objs):
        """带全套 mock 执行 _run_rag，返回 (收集文本, retrieve_and_rank mock, result)。"""
        from services import chat_service

        llm = self._stream_llm()
        llm_config = {
            "model": "deepseek-chat", "platform": "deepseek",
            "api_base": "http://x", "api_key": None, "params": {},
        }

        async def _resolve(kb, model_type):
            if model_type == "rerank":
                return None
            return llm_config

        with patch("services.chat_service.resolve_model_config",
                   new=AsyncMock(side_effect=_resolve)), \
             patch("services.chat_service.create_llm", return_value=llm), \
             patch("services.chat_service.lexicon_service.ensure_cache",
                   new=AsyncMock()), \
             patch("services.chat_service.prompt_service.get_active_prompt",
                   new=AsyncMock(return_value=None)), \
             patch("services.chat_service.get_client", return_value=MagicMock()), \
             patch("services.chat_service.propagate_attributes",
                   new=lambda **kw: nullcontext()), \
             patch("services.chat_service.retrieve_and_rank",
                   new=AsyncMock(return_value=[])) as mock_rar:
            result: dict = {}
            gen = chat_service._run_rag(
                kb_id=KB_ID, message="新问题", conv_id="c1",
                history=history_objs, result=result,
            )
            parts: list[str] = []
            async for delta in gen:
                parts.append(delta)

        return "".join(parts), mock_rar, result

    @pytest.mark.asyncio
    async def test_rewrite_enabled_passes_config_and_history(self):
        """开关开启时：改写配置解析成功并连同最近 4 轮历史传给 retrieve_and_rank。"""
        from config import settings
        from services import chat_service

        original = settings.chat_query_rewrite_enabled
        settings.chat_query_rewrite_enabled = True
        try:
            history_objs = [
                SimpleNamespace(role="user" if i % 2 == 0 else "assistant", content=f"消息{i}")
                for i in range(6)
            ]
            expected_history = [
                {"role": m.role, "content": m.content} for m in history_objs[-4:]
            ]
            text, mock_rar, result = await self._run(history_objs)

            assert text == "你好"  # 流式生成正常
            mock_rar.assert_awaited_once()
            kwargs = mock_rar.await_args.kwargs
            assert kwargs["query_rewrite_config"] == {
                "model": "deepseek-chat", "platform": "deepseek",
                "api_base": "http://x", "api_key": None, "params": {},
            }
            assert kwargs["query_rewrite_history"] == expected_history
            assert result["retrieved"] == []
        finally:
            settings.chat_query_rewrite_enabled = original

    @pytest.mark.asyncio
    async def test_rewrite_disabled_passes_none(self):
        """开关关闭时：retrieve_and_rank 收到 query_rewrite_config=None。"""
        from config import settings

        original = settings.chat_query_rewrite_enabled
        settings.chat_query_rewrite_enabled = False
        try:
            text, mock_rar, _ = await self._run([])

            assert text == "你好"
            mock_rar.assert_awaited_once()
            assert mock_rar.await_args.kwargs["query_rewrite_config"] is None
        finally:
            settings.chat_query_rewrite_enabled = original
