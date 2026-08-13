"""
Model Service 模型连接测试 单元测试。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.model_factory import create_reranker
from services import model_service
from tests.helpers import KB_ID, MockKB


class TestCreateReranker:
    def test_factory_resolves_config(self):
        reranker = create_reranker({
            "model": "bge-reranker-v2-m3",
            "api_base": "http://x/v1",
            "api_key": "sk-abc",
            "params": {},
        })
        assert reranker.model == "bge-reranker-v2-m3"
        assert reranker.api_base == "http://x/v1"
        assert reranker.api_key == "sk-abc"
        assert reranker.endpoint == "/rerank"  # 默认路径

    def test_factory_custom_endpoint(self):
        reranker = create_reranker({
            "model": "qwen3-rerank",
            "api_base": "https://dashscope.aliyuncs.com/compatible-api/v1",
            "api_key": "sk-abc",
            "params": {"endpoint": "/reranks"},
        })
        assert reranker.endpoint == "/reranks"


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
    async def test_rerank_success(self):
        with patch("services.model_service.create_reranker") as mock_create:
            reranker = MagicMock()
            reranker.arerank = AsyncMock(return_value=[(0, 0.95), (1, 0.3)])
            mock_create.return_value = reranker
            result = await model_service.test_model(
                model_type="rerank", platform="x", model="m",
                api_base="http://x", api_key="k",
            )
        assert result["success"] is True
        assert "0.95" in result["detail"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("model_type", ["vision", "ocr"])
    async def test_vision_ocr_not_supported(self, model_type):
        result = await model_service.test_model(
            model_type=model_type, platform="x", model="m",
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


class TestBindModelEmbeddingLock:
    """Embedding 锁定的解绑语义：仅禁止「改变」，跟随默认时解绑是 no-op。"""

    @pytest.mark.asyncio
    async def test_unbind_default_embedding_locked_is_noop(self, mock_async_session, mock_session):
        """已锁定但无绑定的 Embedding 解绑应是 no-op（修复：默认状态下无法改其他模型）。"""
        kb = MockKB(document_count=3)
        kb_result = MagicMock()
        kb_result.scalar_one_or_none.return_value = kb
        binding_result = MagicMock()
        binding_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(side_effect=[kb_result, binding_result, MagicMock()])

        result = await model_service.bind_model(KB_ID, "embedding", None)
        assert result["model_config_id"] is None

    @pytest.mark.asyncio
    async def test_unbind_bound_embedding_locked_raises(self, mock_async_session, mock_session):
        """已锁定且存在绑定时，解绑应被禁止。"""
        kb = MockKB(document_count=3)
        kb_result = MagicMock()
        kb_result.scalar_one_or_none.return_value = kb
        binding_result = MagicMock()
        binding_result.scalar_one_or_none.return_value = MagicMock()  # 存在绑定
        mock_session.execute = AsyncMock(side_effect=[kb_result, binding_result])

        with pytest.raises(ValueError, match="锁定"):
            await model_service.bind_model(KB_ID, "embedding", None)

    @pytest.mark.asyncio
    async def test_bind_rerank_rejected(self, mock_async_session, mock_session):
        """排序模型为检索期全局使用，禁止绑定到知识库。"""
        with pytest.raises(ValueError, match="无效的模型类型"):
            await model_service.bind_model(KB_ID, "rerank", None)
