"""
健康检查端点测试。
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with patch("core.parse_queue.start_worker", new_callable=AsyncMock), \
         patch("core.parse_queue.stop_worker", new_callable=AsyncMock):
        from main import app
        app.router.lifespan_context = None
        return TestClient(app)


class TestHealthCheck:
    def test_health_returns_ok(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["version"] == "0.1.0"

    def test_health_method_not_allowed_on_post(self, client):
        """GET 以外的方法应被拒绝。"""
        response = client.post("/api/health")
        assert response.status_code == 405

    def test_main_neo4j_uses_class_api(self):
        """回归：main.py 生命周期曾以模块级 neo4j_store.ensure_indexes/close 调用
        （实为 Neo4jStore 类 classmethod），GRAPH_ENABLED=true 启动/关闭时报
        AttributeError: module 'core.neo4j_store' has no attribute '...'。"""
        import asyncio

        import main
        from core.neo4j_store import Neo4jStore

        assert main.Neo4jStore is Neo4jStore
        assert asyncio.iscoroutinefunction(Neo4jStore.ensure_indexes)
        assert asyncio.iscoroutinefunction(Neo4jStore.close)
