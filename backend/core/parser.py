"""
文档解析引擎 — Strategy 模式：PDF / DOCX / DOC 各自实现。
输出 LlamaIndex Document 列表，供索引引擎消费。

重要：所有 CPU 密集型同步操作（PyMuPDF, pdfplumber, 图片提取）
必须通过 asyncio.to_thread() 放到线程池执行，避免阻塞 asyncio 事件循环
导致前端请求无响应（"页面阻塞"）。
远程 OCR 为网络 I/O，在线程池内以同步 httpx 阻塞线程而非事件循环。
"""
import asyncio
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from llama_index.core import Document as LlamaDocument

from core.model_factory import create_ocr
from config import settings
from utils.file_utils import get_file_extension
from utils.logging import get_logger

logger = get_logger(__name__)


# ============================================================
# Word 文档标题识别 — 中文投标文档标题正则匹配
# ============================================================

# 层级含义：
#   1 = 章（顶层结构，第X章 / 第X节 / 附件）
#   2 = 节（中文数字 / 阿拉伯单级数字）
#   3 = 子节（多级数字 1.1 / 括号中文数字）
#   4 = 款（括号数字 / 圆圈数字）
#   5 = 项（字母序号）
HEADING_PATTERNS: list[tuple[str, int, str]] = [
    # === Level 1: 章级标题 ===
    # 第X章 / 第X节 / 第X条（合同用）
    (
        r"^第[一二三四五六七八九十百千零\d]+[章节条]",
        1,
        "章标题",
    ),
    # 附件 / 附录（与章同级，独立模块）
    (
        r"^(?:附件|附表|附录)[一二三四五六七八九十\d]*[：:]*",
        1,
        "附件标题",
    ),

    # === Level 2: 节级标题 ===
    # 中文数字 + 顿号/句号：一、二、三、
    (
        r"^[一二三四五六七八九十]+[、．]",
        2,
        "中文数字标题",
    ),
    # 阿拉伯数字 + 顿号：1、2、3、（常见于技术规格书）
    (
        r"^\d{1,2}[、]",
        2,
        "数字顿号标题",
    ),
    # 阿拉伯数字 + 句号：1. 2.（需防列表项误判）
    (
        r"^\d{1,2}\.(?!\d)[\s　]",
        2,
        "数字句号标题",
    ),

    # === Level 3: 子节级标题 ===
    # 多级数字：1.1 / 1.1.1 / 2.3.5
    (
        r"^\d{1,2}\.\d{1,2}(?:\.\d{1,2})*[\s、.]*",
        3,
        "多级数字标题",
    ),
    # 括号中文数字：（一）（二）（三）
    (
        r"^[（(][一二三四五六七八九十]+[)）]",
        3,
        "括号中文数字标题",
    ),

    # === Level 4: 款级标题 ===
    # 括号阿拉伯数字：(1) (2)
    (
        r"^[（(]\d+[)）]",
        4,
        "括号数字标题",
    ),
    # 圆圈数字：①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳
    (
        r"^[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑰⑱⑲⑳]",
        4,
        "圆圈数字标题",
    ),

    # === Level 5: 项级标题 ===
    # 字母 + 句号/顿号/括号：a. b. c. / (a) (b)
    (
        r"^[a-z][\.、)]",
        5,
        "字母序号标题",
    ),
    (
        r"^[（(][a-z][)）]",
        5,
        "括号字母标题",
    ),
]

# 标题最大长度（字符）— 超过此值视为正文
MAX_HEADING_LENGTH = 50

# 明确非标题模式（段落以这些词开头不算标题）
NON_HEADING_PREFIXES: list[str] = [
    r"^注[：:意]",           # 注：注意：
    r"^备注[：:]",
    r"^说明[：:]",
    r"^例如[：:]",
    r"^示例[：:]",
    r"^其中[：:]",
    r"^特别[说提][：:]",     # 特别说明：特别提示：
    r"^包括[：:]",
    r"^如[：:下]",           # 如：如下
    r"^以上[：:]",
    r"^以下[：:]",
    r"^\d+[\.\d]*\s*[）)]",  # 1) 2) — 更可能是列表项
]


def _extract_heading_level(style_name: str) -> int:
    """从 Word 样式名提取层级，如 'Heading 2' → 2。"""
    m = re.search(r"(\d+)", style_name)
    if m:
        level = int(m.group(1))
        return min(level, 5)
    return 1


def is_heading(
    para_text: str,
    style_name: Optional[str] = None,
) -> tuple[bool, Optional[int]]:
    """判定一个段落是否为标题。

    Args:
        para_text: 段落文本
        style_name: python-docx 段落样式名，用于辅助判定：
            - 含 "Heading" → 直接判定为标题
            - 含 "List" / "Bullet" / "Enum" / "TOC" → 直接判定为非标题
            - None / "Normal" / 其他 → 走正则匹配

    Returns:
        (is_heading, level) — level 为 1-5，None 表示非标题

    判定流程（短路）:
        1. 空文本 → False
        2. 样式白名单（Heading） → True
        3. 样式黑名单（List） → False
        4. 非标题前缀 → False
        5. 长度超限 / 标点结尾 → False
        6. 正则匹配 → True + level / False
    """
    text = para_text.strip()
    if not text:
        return False, None

    # --- 样式白名单 ---
    if style_name and "heading" in style_name.lower():
        level = _extract_heading_level(style_name)
        return True, level

    # --- 样式黑名单 ---
    if style_name and any(
        keyword in style_name.lower()
        for keyword in ("list", "bullet", "enum", "toc")
    ):
        return False, None

    # --- 非标题前缀 ---
    for prefix_pattern in NON_HEADING_PREFIXES:
        if re.match(prefix_pattern, text):
            return False, None

    # --- 长度超限 ---
    if len(text) > MAX_HEADING_LENGTH:
        return False, None

    # --- 正文标点结尾（列表项特征） ---
    if text[-1] in ("；", "。", "，", "；"):
        return False, None

    # --- 正则匹配 ---
    for pattern, level, _name in HEADING_PATTERNS:
        if re.match(pattern, text):
            return True, level

    return False, None


# ============================================================
# 段落数据模型（Word 解析中间产物）
# ============================================================

@dataclass
class WordImage:
    """Word 文档中提取出的单张图片。"""

    image_bytes: bytes
    content_type: str
    object_key: str | None = None
    ocr_text: str = ""
    vision_text: str = ""
    image_index: int | None = None


@dataclass
class ParagraphItem:
    """Word 文档解析后的单个段落/表格项"""
    text: str
    is_heading: bool = False
    heading_level: Optional[int] = None
    is_table: bool = False
    images: list[WordImage] = field(default_factory=list)


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
    """PDF 解析：PyMuPDF 提取文本 + pdfplumber 提取表格 + 远程 OCR 识别图片文字。

    所有 CPU 密集型操作均通过 asyncio.to_thread() 放到线程池执行，
    避免阻塞 asyncio 事件循环导致前端页面无响应。
    """

    async def parse(self, file_path: Path, ocr_config: dict | None = None) -> list[LlamaDocument]:
        """异步解析入口：将整个同步解析逻辑交给线程池执行。"""
        logger.info("PDF 解析开始: %s", file_path.name)
        documents = await asyncio.to_thread(self._parse_sync, file_path, ocr_config)
        logger.info("PDF 解析完成: %s, pages=%d", file_path.name, len(documents))
        return documents

    def _parse_sync(self, file_path: Path, ocr_config: dict | None = None) -> list[LlamaDocument]:
        """同步解析核心逻辑（在线程池中执行，不阻塞事件循环）。"""
        import fitz  # PyMuPDF

        documents: list[LlamaDocument] = []

        # 一次性打开 pdfplumber（而非每页重复打开）
        tables_by_page = self._extract_all_tables(file_path)

        # 远程 OCR client（未配置则跳过图片识别）
        ocr_client = create_ocr(ocr_config) if ocr_config else None

        try:
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
                    ocr_text = self._ocr_images_sync(page, page_num, ocr_client)
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
        finally:
            if ocr_client is not None:
                ocr_client.close()

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

    def _ocr_images_sync(self, page: Any, page_num: int, ocr_client) -> str:
        """OCR 识别页面中的嵌入图片（同步方法，在线程池中执行）。ocr_client 为 None 时跳过。"""
        if ocr_client is None:
            return ""
        try:
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
                img_text = ocr_client.recognize(image_bytes)
                if img_text:
                    texts.append(f"[图片 {img_idx + 1}] {img_text}")
            return "\n".join(texts)
        except Exception as e:
            logger.warning("OCR 异常 (page %d): %s", page_num, e)
            return ""


# ============================================================
# DOCX 解析器
# ============================================================
class DocxParser(BaseParser):
    """Word (.docx) 解析：python-docx 提取段落和表格，输出 ParagraphItem 列表。

    不再拼接为单个 LlamaDocument，而是保留段落粒度，供 WordChunker
    进行结构感知分片。

    CPU 密集型操作通过 asyncio.to_thread() 放到线程池执行。
    """

    async def parse(self, file_path: Path) -> list[ParagraphItem]:
        """异步解析入口：将同步解析逻辑交给线程池执行。"""
        logger.info("DOCX 解析开始: %s", file_path.name)
        result = await asyncio.to_thread(self._parse_sync, file_path)
        logger.info(
            "DOCX 解析完成: %s, paragraphs=%d",
            file_path.name,
            len(result),
        )
        return result

    def _parse_sync(self, file_path: Path) -> list[ParagraphItem]:
        """同步解析核心逻辑（在线程池中执行，不阻塞事件循环）。

        按文档 body 的 XML 元素顺序遍历，段落和表格保持原始交错顺序。
        """
        from docx import Document as DocxDocument
        from docx.oxml.ns import qn

        doc = DocxDocument(file_path)
        items: list[ParagraphItem] = []

        # 按文档顺序遍历 body 中的每个块级元素
        for child in doc.element.body:
            tag = child.tag
            if tag == qn("w:p"):
                # 段落
                text = self._extract_paragraph_text(child)
                images = self._extract_images_from_element(child, doc)
                if not text and not images:
                    continue
                style_name = self._get_style_name(child, doc)
                heading, level = is_heading(text, style_name)
                items.append(ParagraphItem(
                    text=text,
                    is_heading=heading,
                    heading_level=level,
                    is_table=False,
                    images=images,
                ))
            elif tag == qn("w:tbl"):
                # 表格
                table_text = self._extract_table_text(child)
                images = self._extract_images_from_element(child, doc)
                if not table_text and not images:
                    continue
                items.append(ParagraphItem(
                    text=table_text,
                    is_heading=False,
                    heading_level=None,
                    is_table=True,
                    images=images,
                ))

        return items

    @staticmethod
    def _extract_images_from_element(element, doc) -> list[WordImage]:
        """从段落或表格 XML 中提取图片关系对应的图片字节。"""
        from docx.oxml.ns import qn

        images: list[WordImage] = []
        rels = doc.part.rels
        seen: set[str] = set()
        vml_imagedata_tag = "{urn:schemas-microsoft-com:vml}imagedata"

        def _add(rid: str | None) -> None:
            if not rid or rid in seen:
                return
            seen.add(rid)
            rel = rels.get(rid)
            if rel is None:
                return
            try:
                part = rel.target_part
                blob = part.blob
                content_type = getattr(part, "content_type", "application/octet-stream")
            except Exception:
                return
            images.append(WordImage(image_bytes=blob, content_type=content_type))

        for blip in element.iter(qn("a:blip")):
            _add(blip.get(qn("r:embed")))
        for imagedata in element.iter(vml_imagedata_tag):
            _add(imagedata.get(qn("r:id")))
        return images

    @staticmethod
    def _extract_paragraph_text(p_elem) -> str:
        """从 w:p XML 元素提取文本。"""
        texts: list[str] = []
        for t in p_elem.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"):
            if t.text:
                texts.append(t.text)
        return "".join(texts).strip()

    @staticmethod
    def _get_style_name(p_elem, doc) -> str | None:
        """从 w:p XML 元素获取样式名。"""
        from docx.oxml.ns import qn
        style_elem = p_elem.find(qn("w:pPr"))
        if style_elem is not None:
            style_id_elem = style_elem.find(qn("w:pStyle"))
            if style_id_elem is not None:
                style_id = style_id_elem.get(qn("w:val"))
                if style_id and doc.styles:
                    style = doc.styles[style_id]
                    return style.name if style else None
        return None

    @staticmethod
    def _extract_table_text(tbl_elem) -> str:
        """从 w:tbl XML 元素提取表格文本。"""
        rows: list[str] = []
        for tr in tbl_elem.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tr"):
            cells: list[str] = []
            for tc in tr.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tc"):
                cell_texts: list[str] = []
                for t in tc.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"):
                    if t.text:
                        cell_texts.append(t.text)
                cells.append("".join(cell_texts).strip())
            rows.append(" | ".join(cells))
        return "\n".join(rows)


# ============================================================
# DOC 解析器（.doc 旧格式 → LibreOffice 转换 → .docx）
# ============================================================
class DocParser(BaseParser):
    """Legacy .doc 解析器：LibreOffice 无头转换 → DocxParser。

    如果环境没有 LibreOffice，回退到 textract / antiword。
    """

    async def parse(self, file_path: Path) -> list[ParagraphItem]:
        """异步解析入口：.doc → .docx 转换后走 DocxParser。"""
        logger.info("DOC 解析开始: %s", file_path.name)
        docx_path = await asyncio.to_thread(self._convert_to_docx, file_path)
        if docx_path is None:
            raise ValueError(
                f"无法解析 .doc 文件: {file_path.name}。"
                f"请安装 LibreOffice（soffice）或 textract。"
            )

        try:
            parser = DocxParser()
            result = await parser.parse(docx_path)
            logger.info("DOC 解析完成: %s, paragraphs=%d", file_path.name, len(result))
            return result
        finally:
            # 清理临时 .docx
            if docx_path.exists():
                try:
                    docx_path.unlink()
                except OSError:
                    pass

    def _convert_to_docx(self, file_path: Path) -> Optional[Path]:
        """将 .doc 转换为 .docx，返回临时 .docx 路径。

        优先使用 LibreOffice，回退到 textract。
        """
        # 方案 1: LibreOffice 无头转换
        docx_path = self._convert_via_libreoffice(file_path)
        if docx_path is not None:
            return docx_path

        # 方案 2: textract（降级，仅提取文本）
        logger.debug("LibreOffice 不可用，回退到 textract")
        return self._convert_via_textract(file_path)

    @staticmethod
    def _convert_via_libreoffice(file_path: Path) -> Optional[Path]:
        """LibreOffice 无头转换 .doc → .docx。"""
        import subprocess
        import shutil

        soffice = shutil.which("soffice")
        if soffice is None:
            logger.debug("soffice 未找到，跳过 LibreOffice 转换")
            return None

        output_dir = file_path.parent / f"_convert_{file_path.stem}"
        output_dir.mkdir(exist_ok=True)

        try:
            result = subprocess.run(
                [soffice, "--headless", "--convert-to", "docx",
                 "--outdir", str(output_dir), str(file_path)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                logger.warning(
                    "LibreOffice 转换失败: returncode=%d, stderr=%s",
                    result.returncode, result.stderr,
                )
                return None

            docx_path = output_dir / (file_path.stem + ".docx")
            if docx_path.exists():
                return docx_path
            return None
        except FileNotFoundError:
            logger.debug("soffice 执行失败")
            return None
        except subprocess.TimeoutExpired:
            logger.warning("LibreOffice 转换超时: %s", file_path.name)
            return None

    @staticmethod
    def _convert_via_textract(file_path: Path) -> Optional[Path]:
        """textract 降级方案：提取纯文本写入临时 .docx。

        注意：textract 只提取文本，不保留格式和表格结构。
        """
        try:
            import textract
            import tempfile
            from docx import Document as DocxDocument

            text = textract.process(str(file_path)).decode("utf-8", errors="replace")
            if not text.strip():
                return None

            # 将纯文本写入一个最小 .docx，走 DocxParser 同一条链路
            tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
            doc = DocxDocument()
            for line in text.split("\n"):
                doc.add_paragraph(line)
            doc.save(tmp.name)
            return Path(tmp.name)
        except ImportError:
            logger.debug("textract 未安装")
            return None
        except Exception as e:
            logger.warning("textract 提取失败: %s", e)
            return None


# ============================================================
# Parser Registry（Strategy 注册表）
# ============================================================
PARSER_REGISTRY: dict[str, BaseParser] = {
    "pdf": PDFParser(),
    "docx": DocxParser(),
    "doc": DocParser(),
}


# ============================================================
# 统一入口
# ============================================================
async def parse_document(
    file_path: Path,
    file_type: str,
    ocr_config: dict | None = None,
) -> list[LlamaDocument] | list[ParagraphItem]:
    """解析文档，按文件类型分派到对应 Parser。

    Args:
        file_path: 上传文件路径
        file_type: 文件类型（不含点，如 'pdf', 'docx', 'doc'）
        ocr_config: 远程 OCR 模型配置（仅 PDF 使用；None 则跳过图片识别）

    Returns:
        - PDF: list[LlamaDocument]（每页一个）
        - DOCX/DOC: list[ParagraphItem]（段落粒度，供 WordChunker 消费）

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
    if isinstance(parser, PDFParser):
        return await parser.parse(file_path, ocr_config=ocr_config)
    return await parser.parse(file_path)
