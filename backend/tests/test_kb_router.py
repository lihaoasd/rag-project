"""
知识库管理 API 端点测试。
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.helpers import KB_ID


# 测试用的知识库响应数据
def make_kb_dict(name="测试知识库", description="描述", doc_count=0):
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "description": description,
        "document_count": doc_count,
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00",
    }


@pytest.fixture
def client():
    """创建 TestClient，mock 掉 lifespan。"""
    with patch("core.parse_queue.start_worker", new_callable=AsyncMock), \
         patch("core.parse_queue.stop_worker", new_callable=AsyncMock):
        from main import app
        # 清除 lifespan 避免启动/停止 worker
        app.router.lifespan_context = None
        return TestClient(app)


# ============================================================
# POST /api/knowledge-bases — 创建知识库
# ============================================================
class TestCreateKnowledgeBase:
    def test_create_success(self, client):
        with patch("routers.knowledge_base.kb_service.create_knowledge_base",
                   new_callable=AsyncMock) as mock_create:
            mock_create.return_value = make_kb_dict()
            response = client.post("/api/knowledge-bases", json={"name": "新知识库"})
            assert response.status_code == 201
            data = response.json()
            assert data["name"] == "测试知识库"

    def test_create_with_description(self, client):
        with patch("routers.knowledge_base.kb_service.create_knowledge_base",
                   new_callable=AsyncMock) as mock_create:
            mock_create.return_value = make_kb_dict(name="带描述", description="我的描述")
            response = client.post("/api/knowledge-bases", json={
                "name": "带描述",
                "description": "我的描述",
            })
            assert response.status_code == 201
            data = response.json()
            assert data["description"] == "我的描述"

    def test_create_missing_name(self, client):
        """缺少必填字段 name，FastAPI 自动返回 422。"""
        response = client.post("/api/knowledge-bases", json={})
        assert response.status_code == 422

    def test_create_service_error(self, client):
        with patch("routers.knowledge_base.kb_service.create_knowledge_base",
                   new_callable=AsyncMock) as mock_create:
            mock_create.side_effect = Exception("数据库连接失败")
            response = client.post("/api/knowledge-bases", json={"name": "测试"})
            assert response.status_code == 500


# ============================================================
# GET /api/knowledge-bases — 列表
# ============================================================
class TestListKnowledgeBases:
    def test_list_empty(self, client):
        with patch("routers.knowledge_base.kb_service.list_knowledge_bases",
                   new_callable=AsyncMock) as mock_list:
            mock_list.return_value = []
            response = client.get("/api/knowledge-bases")
            assert response.status_code == 200
            assert response.json() == []

    def test_list_with_items(self, client):
        with patch("routers.knowledge_base.kb_service.list_knowledge_bases",
                   new_callable=AsyncMock) as mock_list:
            mock_list.return_value = [make_kb_dict(), make_kb_dict(name="KB2")]
            response = client.get("/api/knowledge-bases")
            assert response.status_code == 200
            assert len(response.json()) == 2


# ============================================================
# GET /api/knowledge-bases/{kb_id} — 详情
# ============================================================
class TestGetKnowledgeBase:
    def test_get_exists(self, client):
        with patch("routers.knowledge_base.kb_service.get_knowledge_base",
                   new_callable=AsyncMock) as mock_get:
            mock_get.return_value = make_kb_dict()
            response = client.get(f"/api/knowledge-bases/{KB_ID}")
            assert response.status_code == 200
            assert response.json()["name"] == "测试知识库"

    def test_get_not_found(self, client):
        with patch("routers.knowledge_base.kb_service.get_knowledge_base",
                   new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            response = client.get(f"/api/knowledge-bases/{uuid.uuid4()}")
            assert response.status_code == 404


# ============================================================
# POST /api/knowledge-bases/{kb_id}/delete — 删除
# ============================================================
class TestDeleteKnowledgeBase:
    def test_delete_success(self, client):
        with patch("routers.knowledge_base.kb_service.delete_knowledge_base",
                   new_callable=AsyncMock) as mock_delete:
            mock_delete.return_value = True
            response = client.post(f"/api/knowledge-bases/{KB_ID}/delete")
            assert response.status_code == 204

    def test_delete_not_found(self, client):
        with patch("routers.knowledge_base.kb_service.delete_knowledge_base",
                   new_callable=AsyncMock) as mock_delete:
            mock_delete.return_value = False
            response = client.post(f"/api/knowledge-bases/{uuid.uuid4()}/delete")
            assert response.status_code == 404
