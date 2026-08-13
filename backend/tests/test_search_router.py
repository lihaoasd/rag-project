"""
文档检索 API 端点测试。
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.helpers import KB_ID, DOC_ID


@pytest.fixture
def client():
    with patch("core.parse_queue.start_worker", new_callable=AsyncMock), \
         patch("core.parse_queue.stop_worker", new_callable=AsyncMock):
        from main import app
        app.router.lifespan_context = None
        return TestClient(app)


class TestSearch:
    def test_search_success(self, client):
        with patch("routers.search.search_service.search",
                   new_callable=AsyncMock) as mock_search:
            mock_search.return_value = {
                "query": "测试查询",
                "total": 2,
                "results": [
                    {
                        "rank": 1,
                        "score": 0.95,
                        "text": "第一条结果",
                        "doc_id": DOC_ID,
                        "file_name": "test.pdf",
                        "page_number": 1,
                        "chunk_index": 0,
                    },
                    {
                        "rank": 2,
                        "score": 0.85,
                        "text": "第二条结果",
                        "doc_id": DOC_ID,
                        "file_name": "test.pdf",
                        "page_number": 2,
                        "chunk_index": 1,
                    },
                ],
            }
            response = client.post(
                f"/api/knowledge-bases/{KB_ID}/search",
                json={"query": "测试查询"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 2
            assert len(data["results"]) == 2

    def test_search_empty_results(self, client):
        with patch("routers.search.search_service.search",
                   new_callable=AsyncMock) as mock_search:
            mock_search.return_value = {
                "query": "无结果查询",
                "total": 0,
                "results": [],
            }
            response = client.post(
                f"/api/knowledge-bases/{KB_ID}/search",
                json={"query": "无结果查询"},
            )
            assert response.status_code == 200
            assert response.json()["total"] == 0

    def test_search_missing_query(self, client):
        response = client.post(
            f"/api/knowledge-bases/{KB_ID}/search",
            json={},
        )
        assert response.status_code == 422

    def test_search_service_error(self, client):
        with patch("routers.search.search_service.search",
                   new_callable=AsyncMock) as mock_search:
            mock_search.side_effect = Exception("Milvus 服务不可用")
            response = client.post(
                f"/api/knowledge-bases/{KB_ID}/search",
                json={"query": "测试"},
            )
            assert response.status_code == 500
