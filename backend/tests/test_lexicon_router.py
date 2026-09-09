"""
词库管理 API 端点测试（service 层 mock）。
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

TERM_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def client():
    with patch("core.parse_queue.start_worker", new_callable=AsyncMock), \
         patch("core.parse_queue.stop_worker", new_callable=AsyncMock):
        from main import app
        app.router.lifespan_context = None
        return TestClient(app)


def _term_dict(term="甲公司", enabled=True):
    return {
        "id": TERM_ID,
        "term": term,
        "aliases": ["甲建设集团"],
        "enabled": enabled,
        "created_at": "",
        "updated_at": "",
    }


def _mapping_dict(pid="p1", code="Organization"):
    return {
        "domain_profile_id": pid,
        "domain_name": "通用",
        "entity_code": code,
        "entity_name": "组织",
    }


class TestTermsCRUD:
    def test_create_term(self, client):
        with patch("routers.lexicon.lexicon_service.create_term",
                   new_callable=AsyncMock) as m:
            m.return_value = _term_dict()
            resp = client.post(
                "/api/lexicon/terms",
                json={"term": "甲公司", "aliases": ["甲建设集团"]},
            )
        assert resp.status_code == 200
        assert m.await_args.args[0] == "甲公司"
        assert resp.json()["term"] == "甲公司"

    def test_create_duplicate_returns_409(self, client):
        with patch("routers.lexicon.lexicon_service.create_term",
                   new_callable=AsyncMock,
                   side_effect=ValueError("词条已存在: 甲公司")):
            resp = client.post("/api/lexicon/terms", json={"term": "甲公司"})
        assert resp.status_code == 409
        assert "已存在" in resp.json()["detail"]

    def test_list_terms(self, client):
        with patch("routers.lexicon.lexicon_service.list_terms",
                   new_callable=AsyncMock) as m:
            m.return_value = {"items": [_term_dict()], "total": 1}
            resp = client.get("/api/lexicon/terms?search=甲")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert m.await_args.args == (1, 20, "甲")

    def test_update_term(self, client):
        with patch("routers.lexicon.lexicon_service.update_term",
                   new_callable=AsyncMock) as m:
            m.return_value = _term_dict(enabled=False)
            resp = client.put(f"/api/lexicon/terms/{TERM_ID}", json={"enabled": False})
        assert resp.status_code == 200
        # 仅传已变更字段（exclude_unset）
        assert m.await_args.args[1] == {"enabled": False}

    def test_delete_term(self, client):
        with patch("routers.lexicon.lexicon_service.delete_term",
                   new_callable=AsyncMock):
            resp = client.delete(f"/api/lexicon/terms/{TERM_ID}")
        assert resp.status_code == 204


class TestDomainMappings:
    def test_get_mappings(self, client):
        with patch("routers.lexicon.lexicon_service.list_term_mappings",
                   new_callable=AsyncMock) as m:
            m.return_value = [_mapping_dict()]
            resp = client.get(f"/api/lexicon/terms/{TERM_ID}/domain-mappings")
        assert resp.status_code == 200
        assert resp.json()["items"][0]["entity_code"] == "Organization"

    def test_put_mappings_full_replace(self, client):
        with patch("routers.lexicon.lexicon_service.replace_term_mappings",
                   new_callable=AsyncMock) as m:
            m.return_value = [_mapping_dict()]
            resp = client.put(
                f"/api/lexicon/terms/{TERM_ID}/domain-mappings",
                json={"mappings": [{"domain_profile_id": "p1", "entity_code": "Organization"}]},
            )
        assert resp.status_code == 200
        assert resp.json()["items"][0]["domain_profile_id"] == "p1"
        payload = m.await_args.args[1]
        assert payload[0]["domain_profile_id"] == "p1"
        assert payload[0]["entity_code"] == "Organization"

    def test_put_mappings_invalid_code_400(self, client):
        with patch("routers.lexicon.lexicon_service.replace_term_mappings",
                   new_callable=AsyncMock,
                   side_effect=ValueError("实体类型 X 不在领域「通用」中")):
            resp = client.put(
                f"/api/lexicon/terms/{TERM_ID}/domain-mappings",
                json={"mappings": [{"domain_profile_id": "p1", "entity_code": "X"}]},
            )
        assert resp.status_code == 400
        assert "不在领域" in resp.json()["detail"]

    def test_put_mappings_term_missing_404(self, client):
        with patch("routers.lexicon.lexicon_service.replace_term_mappings",
                   new_callable=AsyncMock,
                   side_effect=ValueError("词条不存在")):
            resp = client.put(
                f"/api/lexicon/terms/{TERM_ID}/domain-mappings",
                json={"mappings": []},
            )
        assert resp.status_code == 404


class TestImportExport:
    def test_import_csv(self, client):
        with patch("routers.lexicon.lexicon_service.import_csv",
                   new_callable=AsyncMock) as m:
            m.return_value = {"imported": 1, "skipped": 0, "errors": []}
            resp = client.post("/api/lexicon/import", json={"content": "甲公司,,公司"})
        assert resp.status_code == 200
        assert resp.json()["imported"] == 1

    def test_export_ik_dict(self, client):
        with patch("routers.lexicon.lexicon_service.export_ik_dict",
                   new_callable=AsyncMock) as m:
            m.return_value = "建筑工程施工总承包一级\n甲公司"
            resp = client.get("/api/lexicon/export/ik-dict")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert resp.text == "建筑工程施工总承包一级\n甲公司"

    def test_export_csv(self, client):
        with patch("routers.lexicon.lexicon_service.export_csv",
                   new_callable=AsyncMock) as m:
            m.return_value = "词条,别名\n甲公司,甲建设集团\n"
            resp = client.get("/api/lexicon/export/csv")
        assert resp.status_code == 200
        assert resp.text.startswith("词条,别名")

    def test_stats(self, client):
        with patch("routers.lexicon.lexicon_service.get_stats",
                   new_callable=AsyncMock) as m:
            m.return_value = {"total": 2, "enabled": 1}
            resp = client.get("/api/lexicon/stats")
        assert resp.status_code == 200
        assert resp.json()["total"] == 2
