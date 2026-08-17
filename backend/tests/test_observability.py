"""
Langfuse 观测封装（core/observability.py）单元测试。
"""
from unittest.mock import MagicMock, patch

from core.observability import (
    format_retrieval_observation,
    setup_langfuse,
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
