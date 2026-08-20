"""
ES 管理 API（对账/重建）端点测试。
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.helpers import KB_ID


@pytest.fixture
def client():
    with patch("core.parse_queue.start_worker", new_callable=AsyncMock), \
         patch("core.parse_queue.stop_worker", new_callable=AsyncMock):
        from main import app
        app.router.lifespan_context = None
        return TestClient(app)


class TestEsAdmin:
    def test_status(self, client):
        with patch("routers.es_admin.es_admin_service.reconcile_status",
                   new_callable=AsyncMock) as m:
            m.return_value = {
                "healthy": True,
                "index": "bid_chunks",
                "pg_total": 10,
                "es_total": 8,
                "diff_count": 1,
                "differences": [{"kb_id": KB_ID, "pg": 10, "es": 8}],
                "kbs": [],
                "kb_total": 1,
            }
            resp = client.get("/api/es/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["diff_count"] == 1
        assert data["differences"][0]["kb_id"] == KB_ID
        assert m.await_args.kwargs == {"page": 1, "page_size": 20}  # 默认分页参数

    def test_status_with_pagination(self, client):
        with patch("routers.es_admin.es_admin_service.reconcile_status",
                   new_callable=AsyncMock) as m:
            m.return_value = {"healthy": True, "kbs": [], "kb_total": 30}
            resp = client.get("/api/es/status?page=2&page_size=5")
        assert resp.status_code == 200
        assert m.await_args.kwargs == {"page": 2, "page_size": 5}

    def test_rebuild_all(self, client):
        with patch("routers.es_admin.es_admin_service.rebuild_es",
                   new_callable=AsyncMock) as m:
            m.return_value = {"kb_id": "all", "rebuilt_chunks": 10, "skipped_kbs": []}
            resp = client.post("/api/es/rebuild")
        assert resp.status_code == 200
        assert resp.json()["rebuilt_chunks"] == 10
        assert m.await_args.args == (None,)

    def test_rebuild_specific_kb(self, client):
        with patch("routers.es_admin.es_admin_service.rebuild_es",
                   new_callable=AsyncMock) as m:
            m.return_value = {"kb_id": KB_ID, "rebuilt_chunks": 5, "skipped_kbs": []}
            resp = client.post(f"/api/es/rebuild?kb_id={KB_ID}")
        assert resp.status_code == 200
        assert m.await_args.args == (KB_ID,)
