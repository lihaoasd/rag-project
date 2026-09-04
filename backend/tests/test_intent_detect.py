"""Intent Detect 单元测试：输出解析、提示词获取与失败兜底。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.intent_detect import (
    _build_history_text,
    _classify_text,
    _is_information_request,
    _parse_intent_output,
    detect_intent,
)


# ============================================================
# _parse_intent_output / _classify_text
# ============================================================
class TestParseIntentOutput:
    @pytest.mark.parametrize(
        "text",
        [
            "需要检索",
            "需要检索。",
            "该问题需要检索投标文档",
            "retrieval",
            "Retrieval",
            '{"intent": "retrieval"}',
            '{"intent": "需要检索"}',
            '{"needs_retrieval": true}',
            '{"result": "retrieval"}',
        ],
    )
    def test_parse_retrieval_true(self, text):
        assert _parse_intent_output(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "普通聊天",
            "普通聊天。",
            "闲聊",
            "这只是一句寒暄",
            "chat",
            "CHAT",
            "small talk",
            '{"intent": "chat"}',
            '{"intent": "普通聊天"}',
            '{"needs_retrieval": false}',
            "不需要检索",
            "这不需要检索",
        ],
    )
    def test_parse_chat_false(self, text):
        assert _parse_intent_output(text) is False

    @pytest.mark.parametrize("text", ["", "   ", "无法判定", "{bad json", "12345"])
    def test_parse_unknown_none(self, text):
        assert _parse_intent_output(text) is None

    def test_classify_plain(self):
        assert _classify_text("需要检索") is True
        assert _classify_text("普通聊天") is False
        assert _classify_text("其它内容") is None


class TestBuildHistoryText:
    def test_empty_history(self):
        assert _build_history_text(None) == ""
        assert _build_history_text([]) == ""

    def test_history_roles(self):
        history = [
            {"role": "user", "content": "问题A"},
            {"role": "assistant", "content": "回答A"},
        ]
        text = _build_history_text(history)
        assert "用户: 问题A" in text
        assert "助手: 回答A" in text
        assert "对话历史" in text


# ============================================================
# _get_detect_prompt（激活模板优先 / 内置兜底）
# ============================================================
class TestGetDetectPrompt:
    @pytest.mark.asyncio
    async def test_fallback_when_no_active(self):
        with patch("core.intent_detect.prompt_service.get_active_prompt",
                   new=AsyncMock(return_value=None)):
            prompt = await detect_prompt_helper("评分办法有哪些", "")
        assert "意图分类器" in prompt
        assert "评分办法有哪些" in prompt
        assert "{query}" not in prompt

    @pytest.mark.asyncio
    async def test_active_template_priority(self):
        template = {
            "system_prompt": "自定义分类：{query}",
            "variables": [
                {"name": "query", "label": "用户输入", "required": True, "description": ""},
            ],
        }
        with patch("core.intent_detect.prompt_service.get_active_prompt",
                   new=AsyncMock(return_value=template)):
            prompt = await detect_prompt_helper("你好", "")
        assert prompt == "自定义分类：你好"

    @pytest.mark.asyncio
    async def test_db_error_falls_back(self):
        with patch("core.intent_detect.prompt_service.get_active_prompt",
                   new=AsyncMock(side_effect=RuntimeError("db down"))):
            prompt = await detect_prompt_helper("你好", "")
        assert "意图分类器" in prompt


async def detect_prompt_helper(query: str, history_text: str = "") -> str:
    """包一层：便于复用 _get_detect_prompt 做断言。"""
    from core.intent_detect import _get_detect_prompt

    return await _get_detect_prompt(query, history_text)


# ============================================================
# detect_intent（LLM 调用 + 失败/无法判定兜底 True）
# ============================================================
def _llm_config() -> dict:
    return {
        "model": "deepseek-chat", "platform": "deepseek",
        "api_base": "http://x", "api_key": None, "params": {},
    }


class TestDetectIntent:
    @pytest.mark.asyncio
    async def test_chat_verdict_false(self):
        llm = MagicMock()
        llm.acomplete = AsyncMock(return_value=SimpleNamespace(text="普通聊天"))
        with patch("core.intent_detect.create_llm", return_value=llm), \
             patch("core.intent_detect.prompt_service.get_active_prompt",
                   new=AsyncMock(return_value=None)), \
             patch("core.intent_detect.get_client", return_value=MagicMock()):
            assert await detect_intent("你好", _llm_config()) is False

    @pytest.mark.asyncio
    async def test_retrieval_verdict_true_records_full_generation(self):
        """识别为需要检索：LLM 调用完整入 trace（model/input=prompt/output/usage_details）。"""
        llm = MagicMock()
        resp = SimpleNamespace(
            text="需要检索",
            additional_kwargs={
                "prompt_tokens": 2,
                "completion_tokens": 3,
                "total_tokens": 5,
            },
        )
        llm.acomplete = AsyncMock(return_value=resp)
        with patch("core.intent_detect.create_llm", return_value=llm), \
             patch("core.intent_detect.prompt_service.get_active_prompt",
                   new=AsyncMock(return_value=None)), \
             patch("core.intent_detect.get_client", return_value=MagicMock()) as mock_client:
            assert await detect_intent("资质要求有哪些", _llm_config()) is True
            calls = mock_client.return_value.update_current_generation.call_args_list
            assert len(calls) == 1
            kwargs = calls[0].kwargs
            assert kwargs["model"] == "deepseek-chat"
            assert "资质要求有哪些" in kwargs["input"]  # input 为实际发给 LLM 的 prompt
            assert kwargs["output"] == "需要检索"
            assert kwargs["usage_details"] == {
                "prompt_tokens": 2,
                "completion_tokens": 3,
                "total_tokens": 5,
            }

    @pytest.mark.asyncio
    async def test_llm_error_falls_back_true(self):
        llm = MagicMock()
        llm.acomplete = AsyncMock(side_effect=RuntimeError("timeout"))
        with patch("core.intent_detect.create_llm", return_value=llm), \
             patch("core.intent_detect.prompt_service.get_active_prompt",
                   new=AsyncMock(return_value=None)), \
             patch("core.intent_detect.get_client", return_value=MagicMock()):
            assert await detect_intent("你好", _llm_config()) is True

    @pytest.mark.asyncio
    async def test_unparseable_output_falls_back_true(self):
        llm = MagicMock()
        llm.acomplete = AsyncMock(return_value=SimpleNamespace(text="说了些不相干的话"))
        with patch("core.intent_detect.create_llm", return_value=llm), \
             patch("core.intent_detect.prompt_service.get_active_prompt",
                   new=AsyncMock(return_value=None)), \
             patch("core.intent_detect.get_client", return_value=MagicMock()):
            assert await detect_intent("你好", _llm_config()) is True

    @pytest.mark.asyncio
    async def test_empty_output_falls_back_true(self):
        llm = MagicMock()
        llm.acomplete = AsyncMock(return_value=SimpleNamespace(text=""))
        with patch("core.intent_detect.create_llm", return_value=llm), \
             patch("core.intent_detect.prompt_service.get_active_prompt",
                   new=AsyncMock(return_value=None)), \
             patch("core.intent_detect.get_client", return_value=MagicMock()):
            assert await detect_intent("你好", _llm_config()) is True

    @pytest.mark.asyncio
    async def test_history_passed_into_prompt(self):
        llm = MagicMock()
        llm.acomplete = AsyncMock(return_value=SimpleNamespace(text="普通聊天"))

        async def _fake_complete(prompt, **kwargs):
            assert "本次输入：那这个呢" in prompt
            assert "用户: 资质要求有哪些" in prompt
            return SimpleNamespace(text="需要检索")

        llm.acomplete = _fake_complete
        history = [{"role": "user", "content": "资质要求有哪些"}]
        with patch("core.intent_detect.create_llm", return_value=llm), \
             patch("core.intent_detect.prompt_service.get_active_prompt",
                   new=AsyncMock(return_value=None)), \
             patch("core.intent_detect.get_client", return_value=MagicMock()):
            assert await detect_intent("那这个呢", _llm_config(), history) is True


# ============================================================
# 检索请求兜底守卫：主题陌生的"查询 X"不被误判为普通聊天
# ============================================================
class TestInformationRequestGuard:
    @pytest.mark.parametrize(
        "query",
        [
            "查询冠状动脉粥样硬化性心脏病",
            "查一下心律失常的急救处理方案",
            "帮我查投标保证金比例",
            "找一下评分办法",
            "检索安全员证书要求",
        ],
    )
    def test_information_request_prefixes(self, query):
        assert _is_information_request(query) is True

    @pytest.mark.parametrize("query", ["你好", "谢谢", "讲个笑话", "你是谁", ""])
    def test_plain_chat_not_information_request(self, query):
        assert _is_information_request(query) is False

    @pytest.mark.asyncio
    async def test_llm_chat_verdict_overridden_for_information_request(self):
        """LLM 误判"普通聊天"时，明确检索请求（查询+主题）覆盖改判 True。"""
        llm = MagicMock()
        llm.acomplete = AsyncMock(return_value=SimpleNamespace(text="普通聊天"))
        with patch("core.intent_detect.create_llm", return_value=llm), \
             patch("core.intent_detect.prompt_service.get_active_prompt",
                   new=AsyncMock(return_value=None)), \
             patch("core.intent_detect.get_client", return_value=MagicMock()):
            assert await detect_intent("查询冠状动脉粥样硬化性心脏病", _llm_config()) is True

    @pytest.mark.asyncio
    async def test_plain_chat_keeps_chat_verdict(self):
        """纯闲聊不受守卫影响，仍按 LLM 结果走普通聊天。"""
        llm = MagicMock()
        llm.acomplete = AsyncMock(return_value=SimpleNamespace(text="普通聊天"))
        with patch("core.intent_detect.create_llm", return_value=llm), \
             patch("core.intent_detect.prompt_service.get_active_prompt",
                   new=AsyncMock(return_value=None)), \
             patch("core.intent_detect.get_client", return_value=MagicMock()):
            assert await detect_intent("你好", _llm_config()) is False


def test_fallback_matches_migration_0021_seed():
    """内置兜底文案与迁移 0021 seed 完全一致（同源防漂移），且含"放宽检索/仅闲聊为普通聊天"规则。"""
    import importlib.util
    from pathlib import Path

    from core.intent_detect import FALLBACK_INTENT_DETECT_PROMPT

    path = (
        Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "0021_intent_detect_prompt_scope.py"
    )
    spec = importlib.util.spec_from_file_location("_migration_0021", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod._INTENT_DETECT_PROMPT_V2 == FALLBACK_INTENT_DETECT_PROMPT
    assert "放宽检索" in FALLBACK_INTENT_DETECT_PROMPT
    assert "日常闲聊" in FALLBACK_INTENT_DETECT_PROMPT
    assert "专业知识" in FALLBACK_INTENT_DETECT_PROMPT
