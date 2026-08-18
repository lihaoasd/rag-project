"""
检索引擎 rerank / retrieve_and_rank 单元测试（core/retrieval.py）。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import retrieval
from tests.helpers import KB_ID, DOC_ID


def _hit(score: float, text: str) -> dict:
    return {
        "score": score,
        "text": text,
        "doc_id": DOC_ID,
        "file_name": "test.pdf",
        "page_number": 1,
        "chunk_index": 0,
    }


class TestRerank:
    def test_resolve_retrieval_method_vector_disables_hybrid(self):
        assert retrieval._resolve_retrieval_method("vector") is False

    def test_resolve_retrieval_method_hybrid_enables_hybrid(self):
        assert retrieval._resolve_retrieval_method("hybrid") is True

    def test_resolve_retrieval_method_default_uses_settings(self, monkeypatch):
        monkeypatch.setattr(retrieval.settings, "retrieval_hybrid_enabled", True)
        assert retrieval._resolve_retrieval_method(None) is True

        monkeypatch.setattr(retrieval.settings, "retrieval_hybrid_enabled", False)
        assert retrieval._resolve_retrieval_method(None) is False

    def test_resolve_retrieval_method_rejects_unknown(self):
        with pytest.raises(ValueError):
            retrieval._resolve_retrieval_method("graph")

    def test_resolve_hybrid_ranker_defaults_to_rrf(self):
        assert retrieval._resolve_hybrid_ranker(None) == ("RRFRanker", {})
        assert retrieval._resolve_hybrid_ranker("rrf") == ("RRFRanker", {})

    def test_resolve_hybrid_ranker_weighted(self):
        assert retrieval._resolve_hybrid_ranker("weighted") == (
            "WeightedRanker",
            {"weights": [1.0, 1.0]},
        )
        assert retrieval._resolve_hybrid_ranker("weighted", [0.7, 0.3]) == (
            "WeightedRanker",
            {"weights": [0.7, 0.3]},
        )

    def test_resolve_hybrid_ranker_rejects_invalid(self):
        with pytest.raises(ValueError):
            retrieval._resolve_hybrid_ranker("score")

    @pytest.mark.asyncio
    async def test_rerank_overrides_score_and_reorders(self):
        hits = [_hit(0.9, "文本0"), _hit(0.8, "文本1"), _hit(0.7, "文本2")]
        reranker = MagicMock()
        reranker.arerank = AsyncMock(return_value=[(2, 0.99), (1, 0.5), (0, 0.1)])
        config = {"model": "bge-reranker", "api_base": "http://x", "api_key": None, "params": {}}

        with patch("core.retrieval.create_reranker", return_value=reranker):
            result = await retrieval.rerank("查询", hits, config)

        assert [h["text"] for h in result] == ["文本2", "文本1", "文本0"]
        assert result[0]["score"] == 0.99

    @pytest.mark.asyncio
    async def test_rerank_honors_top_n(self):
        hits = [_hit(0.9, f"文本{i}") for i in range(5)]
        reranker = MagicMock()
        reranker.arerank = AsyncMock(return_value=[(0, 0.9), (1, 0.8)])
        config = {"model": "r", "api_base": "http://x", "api_key": None, "params": {}}

        with patch("core.retrieval.create_reranker", return_value=reranker):
            await retrieval.rerank("查询", hits, config, top_n=2)

        assert reranker.arerank.call_args.kwargs["top_n"] == 2


class TestRetrieveAndRank:
    @pytest.mark.asyncio
    async def test_no_rerank_config_delegates_to_retrieve(self):
        retrieved = [_hit(0.9, "文本")]
        with patch("core.retrieval.retrieve", new=AsyncMock(return_value=retrieved)) as mock_retrieve, \
             patch("core.retrieval.rerank", new=AsyncMock()) as mock_rerank:
            result = await retrieval.retrieve_and_rank(
                query="查询", kb_id=KB_ID, top_k=5, similarity_threshold=0.6,
            )

        assert result == retrieved
        mock_retrieve.assert_awaited_once()
        mock_rerank.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_with_rerank_config_applies_rerank(self):
        retrieved = [_hit(0.9, "文本0"), _hit(0.8, "文本1")]
        reranked = [_hit(0.99, "文本1"), _hit(0.85, "文本0")]
        config = {"model": "r"}

        with patch("core.retrieval.retrieve", new=AsyncMock(return_value=retrieved)), \
             patch("core.retrieval.rerank", new=AsyncMock(return_value=reranked)) as mock_rerank:
            result = await retrieval.retrieve_and_rank(
                query="查询", kb_id=KB_ID, top_k=5, similarity_threshold=0.6,
                rerank_config=config,
            )

        assert result == reranked
        mock_rerank.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_with_rerank_config_filters_below_threshold(self):
        retrieved = [_hit(0.9, "文本0"), _hit(0.8, "文本1")]
        reranked = [_hit(0.99, "文本1"), _hit(0.5, "文本0")]
        config = {"model": "r"}

        with patch("core.retrieval.retrieve", new=AsyncMock(return_value=retrieved)), \
             patch("core.retrieval.rerank", new=AsyncMock(return_value=reranked)):
            result = await retrieval.retrieve_and_rank(
                query="查询", kb_id=KB_ID, top_k=5, similarity_threshold=0.6,
                rerank_config=config, rerank_threshold=0.8,
            )

        assert result == [_hit(0.99, "文本1")]
