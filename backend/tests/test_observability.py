"""
Langfuse 观测封装（core/observability.py）单元测试。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.observability import (
    format_retrieval_observation,
    setup_langfuse,
    traced_span,
)


class TestFormatRetrievalObservation:
    def test_structure(self):
        hits = [
            {"score": 0.9, "doc_id": "d1", "file_name": "a.pdf",
             "page_number": 1, "chunk_index": 0, "text": "内容A"},
            {"score": 0.8, "doc_id": "d2", "file_name": "b.pdf",
             "page_number": 2, "chunk_index": 1, "text": "内容B"},
        ]
        obs = format_retrieval_observation(hits)
        assert obs["hit_count"] == 2
        assert len(obs["hits"]) == 2

        first = obs["hits"][0]
        assert first["rank"] == 1
        assert first["score"] == 0.9
        assert first["doc_id"] == "d1"
        assert first["file_name"] == "a.pdf"
        assert first["page_number"] == 1
        assert first["chunk_index"] == 0
        assert first["text"] == "内容A"

    def test_empty(self):
        assert format_retrieval_observation([]) == {"hit_count": 0, "hits": []}

    def test_text_truncation(self):
        obs = format_retrieval_observation([{"score": 1.0, "text": "x" * 500}], text_limit=200)
        assert len(obs["hits"][0]["text"]) == 200

    def test_missing_keys_defaulted(self):
        obs = format_retrieval_observation([{"text": "无元数据"}])
        h = obs["hits"][0]
        assert h["doc_id"] == ""
        assert h["file_name"] == ""


class TestSetupLangfuse:
    def test_setup_langfuse_disabled_is_noop(self):
        with patch("core.observability.settings.langfuse_enabled", False):
            assert setup_langfuse() is None

    def test_setup_langfuse_enabled_constructs_client(self):
        mock_client = MagicMock()
        mock_client.auth_check.return_value = True
        with patch("core.observability.settings.langfuse_enabled", True), \
             patch("core.observability.settings.langfuse_public_key", "pk"), \
             patch("core.observability.settings.langfuse_secret_key", "sk"), \
             patch("core.observability.settings.langfuse_base_url", "http://lf"), \
             patch("core.observability.settings.langfuse_sample_rate", 1.0), \
             patch("langfuse.Langfuse", return_value=mock_client) as mock_langfuse:
            setup_langfuse()
            mock_langfuse.assert_called_once()
            mock_client.auth_check.assert_called_once()

    def test_setup_langfuse_init_failure_is_noop(self):
        with patch("core.observability.settings.langfuse_enabled", True), \
             patch("core.observability.settings.langfuse_public_key", "pk"), \
             patch("core.observability.settings.langfuse_secret_key", "sk"), \
             patch("core.observability.settings.langfuse_base_url", "http://lf"), \
             patch("core.observability.settings.langfuse_sample_rate", 1.0), \
             patch("langfuse.Langfuse", side_effect=RuntimeError("boom")):
            assert setup_langfuse() is None


class TestTracedSpan:
    """统一 span 装饰器：命名/类型/自动 elapsed_ms/异常 error；支持 sync/async 与动态名。"""

    def _collect(self, mock_lf):
        return mock_lf.update_current_span.call_args_list

    @pytest.mark.asyncio
    async def test_async_records_elapsed_ms_and_name(self):
        """async 函数：span 名与 elapsed_ms 自动记录，业务 metadata 保留。"""
        @traced_span("my_span")
        async def fn(x: int) -> int:
            return x + 1

        mock_lf = MagicMock()
        with patch("core.observability.get_client", return_value=mock_lf):
            result = await fn(1)

        assert result == 2
        calls = self._collect(mock_lf)
        assert len(calls) == 1
        _, kwargs = calls[0]
        assert kwargs["name"] == "my_span"
        assert kwargs["metadata"]["elapsed_ms"] >= 0

    def test_sync_records_elapsed_ms(self):
        """sync 函数同样支持（如 rrf_merge）。"""
        @traced_span("sync_span")
        def fn(x: int) -> int:
            return x * 2

        mock_lf = MagicMock()
        with patch("core.observability.get_client", return_value=mock_lf):
            result = fn(2)

        assert result == 4
        _, kwargs = self._collect(mock_lf)[0]
        assert kwargs["name"] == "sync_span"
        assert kwargs["metadata"]["elapsed_ms"] >= 0

    def test_dynamic_name_from_args(self):
        """name 为可调用对象时按实参动态命名（如 recall_{method}）。"""
        @traced_span(lambda query, kb_ids, method, top_k=5, threshold=None: f"recall_{method}")
        def fn(query, kb_ids, method, top_k=5, threshold=None):
            return method

        mock_lf = MagicMock()
        with patch("core.observability.get_client", return_value=mock_lf):
            assert fn("q", ["kb1"], "dense") == "dense"
            assert fn("q", ["kb1"], "sparse") == "sparse"

        names = [c.kwargs["name"] for c in self._collect(mock_lf)]
        assert names == ["recall_dense", "recall_sparse"]

    @pytest.mark.asyncio
    async def test_exception_records_error(self):
        """函数抛异常时记录 error 字段并继续抛出。"""
        @traced_span("bad_span")
        async def fn():
            raise RuntimeError("boom")

        mock_lf = MagicMock()
        with patch("core.observability.get_client", return_value=mock_lf):
            with pytest.raises(RuntimeError, match="boom"):
                await fn()

        _, kwargs = self._collect(mock_lf)[0]
        assert kwargs["metadata"]["error"] == "boom"
        assert kwargs["metadata"]["elapsed_ms"] >= 0
