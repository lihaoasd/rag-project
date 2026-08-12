"""
切片管理 API 端点测试。
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.helpers import KB_ID, DOC_ID, CHUNK_ID


@pytest.fixture
def client():
    with patch("core.parse_queue.start_worker", new_callable=AsyncMock), \
         patch("core.parse_queue.stop_worker", new_callable=AsyncMock):
        from main import app
        app.router.lifespan_context = None
        return TestClient(app)


def make_chunk_list():
    return {
        "items": [
            {
                "id": str(uuid.uuid4()),
                "chunk_index": 0,
                "page_number": 1,
                "char_count": 512,
                "text_preview": "第一段文本预览...",
                "created_at": "2024-01-01T00:00:00",
            },
            {
                "id": str(uuid.uuid4()),
                "chunk_index": 1,
                "page_number": 1,
                "char_count": 480,
                "text_preview": "第二段文本预览...",
                "created_at": "2024-01-01T00:00:00",
            },
        ],
        "total": 2,
        "page": 1,
        "page_size": 20,
    }


def make_chunk_detail():
    return {
        "id": CHUNK_ID,
        "doc_id": DOC_ID,
        "chunk_index": 0,
        "page_number": 1,
        "char_count": 512,
        "text_content": "完整切片文本内容",
        "milvus_pk": "pk_12345",
        "created_at": "2024-01-01T00:00:00",
    }


# ============================================================
# GET /chunks — 切片列表
# ============================================================
class TestListChunks:
    def test_list_chunks(self, client):
        with patch("routers.chunk.document_service.list_chunks",
                   new_callable=AsyncMock) as mock_list:
            mock_list.return_value = make_chunk_list()
            response = client.get(
                f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/chunks"
            )
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 2
            assert len(data["items"]) == 2

    def test_list_chunks_with_pagination(self, client):
        with patch("routers.chunk.document_service.list_chunks",
                   new_callable=AsyncMock) as mock_list:
            mock_list.return_value = make_chunk_list()
            response = client.get(
                f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/chunks"
                "?page=2&page_size=10"
            )
            assert response.status_code == 200

    def test_list_chunks_invalid_page(self, client):
        """page 参数必须 >= 1。"""
        response = client.get(
            f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/chunks"
            "?page=0"
        )
        assert response.status_code == 422


# ============================================================
# GET /chunks/{chunk_id} — 切片详情
# ============================================================
class TestGetChunk:
    def test_get_chunk_exists(self, client):
        with patch("routers.chunk.document_service.get_chunk",
                   new_callable=AsyncMock) as mock_get:
            mock_get.return_value = make_chunk_detail()
            response = client.get(
                f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/chunks/{CHUNK_ID}"
            )
            assert response.status_code == 200
            data = response.json()
            assert data["text_content"] == "完整切片文本内容"

    def test_get_chunk_not_found(self, client):
        with patch("routers.chunk.document_service.get_chunk",
                   new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            response = client.get(
                f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/chunks/{uuid.uuid4()}"
            )
            assert response.status_code == 404


# ============================================================
# PUT /chunks/{chunk_id} — 编辑切片
# ============================================================
class TestUpdateChunk:
    def test_update_chunk_success(self, client):
        with patch("routers.chunk.document_service.update_chunk",
                   new_callable=AsyncMock) as mock_update:
            mock_update.return_value = make_chunk_detail()
            response = client.put(
                f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/chunks/{CHUNK_ID}",
                json={"text": "更新后的文本"},
            )
            assert response.status_code == 200

    def test_update_chunk_not_found(self, client):
        with patch("routers.chunk.document_service.update_chunk",
                   new_callable=AsyncMock) as mock_update:
            mock_update.side_effect = ValueError("切片不存在")
            response = client.put(
                f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/chunks/{CHUNK_ID}",
                json={"text": "更新后的文本"},
            )
            assert response.status_code == 404

    def test_update_chunk_empty_text(self, client):
        """空文本应返回 422。"""
        response = client.put(
            f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/chunks/{CHUNK_ID}",
            json={"text": ""},
        )
        assert response.status_code == 422


# ============================================================
# POST /chunks/{chunk_id}/delete — 删除切片
# ============================================================
class TestDeleteChunk:
    def test_delete_chunk_success(self, client):
        with patch("routers.chunk.document_service.delete_chunk",
                   new_callable=AsyncMock) as mock_delete:
            mock_delete.return_value = True
            response = client.post(
                f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/chunks/{CHUNK_ID}/delete"
            )
            assert response.status_code == 204

    def test_delete_chunk_not_found(self, client):
        with patch("routers.chunk.document_service.delete_chunk",
                   new_callable=AsyncMock) as mock_delete:
            mock_delete.return_value = False
            response = client.post(
                f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/chunks/{uuid.uuid4()}/delete"
            )
            assert response.status_code == 404


# ============================================================
# POST /rechunk — 重新切片
# ============================================================
class TestRechunkDocument:
    def test_rechunk_enqueued(self, client):
        with patch("routers.chunk.document_service.get_document",
                   new_callable=AsyncMock) as mock_get, \
             patch("routers.chunk.parse_queue.enqueue_rechunk",
                   new_callable=AsyncMock) as mock_enqueue, \
             patch("routers.chunk.async_session") as mock_session_ctx:

            mock_get.return_value = {
                "id": DOC_ID,
                "status": "completed",
                "file_name": "test.pdf",
            }
            mock_enqueue.return_value = 1

            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__.return_value = mock_session

            response = client.post(
                f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/rechunk",
                json={"chunk_size": 1024, "chunk_overlap": 128},
            )
            assert response.status_code == 202
            data = response.json()
            assert data["status"] == "queued"

    def test_rechunk_doc_not_completed(self, client):
        with patch("routers.chunk.document_service.get_document",
                   new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {
                "id": DOC_ID,
                "status": "uploaded",
                "file_name": "test.pdf",
            }
            response = client.post(
                f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/rechunk",
                json={"chunk_size": 1024, "chunk_overlap": 128},
            )
            assert response.status_code == 400
            assert "只能对已完成解析" in response.json()["detail"]

    def test_rechunk_doc_not_found(self, client):
        with patch("routers.chunk.document_service.get_document",
                   new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            response = client.post(
                f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/rechunk",
                json={"chunk_size": 1024, "chunk_overlap": 128},
            )
            assert response.status_code == 404

    def test_rechunk_default_params(self, client):
        """不传参数使用默认值。"""
        with patch("routers.chunk.document_service.get_document",
                   new_callable=AsyncMock) as mock_get, \
             patch("routers.chunk.parse_queue.enqueue_rechunk",
                   new_callable=AsyncMock) as mock_enqueue, \
             patch("routers.chunk.async_session") as mock_session_ctx:

            mock_get.return_value = {
                "id": DOC_ID,
                "status": "completed",
                "file_name": "test.pdf",
            }
            mock_enqueue.return_value = 1
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__.return_value = mock_session

            response = client.post(
                f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/rechunk",
                json={},
            )
            assert response.status_code == 202

    def test_rechunk_failed_status_allowed(self, client):
        """failed 状态的文档也允许重切片。"""
        with patch("routers.chunk.document_service.get_document",
                   new_callable=AsyncMock) as mock_get, \
             patch("routers.chunk.parse_queue.enqueue_rechunk",
                   new_callable=AsyncMock) as mock_enqueue, \
             patch("routers.chunk.async_session") as mock_session_ctx:

            mock_get.return_value = {
                "id": DOC_ID,
                "status": "failed",
                "file_name": "test.pdf",
            }
            mock_enqueue.return_value = 1
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__.return_value = mock_session

            response = client.post(
                f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/rechunk",
                json={},
            )
            assert response.status_code == 202
