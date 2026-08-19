"""Word 图片识别服务单元测试。"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.parser import ParagraphItem, WordImage
from services import image_recognition_service as svc


class TestProcessWordImages:
    @pytest.mark.asyncio
    async def test_uploads_and_recognizes(self, monkeypatch):
        image = WordImage(image_bytes=b"fake-png", content_type="image/png")
        item = ParagraphItem(text="", images=[image])

        ocr = MagicMock()
        ocr.recognize.return_value = "OCR 识别结果"
        vision = MagicMock()
        vision.recognize.return_value = "视觉描述结果"

        monkeypatch.setattr(svc, "create_ocr", lambda config: ocr)
        monkeypatch.setattr(svc, "create_vision_client", lambda config: vision)
        monkeypatch.setattr(
            svc.prompt_service,
            "get_active_prompt",
            AsyncMock(return_value={"system_prompt": "来自提示词管理的视觉提示词"}),
        )
        monkeypatch.setattr(svc.minio_client, "upload_bytes", MagicMock())

        await svc.process_word_images(
            [item],
            kb_id="kb-1",
            doc_id="doc-1",
            ocr_config={"api_base": "http://ocr"},
            vision_config={"api_base": "http://vision"},
        )

        assert image.object_key.startswith("kb-1/doc-1/images/")
        assert image.object_key.endswith(".png")
        assert image.ocr_text == "OCR 识别结果"
        assert image.vision_text == "视觉描述结果"
        assert vision.recognize.call_args.kwargs["prompt"] == "来自提示词管理的视觉提示词"
        ocr.close.assert_called_once()
        vision.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_errors_are_logged_and_do_not_raise(self, monkeypatch):
        image = WordImage(image_bytes=b"fake-png", content_type="image/png")
        item = ParagraphItem(text="", images=[image])

        ocr = MagicMock()
        ocr.recognize.side_effect = RuntimeError("ocr failed")
        vision = MagicMock()
        vision.recognize.side_effect = RuntimeError("vision failed")

        monkeypatch.setattr(svc, "create_ocr", lambda config: ocr)
        monkeypatch.setattr(svc, "create_vision_client", lambda config: vision)
        monkeypatch.setattr(
            svc.minio_client,
            "upload_bytes",
            MagicMock(side_effect=RuntimeError("upload failed")),
        )

        await svc.process_word_images([item], "kb-1", "doc-1")

        assert image.object_key is None
        assert image.ocr_text == ""
        assert image.vision_text == ""
