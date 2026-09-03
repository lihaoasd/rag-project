"""
Search Service 业务逻辑单元测试。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import search_service
from tests.helpers import KB_ID, DOC_ID


@pytest.fixture(autouse=True)
def _default_dense():
    """本文件聚焦 Milvus 稠密路径，固定默认策略为 dense；
    真实默认值（retrieval_default_methods）由 test_search_es.py 覆盖。"""
    from config import settings
    original = settings.retrieval_default_methods
    settings.retrieval_default_methods = ["dense"]
    yield
    settings.retrieval_default_methods = original


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
    async def test_search_passes_dense_method(self, mock_retrieve):
        """dense 策略应透传到 retrieve。"""
        mock_retrieve.return_value = []

        await search_service.search(
            kb_ids=[KB_ID],
            query="test",
            retrieval_methods=["dense"],
        )

        assert mock_retrieve.call_args.kwargs["retrieval_method"] == "dense"

    @pytest.mark.asyncio
    async def test_search_passes_sparse_method_without_threshold(self, mock_retrieve):
        """sparse 策略透传到 retrieve，且不传相似度阈值（BM25 分数无 [0,1] 语义）。"""
        mock_retrieve.return_value = []

        await search_service.search(
            kb_ids=[KB_ID],
            query="test",
            retrieval_methods=["sparse"],
            similarity_threshold=0.6,
        )

        assert mock_retrieve.call_args.kwargs["retrieval_method"] == "sparse"
        assert "similarity_threshold" not in mock_retrieve.call_args.kwargs

    @pytest.mark.asyncio
    async def test_single_method_returns_directly_without_rrf(self, mock_retrieve):
        """单选 dense 时直接返回该路结果，不做 RRF 融合（分数保留原始语义）。"""
        mock_retrieve.return_value = [{
            "score": 0.88,
            "text": "文本",
            "doc_id": DOC_ID,
            "file_name": "test.pdf",
            "page_number": 1,
            "chunk_index": 3,
            "kb_id": KB_ID,
        }]

        result = await search_service.search(
            kb_ids=[KB_ID], query="test", retrieval_methods=["dense"],
        )

        assert result["total"] == 1
        assert result["results"][0]["score"] == 0.88  # 未被 RRF 改写
        assert result["results"][0]["chunk_index"] == 3

    @pytest.mark.asyncio
    async def test_single_method_sorts_by_score_across_kbs(self, mock_retrieve):
        """单选 dense 多知识库时，拼接后的结果仍应按分数降序（跨 kb 全局排序）。"""
        async def _side_effect(**kwargs):
            if kwargs["kb_id"] == "kb_a":
                return [{
                    "score": 0.5, "text": "低分", "doc_id": DOC_ID,
                    "file_name": "a.pdf", "page_number": 1, "chunk_index": 0,
                    "kb_id": "kb_a",
                }]
            return [{
                "score": 0.9, "text": "高分", "doc_id": DOC_ID,
                "file_name": "b.pdf", "page_number": 1, "chunk_index": 0,
                "kb_id": "kb_b",
            }]
        mock_retrieve.side_effect = _side_effect

        result = await search_service.search(
            kb_ids=["kb_a", "kb_b"], query="test", retrieval_methods=["dense"],
        )

        assert [r["score"] for r in result["results"]] == [0.9, 0.5]
        assert result["results"][0]["kb_id"] == "kb_b"

    @pytest.mark.asyncio
    async def test_multiple_methods_merge_with_rrf(self, mock_retrieve):
        """多选（dense+sparse）时两路召回按 RRF 融合。"""
        lexicon_cache = {}
        from services import lexicon_service
        lexicon_service._cache = lexicon_cache
        try:
            dense_hits = [
                {"score": 0.9, "text": "向量文本0", "doc_id": DOC_ID,
                 "file_name": "t.pdf", "page_number": 1, "chunk_index": 0,
                 "chunk_type": "text", "source_chunk_index": -1, "kb_id": KB_ID},
            ]
            sparse_hits = [
                {"score": 5.0, "text": "稀疏文本1", "doc_id": DOC_ID,
                 "file_name": "t.pdf", "page_number": 1, "chunk_index": 1,
                 "chunk_type": "text", "source_chunk_index": -1, "kb_id": KB_ID},
            ]

            async def _side_effect(**kwargs):
                return dense_hits if kwargs.get("retrieval_method") == "dense" else sparse_hits
            mock_retrieve.side_effect = _side_effect

            result = await search_service.search(
                kb_ids=[KB_ID], query="测试", retrieval_methods=["dense", "sparse"],
            )

            assert mock_retrieve.await_count == 2  # 两路各一次
            assert result["total"] == 2
            # dense chunk0：rank0 → 1/61；sparse chunk1：rank0 → 1/61，k=60
            assert abs(result["results"][0]["score"] - round(1.0 / 61, 4)) < 1e-6
        finally:
            lexicon_service._cache = None

    @pytest.mark.asyncio
    async def test_invalid_method_raises(self, mock_retrieve):
        with pytest.raises(ValueError):
            await search_service.search(
                kb_ids=[KB_ID], query="x", retrieval_methods=["bm25"]
            )

    @pytest.mark.asyncio
    async def test_empty_methods_raises(self, mock_retrieve):
        with pytest.raises(ValueError):
            await search_service.search(
                kb_ids=[KB_ID], query="x", retrieval_methods=[]
            )

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
    async def test_search_rerank_default_vector_gate_is_zero(self, mock_retrieve):
        """有 rerank 时，向量门槛应为 0（全召回交给 rerank）。"""
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


class TestSearchWithQueryRewrite:
    """Query 改写开关：默认关、显式开、服务端默认跟随、rerank 用原查询。"""

    @pytest.mark.asyncio
    async def test_disabled_does_not_rewrite(self, mock_retrieve):
        """显式关闭（或默认关）时不解析改写 LLM、不调用改写。"""
        mock_retrieve.return_value = []
        with patch("services.search_service.rewrite_query",
                   new=AsyncMock(return_value="改写词")) as mock_rewrite, \
             patch("services.search_service.resolve_default_model_config",
                   new=AsyncMock(return_value={"model": "m"})) as mock_resolve:
            result = await search_service.search(
                kb_ids=[KB_ID], query="原词", retrieval_methods=["dense"],
                query_rewrite=False,
            )

        mock_resolve.assert_not_awaited()
        mock_rewrite.assert_not_awaited()
        assert mock_retrieve.call_args.kwargs["query"] == "原词"
        assert result["query"] == "原词"

    @pytest.mark.asyncio
    async def test_enabled_without_default_llm_skips(self, mock_retrieve):
        """开启但无全局默认 LLM 时跳过改写，等同关闭。"""
        mock_retrieve.return_value = []
        with patch("services.search_service.rewrite_query",
                   new=AsyncMock(return_value="改写词")) as mock_rewrite, \
             patch("services.search_service.resolve_default_model_config",
                   new=AsyncMock(return_value=None)) as mock_resolve:
            result = await search_service.search(
                kb_ids=[KB_ID], query="原词", retrieval_methods=["dense"],
                query_rewrite=True,
            )

        mock_resolve.assert_awaited_once()
        mock_rewrite.assert_not_awaited()
        assert mock_retrieve.call_args.kwargs["query"] == "原词"
        assert result["query"] == "原词"

    @pytest.mark.asyncio
    async def test_enabled_rewrites_recall_keeps_original_response(self, mock_retrieve):
        """开启且有默认 LLM：召回路用改写词，响应 query 仍是原词。"""
        mock_retrieve.return_value = []
        with patch("services.search_service.rewrite_query",
                   new=AsyncMock(return_value="改写词")) as mock_rewrite, \
             patch("services.search_service.resolve_default_model_config",
                   new=AsyncMock(return_value={"model": "deepseek-chat"})) as mock_resolve:
            result = await search_service.search(
                kb_ids=[KB_ID], query="原词", retrieval_methods=["dense"],
                query_rewrite=True,
            )

        mock_resolve.assert_awaited_once()
        mock_rewrite.assert_awaited_once()
        assert mock_rewrite.await_args.args[0] == "原词"
        assert mock_rewrite.await_args.args[1]["model"] == "deepseek-chat"
        assert mock_retrieve.call_args.kwargs["query"] == "改写词"
        assert result["query"] == "原词"

    @pytest.mark.asyncio
    async def test_none_follows_server_default(self, mock_retrieve):
        """query_rewrite=None 时跟随 settings.query_rewrite_enabled。"""
        from config import settings

        original = settings.query_rewrite_enabled
        settings.query_rewrite_enabled = True
        mock_retrieve.return_value = []
        try:
            with patch("services.search_service.rewrite_query",
                       new=AsyncMock(return_value="改写词")) as mock_rewrite, \
                 patch("services.search_service.resolve_default_model_config",
                       new=AsyncMock(return_value={"model": "m"})):
                await search_service.search(
                    kb_ids=[KB_ID], query="原词", retrieval_methods=["dense"],
                    query_rewrite=None,
                )
            mock_rewrite.assert_awaited_once()
        finally:
            settings.query_rewrite_enabled = original

    @pytest.mark.asyncio
    async def test_rerank_uses_original_query_when_rewritten(self, mock_retrieve):
        """开启改写 + rerank 时：召回用改写词，rerank 收到原查询。"""
        hits = [
            {"score": 0.9, "text": "文本0", "doc_id": DOC_ID,
             "file_name": "test.pdf", "page_number": 1, "chunk_index": 0},
        ]
        mock_retrieve.return_value = hits
        reranker = MagicMock()
        reranker.arerank = AsyncMock(return_value=[(0, 0.99)])

        with patch("services.search_service.rewrite_query",
                   new=AsyncMock(return_value="改写词")), \
             patch("services.search_service.resolve_default_model_config",
                   new=AsyncMock(return_value={"model": "m"})), \
             patch("core.retrieval.create_reranker", return_value=reranker), \
             patch("services.search_service.model_service.get_model",
                   new=AsyncMock(return_value={"id": "r1", "model_type": "rerank",
                                               "model": "bge-reranker", "api_base": "http://x",
                                               "api_key": None, "params": {}})):
            result = await search_service.search(
                kb_ids=[KB_ID], query="测试", top_k=10, rerank_model_id="r1",
                query_rewrite=True,
            )

        assert mock_retrieve.call_args.kwargs["query"] == "改写词"
        assert reranker.arerank.await_args.args[0] == "测试"  # rerank 用原查询
        assert result["query"] == "测试"
