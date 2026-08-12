"""
文档解析引擎 — Strategy 模式：PDF / DOCX / DOC 各自实现。
输出 LlamaIndex Document 列表，供索引引擎消费。

重要：所有 CPU 密集型同步操作（PyMuPDF, pdfplumber, PaddleOCR）
必须通过 asyncio.to_thread() 放到线程池执行，避免阻塞 asyncio 事件循环
导致前端请求无响应（"页面阻塞"）。
"""
import asyncio
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from llama_index.core import Document as LlamaDocument

from config import settings
from utils.file_utils import get_file_extension
from utils.logging import get_logger

logger = get_logger(__name__)


# ============================================================
# Strategy 接口
# ============================================================
class BaseParser(ABC):
    """文档解析器抽象基类。"""

    @abstractmethod
    async def parse(self, file_path: Path) -> list[LlamaDocument]:
        """解析文档，返回 LlamaIndex Document 列表。

        Returns:
            每个 Document 代表一页，extra_info 含: file_name, page_number, file_type
        """
        ...


# ============================================================
# PDF 解析器
# ============================================================
class PDFParser(BaseParser):
    """PDF 解析：PyMuPDF 提取文本 + pdfplumber 提取表格 + PaddleOCR 识别图片文字。

    所有 CPU 密集型操作均通过 asyncio.to_thread() 放到线程池执行，
    避免阻塞 asyncio 事件循环导致前端页面无响应。
    """

    def __init__(self):
        self._ocr = None  # PaddleOCR 懒加载，全局只初始化一次

    async def parse(self, file_path: Path) -> list[LlamaDocument]:
        """异步解析入口：将整个同步解析逻辑交给线程池执行。"""
        logger.info("PDF 解析开始: %s", file_path.name)
        documents = await asyncio.to_thread(self._parse_sync, file_path)
        logger.info("PDF 解析完成: %s, pages=%d", file_path.name, len(documents))
        return documents

    def _parse_sync(self, file_path: Path) -> list[LlamaDocument]:
        """同步解析核心逻辑（在线程池中执行，不阻塞事件循环）。"""
        import fitz  # PyMuPDF

        documents: list[LlamaDocument] = []

        # 一次性打开 pdfplumber（而非每页重复打开）
        tables_by_page = self._extract_all_tables(file_path)

        with fitz.open(file_path) as doc:
            for page_num, page in enumerate(doc, start=1):
                text_parts: list[str] = []

                # 1. 提取文本
                page_text = page.get_text("text")
                if page_text:
                    text_parts.append(page_text.strip())

                # 2. 获取预提取的表格
                table_text = tables_by_page.get(page_num, "")
                if table_text:
                    text_parts.append(table_text)

                # 3. OCR 识别嵌入图片中的文字
                ocr_text = self._ocr_images_sync(page, page_num)
                if ocr_text:
                    text_parts.append(ocr_text)

                full_text = "\n\n".join(text_parts)
                if not full_text.strip():
                    continue

                documents.append(
                    LlamaDocument(
                        text=full_text,
                        metadata={
                            "file_name": file_path.name,
                            "page_number": page_num,
                            "file_type": "pdf",
                        },
                    )
                )

        return documents

    def _extract_all_tables(self, file_path: Path) -> dict[int, str]:
        """一次性提取整个 PDF 的所有表格（只打开一次 pdfplumber）。"""
        try:
            import pdfplumber

            with pdfplumber.open(file_path) as pdf:
                result: dict[int, str] = {}
                for page_idx, pdf_page in enumerate(pdf.pages):
                    page_num = page_idx + 1
                    tables = pdf_page.extract_tables()
                    if not tables:
                        continue
                    lines: list[str] = []
                    for table_idx, table in enumerate(tables):
                        lines.append(f"[表格 {table_idx + 1}]")
                        for row in table:
                            if row:
                                cells = [str(c) if c else "" for c in row]
                                lines.append(" | ".join(cells))
                    result[page_num] = "\n".join(lines)
                return result
        except Exception as e:
            logger.debug("pdfplumber 表格提取异常: %s", e)
            return {}

    def _get_ocr(self):
        """懒加载 PaddleOCR 实例（全局只初始化一次，避免每页重复加载模型）。"""
        if self._ocr is None:
            try:
                from paddleocr import PaddleOCR
                self._ocr = PaddleOCR(lang="ch", show_log=False)
                logger.info("PaddleOCR 初始化完成")
            except ImportError:
                logger.debug("PaddleOCR 未安装，跳过图片 OCR")
                self._ocr = False  # 标记为不可用
            except Exception as e:
                logger.warning("PaddleOCR 初始化失败: %s", e)
                self._ocr = False
        return self._ocr if self._ocr is not False else None

    def _ocr_images_sync(self, page: Any, page_num: int) -> str:
        """OCR 识别页面中的嵌入图片（同步方法，在线程池中执行）。"""
        try:
            ocr = self._get_ocr()
            if ocr is None:
                return ""

            images = page.get_images(full=True)
            if not images:
                return ""

            texts: list[str] = []
            for img_idx, img_info in enumerate(images):
                xref = img_info[0]
                base_image = page.parent.extract_image(xref)
                if base_image is None:
                    continue
                image_bytes = base_image["image"]
                result = ocr.ocr(image_bytes, cls=True)
                if result and result[0]:
                    img_text = " ".join(
                        line[1][0] for line in result[0] if line
                    )
                    texts.append(f"[图片 {img_idx + 1}] {img_text}")
            return "\n".join(texts)
        except Exception as e:
            logger.warning("OCR 异常 (page %d): %s", page_num, e)
            return ""


# ============================================================
# DOCX 解析器
# ============================================================
class DocxParser(BaseParser):
    """Word (.docx) 解析：python-docx 提取段落和表格。

    CPU 密集型操作通过 asyncio.to_thread() 放到线程池执行。
    """

    async def parse(self, file_path: Path) -> list[LlamaDocument]:
        """异步解析入口：将同步解析逻辑交给线程池执行。"""
        logger.info("DOCX 解析开始: %s", file_path.name)
        result = await asyncio.to_thread(self._parse_sync, file_path)
        if result:
            logger.info("DOCX 解析完成: %s", file_path.name)
        else:
            logger.warning("DOCX 解析结果为空: %s", file_path.name)
        return result

    def _parse_sync(self, file_path: Path) -> list[LlamaDocument]:
        """同步解析核心逻辑（在线程池中执行，不阻塞事件循环）。"""
        from docx import Document as DocxDocument

        doc = DocxDocument(file_path)
        text_parts: list[str] = []

        # 提取段落
        for para in doc.paragraphs:
            if para.text.strip():
                text_parts.append(para.text.strip())

        # 提取表格
        for table_idx, table in enumerate(doc.tables):
            lines = [f"[表格 {table_idx + 1}]"]
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                lines.append(" | ".join(cells))
            text_parts.append("\n".join(lines))

        full_text = "\n\n".join(text_parts)
        if not full_text.strip():
            return []

        # DOCX 无天然页码概念，整篇作为一个 Document
        return [
            LlamaDocument(
                text=full_text,
                metadata={
                    "file_name": file_path.name,
                    "page_number": 1,
                    "file_type": "docx",
                },
            )
        ]


# ============================================================
# Parser Registry（Strategy 注册表）
# ============================================================
PARSER_REGISTRY: dict[str, BaseParser] = {
    "pdf": PDFParser(),
    "docx": DocxParser(),
    "doc": DocxParser(),  # .doc 暂用 DocxParser，后续替换
}


# ============================================================
# 统一入口
# ============================================================
async def parse_document(file_path: Path, file_type: str) -> list[LlamaDocument]:
    """解析文档，按文件类型分派到对应 Parser。

    Args:
        file_path: 上传文件路径
        file_type: 文件类型（不含点，如 'pdf', 'docx', 'doc'）

    Returns:
        LlamaIndex Document 列表，每个 Document 带 metadata

    Raises:
        ValueError: 不支持的文件类型
    """
    parser = PARSER_REGISTRY.get(file_type.lower())
    if parser is None:
        raise ValueError(f"Unsupported file type: {file_type}")

    logger.info(
        "parse_document: file=%s, type=%s → parser=%s",
        file_path.name,
        file_type,
        type(parser).__name__,
    )
    return await parser.parse(file_path)
