"""
文档服务测试 — 切片序列化（image_url）与图片 chunk 读取。
"""
from unittest.mock import AsyncMock, patch

import pytest

from services import document_service
from tests.helpers import KB_ID, DOC_ID, CHUNK_ID, MockChunk

IMAGE_META = {
    "chunk_type": "image",
    "object_key": f"{KB_ID}/{DOC_ID}/images/abc.png",
    "mime_type": "image/png",
    "image_index": 1,
    "source_chunk_index": 2,
}

EXPECTED_IMAGE_URL = (
    f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/chunks/{CHUNK_ID}/image"
)


# ============================================================
# 切片序列化 — image_url
# ============================================================
class TestChunkSerialization:
    def test_image_chunk_has_image_url(self):
        chunk = MockChunk(chunk_metadata=IMAGE_META)
        result = document_service._chunk_to_dict(chunk)
        assert result["image_url"] == EXPECTED_IMAGE_URL

    def test_text_chunk_image_url_none(self):
        chunk = MockChunk(chunk_metadata={"chunk_type": "text"})
        result = document_service._chunk_to_dict(chunk)
        assert result["image_url"] is None

    def test_image_chunk_without_object_key_image_url_none(self):
        chunk = MockChunk(chunk_metadata={"chunk_type": "image", "object_key": ""})
        result = document_service._chunk_to_dict(chunk)
        assert result["image_url"] is None

    def test_detail_image_chunk_has_image_url(self):
        chunk = MockChunk(chunk_metadata=IMAGE_META)
        result = document_service._chunk_to_detail_dict(chunk)
        assert result["image_url"] == EXPECTED_IMAGE_URL
        assert result["metadata"]["object_key"] == IMAGE_META["object_key"]

    def test_detail_text_chunk_image_url_none(self):
        chunk = MockChunk(chunk_metadata={"chunk_type": "text"})
        result = document_service._chunk_to_detail_dict(chunk)
        assert result["image_url"] is None


class TestChunkImageUrls:
    def test_text_chunk_with_image_keys(self):
        chunk = MockChunk(chunk_metadata={
            "chunk_type": "text",
            "image_keys": ["kb/doc/images/a.png", "kb/doc/images/b.png"],
        })
        urls = document_service._chunk_image_urls(chunk)
        assert urls == [
            f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/chunks/{CHUNK_ID}/image/0",
            f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/chunks/{CHUNK_ID}/image/1",
        ]

    def test_text_chunk_without_image_keys(self):
        chunk = MockChunk(chunk_metadata={"chunk_type": "text"})
        assert document_service._chunk_image_urls(chunk) == []

    def test_legacy_image_chunk_single_url(self):
        chunk = MockChunk(chunk_metadata={"chunk_type": "image", "object_key": "x.png"})
        assert document_service._chunk_image_urls(chunk) == [
            f"/api/knowledge-bases/{KB_ID}/documents/{DOC_ID}/chunks/{CHUNK_ID}/image"
        ]

    def test_serialization_includes_image_urls(self):
        chunk = MockChunk(chunk_metadata={
            "chunk_type": "text",
            "image_keys": ["kb/doc/images/a.png"],
        })
        result = document_service._chunk_to_dict(chunk)
        assert len(result["image_urls"]) == 1
        assert result["image_urls"][0].endswith("/image/0")


# ============================================================
# get_chunk_image — 读取图片 chunk 关联图片
# ============================================================
class TestGetChunkImage:
    @pytest.fixture(autouse=True)
    def mock_repo(self):
        with patch("services.document_service.DocumentRepository") as mock_cls:
            repo = mock_cls.return_value
            repo.get_chunk_by_id = AsyncMock()
            yield repo

    @pytest.mark.asyncio
    async def test_image_chunk_returns_bytes(self, mock_repo, mock_minio):
        mock_repo.get_chunk_by_id.return_value = MockChunk(chunk_metadata=IMAGE_META)
        result = await document_service.get_chunk_image(KB_ID, CHUNK_ID)
        assert result == (b"\x89PNG fake", "image/png")
        mock_minio["get_bytes"].assert_called_once_with(IMAGE_META["object_key"])

    @pytest.mark.asyncio
    async def test_chunk_not_found_returns_none(self, mock_repo, mock_minio):
        mock_repo.get_chunk_by_id.return_value = None
        result = await document_service.get_chunk_image(KB_ID, CHUNK_ID)
        assert result is None
        mock_minio["get_bytes"].assert_not_called()

    @pytest.mark.asyncio
    async def test_text_chunk_returns_none(self, mock_repo, mock_minio):
        mock_repo.get_chunk_by_id.return_value = MockChunk(
            chunk_metadata={"chunk_type": "text"}
        )
        result = await document_service.get_chunk_image(KB_ID, CHUNK_ID)
        assert result is None
        mock_minio["get_bytes"].assert_not_called()

    @pytest.mark.asyncio
    async def test_minio_object_missing_returns_none(self, mock_repo, mock_minio):
        mock_repo.get_chunk_by_id.return_value = MockChunk(chunk_metadata=IMAGE_META)
        mock_minio["get_bytes"].return_value = None
        result = await document_service.get_chunk_image(KB_ID, CHUNK_ID)
        assert result is None

    @pytest.mark.asyncio
    async def test_text_chunk_image_by_index(self, mock_repo, mock_minio):
        """文本 chunk 按 image_keys 下标读取关联图片。"""
        mock_repo.get_chunk_by_id.return_value = MockChunk(chunk_metadata={
            "chunk_type": "text",
            "image_keys": ["kb/doc/images/a.png", "kb/doc/images/b.png"],
        })
        mock_minio["get_bytes"].return_value = b"image-b"
        result = await document_service.get_chunk_image(KB_ID, CHUNK_ID, image_index=1)
        assert result == (b"image-b", "application/octet-stream")
        mock_minio["get_bytes"].assert_called_once_with("kb/doc/images/b.png")

    @pytest.mark.asyncio
    async def test_text_chunk_image_index_out_of_range(self, mock_repo, mock_minio):
        mock_repo.get_chunk_by_id.return_value = MockChunk(chunk_metadata={
            "chunk_type": "text",
            "image_keys": ["kb/doc/images/a.png"],
        })
        result = await document_service.get_chunk_image(KB_ID, CHUNK_ID, image_index=3)
        assert result is None
        mock_minio["get_bytes"].assert_not_called()
