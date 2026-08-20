"""
ES 全文检索封装单元测试（mock AsyncElasticsearch，不连真实 ES）。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from elasticsearch import NotFoundError

from core import es_store
from tests.helpers import KB_ID, DOC_ID


def _make_client():
    client = MagicMock()
    client.search = AsyncMock()
    client.bulk = AsyncMock()
    client.indices = MagicMock()
    client.indices.exists = AsyncMock()
    client.indices.create = AsyncMock()
    client.delete_by_query = AsyncMock()
    client.delete = AsyncMock()
    return client


class TestDocId:
    def test_format(self):
        assert es_store._doc_id(DOC_ID, 3) == f"{DOC_ID}_3"


class TestEnsureIndex:
    @pytest.fixture(autouse=True)
    def _reset_analyzer_mode(self):
        """_analyzer_mode 是模块级状态，避免跨用例污染。"""
        es_store._analyzer_mode = None
        yield
        es_store._analyzer_mode = None

    @pytest.mark.asyncio
    async def test_create_when_missing(self):
        client = _make_client()
        client.indices.exists.return_value = False
        with patch("core.es_store.get_client", return_value=client):
            ok = await es_store.ensure_index()
        assert ok is True
        client.indices.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skip_when_exists(self):
        client = _make_client()
        client.indices.exists.return_value = True
        with patch("core.es_store.get_client", return_value=client):
            await es_store.ensure_index()
        client.indices.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failure_returns_false(self):
        client = _make_client()
        client.indices.exists.return_value = False
        client.indices.create.side_effect = Exception("boom")
        with patch("core.es_store.get_client", return_value=client):
            ok = await es_store.ensure_index()
        assert ok is False

    @pytest.mark.asyncio
    async def test_falls_back_to_cjk_without_ik(self):
        """IK 插件缺失（unknown analyzer）时自动降级为内置 cjk 分析器。"""
        client = _make_client()
        client.indices.exists.return_value = False
        client.indices.create.side_effect = [
            Exception("unknown analyzer [ik_max_word]"),  # ik 失败
            MagicMock(),  # cjk 成功
        ]
        with patch("core.es_store.get_client", return_value=client):
            ok = await es_store.ensure_index()
        assert ok is True
        assert client.indices.create.await_count == 2
        # 第二次创建使用 cjk 分析器（mapping 中不再引用 ik_*）
        cjk_call = client.indices.create.await_args.kwargs
        assert cjk_call["mappings"]["properties"]["text"]["analyzer"] == "cjk"
        assert es_store._analyzer_mode == "cjk"

    @pytest.mark.asyncio
    async def test_no_fallback_on_unrelated_error(self):
        """非分析器错误不应触发降级。"""
        client = _make_client()
        client.indices.exists.return_value = False
        client.indices.create.side_effect = Exception("connection refused")
        with patch("core.es_store.get_client", return_value=client):
            ok = await es_store.ensure_index()
        assert ok is False
        assert client.indices.create.await_count == 1


class TestBulkIndex:
    @pytest.mark.asyncio
    async def test_success(self):
        client = _make_client()
        client.bulk.return_value = {"errors": False, "items": []}
        with patch("core.es_store.get_client", return_value=client):
            ok = await es_store.bulk_index(
                ["文本"], [{"doc_id": DOC_ID, "chunk_index": 0}], KB_ID
            )
        assert ok is True
        ops = client.bulk.await_args.kwargs["operations"]
        assert ops[1]["doc_id"] == DOC_ID
        assert ops[1]["kb_id"] == KB_ID
        assert ops[0]["index"]["_id"] == f"{DOC_ID}_0"

    @pytest.mark.asyncio
    async def test_errors_returns_false(self):
        client = _make_client()
        client.bulk.return_value = {
            "errors": True,
            "items": [{"index": {"status": 500, "_id": f"{DOC_ID}_0"}}],
        }
        with patch("core.es_store.get_client", return_value=client):
            ok = await es_store.bulk_index(
                ["文本"], [{"doc_id": DOC_ID, "chunk_index": 0}], KB_ID
            )
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        client = _make_client()
        client.bulk.side_effect = Exception("conn refused")
        with patch("core.es_store.get_client", return_value=client):
            ok = await es_store.bulk_index(
                ["文本"], [{"doc_id": DOC_ID, "chunk_index": 0}], KB_ID
            )
        assert ok is False


class TestEsSearch:
    def _resp(self):
        return {
            "hits": {
                "hits": [
                    {
                        "_score": 12.3,
                        "_source": {
                            "doc_id": DOC_ID,
                            "kb_id": KB_ID,
                            "file_name": "a.pdf",
                            "page_number": 3,
                            "chunk_index": 2,
                            "chunk_type": "text",
                            "source_chunk_index": -1,
                            "heading": "",
                            "heading_path": "",
                            "text": "建筑工程施工总承包一级",
                        },
                        "highlight": {"text": ["建筑工程<em>施工总承包</em>一级"]},
                    }
                ]
            }
        }

    @pytest.mark.asyncio
    async def test_returns_hits_with_highlight_and_kb(self):
        client = _make_client()
        client.search.return_value = self._resp()
        with patch("core.es_store.get_client", return_value=client):
            hits = await es_store.es_search("施工总承包", [KB_ID], top_k=5, highlight=True)
        assert len(hits) == 1
        h = hits[0]
        assert h["kb_id"] == KB_ID
        assert h["highlight"] == "建筑工程<em>施工总承包</em>一级"
        assert h["chunk_index"] == 2
        assert h["score"] == 12.3

    @pytest.mark.asyncio
    async def test_empty_without_kb(self):
        hits = await es_store.es_search("x", [], top_k=5)
        assert hits == []

    @pytest.mark.asyncio
    async def test_failure_returns_empty(self):
        client = _make_client()
        client.search.side_effect = Exception("es down")
        with patch("core.es_store.get_client", return_value=client):
            hits = await es_store.es_search("x", [KB_ID])
        assert hits == []

    @pytest.mark.asyncio
    async def test_highlight_disabled_no_highlight_block(self):
        client = _make_client()
        client.search.return_value = {"hits": {"hits": []}}
        with patch("core.es_store.get_client", return_value=client), \
             patch.object(es_store.settings, "es_highlight_enabled", False):
            await es_store.es_search("x", [KB_ID], top_k=5, highlight=True)
        assert "highlight" not in client.search.await_args.kwargs

    @pytest.mark.asyncio
    async def test_list_queries_build_should(self):
        """多个查询词（词库扩展）应构造 bool.should OR 语义。"""
        client = _make_client()
        client.search.return_value = {"hits": {"hits": []}}
        with patch("core.es_store.get_client", return_value=client):
            await es_store.es_search(["原词", "扩展词"], [KB_ID], top_k=5)
        q = client.search.await_args.kwargs["query"]
        assert q["bool"]["minimum_should_match"] == 1
        assert len(q["bool"]["should"]) == 2


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_by_chunk_not_found_ok(self):
        client = _make_client()
        client.delete.side_effect = NotFoundError("404", "not_found", "x")
        with patch("core.es_store.get_client", return_value=client):
            ok = await es_store.delete_by_chunk(DOC_ID, 0)
        assert ok is True

    @pytest.mark.asyncio
    async def test_delete_by_doc(self):
        client = _make_client()
        client.delete_by_query.return_value = {}
        with patch("core.es_store.get_client", return_value=client):
            ok = await es_store.delete_by_doc(DOC_ID)
        assert ok is True
        assert client.delete_by_query.await_args.kwargs["query"] == {
            "term": {"doc_id": DOC_ID}
        }

    @pytest.mark.asyncio
    async def test_delete_by_kb(self):
        client = _make_client()
        client.delete_by_query.return_value = {}
        with patch("core.es_store.get_client", return_value=client):
            ok = await es_store.delete_by_kb(KB_ID)
        assert ok is True
        assert client.delete_by_query.await_args.kwargs["query"] == {
            "term": {"kb_id": KB_ID}
        }


class TestStats:
    @pytest.mark.asyncio
    async def test_kb_stats(self):
        client = _make_client()
        client.search.return_value = {
            "aggregations": {
                "by_kb": {
                    "buckets": [
                        {"key": KB_ID, "doc_count": 10, "docs": {"value": 3}},
                        {"key": "kb2", "doc_count": 5, "docs": {"value": 2}},
                    ]
                }
            }
        }
        with patch("core.es_store.get_client", return_value=client):
            result = await es_store.kb_stats()
        assert result == {KB_ID: {"chunks": 10, "docs": 3}, "kb2": {"chunks": 5, "docs": 2}}

    @pytest.mark.asyncio
    async def test_kb_stats_failure_returns_empty(self):
        client = _make_client()
        client.search.side_effect = Exception("es down")
        with patch("core.es_store.get_client", return_value=client):
            assert await es_store.kb_stats() == {}

    @pytest.mark.asyncio
    async def test_count_by_doc(self):
        client = _make_client()
        client.search.return_value = {
            "aggregations": {
                "by_doc": {
                    "buckets": [
                        {"key": DOC_ID, "doc_count": 3},
                        {"key": "doc2", "doc_count": 5},
                    ]
                }
            }
        }
        with patch("core.es_store.get_client", return_value=client):
            result = await es_store.count_by_doc()
        assert result == {DOC_ID: 3, "doc2": 5}

    @pytest.mark.asyncio
    async def test_index_stats(self):
        client = _make_client()
        client.cat = MagicMock()
        client.cat.indices = AsyncMock(
            return_value=[
                {
                    "health": "green",
                    "docs.count": "1260",
                    "docs.deleted": "2",
                    "store.size": "12.5kb",
                    "segments.count": "3",
                }
            ]
        )
        with patch("core.es_store.get_client", return_value=client):
            result = await es_store.index_stats()
        assert result == {
            "health": "green",
            "docs_count": 1260,
            "deleted_docs": 2,
            "store_size": "12.5kb",
            "segments": 3,
        }

    @pytest.mark.asyncio
    async def test_index_stats_failure_returns_none(self):
        client = _make_client()
        client.cat.indices.side_effect = Exception("es down")
        with patch("core.es_store.get_client", return_value=client):
            assert await es_store.index_stats() is None
