"""
文档解析引擎 — Strategy 模式：PDF / DOCX / DOC 各自实现。
- Word（docx/doc）：段落粒度 ParagraphItem 列表，供 word_chunker 结构化分块；
- PDF：结构化版式提取（core/pdf_extractor，标题/段落/表格/图片重建）返回
  (ParagraphItem 列表, 物理页数)。

重要：所有 CPU 密集型同步操作（PyMuPDF、pdfplumber、python-docx、图片提取）
必须通过 asyncio.to_thread() 放到线程池执行，避免阻塞 asyncio 事件循环
导致前端请求无响应（"页面阻塞"）。
图片 OCR/vision 识别与 MinIO 上传由 services.image_recognition_service 在
逻辑块级统一处理（远程调用为网络 I/O，在线程池内以同步 httpx 阻塞线程而非事件循环）。
"""
import asyncio
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

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
    """文档解析后的单个段落/表格项（Word/PDF 复用）。

    page_number: 来源页码（Word 无页码概念不填；PDF 结构化解析每项填来源页）。
    """
    text: str
    is_heading: bool = False
    heading_level: Optional[int] = None
    is_table: bool = False
    images: list[WordImage] = field(default_factory=list)
    page_number: Optional[int] = None


# ============================================================
# Strategy 接口
# ============================================================
class BaseParser(ABC):
    """文档解析器抽象基类。"""

    @abstractmethod
    async def parse(self, file_path: Path, extract_images: bool = True):
        """解析文档（CPU 密集同步实现放线程池执行）。

        Returns:
            - Word（docx/doc）: list[ParagraphItem]（段落粒度）
            - PDF: tuple[list[ParagraphItem], int]（段落项 + 物理页数）
        """
        ...


# ============================================================
# PDF 解析器
# ============================================================
class PDFParser(BaseParser):
    """PDF 结构化解析：PyMuPDF + pdfplumber 版式提取 → ParagraphItem 列表。

    与 Word 走同一条分块链路：返回的 ParagraphItem 由 word_chunker 聚合分块，
    图片字节随项携带（MinIO 上传 / OCR / vision 由 image_recognition_service
    在块级处理，本解析器不做识别）。

    纯扫描件（整篇无文本层，如身份证扫描 PDF）抛 ValueError，由调用方给出友好提示。
    CPU 密集型操作均通过 asyncio.to_thread() 放到线程池执行。
    """

    async def parse(self, file_path: Path, extract_images: bool = True) -> tuple[list[ParagraphItem], int]:
        """异步解析入口：将整个同步解析逻辑交给线程池执行。

        Returns:
            (items, page_count)：段落项列表与物理页数（无文本层的扫描页跳过、不计入 items）。
        """
        logger.info("PDF 解析开始: %s (extract_images=%s)", file_path.name, extract_images)
        result = await asyncio.to_thread(self._parse_sync, file_path, extract_images)
        logger.info(
            "PDF 解析完成: %s, items=%d, pages=%d, skipped=%d",
            file_path.name,
            len(result.items),
            result.page_count,
            len(result.skipped_pages),
        )
        return result.items, result.page_count

    def _parse_sync(self, file_path: Path, extract_images: bool = True):
        """同步解析核心逻辑（在线程池中执行，不阻塞事件循环）。"""
        from core.pdf_extractor import extract_pdf_items

        result = extract_pdf_items(file_path, extract_images=extract_images)
        if result.page_count > 0 and not result.items:
            raise ValueError(
                "PDF 无可解析文本（疑似扫描件：未配置 OCR/图片识别模型，或页面确无内容）"
            )
        return result


# ============================================================
# DOCX 解析器
# ============================================================
class DocxParser(BaseParser):
    """Word (.docx) 解析：python-docx 提取段落和表格，输出 ParagraphItem 列表。

    不再拼接为单个 LlamaDocument，而是保留段落粒度，供 WordChunker
    进行结构感知分片。

    CPU 密集型操作通过 asyncio.to_thread() 放到线程池执行。
    """

    async def parse(self, file_path: Path, extract_images: bool = True) -> list[ParagraphItem]:
        """异步解析入口：将同步解析逻辑交给线程池执行。

        Args:
            file_path: .docx 文件路径
            extract_images: 是否提取文档内嵌图片（视觉模型未配置时传 False，完全跳过图片处理）
        """
        logger.info("DOCX 解析开始: %s (extract_images=%s)", file_path.name, extract_images)
        result = await asyncio.to_thread(self._parse_sync, file_path, extract_images)
        logger.info(
            "DOCX 解析完成: %s, paragraphs=%d",
            file_path.name,
            len(result),
        )
        return result

    def _parse_sync(self, file_path: Path, extract_images: bool = True) -> list[ParagraphItem]:
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
                images = (
                    self._extract_images_from_element(child, doc) if extract_images else []
                )
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
                images = (
                    self._extract_images_from_element(child, doc) if extract_images else []
                )
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

    async def parse(self, file_path: Path, extract_images: bool = True) -> list[ParagraphItem]:
        """异步解析入口：.doc → .docx 转换后走 DocxParser。

        Args:
            file_path: .doc 文件路径
            extract_images: 是否提取文档内嵌图片（透传给 DocxParser）
        """
        logger.info("DOC 解析开始: %s (extract_images=%s)", file_path.name, extract_images)
        docx_path = await asyncio.to_thread(self._convert_to_docx, file_path)
        if docx_path is None:
            raise ValueError(
                f"无法解析 .doc 文件: {file_path.name}。"
                f"请安装 LibreOffice（soffice）或 textract。"
            )

        try:
            parser = DocxParser()
            result = await parser.parse(docx_path, extract_images=extract_images)
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
    extract_images: bool = True,
) -> list[ParagraphItem] | tuple[list[ParagraphItem], int]:
    """解析文档，按文件类型分派到对应 Parser。

    Args:
        file_path: 上传文件路径
        file_type: 文件类型（不含点，如 'pdf', 'docx', 'doc'）
        extract_images: 是否提取文档内嵌图片（OCR/vision 均未配置时传 False，
            完全跳过图片提取与后续识别；docx/doc/pdf 通用）

    Returns:
        - PDF: tuple[list[ParagraphItem], int]（段落项 + 物理页数）
        - DOCX/DOC: list[ParagraphItem]（段落粒度，供 word_chunker 结构化分块）

    Raises:
        ValueError: 不支持的文件类型；PDF 整篇无文本层（疑似扫描件）
    """
    parser = PARSER_REGISTRY.get(file_type.lower())
    if parser is None:
        raise ValueError(f"Unsupported file type: {file_type}")

    logger.info(
        "parse_document: file=%s, type=%s → parser=%s (extract_images=%s)",
        file_path.name,
        file_type,
        type(parser).__name__,
        extract_images,
    )
    return await parser.parse(file_path, extract_images=extract_images)
