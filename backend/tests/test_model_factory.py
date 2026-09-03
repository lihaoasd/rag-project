"""
model_factory resolve_model_config 测试 — 绑定优先、停用模型过滤、默认兜底。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core import model_factory
from tests.helpers import KB_ID


def _mc(**kwargs):
    """构造最小 ModelConfig 模拟对象。"""
    data = {
        "id": "mc_1",
        "name": "M1",
        "model_type": "llm",
        "platform": "openai",
        "model": "gpt-4o",
        "api_base": "http://x",
        "api_key": "k",
        "params": {},
        "is_default": False,
        "is_active": True,
    }
    data.update(kwargs)
    return SimpleNamespace(**data)


class TestResolveModelConfig:
    @pytest.mark.asyncio
    async def test_binding_wins_over_default(self, mock_session):
        """启用状态的绑定优先于全局默认。"""
        bound = _mc(id="mc_b", name="Bound")
        default = _mc(id="mc_d", name="Default")
        binding_result = MagicMock()
        binding_result.scalar_one_or_none.return_value = MagicMock()  # 存在绑定
        bound_result = MagicMock()
        bound_result.scalar_one_or_none.return_value = bound
        default_result = MagicMock()
        default_result.scalar_one_or_none.return_value = default
        mock_session.execute = AsyncMock(side_effect=[binding_result, bound_result, default_result])

        config = await model_factory.resolve_model_config(KB_ID, "llm")
        assert config["id"] == "mc_b"

    @pytest.mark.asyncio
    async def test_inactive_binding_falls_back_to_default(self, mock_session):
        """绑定指向停用模型时视为未命中，回退到启用状态的默认。"""
        bound = _mc(id="mc_b", name="BoundInactive", is_active=False)
        default = _mc(id="mc_d", name="Default")
        binding_result = MagicMock()
        binding_result.scalar_one_or_none.return_value = MagicMock()
        bound_result = MagicMock()
        bound_result.scalar_one_or_none.return_value = bound
        default_result = MagicMock()
        default_result.scalar_one_or_none.return_value = default
        mock_session.execute = AsyncMock(side_effect=[binding_result, bound_result, default_result])

        config = await model_factory.resolve_model_config(KB_ID, "llm")
        assert config["id"] == "mc_d"

    @pytest.mark.asyncio
    async def test_inactive_default_not_resolved_required_type_raises(self, mock_session):
        """停用的默认不被解析，必需类型无可用配置时抛错。"""
        binding_result = MagicMock()
        binding_result.scalar_one_or_none.return_value = None
        default_result = MagicMock()
        default_result.scalar_one_or_none.return_value = _mc(id="mc_d", is_active=False)
        mock_session.execute = AsyncMock(side_effect=[binding_result, default_result])

        with pytest.raises(RuntimeError, match="未配置"):
            await model_factory.resolve_model_config(KB_ID, "llm")

    @pytest.mark.asyncio
    async def test_no_config_optional_type_returns_none(self, mock_session):
        """可选类型（rerank）无可用配置时返回 None，不抛错。"""
        binding_result = MagicMock()
        binding_result.scalar_one_or_none.return_value = None
        default_result = MagicMock()
        default_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(side_effect=[binding_result, default_result])

        config = await model_factory.resolve_model_config(KB_ID, "rerank")
        assert config is None


class TestResolveDefaultModelConfig:
    """resolve_default_model_config — 仅全局默认启用模型，无则不抛错。"""

    @pytest.mark.asyncio
    async def test_returns_default_active(self, mock_session):
        default = _mc(id="mc_d", name="Default")
        default_result = MagicMock()
        default_result.scalar_one_or_none.return_value = default
        mock_session.execute = AsyncMock(return_value=default_result)

        config = await model_factory.resolve_default_model_config("llm")
        assert config["id"] == "mc_d"
        assert config["name"] == "Default"

    @pytest.mark.asyncio
    async def test_inactive_default_returns_none(self, mock_session):
        default_result = MagicMock()
        default_result.scalar_one_or_none.return_value = _mc(id="mc_d", is_active=False)
        mock_session.execute = AsyncMock(return_value=default_result)

        assert await model_factory.resolve_default_model_config("llm") is None

    @pytest.mark.asyncio
    async def test_no_default_returns_none(self, mock_session):
        default_result = MagicMock()
        default_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=default_result)

        assert await model_factory.resolve_default_model_config("llm") is None
