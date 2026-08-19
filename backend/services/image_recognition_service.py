"""Word 图片识别服务：MinIO 上传 + OCR + Vision，失败只记日志。"""
import asyncio
import uuid
from typing import Any

from core.model_factory import create_ocr, create_vision_client
from core.parser import WordImage
from services import prompt_service
from utils import minio_client
from utils.logging import get_logger

logger = get_logger(__name__)

MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}


def _extension_for(content_type: str) -> str:
    return MIME_EXTENSIONS.get(content_type.split(";")[0].strip().lower(), ".img")


async def process_word_images(
    items: list[Any],
    kb_id: str,
    doc_id: str,
    ocr_config: dict | None = None,
    vision_config: dict | None = None,
) -> list[Any]:
    """上传并识别图片，结果写回 ParagraphItem 或 LogicalBlock 的 images。"""
    ocr_client = create_ocr(ocr_config) if ocr_config else None
    vision_client = create_vision_client(vision_config) if vision_config else None
    vision_prompt = None
    if vision_client is not None:
        try:
            active_prompt = await prompt_service.get_active_prompt("image_recognition")
            if active_prompt:
                vision_prompt = active_prompt.get("system_prompt")
        except Exception as e:
            logger.warning("获取图片识别提示词失败，使用内置默认值: %s", e)
    try:
        for item in items:
            for image in item.images:
                await _process_image(
                    image,
                    kb_id,
                    doc_id,
                    ocr_client,
                    vision_client,
                    vision_prompt,
                )
    finally:
        if ocr_client is not None:
            ocr_client.close()
        if vision_client is not None:
            vision_client.close()
    return items


async def _process_image(
    image: WordImage,
    kb_id: str,
    doc_id: str,
    ocr_client,
    vision_client,
    vision_prompt: str | None,
) -> None:
    if image.object_key:
        return

    object_key = f"{kb_id}/{doc_id}/images/{uuid.uuid4()}{_extension_for(image.content_type)}"
    try:
        await asyncio.to_thread(
            minio_client.upload_bytes,
            object_key,
            image.image_bytes,
            image.content_type or "application/octet-stream",
        )
        image.object_key = object_key
    except Exception as e:
        logger.warning("MinIO 上传图片失败: object_key=%s, error=%s", object_key, e)

    if ocr_client is not None:
        try:
            image.ocr_text = await asyncio.to_thread(ocr_client.recognize, image.image_bytes)
        except Exception as e:
            logger.warning("PaddleOCR 识别失败: image=%s, error=%s", object_key, e)

    # vision 仅在 OCR 未识别到文字时兜底
    if vision_client is not None and not (image.ocr_text or "").strip():
        try:
            image.vision_text = await asyncio.to_thread(
                vision_client.recognize,
                image.image_bytes,
                content_type=image.content_type or "image/png",
                prompt=vision_prompt,
            )
        except Exception as e:
            logger.warning("Vision 识别失败: image=%s, error=%s", object_key, e)
