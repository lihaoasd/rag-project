"""
Query 改写（core/query_rewrite.py）单元测试 — 提示词加载 + 改写行为与回退。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import query_rewrite
from core.query_rewrite import _build_history_text

CONFIG = {
    "model": "deepseek-chat",
    "platform": "deepseek",
    "api_base": "http://x",
    "api_key": None,
    "params": {},
}


def _llm_with_text(text: str):
    """构造 mock LLM：acomplete 返回指定文本。"""
    llm = MagicMock()
    resp = MagicMock()
    resp.text = text
    llm.acomplete = AsyncMock(return_value=resp)
    return llm


@pytest.fixture(autouse=True)
def _no_active_prompt_and_lf():
    """默认无激活模板（走内置 fallback）；langfuse span 更新为 no-op。"""
    with patch("core.query_rewrite.prompt_service.get_active_prompt",
               new=AsyncMock(return_value=None)), \
         patch("core.query_rewrite.get_client", return_value=MagicMock()):
        yield


# ============================================================
# 提示词加载：激活模板优先 / 无激活回退 / DB 异常回退
# ============================================================
class TestPromptLoading:
    @pytest.mark.asyncio
    async def test_active_template_used_and_rendered(self):
        active = {
            "system_prompt": "【改写】\n{history}查询：{query}",
            "variables": [
                {"name": "query", "label": "检索输入", "required": True},
                {"name": "history", "label": "对话历史", "required": False},
            ],
        }
        with patch("core.query_rewrite.prompt_service.get_active_prompt",
                   new=AsyncMock(return_value=active)) as mock_get:
            prompt = await query_rewrite._get_rewrite_prompt("原词", "历史段")

        mock_get.assert_awaited_once()
        assert prompt.startswith("【改写】")
        assert "原词" in prompt
        assert "历史段" in prompt

    @pytest.mark.asyncio
    async def test_no_active_template_uses_fallback(self):
        prompt = await query_rewrite._get_rewrite_prompt("测试查询", "")
        assert "测试查询" in prompt
        assert "检索输入" in prompt  # fallback 常量文案

    @pytest.mark.asyncio
    async def test_db_error_falls_back(self):
        with patch("core.query_rewrite.prompt_service.get_active_prompt",
                   new=AsyncMock(side_effect=RuntimeError("db down"))):
            prompt = await query_rewrite._get_rewrite_prompt("测试查询", "")
        assert "测试查询" in prompt


# ============================================================
# 改写行为：成功清洗 / 空输出 / 未变化 / 异常 / 历史渲染
# ============================================================
class TestRewriteQuery:
    @pytest.mark.asyncio
    async def test_success_returns_cleaned_first_line(self):
        """成功时取首行并剥离引号等包裹符号。"""
        with patch("core.query_rewrite.create_llm",
                   return_value=_llm_with_text('"改写后的查询词"\n不要的解释')):
            result = await query_rewrite.rewrite_query("原词", CONFIG)

        assert result == "改写后的查询词"

    @pytest.mark.asyncio
    async def test_empty_output_falls_back(self):
        with patch("core.query_rewrite.create_llm", return_value=_llm_with_text("   ")):
            assert await query_rewrite.rewrite_query("原词", CONFIG) == "原词"

    @pytest.mark.asyncio
    async def test_unchanged_output_falls_back(self):
        with patch("core.query_rewrite.create_llm", return_value=_llm_with_text("原词")):
            assert await query_rewrite.rewrite_query("原词", CONFIG) == "原词"

    @pytest.mark.asyncio
    async def test_llm_error_falls_back(self):
        llm = MagicMock()
        llm.acomplete = AsyncMock(side_effect=RuntimeError("timeout"))
        with patch("core.query_rewrite.create_llm", return_value=llm):
            assert await query_rewrite.rewrite_query("原词", CONFIG) == "原词"

    @pytest.mark.asyncio
    async def test_history_is_rendered_into_prompt(self):
        llm = _llm_with_text("改写词")
        with patch("core.query_rewrite.create_llm", return_value=llm):
            await query_rewrite.rewrite_query(
                "它的评分标准是？",
                CONFIG,
                history=[
                    {"role": "user", "content": "这份招标文件要求什么？"},
                    {"role": "assistant", "content": "要求具备甲级资质。"},
                ],
            )

        prompt_arg = llm.acomplete.await_args.args[0]
        assert "用户: 这份招标文件要求什么？" in prompt_arg
        assert "助手: 要求具备甲级资质。" in prompt_arg
        assert "它的评分标准是？" in prompt_arg

    def test_build_history_text_empty(self):
        assert _build_history_text(None) == ""
        assert _build_history_text([]) == ""


# ============================================================
# Langfuse 记录：每次实际 LLM 调用记为 generation（model/input/output/usage）
# ============================================================
class TestRewriteLangfuseRecording:
    @pytest.mark.asyncio
    async def test_success_records_generation_with_usage(self):
        llm = MagicMock()
        resp = MagicMock()
        resp.text = '"改写词"\n多余解释'
        resp.additional_kwargs = {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}
        llm.acomplete = AsyncMock(return_value=resp)
        mock_lf = MagicMock()
        with patch("core.query_rewrite.create_llm", return_value=llm), \
             patch("core.query_rewrite.get_client", return_value=mock_lf):
            result = await query_rewrite.rewrite_query("原词", CONFIG)

        assert result == "改写词"
        mock_lf.update_current_generation.assert_called_once()
        kwargs = mock_lf.update_current_generation.call_args.kwargs
        assert kwargs["model"] == CONFIG["model"]
        assert kwargs["input"]  # 渲染后的提示词
        assert kwargs["output"] == "改写词"
        assert kwargs["usage_details"] == {
            "prompt_tokens": 12,
            "completion_tokens": 3,
            "total_tokens": 15,
        }

    @pytest.mark.asyncio
    async def test_empty_output_records_generation_then_falls_back(self):
        """空输出也先记录 generation（output=''），再回退原词。"""
        mock_lf = MagicMock()
        with patch("core.query_rewrite.create_llm", return_value=_llm_with_text("   ")), \
             patch("core.query_rewrite.get_client", return_value=mock_lf):
            result = await query_rewrite.rewrite_query("原词", CONFIG)

        assert result == "原词"
        mock_lf.update_current_generation.assert_called_once()
        assert mock_lf.update_current_generation.call_args.kwargs["output"] == ""

    @pytest.mark.asyncio
    async def test_llm_error_records_error_generation(self):
        mock_lf = MagicMock()
        with patch("core.query_rewrite.create_llm", side_effect=RuntimeError("timeout")), \
             patch("core.query_rewrite.get_client", return_value=mock_lf):
            result = await query_rewrite.rewrite_query("原词", CONFIG)

        assert result == "原词"
        mock_lf.update_current_generation.assert_called_once()
        kwargs = mock_lf.update_current_generation.call_args.kwargs
        assert kwargs["level"] == "ERROR"
        assert "timeout" in kwargs["status_message"]
        assert kwargs["model"] == CONFIG["model"]
        assert kwargs["input"] == "原词"


# ============================================================
# 多意图拆解：JSON 数组 → list；单意图/解析失败 → str（回退现状）
# ============================================================
class TestMultiIntentParsing:
    @pytest.mark.asyncio
    async def test_json_array_multi_intent_returns_list_and_records(self):
        """≥2 条子查询的 JSON 数组 → list[str]，generation output 记录为数组。"""
        mock_lf = MagicMock()
        with patch("core.query_rewrite.create_llm",
                   return_value=_llm_with_text('["查A","查B","查C"]')), \
             patch("core.query_rewrite.get_client", return_value=mock_lf):
            result = await query_rewrite.rewrite_query("原词", CONFIG)

        assert result == ["查A", "查B", "查C"]
        mock_lf.update_current_generation.assert_called_once()
        assert mock_lf.update_current_generation.call_args.kwargs["output"] == result

    @pytest.mark.asyncio
    async def test_multiline_json_array_parsed(self):
        with patch("core.query_rewrite.create_llm",
                   return_value=_llm_with_text('[\n  "查A",\n  "查B"\n]')):
            result = await query_rewrite.rewrite_query("原词", CONFIG)

        assert result == ["查A", "查B"]

    @pytest.mark.asyncio
    async def test_json_array_single_element_unwrapped(self):
        """单元素数组按单查询解包（等价于单意图）。"""
        with patch("core.query_rewrite.create_llm",
                   return_value=_llm_with_text('["查A"]')):
            result = await query_rewrite.rewrite_query("原词", CONFIG)

        assert isinstance(result, str)
        assert result == "查A"

    @pytest.mark.asyncio
    async def test_empty_array_items_filtered(self):
        """空元素过滤：['查A','','查B'] → 两条有效子查询。"""
        with patch("core.query_rewrite.create_llm",
                   return_value=_llm_with_text('["查A","","查B"]')):
            result = await query_rewrite.rewrite_query("原词", CONFIG)

        assert result == ["查A", "查B"]

    @pytest.mark.asyncio
    async def test_single_line_text_stays_str(self):
        with patch("core.query_rewrite.create_llm",
                   return_value=_llm_with_text('投标保证金比例')):
            result = await query_rewrite.rewrite_query("原词", CONFIG)

        assert isinstance(result, str)
        assert result == "投标保证金比例"

    @pytest.mark.asyncio
    async def test_invalid_json_falls_back_to_text(self):
        """以 '[' 开头但非合法 JSON → 按单行文本处理（回退单查询语义）。"""
        with patch("core.query_rewrite.create_llm",
                   return_value=_llm_with_text('[abc')):
            result = await query_rewrite.rewrite_query("原词", CONFIG)

        assert isinstance(result, str)
        assert result == "[abc"
