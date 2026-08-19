"""DocxParser 图片提取单元测试。"""
import base64
import shutil
from io import BytesIO
from pathlib import Path

from docx import Document

from core.parser import DocxParser

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
    "AAAAC0lEQVR42mP8/x8AAusB9Y9ZQmcAAAAASUVORK5CYII="
)


def test_extract_inline_image_from_docx():
    tmp_dir = Path(__file__).resolve().parent / "_tmp_image_extraction"
    tmp_dir.mkdir(exist_ok=True)
    docx_path = tmp_dir / "image.docx"
    try:
        doc = Document()
        doc.add_paragraph("正文段落")
        doc.add_picture(BytesIO(PNG_BYTES))
        doc.add_paragraph("后续段落")
        doc.save(docx_path)

        items = DocxParser()._parse_sync(docx_path)
        image_items = [item for item in items if item.images]

        assert len(image_items) == 1
        assert image_items[0].text == ""
        assert image_items[0].images[0].content_type == "image/png"
        assert image_items[0].images[0].image_bytes == PNG_BYTES
    finally:
        if docx_path.exists():
            docx_path.unlink()
        shutil.rmtree(tmp_dir, ignore_errors=True)
