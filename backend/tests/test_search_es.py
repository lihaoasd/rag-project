"""
Search Service ES 路径测试 — fulltext / hybrid_es / 默认方式 / RRF 融合。
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
        """fulltext 走 ES 检索，查询词经词库扩展（OR 语义），返回高亮。"""
        lexicon_service._cache = {"甲公司": ["甲建设集团"]}
        try:
            with patch("core.es_store.es_search", new_callable=AsyncMock) as mock_es:
                mock_es.return_value = [_es_hit(0)]
                result = await search_service.search(
                    kb_ids=[KB_ID],
                    query="甲建设集团",
                    retrieval_method="fulltext",
                    highlight=True,
                )
            # 查询扩展：原词 + 别名命中的标准词
            assert mock_es.call_args.args[0] == ["甲建设集团", "甲公司"]
            assert mock_es.call_args.args[1] == [KB_ID]
            assert result["total"] == 1
            assert result["results"][0]["highlight"] == "<em>关键词</em>0"
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
                    retrieval_method="fulltext",
                    highlight=False,
                )
            assert mock_es.call_args.kwargs["highlight"] is False
        finally:
            lexicon_service._cache = None

    @pytest.mark.asyncio
    async def test_default_method_is_fulltext(self, mock_retrieve):
        """未传 retrieval_method 时默认走 fulltext（ES 路径）。"""
        lexicon_service._cache = {}
        try:
            with patch("core.es_store.es_search", new_callable=AsyncMock) as mock_es:
                mock_es.return_value = []
                assert settings.retrieval_default_method == "fulltext"
                await search_service.search(kb_ids=[KB_ID], query="测试")
            mock_es.assert_awaited_once()
        finally:
            lexicon_service._cache = None


class TestHybridEs:
    @pytest.mark.asyncio
    async def test_rrf_merge_vector_and_es(self, mock_retrieve):
        """hybrid_es = 向量 + ES 关键词 RRF 融合。"""
        lexicon_service._cache = {}
        try:
            mock_retrieve.return_value = [_vec_hit(0), _vec_hit(1)]
            es_hits = [_es_hit(1), _es_hit(2)]
            with patch("core.es_store.es_search", new_callable=AsyncMock) as mock_es:
                mock_es.return_value = es_hits
                result = await search_service.search(
                    kb_ids=[KB_ID], query="测试", retrieval_method="hybrid_es",
                )
            # chunk1：a 列表 rank1(1/62) + b 列表 rank0(1/61)，k=60（score 保留 4 位小数）
            expected = round(1.0 / 61 + 1.0 / 62, 4)
            assert result["results"][0]["chunk_index"] == 1
            assert abs(result["results"][0]["score"] - expected) < 1e-6
            assert len(result["results"]) == 3
        finally:
            lexicon_service._cache = None

    @pytest.mark.asyncio
    async def test_vector_side_uses_plain_dense(self, mock_retrieve):
        """hybrid_es 的向量路应显式用 retrieval_method=vector。"""
        lexicon_service._cache = {}
        try:
            mock_retrieve.return_value = []
            with patch("core.es_store.es_search", new_callable=AsyncMock) as mock_es:
                mock_es.return_value = []
                await search_service.search(
                    kb_ids=[KB_ID], query="测试", retrieval_method="hybrid_es",
                )
            assert mock_retrieve.call_args.kwargs["retrieval_method"] == "vector"
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
                kb_ids=[KB_ID], query="x", retrieval_method="graph"
            )
