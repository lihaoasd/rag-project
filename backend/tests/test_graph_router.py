"""
图谱 API 路由测试 — 未启用时 503 / 文档校验 / 异步入队 / 实体列表与编辑。
"""
from unittest.mock import AsyncMock, patch

import pytest

from services.graph_service import EntityConflictError
from tests.helpers import KB_ID, DOC_ID


class TestGraphDisabled:
    def test_stats_returns_503(self, client):
        # GRAPH_ENABLED=false（默认）时图谱接口统一 503
        resp = client.get(f"/api/knowledge-bases/{KB_ID}/graph/stats")
        assert resp.status_code == 503
        assert "图谱" in resp.json()["detail"]

    def test_graph_data_returns_503(self, client):
        resp = client.get(f"/api/knowledge-bases/{KB_ID}/graph")
        assert resp.status_code == 503

    def test_search_returns_503(self, client):
        resp = client.post(
            f"/api/knowledge-bases/{KB_ID}/graph/search",
            json={"query": "甲公司"},
        )
        assert resp.status_code == 503

    def test_entity_list_returns_503(self, client):
        resp = client.get(f"/api/knowledge-bases/{KB_ID}/graph/entities/list")
        assert resp.status_code == 503

    def test_entity_update_returns_503(self, client):
        resp = client.put(
            f"/api/knowledge-bases/{KB_ID}/graph/entities",
            json={"old_name": "甲公司", "old_type": "Organization",
                  "name": "甲公司", "type": "Organization", "description": ""},
        )
        assert resp.status_code == 503


class TestEntityList:
    def test_list_ok(self, client):
        with patch(
            "services.graph_service.list_entities",
            new_callable=AsyncMock,
            return_value={
                "total": 2, "page": 1, "page_size": 50,
                "items": [
                    {"name": "甲公司", "type": "Organization", "description": "建筑公司",
                     "relation_count": 5, "chunk_count": 3},
                ],
            },
        ) as mock_list:
            resp = client.get(f"/api/knowledge-bases/{KB_ID}/graph/entities/list?page=1&page_size=20&q=甲")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert body["items"][0]["relation_count"] == 5
        mock_list.assert_awaited_once_with(KB_ID, 1, 20, "甲")


class TestEntityUpdate:
    def test_update_ok(self, client):
        with patch(
            "services.graph_service.update_entity",
            new_callable=AsyncMock,
            return_value={"name": "甲建设集团", "type": "Project", "description": "新描述"},
        ) as mock_update:
            resp = client.put(
                f"/api/knowledge-bases/{KB_ID}/graph/entities",
                json={"old_name": "甲公司", "old_type": "Organization",
                      "name": "甲建设集团", "type": "Project", "description": "新描述"},
            )
        assert resp.status_code == 200
        assert resp.json()["name"] == "甲建设集团"
        mock_update.assert_awaited_once_with(
            KB_ID, old_name="甲公司", old_type="Organization",
            name="甲建设集团", type="Project", description="新描述",
        )

    def test_update_not_found_404(self, client):
        with patch("services.graph_service.update_entity", new_callable=AsyncMock, return_value=None):
            resp = client.put(
                f"/api/knowledge-bases/{KB_ID}/graph/entities",
                json={"old_name": "不存在", "old_type": "Organization",
                      "name": "甲公司", "type": "Organization", "description": ""},
            )
        assert resp.status_code == 404

    def test_update_conflict_409(self, client):
        with patch(
            "services.graph_service.update_entity",
            new_callable=AsyncMock,
            side_effect=EntityConflictError("实体已存在: Organization/乙公司"),
        ):
            resp = client.put(
                f"/api/knowledge-bases/{KB_ID}/graph/entities",
                json={"old_name": "甲公司", "old_type": "Organization",
                      "name": "乙公司", "type": "Organization", "description": ""},
            )
        assert resp.status_code == 409

    def test_update_illegal_type_400(self, client):
        with patch(
            "services.graph_service.update_entity",
            new_callable=AsyncMock,
            side_effect=ValueError("实体类型非法: Hacker"),
        ):
            resp = client.put(
                f"/api/knowledge-bases/{KB_ID}/graph/entities",
                json={"old_name": "甲公司", "old_type": "Organization",
                      "name": "甲公司", "type": "Hacker", "description": ""},
            )
        assert resp.status_code == 400


class TestBuildDocGraph:
    def test_doc_not_found_404(self, client):
        with patch("services.document_service.get_document", new_callable=AsyncMock, return_value=None):
            resp = client.post(f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/graph/build")
        assert resp.status_code == 404

    def test_unparsed_doc_400(self, client):
        with patch(
            "services.document_service.get_document",
            new_callable=AsyncMock,
            return_value={"id": DOC_ID, "status": "uploaded", "graph_status": "none"},
        ):
            resp = client.post(f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/graph/build")
        assert resp.status_code == 400

    def test_building_doc_400(self, client):
        with patch(
            "services.document_service.get_document",
            new_callable=AsyncMock,
            return_value={"id": DOC_ID, "status": "completed", "graph_status": "building"},
        ):
            resp = client.post(f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/graph/build")
        assert resp.status_code == 400

    def test_enqueue_success_202(self, client):
        with patch(
            "services.document_service.get_document",
            new_callable=AsyncMock,
            return_value={"id": DOC_ID, "status": "completed", "graph_status": "none"},
        ), patch(
            "services.graph_service.build_file_task",
            new_callable=AsyncMock,
            return_value=1,
        ) as mock_task:
            resp = client.post(f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/graph/build")
        assert resp.status_code == 202
        assert resp.json()["status"] == "queued"
        mock_task.assert_awaited_once_with(KB_ID, DOC_ID)

    def test_clear_doc_404(self, client):
        with patch("services.document_service.get_document", new_callable=AsyncMock, return_value=None):
            resp = client.delete(f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/graph")
        assert resp.status_code == 404

    def test_doc_status(self, client):
        with patch(
            "services.document_service.get_document",
            new_callable=AsyncMock,
            return_value={
                "id": DOC_ID, "status": "completed",
                "graph_status": "completed", "graph_error_message": None,
                "graph_built_at": None, "graph_duration_ms": 100,
            },
        ):
            resp = client.get(f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/graph/status")
        assert resp.status_code == 200
        assert resp.json()["graph_status"] == "completed"
