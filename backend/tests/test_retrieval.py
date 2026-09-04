"""
检索引擎 rerank / retrieve_and_rank / 查询模式解析单元测试（core/retrieval.py）。
"""
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from llama_index.core.vector_stores.types import VectorStoreQueryMode

from core import retrieval
from config import settings
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


class TestResolveQueryMode:
    def test_dense_default(self):
        assert retrieval._resolve_query_mode(None) == VectorStoreQueryMode.DEFAULT
        assert retrieval._resolve_query_mode("dense") == VectorStoreQueryMode.DEFAULT

    def test_sparse(self):
        assert retrieval._resolve_query_mode("sparse") == VectorStoreQueryMode.SPARSE

    def test_rejects_unknown(self):
        with pytest.raises(ValueError):
            retrieval._resolve_query_mode("graph")

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


class TestRetrieveSpanTracing:
    """retrieve() 的 Langfuse 子 span：向量化（_embed_query）/ Milvus 检索（_milvus_search）+ 环节耗时。"""

    def _patch_retrieve_env(self, embed_model, vector_store, retriever, mock_lf):
        """patch retrieve() 依赖：config/embedding/vector_store/index/client。"""
        index = MagicMock()
        index.as_retriever.return_value = retriever
        stack = ExitStack()
        stack.enter_context(patch("core.retrieval.resolve_model_config",
                                  new=AsyncMock(return_value={"model": "bge-m3", "api_base": "http://x", "params": {}})))
        stack.enter_context(patch("core.retrieval.create_embedding", return_value=embed_model))
        stack.enter_context(patch("core.retrieval.get_vector_store", return_value=vector_store))
        stack.enter_context(patch("core.retrieval.VectorStoreIndex.from_vector_store", return_value=index))
        stack.enter_context(patch("core.retrieval.get_client", return_value=mock_lf))
        return stack

    def _retrieve_metadata(self, mock_lf):
        for call in mock_lf.update_current_span.call_args_list:
            md = call.kwargs.get("metadata") or {}
            if "embed_ms" in md:
                return md
        return {}

    @pytest.mark.asyncio
    async def test_dense_calls_embed_and_search_sub_spans(self):
        """dense 检索应调用 _embed_query（向量化）与 _milvus_search 子 span，retrieve span 带环节耗时。"""
        embed_model = MagicMock()
        embed_model.aget_query_embedding = AsyncMock(return_value=[0.1] * 4)
        vector_store = MagicMock()
        vector_store.collection_name = f"kb_{KB_ID}"
        vector_store.client.list_collections.return_value = [vector_store.collection_name]
        retriever = MagicMock()
        retriever.aretrieve = AsyncMock(return_value=[])
        mock_lf = MagicMock()

        with self._patch_retrieve_env(embed_model, vector_store, retriever, mock_lf), \
             patch("core.retrieval._embed_query", new=AsyncMock(return_value=[0.1] * 4)) as mock_embed, \
             patch("core.retrieval._milvus_search", new=AsyncMock(return_value=[])) as mock_search:
            hits = await retrieval.retrieve(query="测试", kb_id=KB_ID, top_k=5)

        assert hits == []
        # 向量化与检索两个子 span 函数均被调用（observe 装饰后自动嵌套为 retrieve 子 span）
        assert mock_embed.await_count == 1
        assert mock_search.await_count == 1
        assert mock_embed.await_args.args[0] == "测试"
        assert mock_search.await_args.kwargs["collection"] == f"kb_{KB_ID}"
        # 环节耗时写入 retrieve span metadata
        md = self._retrieve_metadata(mock_lf)
        assert md["embed_ms"] >= 0
        assert md["search_ms"] >= 0
        assert md["filter_ms"] >= 0
        assert md["embedding_model"] == "bge-m3"
        assert md["dim"] == 1024  # 未配 dimension 时回退 settings.embedding_dim

    @pytest.mark.asyncio
    async def test_sparse_skips_embedding_sub_span(self):
        """sparse 走 Milvus BM25，不调用 _embed_query，embed_ms 为 None。"""
        embed_model = MagicMock()
        vector_store = MagicMock()
        vector_store.collection_name = f"kb_{KB_ID}"
        vector_store.client.list_collections.return_value = [vector_store.collection_name]
        retriever = MagicMock()
        retriever.aretrieve = AsyncMock(return_value=[])
        mock_lf = MagicMock()

        with self._patch_retrieve_env(embed_model, vector_store, retriever, mock_lf), \
             patch("core.retrieval._embed_query", new=AsyncMock()) as mock_embed, \
             patch("core.retrieval._milvus_search", new=AsyncMock(return_value=[])) as mock_search:
            hits = await retrieval.retrieve(
                query="测试", kb_id=KB_ID, top_k=5, retrieval_method="sparse"
            )

        assert hits == []
        mock_embed.assert_not_awaited()
        assert mock_search.await_count == 1
        md = self._retrieve_metadata(mock_lf)
        assert md["embed_ms"] is None

    @pytest.mark.asyncio
    async def test_collection_missing_no_sub_spans(self):
        """collection 不存在时提前返回，不调用任何子 span 函数。"""
        embed_model = MagicMock()
        vector_store = MagicMock()
        vector_store.collection_name = f"kb_{KB_ID}"
        vector_store.client.list_collections.return_value = []  # collection 不存在
        retriever = MagicMock()
        mock_lf = MagicMock()

        with self._patch_retrieve_env(embed_model, vector_store, retriever, mock_lf), \
             patch("core.retrieval._embed_query", new=AsyncMock()) as mock_embed, \
             patch("core.retrieval._milvus_search", new=AsyncMock()) as mock_search:
            hits = await retrieval.retrieve(query="测试", kb_id=KB_ID, top_k=5)

        assert hits == []
        mock_embed.assert_not_awaited()
        mock_search.assert_not_awaited()


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

    # ============================================================
    # 三路混合（dense + sparse + fulltext）— 问答链路检索
    # ============================================================
    @pytest.mark.asyncio
    async def test_three_way_mixed_recalls_and_rrf_merges(self):
        """三路混合：dense/sparse 走 retrieve，fulltext 走 es_search（含词库查询扩展），RRF 融合并补 kb_id。"""
        dense_hits = [_hit(0.9, "dense文本")]
        sparse_hits = [_hit(0.8, "sparse文本")]
        es_hits = [dict(_hit(0.7, "es文本"), kb_id=KB_ID, highlight=None)]
        dense_hits[0]["chunk_index"] = 0
        sparse_hits[0]["chunk_index"] = 1
        es_hits[0]["chunk_index"] = 2

        with patch("core.retrieval.retrieve",
                   new=AsyncMock(side_effect=[dense_hits, sparse_hits])) as mock_retrieve, \
             patch("core.retrieval.es_store.es_search",
                   new=AsyncMock(return_value=es_hits)) as mock_es:
            result = await retrieval.retrieve_and_rank(
                query="查询", kb_id=KB_ID, top_k=5, similarity_threshold=0.6,
                retrieval_methods=["dense", "sparse", "fulltext"],
                expand_query=lambda q: [q, "扩展词"],
            )

        # 三路均被召回：dense + sparse 各走一次 retrieve
        assert mock_retrieve.await_count == 2
        methods = [c.kwargs["retrieval_method"] for c in mock_retrieve.await_args_list]
        assert methods == ["dense", "sparse"]
        # fulltext 走 ES：查询扩展 + kb 过滤 + top_k 放大 + 不返回高亮
        assert mock_es.await_count == 1
        assert mock_es.await_args.args[0] == ["查询", "扩展词"]
        assert mock_es.await_args.args[1] == [KB_ID]
        assert mock_es.await_args.kwargs["top_k"] == 5 * settings.retrieval_es_top_k_factor
        assert mock_es.await_args.kwargs["highlight"] is False
        # RRF 融合后共 3 条（不同 chunk_index 不合并），且每路命中均补 kb_id
        assert len(result) == 3
        assert all(h["kb_id"] == KB_ID for h in result)

    @pytest.mark.asyncio
    async def test_fulltext_failure_degrades_to_dense_sparse(self):
        """ES 检索异常时 fulltext 降级为空，dense+sparse 正常，整体不抛错。"""
        dense_hits = [_hit(0.9, "dense文本")]
        sparse_hits = [_hit(0.8, "sparse文本")]
        dense_hits[0]["chunk_index"] = 0
        sparse_hits[0]["chunk_index"] = 1

        with patch("core.retrieval.retrieve",
                   new=AsyncMock(side_effect=[dense_hits, sparse_hits])), \
             patch("core.retrieval.es_store.es_search",
                   new=AsyncMock(side_effect=RuntimeError("ES 不可用"))):
            result = await retrieval.retrieve_and_rank(
                query="查询", kb_id=KB_ID, top_k=5,
                retrieval_methods=["dense", "sparse", "fulltext"],
            )

        assert len(result) == 2  # 仅 dense + sparse，无 fulltext 命中

    @pytest.mark.asyncio
    async def test_single_fulltext_returns_directly(self):
        """单选 fulltext 直返，不做 RRF，保留 ES 原始分数并补 kb_id。"""
        es_hits = [_hit(5.0, "es文本")]

        with patch("core.retrieval.es_store.es_search",
                   new=AsyncMock(return_value=es_hits)):
            result = await retrieval.retrieve_and_rank(
                query="查询", kb_id=KB_ID, top_k=5,
                retrieval_methods=["fulltext"],
            )

        assert len(result) == 1
        assert result[0]["score"] == 5.0
        assert result[0]["kb_id"] == KB_ID

    @pytest.mark.asyncio
    async def test_invalid_method_raises(self):
        with pytest.raises(ValueError):
            await retrieval.retrieve_and_rank(
                query="查询", kb_id=KB_ID, retrieval_methods=["graph"],
            )

    # ============================================================
    # Query 改写：改写词用于召回，rerank 用原查询，默认不改写
    # ============================================================
    @pytest.mark.asyncio
    async def test_query_rewrite_rewrites_before_recall(self):
        """有改写配置时：改写词用于 dense 召回，改写函数收到原词与历史。"""
        hit = _hit(0.9, "文本")
        history = [{"role": "user", "content": "x"}]
        with patch("core.retrieval.rewrite_query",
                   new=AsyncMock(return_value="改写词")) as mock_rewrite, \
             patch("core.retrieval.retrieve",
                   new=AsyncMock(return_value=[hit])) as mock_retrieve, \
             patch("core.retrieval.rerank", new=AsyncMock()) as mock_rerank:
            result = await retrieval.retrieve_and_rank(
                query="原词", kb_id=KB_ID, top_k=5,
                retrieval_methods=["dense"],
                query_rewrite_config={"model": "m"},
                query_rewrite_history=history,
            )

        mock_rewrite.assert_awaited_once()
        assert mock_rewrite.await_args.args[0] == "原词"
        assert mock_rewrite.await_args.kwargs["history"] == history
        assert mock_retrieve.await_args.kwargs["query"] == "改写词"
        mock_rerank.assert_not_awaited()
        assert result == [hit]

    @pytest.mark.asyncio
    async def test_query_rewrite_rerank_uses_original_query(self):
        """改写只服务召回：rerank 用改写前的原问题判断相关性。"""
        hit = _hit(0.9, "文本")
        reranked = [dict(hit, score=0.99)]
        with patch("core.retrieval.rewrite_query", new=AsyncMock(return_value="改写词")), \
             patch("core.retrieval.retrieve", new=AsyncMock(return_value=[hit])), \
             patch("core.retrieval.rerank",
                   new=AsyncMock(return_value=reranked)) as mock_rerank:
            await retrieval.retrieve_and_rank(
                query="原词", kb_id=KB_ID, top_k=5,
                retrieval_methods=["dense"],
                rerank_config={"model": "r"}, rerank_threshold=0.0,
                query_rewrite_config={"model": "m"},
            )

        assert mock_rerank.await_args.args[0] == "原词"

    @pytest.mark.asyncio
    async def test_query_rewrite_fulltext_uses_rewritten(self):
        """fulltext 路用改写词直查（词库扩展前已是改写词）。"""
        es_hits = [dict(_hit(2.0, "es文本"), kb_id=KB_ID)]
        with patch("core.retrieval.rewrite_query", new=AsyncMock(return_value="改写词")), \
             patch("core.retrieval.es_store.es_search",
                   new=AsyncMock(return_value=es_hits)) as mock_es:
            await retrieval.retrieve_and_rank(
                query="原词", kb_id=KB_ID, top_k=5,
                retrieval_methods=["fulltext"],
                query_rewrite_config={"model": "m"},
            )

        assert mock_es.await_args.args[0] == "改写词"

    @pytest.mark.asyncio
    async def test_no_rewrite_config_skips_rewrite(self):
        """不传 query_rewrite_config 时完全不调用改写，行为与现状一致（回归）。"""
        hit = _hit(0.9, "文本")
        with patch("core.retrieval.rewrite_query",
                   new=AsyncMock(return_value="改写词")) as mock_rewrite, \
             patch("core.retrieval.retrieve",
                   new=AsyncMock(return_value=[hit])) as mock_retrieve:
            await retrieval.retrieve_and_rank(
                query="原词", kb_id=KB_ID, top_k=5, retrieval_methods=["dense"],
            )

        mock_rewrite.assert_not_awaited()
        assert mock_retrieve.await_args.kwargs["query"] == "原词"


class TestRetrieveAndRankMultiIntent:
    """多意图：rewrite 返回 list → 逐子查询召回 + 按子查询 rerank + 各取 top_k → 跨意图去重合并。"""

    @pytest.mark.asyncio
    async def test_recalls_and_reranks_per_intent_then_merges_dedupe(self):
        """2 意图：retrieve/rerank 各 2 次且 rerank 用各自子查询；重复切片只保留先出现意图。"""
        hits_a = [_hit(0.9, "A0"), _hit(0.8, "A1")]
        hits_b = [_hit(0.7, "B1"), _hit(0.6, "B2")]
        hits_a[0]["chunk_index"] = 0
        hits_a[1]["chunk_index"] = 1
        hits_b[0]["chunk_index"] = 1  # 与意图 A 的 chunk1 重复
        hits_b[1]["chunk_index"] = 2
        reranked_a = [dict(h, score=s) for h, s in zip(hits_a, (0.99, 0.88))]
        reranked_b = [dict(h, score=s) for h, s in zip(hits_b, (0.95, 0.6))]

        with patch("core.retrieval.rewrite_query",
                   new=AsyncMock(return_value=["子A", "子B"])), \
             patch("core.retrieval.retrieve",
                   new=AsyncMock(side_effect=[hits_a, hits_b])) as mock_retrieve, \
             patch("core.retrieval.rerank",
                   new=AsyncMock(side_effect=[reranked_a, reranked_b])) as mock_rerank:
            result = await retrieval.retrieve_and_rank(
                query="原词", kb_id=KB_ID, top_k=2,
                retrieval_methods=["dense"],
                rerank_config={"model": "r"}, rerank_threshold=0.0,
                query_rewrite_config={"model": "m"},
            )

        # 每个子查询各一次完整召回
        assert [c.kwargs["query"] for c in mock_retrieve.await_args_list] == ["子A", "子B"]
        # 重排按各自子查询（非原查询）
        assert [c.args[0] for c in mock_rerank.await_args_list] == ["子A", "子B"]
        # 合并去重：A0/A1(先出现保留)/B2，共 3 条，顺序=意图顺序拼接
        assert [h["text"] for h in result] == ["A0", "A1", "B2"]

    @pytest.mark.asyncio
    async def test_no_rerank_truncates_each_intent_to_top_k(self):
        """无 rerank 时每意图各取 top_k 后合并（不全局截断）。"""
        hits_a = [_hit(0.9, "A0"), _hit(0.8, "A1"), _hit(0.7, "A2")]
        hits_b = [_hit(0.6, "B0"), _hit(0.5, "B1"), _hit(0.4, "B2")]
        for i, h in enumerate(hits_a + hits_b):
            h["chunk_index"] = i

        with patch("core.retrieval.rewrite_query",
                   new=AsyncMock(return_value=["子A", "子B"])), \
             patch("core.retrieval.retrieve",
                   new=AsyncMock(side_effect=[hits_a, hits_b])) as mock_retrieve, \
             patch("core.retrieval.rerank", new=AsyncMock()) as mock_rerank:
            result = await retrieval.retrieve_and_rank(
                query="原词", kb_id=KB_ID, top_k=2,
                retrieval_methods=["dense"],
                query_rewrite_config={"model": "m"},
            )

        assert [c.kwargs["query"] for c in mock_retrieve.await_args_list] == ["子A", "子B"]
        mock_rerank.assert_not_awaited()
        # 每意图截到 2 条，共 4 条（A0/A1/B0/B1），A2/B2 被丢弃
        assert [h["text"] for h in result] == ["A0", "A1", "B0", "B1"]

    @pytest.mark.asyncio
    async def test_single_str_result_keeps_existing_single_flow(self):
        """改写返回 str（单意图）时行为与现状一致（回归）。"""
        hit = _hit(0.9, "文本")
        with patch("core.retrieval.rewrite_query", new=AsyncMock(return_value="改写词")), \
             patch("core.retrieval.retrieve", new=AsyncMock(return_value=[hit])) as mock_retrieve:
            result = await retrieval.retrieve_and_rank(
                query="原词", kb_id=KB_ID, top_k=5, retrieval_methods=["dense"],
                query_rewrite_config={"model": "m"},
            )

        mock_retrieve.assert_awaited_once()
        assert mock_retrieve.await_args.kwargs["query"] == "改写词"
        assert result == [hit]
