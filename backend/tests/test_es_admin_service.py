"""
ES 对账服务单元测试（mock es_store 与 DocumentRepository / KnowledgeBaseRepository）。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import es_admin_service
from tests.helpers import KB_ID, DOC_ID


class TestReconcileStatus:
    @pytest.mark.asyncio
    async def test_reconcile_full(self):
        """全库三态 + 文档级差异 + 兼容字段 + 知识库名称。"""
        repo = MagicMock()
        repo.count_chunks_by_kb = AsyncMock(return_value={KB_ID: 10, "kb2": 5})
        repo.count_docs_by_kb = AsyncMock(return_value={KB_ID: 2, "kb2": 1})
        repo.count_chunks_by_doc = AsyncMock(return_value={DOC_ID: 3, "doc2": 5})
        repo.list_doc_meta = AsyncMock(
            return_value={
                DOC_ID: {"kb_id": KB_ID, "file_name": "a.pdf"},
                "doc2": {"kb_id": "kb2", "file_name": "b.pdf"},
            }
        )
        kb_repo = MagicMock()
        kb_repo.list_all = AsyncMock(
            return_value=[
                SimpleNamespace(id=KB_ID, name="医学知识库"),
                SimpleNamespace(id="kb2", name="招标知识库"),
            ]
        )

        with patch("services.es_admin_service.es_store.ping",
                   new_callable=AsyncMock, return_value=True), \
             patch("services.es_admin_service.es_store.kb_stats",
                   new_callable=AsyncMock,
                   return_value={KB_ID: {"chunks": 10, "docs": 2},
                                 "kb2": {"chunks": 4, "docs": 1}}), \
             patch("services.es_admin_service.es_store.count_by_doc",
                   new_callable=AsyncMock, return_value={DOC_ID: 3, "doc2": 4}), \
             patch("services.es_admin_service.es_store.index_stats",
                   new_callable=AsyncMock,
                   return_value={"health": "green", "docs_count": 14,
                                 "deleted_docs": 0, "store_size": "1kb", "segments": 1}), \
             patch("services.es_admin_service.DocumentRepository", return_value=repo), \
             patch("services.es_admin_service.KnowledgeBaseRepository", return_value=kb_repo):
            result = await es_admin_service.reconcile_status()

        assert result["healthy"] is True
        assert result["pg_total"] == 15
        assert result["es_total"] == 14
        assert result["index_stats"]["health"] == "green"
        assert result["kb_total"] == 2

        # 全库三态
        kbs = {k["kb_id"]: k for k in result["kbs"]}
        assert kbs[KB_ID]["status"] == "ok"
        assert kbs[KB_ID]["kb_name"] == "医学知识库"
        assert kbs[KB_ID]["pg_docs"] == 2
        assert kbs[KB_ID]["es_docs"] == 2
        assert kbs["kb2"]["status"] == "missing"  # pg5 > es4
        assert kbs["kb2"]["kb_name"] == "招标知识库"
        assert result["diff_count"] == 1
        assert result["differences"] == [{"kb_id": "kb2", "pg": 5, "es": 4}]

        # 文档级差异：doc2 pg5 es4
        assert result["doc_diff_count"] == 1
        assert result["doc_diffs"][0]["doc_id"] == "doc2"
        assert result["doc_diffs"][0]["file_name"] == "b.pdf"

    @pytest.mark.asyncio
    async def test_reconcile_pagination(self):
        """分页：kbs 按 page/page_size 切片，kb_total 为全量。"""
        repo = MagicMock()
        kb_ids = [f"kb{i}" for i in range(5)]
        repo.count_chunks_by_kb = AsyncMock(return_value={kid: 1 for kid in kb_ids})
        repo.count_docs_by_kb = AsyncMock(return_value={kid: 1 for kid in kb_ids})
        repo.count_chunks_by_doc = AsyncMock(return_value={})
        repo.list_doc_meta = AsyncMock(return_value={})
        kb_repo = MagicMock()
        kb_repo.list_all = AsyncMock(
            return_value=[SimpleNamespace(id=kid, name=f"知识库{i}") for i, kid in enumerate(kb_ids)]
        )

        with patch("services.es_admin_service.es_store.ping",
                   new_callable=AsyncMock, return_value=True), \
             patch("services.es_admin_service.es_store.kb_stats",
                   new_callable=AsyncMock,
                   return_value={kid: {"chunks": 1, "docs": 1} for kid in kb_ids}), \
             patch("services.es_admin_service.es_store.count_by_doc",
                   new_callable=AsyncMock, return_value={}), \
             patch("services.es_admin_service.es_store.index_stats",
                   new_callable=AsyncMock, return_value=None), \
             patch("services.es_admin_service.DocumentRepository", return_value=repo), \
             patch("services.es_admin_service.KnowledgeBaseRepository", return_value=kb_repo):
            result = await es_admin_service.reconcile_status(page=2, page_size=2)

        assert result["kb_total"] == 5
        assert [k["kb_id"] for k in result["kbs"]] == ["kb2", "kb3"]
        assert result["kbs"][0]["kb_name"] == "知识库2"

    @pytest.mark.asyncio
    async def test_reconcile_page_out_of_range(self):
        """页码超出范围时 kbs 为空，kb_total 不变。"""
        repo = MagicMock()
        repo.count_chunks_by_kb = AsyncMock(return_value={KB_ID: 1})
        repo.count_docs_by_kb = AsyncMock(return_value={KB_ID: 1})
        repo.count_chunks_by_doc = AsyncMock(return_value={})
        repo.list_doc_meta = AsyncMock(return_value={})
        kb_repo = MagicMock()
        kb_repo.list_all = AsyncMock(return_value=[SimpleNamespace(id=KB_ID, name="医学知识库")])

        with patch("services.es_admin_service.es_store.ping",
                   new_callable=AsyncMock, return_value=True), \
             patch("services.es_admin_service.es_store.kb_stats",
                   new_callable=AsyncMock,
                   return_value={KB_ID: {"chunks": 1, "docs": 1}}), \
             patch("services.es_admin_service.es_store.count_by_doc",
                   new_callable=AsyncMock, return_value={}), \
             patch("services.es_admin_service.es_store.index_stats",
                   new_callable=AsyncMock, return_value=None), \
             patch("services.es_admin_service.DocumentRepository", return_value=repo), \
             patch("services.es_admin_service.KnowledgeBaseRepository", return_value=kb_repo):
            result = await es_admin_service.reconcile_status(page=10, page_size=2)

        assert result["kb_total"] == 1
        assert result["kbs"] == []

    @pytest.mark.asyncio
    async def test_reconcile_residual_and_es_only_doc(self):
        """es>pg 记 residual；ES 独有文档（PG 已删）应出现在文档级差异。"""
        repo = MagicMock()
        repo.count_chunks_by_kb = AsyncMock(return_value={KB_ID: 5})
        repo.count_docs_by_kb = AsyncMock(return_value={KB_ID: 1})
        repo.count_chunks_by_doc = AsyncMock(return_value={DOC_ID: 5})
        repo.list_doc_meta = AsyncMock(
            return_value={DOC_ID: {"kb_id": KB_ID, "file_name": "a.pdf"}}
        )
        kb_repo = MagicMock()
        kb_repo.list_all = AsyncMock(return_value=[SimpleNamespace(id=KB_ID, name="医学知识库")])

        with patch("services.es_admin_service.es_store.ping",
                   new_callable=AsyncMock, return_value=True), \
             patch("services.es_admin_service.es_store.kb_stats",
                   new_callable=AsyncMock,
                   return_value={KB_ID: {"chunks": 7, "docs": 2}}), \
             patch("services.es_admin_service.es_store.count_by_doc",
                   new_callable=AsyncMock, return_value={DOC_ID: 5, "ghost_doc": 2}), \
             patch("services.es_admin_service.es_store.index_stats",
                   new_callable=AsyncMock, return_value=None), \
             patch("services.es_admin_service.DocumentRepository", return_value=repo), \
             patch("services.es_admin_service.KnowledgeBaseRepository", return_value=kb_repo):
            result = await es_admin_service.reconcile_status()

        assert result["kbs"][0]["status"] == "residual"  # pg5 < es7
        assert result["kbs"][0]["kb_name"] == "医学知识库"
        # ghost_doc 仅存在于 ES（PG 中已删除）→ 残留差异
        ghost = next(d for d in result["doc_diffs"] if d["doc_id"] == "ghost_doc")
        assert ghost["pg"] == 0 and ghost["es"] == 2

    @pytest.mark.asyncio
    async def test_reconcile_es_down(self):
        """ES 不可达时连接问题单独表达，不混入数据差异误报。"""
        repo = MagicMock()
        repo.count_chunks_by_kb = AsyncMock(return_value={KB_ID: 10})

        with patch("services.es_admin_service.es_store.ping",
                   new_callable=AsyncMock, return_value=False), \
             patch("services.es_admin_service.DocumentRepository", return_value=repo):
            result = await es_admin_service.reconcile_status()

        assert result["healthy"] is False
        assert result["es_total"] == 0
        assert result["kbs"] == []
        assert result["kb_total"] == 0
        assert result["doc_diffs"] == []
        assert result["diff_count"] == 0
        assert result["pg_total"] == 10
