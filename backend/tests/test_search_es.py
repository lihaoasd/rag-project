"""
Search Service ES 路径测试 — fulltext / 多选 RRF 融合 / 默认策略 / Langfuse 追踪。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import settings
from core.retrieval import rrf_merge
from services import lexicon_service, search_service
from tests.helpers import KB_ID, DOC_ID


def _vec_hit(chunk_index: int = 0, doc_id: str = DOC_ID) -> dict:
    return {
        "score": 0.9,
        "text": f"向量文本{chunk_index}",
        "doc_id": doc_id,
        "file_name": "t.pdf",
        "page_number": 1,
        "chunk_index": chunk_index,
        "chunk_type": "text",
        "source_chunk_index": -1,
    }


def _es_hit(chunk_index: int = 0, doc_id: str = DOC_ID, kb_id: str = KB_ID) -> dict:
    return {
        "score": 5.0,
        "text": f"ES文本{chunk_index}",
        "doc_id": doc_id,
        "file_name": "t.pdf",
        "page_number": 1,
        "chunk_index": chunk_index,
        "chunk_type": "text",
        "source_chunk_index": -1,
        "kb_id": kb_id,
        "highlight": f"<em>关键词</em>{chunk_index}",
    }


class TestFulltext:
    @pytest.mark.asyncio
    async def test_fulltext_calls_es_search_with_expansion(self, mock_retrieve):
        """fulltext 单选走 ES 检索，查询词经词库扩展（OR 语义），返回高亮且不做 RRF。"""
        lexicon_service._cache = {"甲公司": ["甲建设集团"]}
        try:
            with patch("core.es_store.es_search", new_callable=AsyncMock) as mock_es:
                mock_es.return_value = [_es_hit(0)]
                result = await search_service.search(
                    kb_ids=[KB_ID],
                    query="甲建设集团",
                    retrieval_methods=["fulltext"],
                    highlight=True,
                )
            # 查询扩展：原词 + 别名命中的标准词
            assert mock_es.call_args.args[0] == ["甲建设集团", "甲公司"]
            assert mock_es.call_args.args[1] == [KB_ID]
            assert result["total"] == 1
            assert result["results"][0]["highlight"] == "<em>关键词</em>0"
            assert result["results"][0]["score"] == 5.0  # 单选直返，保留 ES 原始分数
        finally:
            lexicon_service._cache = None

    @pytest.mark.asyncio
    async def test_fulltext_passes_highlight_flag(self, mock_retrieve):
        lexicon_service._cache = {}
        try:
            with patch("core.es_store.es_search", new_callable=AsyncMock) as mock_es:
                mock_es.return_value = []
                await search_service.search(
                    kb_ids=[KB_ID],
                    query="测试",
                    retrieval_methods=["fulltext"],
                    highlight=False,
                )
            assert mock_es.call_args.kwargs["highlight"] is False
        finally:
            lexicon_service._cache = None

    @pytest.mark.asyncio
    async def test_default_methods_is_dense(self, mock_retrieve):
        """未传 retrieval_methods 时默认仅稠密向量（dense），走 Milvus 不调 ES。"""
        try:
            with patch("core.es_store.es_search", new_callable=AsyncMock) as mock_es:
                assert settings.retrieval_default_methods == ["dense"]
                await search_service.search(kb_ids=[KB_ID], query="测试")
            mock_es.assert_not_awaited()
        finally:
            lexicon_service._cache = None


class TestDenseFulltextMerge:
    @pytest.mark.asyncio
    async def test_rrf_merge_dense_and_es(self, mock_retrieve):
        """多选 dense+fulltext = 向量 + ES 关键词 RRF 融合（等价旧 hybrid_es）。"""
        lexicon_service._cache = {}
        try:
            mock_retrieve.return_value = [_vec_hit(0), _vec_hit(1)]
            es_hits = [_es_hit(1), _es_hit(2)]
            with patch("core.es_store.es_search", new_callable=AsyncMock) as mock_es:
                mock_es.return_value = es_hits
                result = await search_service.search(
                    kb_ids=[KB_ID], query="测试",
                    retrieval_methods=["dense", "fulltext"],
                )
            # chunk1：a 列表 rank1(1/62) + b 列表 rank0(1/61)，k=60（score 保留 4 位小数）
            expected = round(1.0 / 61 + 1.0 / 62, 4)
            assert result["results"][0]["chunk_index"] == 1
            assert abs(result["results"][0]["score"] - expected) < 1e-6
            assert len(result["results"]) == 3
        finally:
            lexicon_service._cache = None

    @pytest.mark.asyncio
    async def test_dense_side_uses_plain_dense(self, mock_retrieve):
        """dense+fulltext 的向量路应显式用 retrieval_method=dense。"""
        lexicon_service._cache = {}
        try:
            mock_retrieve.return_value = []
            with patch("core.es_store.es_search", new_callable=AsyncMock) as mock_es:
                mock_es.return_value = []
                await search_service.search(
                    kb_ids=[KB_ID], query="测试",
                    retrieval_methods=["dense", "fulltext"],
                )
            assert mock_retrieve.call_args.kwargs["retrieval_method"] == "dense"
        finally:
            lexicon_service._cache = None


class TestSparseFulltextMerge:
    @pytest.mark.asyncio
    async def test_rrf_merge_sparse_and_es(self, mock_retrieve):
        """多选 sparse+fulltext = Milvus BM25 稀疏 + ES 关键词 RRF 融合。"""
        lexicon_service._cache = {}
        try:
            sparse_hits = [_vec_hit(0), _vec_hit(1)]
            es_hits = [_es_hit(1), _es_hit(2)]
            mock_retrieve.return_value = sparse_hits
            with patch("core.es_store.es_search", new_callable=AsyncMock) as mock_es:
                mock_es.return_value = es_hits
                result = await search_service.search(
                    kb_ids=[KB_ID], query="测试",
                    retrieval_methods=["sparse", "fulltext"],
                )
            assert mock_retrieve.call_args.kwargs["retrieval_method"] == "sparse"
            assert "similarity_threshold" not in mock_retrieve.call_args.kwargs
            expected = round(1.0 / 61 + 1.0 / 62, 4)
            assert result["results"][0]["chunk_index"] == 1
            assert abs(result["results"][0]["score"] - expected) < 1e-6
            assert len(result["results"]) == 3
        finally:
            lexicon_service._cache = None


class TestRrfMerge:
    def test_merge_scores_and_order(self):
        a = [{"doc_id": "d", "chunk_index": 0}, {"doc_id": "d", "chunk_index": 1}]
        b = [{"doc_id": "d", "chunk_index": 1}, {"doc_id": "d", "chunk_index": 2}]
        merged = rrf_merge([a, b], k=60)
        assert merged[0]["chunk_index"] == 1
        # chunk1：a 列表 rank1(1/62) + b 列表 rank0(1/61)，保留 4 位小数
        assert abs(merged[0]["score"] - round(1.0 / 61 + 1.0 / 62, 4)) < 1e-6
        assert {m["chunk_index"] for m in merged} == {0, 1, 2}
        # 补默认字段
        assert merged[0]["highlight"] is None
        assert merged[0]["kb_id"] == ""


class TestValidation:
    @pytest.mark.asyncio
    async def test_invalid_method_raises(self, mock_retrieve):
        with pytest.raises(ValueError):
            await search_service.search(
                kb_ids=[KB_ID], query="x", retrieval_methods=["graph"]
            )


class TestLangfuseTracing:
    """三路召回与 search 总 span 的 Langfuse 追踪断言。"""

    def _collect_span_metadata(self, mock_lf):
        """从 update_current_span 调用中收集 metadata（跳过空 metadata 调用）。"""
        metas = []
        for call in mock_lf.update_current_span.call_args_list:
            md = call.kwargs.get("metadata") or {}
            metas.append(md)
        return metas

    @pytest.mark.asyncio
    async def test_search_records_per_method_hits(self, mock_retrieve):
        """search 总 span 应汇总每路召回命中数（per_method_hits）。"""
        mock_lf = MagicMock()
        lexicon_service._cache = {}
        try:
            mock_retrieve.return_value = [_vec_hit(0), _vec_hit(1)]
            with patch("core.es_store.es_search", new_callable=AsyncMock) as mock_es, \
                 patch("services.search_service.get_client", return_value=mock_lf):
                mock_es.return_value = [_es_hit(2)]
                await search_service.search(
                    kb_ids=[KB_ID], query="测试",
                    retrieval_methods=["dense", "sparse", "fulltext"],
                )
        finally:
            lexicon_service._cache = None

        metas = self._collect_span_metadata(mock_lf)
        assert {"dense": 2, "sparse": 2, "fulltext": 1} in [
            m.get("per_method_hits") for m in metas
        ]

    @pytest.mark.asyncio
    async def test_recall_spans_record_method_and_hit_count(self, mock_retrieve):
        """每路召回 span 应记录 method / hit_count / elapsed_ms。"""
        mock_lf = MagicMock()
        lexicon_service._cache = {}
        try:
            mock_retrieve.return_value = [_vec_hit(0)]
            with patch("core.es_store.es_search", new_callable=AsyncMock) as mock_es, \
                 patch("services.search_service.get_client", return_value=mock_lf):
                mock_es.return_value = [_es_hit(1)]
                await search_service.search(
                    kb_ids=[KB_ID], query="测试",
                    retrieval_methods=["dense", "fulltext"],
                )
        finally:
            lexicon_service._cache = None

        metas = self._collect_span_metadata(mock_lf)
        dense_md = next(m for m in metas if m.get("method") == "dense")
        assert dense_md["hit_count"] == 1
        assert dense_md["elapsed_ms"] >= 0
        fulltext_md = next(m for m in metas if m.get("method") == "fulltext")
        assert fulltext_md["hit_count"] == 1
        assert fulltext_md["elapsed_ms"] >= 0

    @pytest.mark.asyncio
    async def test_rrf_merge_records_span(self, mock_retrieve):
        """多选召回时 RRF 融合应有独立 span（method=rrf + k + 各路命中数 + 融合输出）。"""
        mock_lf = MagicMock()
        lexicon_service._cache = {}
        try:
            mock_retrieve.return_value = [_vec_hit(0), _vec_hit(1)]
            with patch("core.es_store.es_search", new_callable=AsyncMock) as mock_es, \
                 patch("services.search_service.get_client", return_value=mock_lf):
                mock_es.return_value = [_es_hit(2)]
                await search_service.search(
                    kb_ids=[KB_ID], query="测试",
                    retrieval_methods=["dense", "sparse", "fulltext"],
                )
        finally:
            lexicon_service._cache = None

        calls = mock_lf.update_current_span.call_args_list
        # metadata：method=rrf + k
        metas = self._collect_span_metadata(mock_lf)
        rrf_md = next(m for m in metas if m.get("method") == "rrf")
        assert rrf_md["k"] == 60
        # input：各路召回命中数
        inputs = [c.kwargs.get("input") for c in calls]
        assert {"dense": 2, "sparse": 2, "fulltext": 1} in [
            i.get("per_method_hits") for i in inputs if isinstance(i, dict)
        ]
        # output：融合结果明细
        outputs = [c.kwargs.get("output") for c in calls]
        assert any(
            isinstance(o, dict) and o.get("hit_count") == 3 for o in outputs
        )

    @pytest.mark.asyncio
    async def test_single_method_skips_rrf_span(self, mock_retrieve):
        """单选召回不走 RRF，不应出现 method=rrf 的 span。"""
        mock_lf = MagicMock()
        lexicon_service._cache = {}
        try:
            mock_retrieve.return_value = [_vec_hit(0)]
            with patch("services.search_service.get_client", return_value=mock_lf):
                await search_service.search(
                    kb_ids=[KB_ID], query="测试",
                    retrieval_methods=["dense"],
                )
        finally:
            lexicon_service._cache = None

        metas = self._collect_span_metadata(mock_lf)
        assert all(m.get("method") != "rrf" for m in metas)
