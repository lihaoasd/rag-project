"""
Model Service 模型连接测试 单元测试。
"""
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.model_factory import PaddleOCRHttpClient, create_ocr, create_reranker
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


class TestCreateOcr:
    def test_factory_resolves_config(self):
        ocr = create_ocr({
            "model": "ch_pp-ocrv4",
            "api_base": "http://ocr-host:8866",
            "api_key": "sk-abc",
            "params": {},
        })
        assert isinstance(ocr, PaddleOCRHttpClient)
        assert ocr.api_base == "http://ocr-host:8866"
        assert ocr.api_key == "sk-abc"
        assert ocr.endpoint == "/ocr"

    def test_factory_custom_endpoint(self):
        ocr = create_ocr({
            "model": "ch_pp-ocrv4",
            "api_base": "http://x",
            "api_key": None,
            "params": {"endpoint": "/ocr/v1"},
        })
        assert ocr.endpoint == "/ocr/v1"

    def test_factory_multipart_mode(self):
        """PP-OCRv6 服务：multipart 上传模式。"""
        ocr = create_ocr({
            "model": "pp_ocr",
            "api_base": "http://192.168.60.121:8866",
            "api_key": None,
            "params": {"endpoint": "/ocr/gpu/json", "upload_mode": "multipart"},
        })
        assert isinstance(ocr, PaddleOCRHttpClient)
        assert ocr.api_base == "http://192.168.60.121:8866"
        assert ocr.endpoint == "/ocr/gpu/json"
        assert ocr.upload_mode == "multipart"

    def test_recognize_multipart_sends_files(self):
        """multipart 模式发送 files 字段而非 JSON base64。"""
        from unittest.mock import MagicMock

        client = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"text": "投标保证金 500 万元"}
        client.post.return_value = resp

        ocr = PaddleOCRHttpClient(
            api_base="http://192.168.60.121:8866",
            endpoint="/ocr/gpu/json",
            upload_mode="multipart",
        )
        ocr._client = client

        result = ocr.recognize(b"fake-png-bytes")

        assert result == "投标保证金 500 万元"
        call_kwargs = client.post.call_args
        assert call_kwargs.args[0] == "http://192.168.60.121:8866/ocr/gpu/json"
        files = call_kwargs.kwargs["files"]
        assert files["file"][0] == "image.png"
        assert files["file"][1] == b"fake-png-bytes"
        assert "json" not in call_kwargs.kwargs

    def test_extract_text(self):
        assert PaddleOCRHttpClient._extract_text({"text": "你好"}) == "你好"
        assert PaddleOCRHttpClient._extract_text({"text": "", "rec_texts": ["a", "b"]}) == "a b"
        assert PaddleOCRHttpClient._extract_text({"success": False}) == ""
        assert PaddleOCRHttpClient._extract_text(None) == ""


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
    async def test_ocr_no_api_base(self):
        result = await model_service.test_model(
            model_type="ocr", platform="x", model="m",
        )
        assert result["success"] is False
        assert "未配置 API 地址" in result["message"]

    @pytest.mark.asyncio
    async def test_ocr_connectivity_success(self):
        resp = MagicMock()
        resp.status_code = 200
        client = MagicMock()
        client.get = AsyncMock(return_value=resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=client):
            result = await model_service.test_model(
                model_type="ocr", platform="x", model="m",
                api_base="http://ocr-host:8866",
            )
        assert result["success"] is True
        assert "HTTP 200" in result["detail"]

    @pytest.mark.asyncio
    async def test_ocr_connectivity_error(self):
        import httpx

        client = MagicMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=client):
            result = await model_service.test_model(
                model_type="ocr", platform="x", model="m",
                api_base="http://ocr-host:8866",
            )
        assert result["success"] is False
        assert "连接失败" in result["message"]

    @pytest.mark.asyncio
    async def test_vision_success(self):
        with patch("services.model_service.create_vision_llm") as mock_create:
            mock_create.return_value = _mock_llm("OK")
            result = await model_service.test_model(
                model_type="vision", platform="openai", model="qwen-vl",
                api_base="http://x", api_key="k",
            )
        assert result["success"] is True
        assert "latency_ms" in result
        assert result["detail"] == "OK"

    @pytest.mark.asyncio
    async def test_vision_empty_response(self):
        with patch("services.model_service.create_vision_llm") as mock_create:
            mock_create.return_value = _mock_llm("")
            result = await model_service.test_model(
                model_type="vision", platform="x", model="m",
            )
        assert result["success"] is False
        assert "空响应" in result["message"]

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


# ============================================================
# 启用/停用（is_active）
# ============================================================
def _mc(**kwargs):
    """构造最小 ModelConfig 模拟对象。"""
    from types import SimpleNamespace

    data = {
        "id": "mc_1",
        "name": "M1",
        "model_type": "llm",
        "platform": "openai",
        "model": "gpt-4o",
        "api_base": None,
        "api_key": None,
        "params": None,
        "is_default": False,
        "is_active": True,
        "created_at": None,
        "updated_at": None,
    }
    data.update(kwargs)
    return SimpleNamespace(**data)


class TestModelActive:
    @pytest.mark.asyncio
    async def test_create_model_with_is_active_false(self, mock_session):
        mock_session.refresh = AsyncMock()
        result = await model_service.create_model(
            name="X", model_type="llm", platform="p", model="m",
            is_active=False,
        )
        assert result["is_active"] is False

    @pytest.mark.asyncio
    async def test_update_model_deactivate_default_clears_default(self, mock_session):
        """停用默认模型时同步取消默认标记。"""
        mc = _mc(is_default=True)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = mc
        mock_session.execute = AsyncMock(return_value=result_mock)

        result = await model_service.update_model("mc_1", is_active=False)
        assert result["is_active"] is False
        assert result["is_default"] is False

    @pytest.mark.asyncio
    async def test_set_default_rejects_inactive_model(self, mock_session):
        mc = _mc(is_active=False)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = mc
        mock_session.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(ValueError, match="停用"):
            await model_service.set_default("mc_1")

    @pytest.mark.asyncio
    async def test_bind_inactive_model_rejected(self, mock_session):
        kb = MockKB(document_count=0)
        kb_result = MagicMock()
        kb_result.scalar_one_or_none.return_value = kb
        mc = _mc(is_active=False)
        mc_result = MagicMock()
        mc_result.scalar_one_or_none.return_value = mc
        mock_session.execute = AsyncMock(side_effect=[kb_result, mc_result])

        with pytest.raises(ValueError, match="停用"):
            await model_service.bind_model(KB_ID, "llm", "mc_1")


# ============================================================
# 启动自检
# ============================================================
class TestStartupModelCheck:
    @pytest.mark.asyncio
    async def test_checks_active_models_and_summarizes(self, mock_session):
        m1 = _mc(id="mc_1", name="LLM 模型", model_type="llm")
        m2 = _mc(id="mc_2", name="向量模型", model_type="embedding")
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [m1, m2]
        mock_session.execute = AsyncMock(return_value=result_mock)

        async def fake_test(**kwargs):
            return {"success": kwargs["model_type"] == "llm", "message": "x"}

        with patch("services.model_service.test_model", side_effect=fake_test) as mock_test:
            results = await model_service.startup_model_check()

        assert mock_test.call_count == 2
        assert len(results) == 2
        ok = [r for r in results if r["success"]]
        assert len(ok) == 1
        assert ok[0]["name"] == "LLM 模型"
        failed = [r for r in results if not r["success"]]
        assert failed[0]["name"] == "向量模型"

    @pytest.mark.asyncio
    async def test_no_active_models_returns_empty(self, mock_session):
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=result_mock)
        results = await model_service.startup_model_check()
        assert results == []

    @pytest.mark.asyncio
    async def test_check_exception_recorded_as_failure(self, mock_session):
        m1 = _mc(id="mc_1", name="LLM 模型", model_type="llm")
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [m1]
        mock_session.execute = AsyncMock(return_value=result_mock)

        async def boom(**kwargs):
            raise RuntimeError("boom")

        with patch("services.model_service.test_model", side_effect=boom):
            results = await model_service.startup_model_check()

        assert len(results) == 1
        assert results[0]["success"] is False
        assert "boom" in results[0]["message"]


# ============================================================
# 拉取供应商模型（/models 端点）
# ============================================================
def _mock_httpx_models(payload):
    """mock httpx.AsyncClient 返回给定 /models 响应 payload，返回 client 用于断言 URL。"""
    from types import SimpleNamespace as _NS

    client = AsyncMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    client.get.return_value = resp
    acm = MagicMock()
    acm.__aenter__.return_value = client
    acm.__aexit__.return_value = False
    patcher = patch("httpx.AsyncClient", return_value=acm)
    patcher.start()
    return client, patcher


class TestFetchProviderModels:
    @pytest.mark.asyncio
    async def test_fetch_success(self, mock_session):
        """从供应商 api_base + /models 拉取并解析模型列表。"""
        p = SimpleNamespace(id="p1", name="DeepSeek", api_base="https://api.deepseek.com/v1", api_key="sk-abc")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = p
        mock_session.execute = AsyncMock(return_value=result_mock)

        client, patcher = _mock_httpx_models({
            "data": [{"id": "deepseek-chat", "owned_by": "deepseek"}, {"id": "deepseek-reasoner"}]
        })
        try:
            out = await model_service.fetch_provider_models("p1")
        finally:
            patcher.stop()

        assert out["provider_id"] == "p1"
        assert out["provider_name"] == "DeepSeek"
        assert out["total"] == 2
        assert out["models"][0]["id"] == "deepseek-chat"
        assert out["models"][1]["owned_by"] == ""
        # 端点 = api_base(去尾 /) + /models
        assert client.get.call_args.args[0] == "https://api.deepseek.com/v1/models"

    @pytest.mark.asyncio
    async def test_fetch_uses_env_api_key(self, mock_session):
        """api_key 为 ${ENV} 占位符时解析环境变量后再请求。"""
        p = SimpleNamespace(id="p1", name="X", api_base="http://x/v1", api_key="${FAKE_KEY}")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = p
        mock_session.execute = AsyncMock(return_value=result_mock)

        with patch.dict(os.environ, {"FAKE_KEY": "sk-env"}, clear=True):
            client, patcher = _mock_httpx_models({"data": [{"id": "m1"}]})
            try:
                await model_service.fetch_provider_models("p1")
            finally:
                patcher.stop()
        headers = client.get.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer sk-env"

    @pytest.mark.asyncio
    async def test_fetch_no_api_base_raises(self, mock_session):
        p = SimpleNamespace(id="p1", name="X", api_base=None, api_key=None)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = p
        mock_session.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(ValueError, match="API Base"):
            await model_service.fetch_provider_models("p1")

    @pytest.mark.asyncio
    async def test_fetch_bad_payload_raises(self, mock_session):
        p = SimpleNamespace(id="p1", name="X", api_base="http://x/v1", api_key=None)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = p
        mock_session.execute = AsyncMock(return_value=result_mock)

        client, patcher = _mock_httpx_models({"object": "list"})  # 无 data 数组
        try:
            with pytest.raises(ValueError, match="data"):
                await model_service.fetch_provider_models("p1")
        finally:
            patcher.stop()
