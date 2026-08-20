"""
Search Service ES 路径测试 — fulltext / 多选 RRF 融合 / 默认策略。
"""
from unittest.mock import AsyncMock, patch

import pytest

from config import settings
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
        merged = search_service._rrf_merge([a, b], k=60)
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
