"""
Search Service 业务逻辑单元测试。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import search_service
from tests.helpers import KB_ID, DOC_ID


class TestSearch:
    @pytest.mark.asyncio
    async def test_search_returns_results(self, mock_retrieve):
        result = await search_service.search(
            kb_ids=[KB_ID],
            query="测试查询",
            top_k=10,
            similarity_threshold=0.6,
        )
        assert result["query"] == "测试查询"
        assert result["total"] == 1
        assert len(result["results"]) == 1
        assert result["results"][0]["rank"] == 1
        assert result["results"][0]["score"] == 0.95

    @pytest.mark.asyncio
    async def test_search_empty_results(self, mock_retrieve):
        mock_retrieve.return_value = []

        result = await search_service.search(
            kb_ids=[KB_ID],
            query="无结果查询",
        )
        assert result["total"] == 0
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_search_uses_top_k_for_recall(self, mock_retrieve):
        """无 rerank 时召回数即 top_k，不做放大。"""
        mock_retrieve.return_value = []

        await search_service.search(
            kb_ids=[KB_ID],
            query="test",
            top_k=5,
        )
        mock_retrieve.assert_called_once()
        assert mock_retrieve.call_args.kwargs["top_k"] == 5

    @pytest.mark.asyncio
    async def test_search_no_rerank_uses_similarity_threshold(self, mock_retrieve):
        """无 rerank 时，向量门槛应等于 similarity_threshold。"""
        mock_retrieve.return_value = []

        await search_service.search(
            kb_ids=[KB_ID], query="test", top_k=10, similarity_threshold=0.6,
        )

        assert mock_retrieve.call_args.kwargs["similarity_threshold"] == 0.6


class TestSearchWithRerank:
    @pytest.mark.asyncio
    async def test_search_applies_rerank(self, mock_retrieve):
        """传入 rerank_model_id 时应按 relevance_score 重排。"""
        hits = [
            {"score": 0.9, "text": "文本0", "doc_id": DOC_ID,
             "file_name": "test.pdf", "page_number": 1, "chunk_index": 0},
            {"score": 0.8, "text": "文本1", "doc_id": DOC_ID,
             "file_name": "test.pdf", "page_number": 1, "chunk_index": 1},
            {"score": 0.7, "text": "文本2", "doc_id": DOC_ID,
             "file_name": "test.pdf", "page_number": 1, "chunk_index": 2},
        ]
        mock_retrieve.return_value = hits

        reranker = MagicMock()
        reranker.arerank = AsyncMock(return_value=[(2, 0.99), (1, 0.5), (0, 0.1)])

        with patch("core.retrieval.create_reranker", return_value=reranker), \
             patch("services.search_service.model_service.get_model",
                   new=AsyncMock(return_value={"id": "r1", "model_type": "rerank",
                                              "model": "bge-reranker", "api_base": "http://x",
                                              "api_key": None, "params": {}})):
            result = await search_service.search(
                kb_ids=[KB_ID], query="测试", top_k=10, rerank_model_id="r1",
            )

        assert result["results"][0]["text"] == "文本2"
        assert result["results"][0]["score"] == 0.99

    @pytest.mark.asyncio
    async def test_search_rerank_failure_falls_back(self, mock_retrieve):
        """rerank 失败时应回退相似度排序，不影响返回。"""
        hits = [
            {"score": 0.9, "text": "文本0", "doc_id": DOC_ID,
             "file_name": "test.pdf", "page_number": 1, "chunk_index": 0},
        ]
        mock_retrieve.return_value = hits

        with patch("services.search_service.model_service.get_model",
                   new=AsyncMock(side_effect=RuntimeError("boom"))):
            result = await search_service.search(
                kb_ids=[KB_ID], query="测试", top_k=10, rerank_model_id="r1",
            )

        assert result["total"] == 1
        assert result["results"][0]["score"] == 0.9

    @pytest.mark.asyncio
    async def test_search_rerank_filters_by_rerank_score(self, mock_retrieve):
        """最终阈值应作用于 rerank 分数：低 rerank 分的候选被丢弃。"""
        hits = [
            {"score": 0.9, "text": "文本0", "doc_id": DOC_ID,
             "file_name": "test.pdf", "page_number": 1, "chunk_index": 0},
            {"score": 0.8, "text": "文本1", "doc_id": DOC_ID,
             "file_name": "test.pdf", "page_number": 1, "chunk_index": 1},
            {"score": 0.7, "text": "文本2", "doc_id": DOC_ID,
             "file_name": "test.pdf", "page_number": 1, "chunk_index": 2},
        ]
        mock_retrieve.return_value = hits

        reranker = MagicMock()
        reranker.arerank = AsyncMock(return_value=[(0, 0.9), (1, 0.4), (2, 0.8)])

        with patch("core.retrieval.create_reranker", return_value=reranker), \
             patch("services.search_service.model_service.get_model",
                   new=AsyncMock(return_value={"id": "r1", "model_type": "rerank",
                                              "model": "bge-reranker", "api_base": "http://x",
                                              "api_key": None, "params": {}})):
            result = await search_service.search(
                kb_ids=[KB_ID], query="测试", top_k=10, rerank_model_id="r1",
                similarity_threshold=0.6,
            )

        # 仅保留 rerank 分数 >= 0.6：文本0(0.9)、文本2(0.8)，文本1(0.4) 被丢弃
        assert result["total"] == 2
        assert [r["text"] for r in result["results"]] == ["文本0", "文本2"]
        assert result["results"][0]["score"] == 0.9

    @pytest.mark.asyncio
    async def test_search_rerank_truncates_candidates(self, mock_retrieve):
        """只把前 rerank_top_n 个向量候选送入排序模型。"""
        hits = [
            {"score": 0.9 - i * 0.05, "text": f"文本{i}", "doc_id": DOC_ID,
             "file_name": "test.pdf", "page_number": 1, "chunk_index": i}
            for i in range(5)
        ]
        mock_retrieve.return_value = hits

        reranker = MagicMock()
        reranker.arerank = AsyncMock(return_value=[(0, 0.9), (1, 0.8)])

        with patch("core.retrieval.create_reranker", return_value=reranker), \
             patch("services.search_service.model_service.get_model",
                   new=AsyncMock(return_value={"id": "r1", "model_type": "rerank",
                                              "model": "bge-reranker", "api_base": "http://x",
                                              "api_key": None, "params": {}})):
            await search_service.search(
                kb_ids=[KB_ID], query="测试", top_k=10, rerank_model_id="r1",
                rerank_top_n=2,
            )

        # 只应把前 2 个候选送入排序模型
        called_docs = reranker.arerank.call_args.args[1]
        assert called_docs == ["文本0", "文本1"]

    @pytest.mark.asyncio
    async def test_search_rerank_vector_threshold_gate(self, mock_retrieve):
        """显式传 rerank_vector_threshold 时应作为向量门槛传入 retrieve。"""
        mock_retrieve.return_value = []
        reranker = MagicMock()
        reranker.arerank = AsyncMock(return_value=[])

        with patch("core.retrieval.create_reranker", return_value=reranker), \
             patch("services.search_service.model_service.get_model",
                   new=AsyncMock(return_value={"id": "r1", "model_type": "rerank",
                                              "model": "bge-reranker", "api_base": "http://x",
                                              "api_key": None, "params": {}})):
            await search_service.search(
                kb_ids=[KB_ID], query="测试", top_k=10, rerank_model_id="r1",
                similarity_threshold=0.6, rerank_vector_threshold=0.8,
            )

        assert mock_retrieve.call_args.kwargs["similarity_threshold"] == 0.8

    @pytest.mark.asyncio
    async def test_search_rerank_default_vector_gate_is_zero(self, mock_retrieve):
        """有 rerank 且不传 rerank_vector_threshold 时，向量门槛应为 0（全召回交给 rerank）。"""
        mock_retrieve.return_value = []
        reranker = MagicMock()
        reranker.arerank = AsyncMock(return_value=[])

        with patch("core.retrieval.create_reranker", return_value=reranker), \
             patch("services.search_service.model_service.get_model",
                   new=AsyncMock(return_value={"id": "r1", "model_type": "rerank",
                                              "model": "bge-reranker", "api_base": "http://x",
                                              "api_key": None, "params": {}})):
            await search_service.search(
                kb_ids=[KB_ID], query="测试", top_k=10, rerank_model_id="r1",
                similarity_threshold=0.75,
            )

        assert mock_retrieve.call_args.kwargs["similarity_threshold"] == 0.0

    @pytest.mark.asyncio
    async def test_search_rerank_fetch_top_k_covers_rerank_top_n(self, mock_retrieve):
        """rerank 时召回数覆盖 rerank_top_n，保证有足够候选进排序。"""
        mock_retrieve.return_value = []
        reranker = MagicMock()
        reranker.arerank = AsyncMock(return_value=[])

        with patch("core.retrieval.create_reranker", return_value=reranker), \
             patch("services.search_service.model_service.get_model",
                   new=AsyncMock(return_value={"id": "r1", "model_type": "rerank",
                                              "model": "bge-reranker", "api_base": "http://x",
                                              "api_key": None, "params": {}})):
            await search_service.search(
                kb_ids=[KB_ID], query="测试", top_k=5, rerank_model_id="r1",
                rerank_top_n=20,
            )

        # 应为 max(5, 20)=20
        assert mock_retrieve.call_args.kwargs["top_k"] == 20
