"""
文档服务测试 — 切片序列化（image_url）/ 图片读取 / PDF-Word 统一分块链路 / partial 续跑。
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.parser import ParagraphItem
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


# ============================================================
# _parse_and_store_in_batches — PDF / Word 统一结构化链路
# ============================================================
class TestParseAndStoreBatches:
    @pytest.mark.asyncio
    async def test_pdf_uses_blocks_and_returns_page_count(self):
        """PDF 解析结果为 (ParagraphItem 列表, 物理页数) → 走 blocks 链路，页码落 chunk。"""
        parsed = ([
            ParagraphItem(text="第一章 招标公告", is_heading=True, heading_level=1, page_number=1),
            ParagraphItem(text="本项目已具备招标条件，现进行公开招标。", page_number=1),
        ], 5)
        with patch.object(document_service, "_existing_chunk_md5s",
                          new=AsyncMock(return_value={})), \
                patch.object(document_service, "process_word_images", new_callable=AsyncMock) as mock_img, \
                patch.object(document_service, "build_index", new_callable=AsyncMock) as mock_build, \
                patch.object(document_service.es_store, "bulk_index", new_callable=AsyncMock) as mock_es, \
                patch.object(document_service, "_store_chunk_batch", new_callable=AsyncMock) as mock_store:
            mock_build.return_value = ["pk-1"]
            chunk_count, page_count = await document_service._parse_and_store_in_batches(
                parsed, "pdf", DOC_ID, KB_ID, "a.pdf", None, None,
            )
            assert page_count == 5
            assert chunk_count >= 1
            mock_img.assert_awaited_once()
            assert mock_store.await_count >= 1
            _, _, metadatas, _, _ = mock_store.await_args.args
            assert metadatas[0]["file_name"] == "a.pdf"
            assert metadatas[0]["page_number"] == 1
            assert metadatas[0]["heading"] == "第一章 招标公告"

    @pytest.mark.asyncio
    async def test_docx_unchanged_page_count_1(self):
        """Word 项无 page_number → 仍返回 page_count=1，chunk 默认 page_number=1（回归）。"""
        parsed = [
            ParagraphItem(text="第一章 采购内容", is_heading=True, heading_level=1),
            ParagraphItem(text="正文段落内容。"),
        ]
        with patch.object(document_service, "_existing_chunk_md5s",
                          new=AsyncMock(return_value={})), \
                patch.object(document_service, "process_word_images", new_callable=AsyncMock) as mock_img, \
                patch.object(document_service, "build_index", new_callable=AsyncMock) as mock_build, \
                patch.object(document_service.es_store, "bulk_index", new_callable=AsyncMock) as mock_es, \
                patch.object(document_service, "_store_chunk_batch", new_callable=AsyncMock) as mock_store:
            mock_build.return_value = ["pk-1"]
            chunk_count, page_count = await document_service._parse_and_store_in_batches(
                parsed, "docx", DOC_ID, KB_ID, "a.docx", None, None,
            )
            assert page_count == 1
            assert chunk_count >= 1
            _, _, metadatas, _, _ = mock_store.await_args.args
            assert metadatas[0]["page_number"] == 1


# ============================================================
# _parse_and_store_in_batches — resume_from 续跑语义
# ============================================================
def _fake_chunked_batch(blocks, doc_id, file_name, *, start_chunk_index=0):
    """每批固定产出 3 个 chunk：索引 start_chunk_index..+2（模拟 word_chunk_blocks）。"""
    chunks, metas = [], []
    for j in range(3):
        idx = start_chunk_index + j
        metas.append({
            "doc_id": doc_id, "file_name": file_name, "chunk_index": idx,
            "page_number": 1, "chunk_type": "text",
            "heading": "", "heading_path": "", "text": f"text-{idx}",
        })
        chunks.append(f"text-{idx}")
    return chunks, metas, []


class TestParseAndStoreIdempotent:
    """executor MD5 幂等写（docs/plan/45）：unchanged 跳过 / changed/missing 只写差集。"""

    @staticmethod
    def _enter_pipeline(existing):
        """进入整条 executor 链路的 mock 上下文，返回 (exit_stack, mocks 字典)。"""
        from contextlib import ExitStack
        stack = ExitStack()
        mocks = {}
        mocks["md5_map"] = stack.enter_context(patch.object(
            document_service, "_existing_chunk_md5s",
            new=AsyncMock(return_value=existing),
        ))
        stack.enter_context(patch.object(
            document_service, "aggregate_paragraphs", return_value=list(range(20))
        ))
        stack.enter_context(patch.object(
            document_service, "word_chunk_blocks", side_effect=_fake_chunked_batch
        ))
        stack.enter_context(patch.object(
            document_service, "process_word_images", new_callable=AsyncMock
        ))
        mocks["build"] = stack.enter_context(patch.object(
            document_service, "build_index", new_callable=AsyncMock
        ))
        stack.enter_context(patch.object(
            document_service.es_store, "bulk_index", new_callable=AsyncMock
        ))
        mocks["del"] = stack.enter_context(patch.object(
            document_service, "delete_vector_nodes", new_callable=AsyncMock
        ))
        mocks["store"] = stack.enter_context(patch.object(
            document_service, "_store_chunk_batch", new_callable=AsyncMock
        ))
        return stack, mocks

    @staticmethod
    def _existing_unchanged(indexes, page: int = 1):
        """构造与 _fake_chunked_batch（text=f"text-{idx}", page=1）一致的存量指纹。"""
        return {
            idx: document_service._chunk_md5(page, f"text-{idx}") for idx in indexes
        }

    @pytest.mark.asyncio
    async def test_first_parse_no_existing_writes_all_batches(self):
        """全新文档：无存量指纹 → 全部补写（每批先删后插 + 入库）。"""
        stack, mocks = self._enter_pipeline(existing={})
        try:
            mocks["build"].side_effect = lambda chunks, metas, kb: [
                f"{DOC_ID}:{m['chunk_index']:06d}" for m in metas
            ]
            chunk_count, page_count = await document_service._parse_and_store_in_batches(
                [ParagraphItem(text="x")], "docx", DOC_ID, KB_ID, "a.docx", None, None,
            )
            assert page_count == 1
            assert chunk_count == 6
            assert mocks["store"].await_count == 2
            stored = []
            for call in mocks["store"].await_args_list:
                _, _, metas, _, processed = call.args
                stored.extend(m["chunk_index"] for m in metas)
                assert processed == metas[-1]["chunk_index"] + 1  # 进度 = 当前全局已处理数
                assert all(m.get("chunk_md5") for m in metas)  # 指纹随行入库
            assert stored == [0, 1, 2, 3, 4, 5]
            assert mocks["del"].await_count == 2  # 每批 to_write 先删后插
            assert mocks["del"].await_args_list[0].args[1] == [
                f"{DOC_ID}:000000", f"{DOC_ID}:000001", f"{DOC_ID}:000002",
            ]
            assert mocks["del"].await_args_list[1].args[1] == [
                f"{DOC_ID}:000003", f"{DOC_ID}:000004", f"{DOC_ID}:000005",
            ]
        finally:
            stack.close()

    @pytest.mark.asyncio
    async def test_all_unchanged_skips_all_writes(self):
        """重跑且内容一致：全部 unchanged → 零删除/零重嵌/零落库。"""
        stack, mocks = self._enter_pipeline(
            existing=self._existing_unchanged([0, 1, 2, 3, 4, 5])
        )
        try:
            mocks["build"].side_effect = lambda chunks, metas, kb: [
                f"{DOC_ID}:{m['chunk_index']:06d}" for m in metas
            ]
            chunk_count, page_count = await document_service._parse_and_store_in_batches(
                [ParagraphItem(text="x")], "docx", DOC_ID, KB_ID, "a.docx", None, None,
            )
            assert chunk_count == 6
            assert mocks["store"].await_count == 0
            mocks["del"].assert_not_awaited()
            mocks["build"].assert_not_awaited()
        finally:
            stack.close()

    @pytest.mark.asyncio
    async def test_changed_and_missing_only_written(self):
        """重复跑：changed（内容变了）与 missing（中断残留/新增）才写，unchanged 跳过。"""
        existing = self._existing_unchanged([0, 1, 3])
        existing[2] = "0" * 32  # index 2 内容已变 → 需要重写；4/5 缺失 → 补写
        stack, mocks = self._enter_pipeline(existing=existing)
        try:
            mocks["build"].side_effect = lambda chunks, metas, kb: [
                f"{DOC_ID}:{m['chunk_index']:06d}" for m in metas
            ]
            chunk_count, page_count = await document_service._parse_and_store_in_batches(
                [ParagraphItem(text="x")], "docx", DOC_ID, KB_ID, "a.docx", None, None,
            )
            assert chunk_count == 6
            assert mocks["store"].await_count == 2
            first = mocks["store"].await_args_list[0].args[2][0]
            assert first["chunk_index"] == 2
            assert first["chunk_md5"] == document_service._chunk_md5(1, "text-2")
            second = [m["chunk_index"] for m in mocks["store"].await_args_list[1].args[2]]
            assert second == [4, 5]
            # 只删除/重写待写集合的确定性 pk
            assert mocks["del"].await_args_list[0].args[1] == [f"{DOC_ID}:000002"]
            assert mocks["del"].await_args_list[1].args[1] == [
                f"{DOC_ID}:000004", f"{DOC_ID}:000005",
            ]
        finally:
            stack.close()

    @pytest.mark.asyncio
    async def test_chunk_md5_fingerprint_stable(self):
        """指纹稳定 & 随内容/页码变化；与 0023 回填公式（| 连接 UTF-8）一致。"""
        import hashlib
        assert document_service._chunk_md5(1, "abc") == hashlib.md5(b"1|abc").hexdigest()
        assert document_service._chunk_md5(1, "abc") != document_service._chunk_md5(2, "abc")
        assert document_service._chunk_md5(1, "abc") != document_service._chunk_md5(1, "abd")
        assert len(document_service._chunk_md5(12, "中文字符")) == 32


# ============================================================
# _mark_parse_failure — partial / failed 判定
# ============================================================
class TestMarkParseFailure:
    @pytest.mark.asyncio
    async def test_committed_gt_0_sets_partial(self):
        with patch("services.document_service.async_session") as ctx, \
                patch("services.document_service.DocumentRepository") as mock_cls:
            repo = mock_cls.return_value
            repo.count_chunks_by_doc_id = AsyncMock(return_value=12)
            repo.update_status = AsyncMock()
            ctx.return_value.__aenter__.return_value = AsyncMock()

            await document_service._mark_parse_failure(DOC_ID, "pg timeout", 1234)
            repo.update_status.assert_awaited_once_with(
                DOC_ID, "partial",
                error_message="pg timeout", processed_chunk_count=12, parse_duration_ms=1234,
            )

    @pytest.mark.asyncio
    async def test_committed_zero_sets_failed(self):
        with patch("services.document_service.async_session") as ctx, \
                patch("services.document_service.DocumentRepository") as mock_cls:
            repo = mock_cls.return_value
            repo.count_chunks_by_doc_id = AsyncMock(return_value=0)
            repo.update_status = AsyncMock()
            ctx.return_value.__aenter__.return_value = AsyncMock()

            await document_service._mark_parse_failure(DOC_ID, "parse error", 10)
            repo.update_status.assert_awaited_once_with(
                DOC_ID, "failed",
                error_message="parse error", processed_chunk_count=0, parse_duration_ms=10,
            )


# ============================================================
# resume_document — 续跑 partial 文档
# ============================================================
class TestResumeDocument:
    def _make_doc(self, **overrides):
        defaults = dict(
            id=DOC_ID, status="partial", processed_chunk_count=3, chunk_count=None,
            object_key="k/d.docx", file_type="docx", file_name="a.docx",
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    @pytest.mark.asyncio
    async def test_resume_partial_completes_without_counting(self):
        """partial 续跑完成置 completed；文档计数不变（上传时已 +1，docs/plan/46）。"""
        doc = self._make_doc()
        with patch("services.document_service.async_session") as ctx, \
                patch("services.document_service.DocumentRepository") as mock_doc_cls, \
                patch("services.document_service.KnowledgeBaseRepository") as mock_kb_cls, \
                patch("services.document_service.minio_client.download"), \
                patch("services.document_service.ensure_temp_dir", return_value=Path("temp")), \
                patch("services.document_service.resolve_model_config",
                      new_callable=AsyncMock, return_value=None), \
                patch("services.document_service.parse_file", new_callable=AsyncMock) as mock_parse, \
                patch("services.document_service._parse_and_store_in_batches",
                      new_callable=AsyncMock) as mock_batches:
            doc_repo = mock_doc_cls.return_value
            doc_repo.get_by_id = AsyncMock(return_value=doc)
            doc_repo.update_status = AsyncMock()
            kb_repo = mock_kb_cls.return_value
            kb_repo.increment_doc_count = AsyncMock()
            ctx.return_value.__aenter__.return_value = AsyncMock()
            mock_parse.return_value = [ParagraphItem(text="正文")]
            mock_batches.return_value = (12, 7)

            await document_service.resume_document(KB_ID, DOC_ID)

            statuses = [c.args[1] for c in doc_repo.update_status.await_args_list]
            assert statuses[0] == "processing"
            assert statuses[-1] == "completed"
            completed_kwargs = doc_repo.update_status.await_args_list[-1].kwargs
            assert completed_kwargs["chunk_count"] == 12
            assert completed_kwargs["processed_chunk_count"] == 12
            assert completed_kwargs["page_count"] == 7
            # 幂等续跑：全量重解析（executor 内按 MD5 指纹只写差集），不传 resume_from 锚点
            assert "resume_from" not in mock_batches.await_args.kwargs
            mock_parse.assert_awaited_once()
            kb_repo.increment_doc_count.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resume_skips_non_partial(self):
        """状态非 partial 直接返回，不触达下载/解析。"""
        doc = self._make_doc(status="failed")
        with patch("services.document_service.async_session") as ctx, \
                patch("services.document_service.DocumentRepository") as mock_doc_cls:
            doc_repo = mock_doc_cls.return_value
            doc_repo.get_by_id = AsyncMock(return_value=doc)
            doc_repo.update_status = AsyncMock()
            ctx.return_value.__aenter__.return_value = AsyncMock()

            await document_service.resume_document(KB_ID, DOC_ID)

            doc_repo.update_status.assert_not_awaited()


# ============================================================
# upload_document — 上传计数（docs/plan/46：行数口径，上传即 +1）
# ============================================================
class TestUploadDocumentCounting:
    @pytest.mark.asyncio
    async def test_upload_increments_doc_count_once(self):
        """上传成功 → 文档行落库且知识库计数 +1（此后解析/重跑不再加减）。"""
        from datetime import datetime

        class FakeUploadFile:
            filename = "a.pdf"

            async def read(self):
                return b"%PDF-1.4 fake"

        doc_row = SimpleNamespace(id=DOC_ID, created_at=datetime.now())
        with patch("services.document_service.ensure_temp_dir",
                   return_value=Path("temp")), \
                patch("services.document_service.minio_client.upload",
                      new_callable=MagicMock) as mock_upload, \
                patch("services.document_service.async_session") as ctx, \
                patch("services.document_service.DocumentRepository") as mock_doc_cls, \
                patch("services.document_service.KnowledgeBaseRepository") as mock_kb_cls:
            doc_repo = mock_doc_cls.return_value
            doc_repo.create = AsyncMock(return_value=doc_row)
            kb_repo = mock_kb_cls.return_value
            kb_repo.increment_doc_count = AsyncMock()
            ctx.return_value.__aenter__.return_value = AsyncMock()

            result = await document_service.upload_document(KB_ID, FakeUploadFile())

            assert result["id"] == DOC_ID
            assert result["status"] == "uploaded"
            mock_upload.assert_called_once()
            doc_repo.create.assert_awaited_once()
            kb_repo.increment_doc_count.assert_awaited_once_with(KB_ID)

    @pytest.mark.asyncio
    async def test_upload_count_failure_does_not_fail_upload(self):
        """计数写入异常只记日志，不阻断上传（删除侧同风格，docs/plan/46）。"""
        from datetime import datetime

        class FakeUploadFile:
            filename = "b.docx"

            async def read(self):
                return b"docx-bytes"

        doc_row = SimpleNamespace(id=DOC_ID, created_at=datetime.now())
        with patch("services.document_service.ensure_temp_dir",
                   return_value=Path("temp")), \
                patch("services.document_service.minio_client.upload",
                      new_callable=MagicMock), \
                patch("services.document_service.async_session") as ctx, \
                patch("services.document_service.DocumentRepository") as mock_doc_cls, \
                patch("services.document_service.KnowledgeBaseRepository") as mock_kb_cls:
            doc_repo = mock_doc_cls.return_value
            doc_repo.create = AsyncMock(return_value=doc_row)
            kb_repo = mock_kb_cls.return_value
            kb_repo.increment_doc_count = AsyncMock(side_effect=RuntimeError("db down"))
            ctx.return_value.__aenter__.return_value = AsyncMock()

            result = await document_service.upload_document(KB_ID, FakeUploadFile())

            assert result["status"] == "uploaded"
