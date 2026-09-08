"""
PDF 版式提取 — 结构化解析为 ParagraphItem 列表（对齐 Word 分块链路）。

目标：把 PDF（文本型）解析成与 Word 一致的段落粒度中间产物，供
`word_chunker.aggregate_paragraphs` 聚合后分块。与旧"每页拼一个大文本"不同，
本模块按版面重建结构：

    1. PyMuPDF 提取每页文本行（保留字号/加粗/bbox 与页码）；
    2. pdfplumber 提取表格区域（bbox + 行列文本）；
    3. 过滤页眉/页脚/页码、表格区域内文本行（防与表格项重复）、装饰图片/水印；
    4. 文本行按纵向间距聚段，单行段做标题判定（正则主判据 + 字号/加粗补充）；
    5. 图片字节提取后归位到所在段落/表格（其 OCR/vision 识别由调用方在块级完成）；
    6. 无文本层页面（扫描件/纯图页）跳过，不计内容。

说明：
- 本模块为同步 CPU 密集代码，调用方必须经 asyncio.to_thread() 执行（仓库约定）。
- 双栏文档按"自上而下、左到右"处理，跨页块取块内首项页码（Word/引用口径一致）。
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.parser import NON_HEADING_PREFIXES, ParagraphItem, WordImage, is_heading
from utils.logging import get_logger

logger = get_logger(__name__)


# ============================================================
# 版式常量（启发式，按真实样本可调）
# ============================================================

# 页眉/页脚条带：顶部 y1 < H*TOP_BAND_RATIO，底部 y0 > H*BOTTOM_BAND_RATIO
TOP_BAND_RATIO = 0.075
BOTTOM_BAND_RATIO = 0.93
# 条带内文本在同一条带重复 ≥ 此页数 → 判为页眉/页脚丢弃
BAND_REPEAT_MIN_PAGES = 2
# 纯页码行（底部条带）无条件丢弃
PAGE_NUMBER_RE = r"^\s*\d+\s*$"

# 聚段：行间距 > 行高中位数 × 此系数 → 换段
PARA_GAP_FACTOR = 0.8
# 聚段（内部直连标题前不需要大间距，正则命中即换段）

# 标题补充判据（未命中 is_heading 正则时）
FALLBACK_HEADING_MAX_LEN = 40
FALLBACK_HEADING_SIZE_RATIO = 1.25
FALLBACK_LEVEL_SIZE_RATIO_1 = 1.6
FALLBACK_LEVEL_SIZE_RATIO_2 = 1.3
# 补充黑名单：图/表题注、目录 行（PDF 特有，Word 无样式场景不含）
PDF_NON_HEADING_PREFIXES: list[str] = [
    r"^(?:图|表)\s*[\d一二三四五六七八九十百]+",
    r"^目\s*录",
]

# 图片过滤
MIN_IMAGE_AREA = 1200.0            # 小于该面积（pt²）视为图标/装饰
DECOR_REPEAT_PAGES = 3             # 同一 xref 出现页数 ≥ 此值且小面积 → 装饰
DECOR_MAX_AREA_RATIO = 0.05        # 装饰判定的单次最大面积占比上限
WATERMARK_COVER_RATIO = 0.5        # 单次面积占比 ≥ 此值视为可能水印/底图
WATERMARK_PAGE_FRACTION = 0.8      # 贯穿 ≥ 总页数×此比例 且大面积 → 水印/底图
IMAGE_BAND_PAD = 1.0               # 图片是否落在页眉/页脚条带的容差

# 扫描页（无文本层）整页 OCR：图片覆盖页面积 ≥ 此比例才视为“整页扫描候选”
# （防止把纯 logo/装饰/局部插图的页当扫描件整页 OCR）
# 无文本层页“整页扫描图”的最小覆盖比：≥ 页面积 × 该值才当作扫描页 OCR 候选。
# 注意保持宽松（0.1）：证件扫描图常只占页面 ~20%（如 身份证.pdf 两张 ~19.7%~20.0%），
# 0.2 会因“< 页面积×0.2”被严格排除 → 整页被当作无可解析内容而失败（2026-09-08 修复）。
SCAN_IMAGE_MIN_COVER = 0.1

# 表格区域文本去重容差（pt，pdfplumber bbox 外扩）
TABLE_ZONE_PAD = 3.0
# 图片归位到段落/表格的纵向容差
ATTACH_PAD = 4.0

# 聚段时也按此条换段的标题预检（复用 Word 正则的廉价预筛）
HEADING_TERMINAL_PUNCT = ("。", "，", "；", "：", "、", "！", "？")


# ============================================================
# 中间数据结构（纯函数单测友好）
# ============================================================

@dataclass
class PdfLine:
    """单条文本行（保留版式特征，供聚段/标题判定）。"""
    text: str
    size: float
    bold: bool
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1
    page_number: int

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def height(self) -> float:
        return max(self.bbox[3] - self.bbox[1], 0.1)


@dataclass
class PdfParagraph:
    """聚段结果（可能多行；is_heading 仅单行段可命中）。"""
    text: str
    bbox: tuple[float, float, float, float]
    size: float
    bold: bool
    single_line: bool
    lines: list[PdfLine] = field(default_factory=list)


@dataclass
class PdfTableZone:
    """pdfplumber 识别出的表格区域。"""
    page_number: int
    bbox: tuple[float, float, float, float]
    rows: list[list[Optional[str]]]


@dataclass
class PdfImageZone:
    """页面可见图片（已提取字节）。"""
    page_number: int
    xref: int
    bbox: tuple[float, float, float, float]
    image: WordImage


@dataclass
class PdfParseResult:
    """解析结果：items 供聚合分块；page_count 物理页数；skipped_pages 无文本层页。"""
    items: list[ParagraphItem]
    page_count: int
    skipped_pages: list[int]


# ============================================================
# 文本工具
# ============================================================

def _is_cjk(ch: str) -> bool:
    """中日韩统一表意文字 + 常用全角符号（用于行间拼接是否补空格）。"""
    if "\u4e00" <= ch <= "\u9fff":
        return True
    return ch in "，。；：、！？（）《》「」『』“”‘’…—·"


_CJK_CLOSING = "）】》」』”’"


def _needs_space(prev: str, nxt: str) -> bool:
    """判断行/span 拼接时是否应插入空格。

    相邻两段文本以 ASCII 字符收尾/开头（英文词）→ 补空格；
    从全角闭括号直接进入 ASCII（"）test"）→ 补空格；其余（CJK 相邻等）不补。
    """
    if prev.isspace() or nxt.isspace():
        return False
    ascii_like = lambda c: ord(c) < 128 and not c.isspace()  # noqa: E731
    if ascii_like(prev) and ascii_like(nxt):
        return True
    if prev in _CJK_CLOSING and not _is_cjk(nxt):
        return True
    return False


def join_texts(texts: list[str]) -> str:
    """把若干行/span 文本按语言习惯拼接（CJK 直接续接，ASCII 两侧补空格）。"""
    result = ""
    for t in texts:
        t = t or ""
        if result and t and _needs_space(result[-1], t[0]):
            result += " "
        result += t
    return result.strip()


def _line_from_spans(spans: list[dict]) -> tuple[str, float, bool]:
    """从 PyMuPDF span 列表拼出 (text, 字号, 是否加粗)。"""
    text = join_texts([s.get("text", "") for s in spans])
    if not spans:
        return text, 0.0, False
    # 字号取字符加权众数，加粗取字符占比过半
    weight: dict[float, int] = {}
    bold_chars = 0
    total = 0
    for s in spans:
        size = float(s.get("size", 0.0) or 0.0)
        n = len(s.get("text", ""))
        weight[size] = weight.get(size, 0) + n
        total += n
        font = (s.get("font") or "").lower()
        is_bold = bool(s.get("flags", 0) & 16) or any(
            kw in font for kw in ("bold", "heavy", "black", "semibold")
        )
        if is_bold:
            bold_chars += n
    size = max(weight, key=lambda k: weight[k]) if weight else 0.0
    return text, size, total > 0 and bold_chars * 2 >= total


# ============================================================
# 文档级统计与过滤
# ============================================================

def _body_size_of(lines: list[PdfLine]) -> Optional[float]:
    """正文基准字号 = 文本行字号（按字符数加权）众数。"""
    weight: dict[float, int] = {}
    for line in lines:
        weight[line.size] = weight.get(line.size, 0) + len(line.text)
    if not weight:
        return None
    return max(weight, key=lambda k: weight[k])


def _band_of(line: PdfLine, page_h: float) -> Optional[str]:
    """行所在条带：'top' / 'bottom' / None。"""
    if line.y1 < page_h * TOP_BAND_RATIO:
        return "top"
    if line.y0 > page_h * BOTTOM_BAND_RATIO:
        return "bottom"
    return None


def _band_key(text: str) -> str:
    """条带重复判定的归一化键：去数字/空白/标点差异（页眉页码变化可归并）。"""
    return re.sub(r"[\d\s\u3000]", "", text)


def _drop_band_keys(lines_by_page: dict[int, list[PdfLine]], page_h_by_page: dict[int, float]) -> set[str]:
    """找出条带内跨 ≥2 页重复出现的文本组键（页眉/页脚）。"""
    seen: dict[str, set[int]] = {}
    for page_no, lines in lines_by_page.items():
        h = page_h_by_page.get(page_no, 1.0)
        for line in lines:
            band = _band_of(line, h)
            if band is None:
                continue
            if band == "bottom" and re.match(PAGE_NUMBER_RE, line.text):
                continue  # 纯页码单独规则处理
            key = _band_key(line.text)
            if not key:
                continue
            seen.setdefault(key, set()).add(page_no)
    return {k for k, pages in seen.items() if len(pages) >= BAND_REPEAT_MIN_PAGES}


def _keep_line(line: PdfLine, page_h: float, drop_band_keys: set[str]) -> bool:
    """页眉/页脚/纯页码过滤。"""
    band = _band_of(line, page_h)
    if band is None:
        return True
    if band == "bottom" and re.match(PAGE_NUMBER_RE, line.text):
        return False
    if _band_key(line.text) in drop_band_keys:
        return False
    return True


def _estimate_body_size(raw_by_page: dict[int, list[PdfLine]]) -> Optional[float]:
    all_lines = [l for lines in raw_by_page.values() for l in lines]
    return _body_size_of(all_lines)


# ============================================================
# 表格与文本行去重
# ============================================================

def _table_text(rows: list[list[Optional[str]]]) -> str:
    """把 pdfplumber 行列转为与 Word 表格一致的文本块。"""
    out: list[str] = []
    for row in rows:
        if not row:
            continue
        cells = [str(c).replace("\n", " ").strip() if c else "" for c in row]
        if not any(cells):
            continue
        out.append(" | ".join(cells))
    return "\n".join(out)


def _inside_zone(bbox: tuple[float, float, float, float], zone_bbox: tuple[float, float, float, float], pad: float = TABLE_ZONE_PAD) -> bool:
    """行中心是否落在表格区域内（容差 pad）。"""
    x0, y0, x1, y1 = bbox
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    zx0, zy0, zx1, zy1 = zone_bbox
    return (zx0 - pad) <= cx <= (zx1 + pad) and (zy0 - pad) <= cy <= (zy1 + pad)


# ============================================================
# 聚段
# ============================================================

def _is_heading_text(text: str) -> bool:
    """正则标题预检（不做排版补充判据）：供聚段换段与最终标题判定使用。"""
    return is_heading(text, None)[0]


def _group_paragraphs(lines: list[PdfLine], line_height: float) -> list[list[PdfLine]]:
    """按纵向间距把文本行聚合为段落；行本身命中标题正则也强制换段。"""
    paragraphs: list[list[PdfLine]] = []
    current: list[PdfLine] = []
    gap_threshold = line_height * PARA_GAP_FACTOR
    for line in lines:
        if current:
            gap = line.y0 - current[-1].y1
            if gap > gap_threshold or _is_heading_text(line.text):
                paragraphs.append(current)
                current = []
        current.append(line)
    if current:
        paragraphs.append(current)
    return paragraphs


def _paragraph_from_lines(lines: list[PdfLine]) -> PdfParagraph:
    first = lines[0]
    last = lines[-1]
    text = join_texts([l.text for l in lines])
    return PdfParagraph(
        text=text,
        bbox=(first.bbox[0], first.y0, max(l.bbox[2] for l in lines), last.y1),
        size=first.size,
        bold=first.bold,
        single_line=len(lines) == 1,
        lines=lines,
    )


# ============================================================
# 标题判定
# ============================================================

def pdf_heading_decision(
    text: str,
    size: float | None,
    bold: bool,
    body_size: float | None,
) -> tuple[bool, Optional[int]]:
    """PDF 行/段标题判定。

    1) 主判据：is_heading(text) —— 与 Word 同一套中文标题正则 + 黑名单/长度/标点规则；
    2) 补充（仅未命中主判据时）：短文本 + 无结尾标点 + 非黑名单（含图/表题注）
       且（字号 ≥ body×1.25 或加粗）→ 标题；level 按字号档 1/2/3（仅加粗 → 3）。
    """
    text = text.strip()
    if not text:
        return False, None

    heading, level = is_heading(text, None)
    if heading:
        return True, level

    # --- 补充判据 ---
    if len(text) > FALLBACK_HEADING_MAX_LEN:
        return False, None
    if text[-1] in HEADING_TERMINAL_PUNCT:
        return False, None
    for pattern in NON_HEADING_PREFIXES + PDF_NON_HEADING_PREFIXES:
        if re.match(pattern, text):
            return False, None

    size_ratio = None
    if body_size and size:
        size_ratio = size / body_size
    size_ok = size_ratio is not None and size_ratio >= FALLBACK_HEADING_SIZE_RATIO
    if not (size_ok or bold):
        return False, None

    if size_ratio and size_ratio >= FALLBACK_LEVEL_SIZE_RATIO_1:
        return True, 1
    if size_ratio and size_ratio >= FALLBACK_LEVEL_SIZE_RATIO_2:
        return True, 2
    return True, 3


# ============================================================
# 图片提取与过滤
# ============================================================

def _image_area(bbox: tuple[float, float, float, float]) -> float:
    return max(bbox[2] - bbox[0], 0) * max(bbox[3] - bbox[1], 0)


def _image_zone_bbox(page, xref: int) -> Optional[tuple[float, float, float, float]]:
    """返回 xref 在本页最大绘制区域；无可见绘制返回 None。"""
    rects = page.get_image_rects(xref)
    if not rects:
        return None
    # 同一图片多次平铺/重复：取面积最大的一次
    best = max(rects, key=lambda r: (r.x1 - r.x0) * (r.y1 - r.y0))
    return (best.x0, best.y0, best.x1, best.y1)


def _collect_image_stats(doc, page_count: int) -> tuple[set[int], set[int], set[int]]:
    """一遍扫描得到装饰/水印/条带图片 xref 集合。"""
    drawn_pages: dict[int, set[int]] = {}
    min_ratio: dict[int, float] = {}
    max_ratio: dict[int, float] = {}
    band_pages: dict[int, set[int]] = {}

    for page_no, page in enumerate(doc, start=1):
        h = page.rect.height
        for xref in {img[0] for img in page.get_images(full=True)}:
            bbox = _image_zone_bbox(page, xref)
            if bbox is None:
                continue
            page_area = page.rect.width * page.rect.height
            ratio = _image_area(bbox) / page_area if page_area else 0.0
            drawn_pages.setdefault(xref, set()).add(page_no)
            min_ratio[xref] = min(min_ratio.get(xref, 1.0), ratio)
            max_ratio[xref] = max(max_ratio.get(xref, 0.0), ratio)
            if bbox[1] < h * TOP_BAND_RATIO * (1 + IMAGE_BAND_PAD) or bbox[3] > h * BOTTOM_BAND_RATIO:
                band_pages.setdefault(xref, set()).add(page_no)

    decor: set[int] = set()
    watermark: set[int] = set()
    band_images: set[int] = set()
    for xref, pages in drawn_pages.items():
        if len(pages) >= DECOR_REPEAT_PAGES and max_ratio[xref] <= DECOR_MAX_AREA_RATIO:
            decor.add(xref)
        if (
            page_count >= 3
            and len(pages) >= max(3, page_count * WATERMARK_PAGE_FRACTION)
            and min_ratio[xref] >= WATERMARK_COVER_RATIO
        ):
            watermark.add(xref)
        if len(band_pages.get(xref, set())) >= BAND_REPEAT_MIN_PAGES:
            band_images.add(xref)
    return decor, watermark, band_images


# ============================================================
# 页面元素落序与图片归位
# ============================================================

def _attach_image(
    image: PdfImageZone,
    paras: list[PdfParagraph],
    tables: list[tuple[PdfTableZone, ParagraphItem]],
) -> Optional[ParagraphItem]:
    """把图片挂到所在段落/表格项：优先垂直范围覆盖中心的容器，否则挂上方最近容器。"""
    cy = (image.bbox[1] + image.bbox[3]) / 2
    owners: list[tuple[float, float, ParagraphItem]] = []

    def add_owner(y0: float, y1: float, item: ParagraphItem) -> None:
        owners.append((y0, y1, item))

    for p in paras:
        add_owner(p.bbox[1], p.bbox[3], _para_item(p))
    for zone, item in tables:
        add_owner(zone.bbox[1], zone.bbox[3], item)

    if not owners:
        return None

    # 1) 中心落在某容器纵向范围（含容差）
    for y0, y1, item in owners:
        if y0 - ATTACH_PAD <= cy <= y1 + ATTACH_PAD:
            return item
    # 2) 上方最近容器（图随上文）
    above = [o for o in owners if o[1] < cy]
    if above:
        return max(above, key=lambda o: o[1])[2]
    # 3) 全部在其下方 → 挂第一个容器
    return min(owners, key=lambda o: o[0])[2]


def _para_item(p: PdfParagraph) -> ParagraphItem:
    """构造段落项（缓存避免重复构建）。"""
    item = getattr(p, "_item", None)
    if item is None:
        if p.single_line:
            heading, level = pdf_heading_decision(p.text, p.size, p.bold, getattr(p, "body_size", None))
        else:
            heading, level = False, None
        item = ParagraphItem(
            text=p.text,
            is_heading=heading,
            heading_level=level,
            page_number=p.lines[0].page_number,
        )
        p._item = item  # type: ignore[attr-defined]
    return item


# ============================================================
# PyMuPDF / pdfplumber 底层采集
# ============================================================

def _collect_page_lines(page, page_no: int) -> list[PdfLine]:
    """从页面 dict 文本块提取所有可见文本行。"""
    lines: list[PdfLine] = []
    try:
        data = page.get_text("dict")
    except Exception as e:
        logger.debug("get_text(dict) 失败 page=%d: %s", page_no, e)
        return lines
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text, size, bold = _line_from_spans(spans)
            if not text:
                continue
            bbox = tuple(float(v) for v in line.get("bbox", (0, 0, 0, 0)))
            lines.append(PdfLine(text=text, size=size, bold=bold, bbox=bbox, page_number=page_no))
    return lines


def _extract_tables(file_path: Path, page_count: int) -> dict[int, list[PdfTableZone]]:
    """一次性用 pdfplumber 提取全文表格区域（bbox + 行列文本）。"""
    result: dict[int, list[PdfTableZone]] = {}
    try:
        import pdfplumber

        with pdfplumber.open(file_path) as pdf:
            for page_no in range(1, page_count + 1):
                try:
                    p = pdf.pages[page_no - 1]
                    zones: list[PdfTableZone] = []
                    for table in p.find_tables():
                        try:
                            rows = table.extract()
                        except Exception:
                            rows = []
                        if not rows:
                            continue
                        bbox = tuple(float(v) for v in table.bbox)
                        zones.append(PdfTableZone(page_number=page_no, bbox=bbox, rows=rows))
                    if zones:
                        result[page_no] = zones
                except Exception as e:
                    logger.warning("pdfplumber 表格提取异常 page=%d: %s", page_no, e)
    except ImportError:
        logger.debug("pdfplumber 未安装，跳过表格识别")
    except Exception as e:
        logger.warning("pdfplumber 打开失败: %s", e)
    return result


# ============================================================
# 主入口
# ============================================================

def extract_pdf_items(file_path: Path, extract_images: bool = True) -> PdfParseResult:
    """结构化解析 PDF，返回段落/表格项 + 物理页数 + 无文本层页列表。"""
    import fitz  # PyMuPDF

    doc = fitz.open(file_path)
    page_count = doc.page_count
    if page_count == 0:
        doc.close()
        return PdfParseResult(items=[], page_count=0, skipped_pages=[])

    # 一遍扫描收集全部文本行与页面高度
    raw_by_page: dict[int, list[PdfLine]] = {}
    page_h: dict[int, float] = {}
    for page_no, page in enumerate(doc, start=1):
        raw_by_page[page_no] = _collect_page_lines(page, page_no)
        page_h[page_no] = page.rect.height
    drop_band_keys = _drop_band_keys(raw_by_page, page_h)
    body_size = _estimate_body_size(raw_by_page)
    decor_xrefs, watermark_xrefs, band_image_xrefs = _collect_image_stats(doc, page_count)
    tables_by_page = _extract_tables(file_path, page_count)

    items: list[ParagraphItem] = []
    skipped_pages: list[int] = []
    try:
        for page_no in range(1, page_count + 1):
            page = doc.load_page(page_no - 1)
            logger.info("PDF 正在解析第 %d/%d 页", page_no, page_count)
            page_items = _parse_page(
                page=page,
                page_no=page_no,
                lines=raw_by_page[page_no],
                page_h=page_h[page_no],
                drop_band_keys=drop_band_keys,
                body_size=body_size,
                zones=tables_by_page.get(page_no, []),
                decor_xrefs=decor_xrefs,
                watermark_xrefs=watermark_xrefs,
                band_image_xrefs=band_image_xrefs,
                extract_images=extract_images,
            )
            if not page_items:
                skipped_pages.append(page_no)
            else:
                items.extend(page_items)
    finally:
        doc.close()

    logger.info(
        "PDF 结构化解析完成: pages=%d, items=%d, skipped(scanned/blank)=%d",
        page_count, len(items), len(skipped_pages),
    )
    return PdfParseResult(items=items, page_count=page_count, skipped_pages=skipped_pages)


def _scan_page_images(
    page,
    page_no: int,
    page_area: float,
    decor_xrefs: set[int],
    watermark_xrefs: set[int],
    band_image_xrefs: set[int],
) -> list[WordImage]:
    """无文本层页：收集覆盖面积 ≥ 页面积 × SCAN_IMAGE_MIN_COVER 的整页图字节。

    典型场景是扫描页（整页就是一张图片，通常每页 1 张）；过滤装饰/水印/条带图片，
    空白页/纯 logo 页返回空列表（不 OCR）。
    """
    candidates: list[WordImage] = []
    for xref in {img[0] for img in page.get_images(full=True)}:
        if xref in decor_xrefs or xref in watermark_xrefs or xref in band_image_xrefs:
            continue
        bbox = _image_zone_bbox(page, xref)
        if bbox is None:
            continue
        if _image_area(bbox) < page_area * SCAN_IMAGE_MIN_COVER:
            continue
        base = page.parent.extract_image(xref)
        if base is None:
            continue
        image_bytes = base.get("image")
        if not image_bytes:
            continue
        candidates.append(
            WordImage(image_bytes=image_bytes, content_type=f"image/{base.get('ext', 'png')}")
        )
    if candidates:
        logger.info(
            "扫描页整页 OCR 候选: page=%d, images=%d", page_no, len(candidates)
        )
    return candidates


def _parse_page(
    page,
    page_no: int,
    lines: list[PdfLine],
    page_h: float,
    drop_band_keys: set[str],
    body_size: Optional[float],
    zones: list[PdfTableZone],
    decor_xrefs: set[int],
    watermark_xrefs: set[int],
    band_image_xrefs: set[int],
    extract_images: bool,
) -> list[ParagraphItem]:
    """单页：去重 → 聚段 → 标题 → 表格/图片归位 → 按版面落序。"""
    # 1. 条带过滤 + 表格区域文本去重（按版面自上而下排序后聚段）
    kept = [l for l in lines if _keep_line(l, page_h, drop_band_keys)]
    outside = [l for l in kept if not any(_inside_zone(l.bbox, z.bbox) for z in zones)]
    outside = sorted(outside, key=lambda l: (l.y0, l.bbox[0]))

    # 2. 段落（行高中位数做聚段阈值）
    paras: list[PdfParagraph] = []
    if outside:
        heights = [l.height for l in outside]
        line_height = sorted(heights)[len(heights) // 2] if heights else 12.0
        for group in _group_paragraphs(outside, line_height):
            p = _paragraph_from_lines(group)
            p.body_size = body_size  # type: ignore[attr-defined]
            paras.append(p)

    # 3. 表格项（文本 + bbox 供落序/图片归位）
    table_items: list[tuple[PdfTableZone, ParagraphItem]] = []
    for zone in sorted(zones, key=lambda z: z.bbox[1]):
        text = _table_text(zone.rows)
        if not text.strip():
            continue
        item = ParagraphItem(
            text=text,
            is_table=True,
            page_number=page_no,
        )
        table_items.append((zone, item))

    # 无文本层判定：无正文段、无表格
    if not paras and not table_items:
        # 整页扫描 → 空文本段落 + 整页图 WordImage，OCR 由块级 process_word_images 完成；
        # 未配置识别模型（extract_images=False）或确无整页图（空白页）→ 维持跳过
        if not extract_images:
            return []
        page_area = page.rect.width * page.rect.height
        scan_images = _scan_page_images(
            page, page_no, page_area,
            decor_xrefs, watermark_xrefs, band_image_xrefs,
        )
        if not scan_images:
            return []
        return [
            ParagraphItem(text="", page_number=page_no, images=[img])
            for img in scan_images
        ]

    # 4. 图片：过滤可见性/装饰/水印/条带 → 提取字节 → 归位
    if extract_images:
        try:
            for xref in {img[0] for img in page.get_images(full=True)}:
                if xref in decor_xrefs or xref in watermark_xrefs or xref in band_image_xrefs:
                    continue
                bbox = _image_zone_bbox(page, xref)
                if bbox is None:
                    continue
                if _image_area(bbox) < MIN_IMAGE_AREA:
                    continue
                base = page.parent.extract_image(xref)
                if base is None:
                    continue
                image_bytes = base.get("image")
                content_type = base.get("ext", "png")
                if not image_bytes:
                    continue
                image_zone = PdfImageZone(
                    page_number=page_no,
                    xref=xref,
                    bbox=bbox,
                    image=WordImage(image_bytes=image_bytes, content_type=f"image/{content_type}"),
                )
                owner = _attach_image(image_zone, paras, table_items)
                if owner is None:
                    continue
                owner.images.append(image_zone.image)
        except Exception as e:
            logger.warning("PDF 图片提取异常 page=%d: %s", page_no, e)

    # 5. 按版面落序（段落 y0 / 表格 y0），构建输出项
    page_items: list[ParagraphItem] = []
    for p in sorted(paras, key=lambda p: (p.bbox[1], p.bbox[0])):
        page_items.append(_para_item(p))
    for zone, item in sorted(table_items, key=lambda t: t[0].bbox[1]):
        page_items.append(item)

    # 附注：图片已归位到所属段/表格项（图片不单独成项，与 Word 一致）
    return page_items
