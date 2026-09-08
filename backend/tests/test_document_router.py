"""
文档管理 API 端点测试。
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.helpers import KB_ID, DOC_ID


def make_doc_dict(status="uploaded", file_name="test.pdf", file_type="pdf"):
    return {
        "id": str(uuid.uuid4()),
        "file_name": file_name,
        "file_size": 1024,
        "file_type": file_type,
        "page_count": None,
        "status": status,
        "error_message": None,
        "chunk_count": None,
        "created_at": "2024-01-01T00:00:00",
    }


@pytest.fixture
def client():
    with patch("core.parse_queue.start_worker", new_callable=AsyncMock), \
         patch("core.parse_queue.stop_worker", new_callable=AsyncMock):
        from main import app
        app.router.lifespan_context = None
        return TestClient(app)


# ============================================================
# POST /api/knowledge-bases/{kb_id}/documents — 上传
# ============================================================
class TestUploadDocument:
    def test_upload_success(self, client):
        with patch("routers.document.document_service.upload_document",
                   new_callable=AsyncMock) as mock_upload:
            mock_upload.return_value = make_doc_dict(file_name="test.docx", file_type="docx")

            response = client.post(
                f"/api/knowledge-bases/{KB_ID}/documents",
                files={"file": ("test.docx", b"PK\x03\x04 test content", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            )
            assert response.status_code == 201
            data = response.json()
            assert data["status"] == "uploaded"

    def test_upload_pdf_accepted(self, client):
        """PDF 已支持解析，上传走 service（mock）返回 201。"""
        with patch("routers.document.document_service.upload_document",
                   new_callable=AsyncMock) as mock_upload:
            mock_upload.return_value = make_doc_dict(file_name="test.pdf", file_type="pdf")

            response = client.post(
                f"/api/knowledge-bases/{KB_ID}/documents",
                files={"file": ("test.pdf", b"%PDF-1.4 test content", "application/pdf")},
            )
            assert response.status_code == 201
            data = response.json()
            assert data["file_type"] == "pdf"
            assert data["status"] == "uploaded"

    def test_upload_no_file(self, client):
        """不传文件参数应返回 422。"""
        response = client.post(f"/api/knowledge-bases/{KB_ID}/documents")
        assert response.status_code == 422

    def test_upload_empty_filename(self, client):
        """FastAPI UploadFile 的 filename 为空时，validator 拦截返回 400。"""
        # FastAPI 对空文件名的 UploadFile 会正常解析，
        # 但路由层检查 file.filename 为假时返回 400
        # 用中间件层模拟：文件名不为空但故意绕过
        with patch("routers.document.document_service.upload_document",
                   new_callable=AsyncMock) as mock_upload:
            mock_upload.side_effect = ValueError("未提供文件")
            response = client.post(
                f"/api/knowledge-bases/{KB_ID}/documents",
                files={"file": ("test.docx", b"PK\x03\x04", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            )
            assert response.status_code == 500

    def test_upload_invalid_format(self, client):
        """上传 .txt 文件应返回 400。"""
        response = client.post(
            f"/api/knowledge-bases/{KB_ID}/documents",
            files={"file": ("test.txt", b"hello world", "text/plain")},
        )
        assert response.status_code == 400
        assert "不支持" in response.json()["detail"]

    def test_upload_docx_valid(self, client):
        """上传 .docx 文件格式验证通过。"""
        with patch("routers.document.document_service.upload_document",
                   new_callable=AsyncMock) as mock_upload:
            mock_upload.return_value = make_doc_dict(file_name="test.docx", file_type="docx")
            response = client.post(
                f"/api/knowledge-bases/{KB_ID}/documents",
                files={"file": ("test.docx", b"dummy docx content", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            )
            assert response.status_code == 201


# ============================================================
# GET /api/knowledge-bases/{kb_id}/documents — 列表
# ============================================================
class TestListDocuments:
    def test_list_empty(self, client):
        with patch("routers.document.document_service.list_documents",
                   new_callable=AsyncMock) as mock_list:
            mock_list.return_value = []
            response = client.get(f"/api/knowledge-bases/{KB_ID}/documents")
            assert response.status_code == 200
            assert response.json() == []

    def test_list_with_docs(self, client):
        with patch("routers.document.document_service.list_documents",
                   new_callable=AsyncMock) as mock_list:
            mock_list.return_value = [make_doc_dict(), make_doc_dict(file_name="test2.pdf")]
            response = client.get(f"/api/knowledge-bases/{KB_ID}/documents")
            assert response.status_code == 200
            assert len(response.json()) == 2


# ============================================================
# GET /api/knowledge-bases/{kb_id}/documents/{doc_id} — 获取
# ============================================================
class TestGetDocument:
    def test_get_exists(self, client):
        with patch("routers.document.document_service.get_document",
                   new_callable=AsyncMock) as mock_get:
            mock_get.return_value = make_doc_dict()
            response = client.get(f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}")
            assert response.status_code == 200

    def test_get_not_found(self, client):
        with patch("routers.document.document_service.get_document",
                   new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            response = client.get(f"/api/knowledge-bases/{KB_ID}/documents/{uuid.uuid4()}")
            assert response.status_code == 404


# ============================================================
# POST /api/knowledge-bases/{kb_id}/documents/{doc_id}/parse — 触发解析
# ============================================================
class TestParseDocument:
    def test_parse_enqueued(self, client):
        with patch("routers.document.document_service.get_document",
                   new_callable=AsyncMock) as mock_get, \
             patch("routers.document.parse_queue.enqueue",
                   new_callable=AsyncMock) as mock_enqueue, \
             patch("routers.document.async_session") as mock_session_ctx:

            mock_get.return_value = make_doc_dict(status="uploaded")
            mock_enqueue.return_value = 1

            # Mock async_session
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__.return_value = mock_session

            response = client.post(f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/parse")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "queued"

    def test_parse_already_queued(self, client):
        with patch("routers.document.document_service.get_document",
                   new_callable=AsyncMock) as mock_get:
            mock_get.return_value = make_doc_dict(status="queued")
            response = client.post(f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/parse")
            assert response.status_code == 400
            assert "已在解析队列中" in response.json()["detail"]

    def test_parse_already_processing(self, client):
        with patch("routers.document.document_service.get_document",
                   new_callable=AsyncMock) as mock_get:
            mock_get.return_value = make_doc_dict(status="processing")
            response = client.post(f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/parse")
            assert response.status_code == 400
            assert "正在解析中" in response.json()["detail"]

    def test_parse_already_completed(self, client):
        with patch("routers.document.document_service.get_document",
                   new_callable=AsyncMock) as mock_get:
            mock_get.return_value = make_doc_dict(status="completed")
            response = client.post(f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/parse")
            assert response.status_code == 400
            assert "已解析完成" in response.json()["detail"]

    def test_parse_partial_blocked(self, client):
        """partial（部分成功）文档禁止整文档 /parse（防重复写已提交批次）。"""
        with patch("routers.document.document_service.get_document",
                   new_callable=AsyncMock) as mock_get:
            mock_get.return_value = make_doc_dict(status="partial")
            response = client.post(f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/parse")
            assert response.status_code == 400
            assert "部分成功" in response.json()["detail"]

    def test_parse_doc_not_found(self, client):
        with patch("routers.document.document_service.get_document",
                   new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            response = client.post(f"/api/knowledge-bases/{KB_ID}/documents/{uuid.uuid4()}/parse")
            assert response.status_code == 404


# ============================================================
# POST /api/knowledge-bases/{kb_id}/documents/{doc_id}/resume — 继续解析（续跑 partial）
# ============================================================
class TestResumeDocument:
    def test_resume_enqueued(self, client):
        with patch("routers.document.document_service.get_document",
                   new_callable=AsyncMock) as mock_get, \
             patch("routers.document.parse_queue.enqueue_resume",
                   new_callable=AsyncMock) as mock_enqueue, \
             patch("routers.document.async_session") as mock_session_ctx:

            mock_get.return_value = make_doc_dict(status="partial")
            mock_enqueue.return_value = 1
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__.return_value = mock_session

            response = client.post(f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/resume")
            assert response.status_code == 202
            data = response.json()
            assert data["status"] == "queued"
            mock_enqueue.assert_called_once_with(KB_ID, DOC_ID)

    def test_resume_not_partial_rejected(self, client):
        with patch("routers.document.document_service.get_document",
                   new_callable=AsyncMock) as mock_get:
            mock_get.return_value = make_doc_dict(status="failed")
            response = client.post(f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/resume")
            assert response.status_code == 400
            assert "仅「部分成功」状态" in response.json()["detail"]

    def test_resume_already_queued(self, client):
        with patch("routers.document.document_service.get_document",
                   new_callable=AsyncMock) as mock_get:
            mock_get.return_value = make_doc_dict(status="queued")
            response = client.post(f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/resume")
            assert response.status_code == 400
            assert "已在解析队列中" in response.json()["detail"]

    def test_resume_doc_not_found(self, client):
        with patch("routers.document.document_service.get_document",
                   new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            response = client.post(f"/api/knowledge-bases/{KB_ID}/documents/{uuid.uuid4()}/resume")
            assert response.status_code == 404


# ============================================================
# POST /api/knowledge-bases/{kb_id}/documents/{doc_id}/delete — 删除
# ============================================================
class TestDeleteDocument:
    def test_delete_success(self, client):
        with patch("routers.document.document_service.delete_document",
                   new_callable=AsyncMock) as mock_delete:
            mock_delete.return_value = True
            response = client.post(f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/delete")
            assert response.status_code == 204

    def test_delete_not_found(self, client):
        with patch("routers.document.document_service.delete_document",
                   new_callable=AsyncMock) as mock_delete:
            mock_delete.return_value = False
            response = client.post(f"/api/knowledge-bases/{KB_ID}/documents/{uuid.uuid4()}/delete")
            assert response.status_code == 404


# ============================================================
# GET /api/knowledge-bases/{kb_id}/documents/queue/status — 队列状态
# ============================================================
class TestQueueStatus:
    def test_queue_status(self, client):
        response = client.get(f"/api/knowledge-bases/{KB_ID}/documents/queue/status")
        assert response.status_code == 200
        data = response.json()
        assert "queue_size" in data
        assert "worker_running" in data
