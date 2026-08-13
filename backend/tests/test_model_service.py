"""
Model Service 模型连接测试 单元测试。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import model_service


def _mock_llm(content="OK"):
    llm = MagicMock()
    llm.achat = AsyncMock()
    resp = MagicMock()
    resp.message = MagicMock()
    resp.message.content = content
    llm.achat.return_value = resp
    return llm


class TestModelTest:
    @pytest.mark.asyncio
    async def test_llm_success(self):
        with patch("services.model_service.create_llm") as mock_create:
            mock_create.return_value = _mock_llm("OK")
            result = await model_service.test_model(
                model_type="llm", platform="openai", model="gpt-4o",
                api_base="http://x", api_key="k",
            )
        assert result["success"] is True
        assert "latency_ms" in result
        assert result["detail"] == "OK"

    @pytest.mark.asyncio
    async def test_embedding_success_reports_dimension(self):
        with patch("services.model_service.create_embedding") as mock_create:
            emb = MagicMock()
            emb.aget_text_embedding = AsyncMock(return_value=[0.1] * 1024)
            mock_create.return_value = emb
            result = await model_service.test_model(
                model_type="embedding", platform="xinference", model="bge-m3",
                api_base="http://x", api_key="k",
            )
        assert result["success"] is True
        assert "1024" in result["detail"]

    @pytest.mark.asyncio
    async def test_rerank_not_supported(self):
        result = await model_service.test_model(
            model_type="rerank", platform="x", model="m",
        )
        assert result["success"] is False
        assert "暂不支持" in result["message"]

    @pytest.mark.asyncio
    async def test_llm_empty_response(self):
        with patch("services.model_service.create_llm") as mock_create:
            mock_create.return_value = _mock_llm("")
            result = await model_service.test_model(
                model_type="llm", platform="x", model="m",
            )
        assert result["success"] is False
        assert "空响应" in result["message"]

    @pytest.mark.asyncio
    async def test_llm_exception(self):
        with patch("services.model_service.create_llm") as mock_create:
            llm = MagicMock()
            llm.achat = AsyncMock(side_effect=RuntimeError("boom"))
            mock_create.return_value = llm
            result = await model_service.test_model(
                model_type="llm", platform="x", model="m",
            )
        assert result["success"] is False
        assert "boom" in result["message"]
