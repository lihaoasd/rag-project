"""
PDF 结构化解析（core.pdf_extractor）测试：纯函数 + 合成 PDF 集成。

不依赖外部服务；PDF 用 PyMuPDF 内置 CJK 字体（china-s）现场生成。
"""
import uuid
from pathlib import Path

import fitz
import pytest

from core.pdf_extractor import (
    PdfImageZone,
    PdfLine,
    PdfParagraph,
    PdfTableZone,
    _attach_image,
    _inside_zone,
    _needs_space,
    extract_pdf_items,
    join_texts,
    pdf_heading_decision,
)
from core.parser import ParagraphItem, WordImage
from core.word_chunker import aggregate_paragraphs, word_chunk_blocks

# 测试 PDF 写到 backend/temp（可写目录），避免依赖系统临时目录
_BACKEND_TMP = Path(__file__).resolve().parents[1] / "temp"


def _new_pdf_path() -> Path:
    _BACKEND_TMP.mkdir(exist_ok=True)
    return _BACKEND_TMP / f"pdf_test_{uuid.uuid4().hex}.pdf"


# ============================================================
# 纯函数：拼接/表格区域/标题判定
# ============================================================

class TestJoinTexts:
    def test_cjk_adjacent_no_space(self):
        assert join_texts(["第一章", "招标公告"]) == "第一章招标公告"

    def test_ascii_adjacent_space(self):
        assert join_texts(["Hello", "world"]) == "Hello world"

    def test_cjk_then_latin_after_close_paren(self):
        assert join_texts(["（见", "附件A）", "test"]) == "（见附件A） test"

    def test_needs_space_rules(self):
        assert _needs_space("a", "b") is True
        assert _needs_space("章", "节") is False
        assert _needs_space("）", "x") is True
        assert _needs_space("节", "一") is False


class TestInsideZone:
    def test_center_inside(self):
        assert _inside_zone((100, 100, 200, 120), (90, 90, 210, 130)) is True

    def test_center_outside(self):
        assert _inside_zone((50, 50, 60, 60), (90, 90, 210, 130)) is False

    def test_near_boundary_with_pad(self):
        # 中心落在表格 bbox 外扩容差内 → 判为表格内文本
        assert _inside_zone((100, 96, 200, 101), (100, 100, 300, 200)) is True   # 中心 98.5，距上沿 3 内
        assert _inside_zone((100, 194, 200, 199), (100, 100, 300, 200)) is True  # 中心 196.5，距下沿 3 内

    def test_beyond_pad_outside(self):
        assert _inside_zone((100, 92, 200, 96), (100, 100, 300, 200)) is False  # 中心 94 < 97（容差外）


class TestPdfHeadingDecision:
    def test_chapter_regex_primary(self):
        # 主判据命中即返回，字号大小无关
        assert pdf_heading_decision("第一章 招标公告", 12.0, False, 12.0) == (True, 1)

    def test_multi_level_regex(self):
        assert pdf_heading_decision("1.1 项目概况", 12.0, False, 12.0) == (True, 3)

    def test_list_item_with_semicolon_not_heading(self):
        # （10）…；以分号结尾 → 列表项，非标题
        assert pdf_heading_decision("（10）职业健康安全管理体系认证证书；", 12.0, False, 12.0) == (False, None)

    def test_figure_caption_not_heading_even_bold(self):
        assert pdf_heading_decision("图1-1 系统架构图", 12.0, True, 12.0) == (False, None)

    def test_table_caption_not_heading_even_large(self):
        assert pdf_heading_decision("表3-1 资格要求", 24.0, False, 12.0) == (False, None)

    def test_toc_line_not_heading(self):
        assert pdf_heading_decision("目 录", 16.0, False, 12.0) == (False, None)

    def test_large_font_fallback_level1(self):
        assert pdf_heading_decision("项目概况", 20.0, False, 12.0) == (True, 1)

    def test_medium_font_fallback_level2(self):
        assert pdf_heading_decision("项目概况", 18.0, False, 12.0) == (True, 2)

    def test_bold_only_fallback_level3(self):
        assert pdf_heading_decision("项目概况", 13.0, True, 12.0) == (True, 3)

    def test_normal_body_not_heading(self):
        assert pdf_heading_decision("本项目位于杭州市，服务期三年。", 12.0, False, 12.0) == (False, None)

    def test_long_line_not_heading(self):
        long_text = "项目概况" * 30
        assert pdf_heading_decision(long_text, 20.0, True, 12.0) == (False, None)

    def test_empty_text_not_heading(self):
        assert pdf_heading_decision("", 20.0, True, 12.0) == (False, None)


# ============================================================
# 纯函数：图片归位
# ============================================================

def _fake_para(text: str, y0: float, y1: float, page: int = 1) -> PdfParagraph:
    line = PdfLine(text=text, size=12.0, bold=False, bbox=(50, y0, 300, y1), page_number=page)
    return PdfParagraph(text=text, bbox=(50, y0, 300, y1), size=12.0, bold=False,
                        single_line=True, lines=[line])


def _fake_image(cy: float, page: int = 1) -> PdfImageZone:
    return PdfImageZone(
        page_number=page,
        xref=99,
        bbox=(100, cy - 10, 200, cy + 10),
        image=WordImage(image_bytes=b"png", content_type="image/png"),
    )


class TestAttachImage:
    def test_center_inside_paragraph(self):
        p = _fake_para("正文段落", 100, 200)
        img = _fake_image(cy=150)
        owner = _attach_image(img, [p], [])
        assert owner is not None
        assert owner.text == "正文段落"
        owner.images.append(img.image)

    def test_below_paragraph_attaches_above(self):
        above = _fake_para("上文", 100, 130)
        below = _fake_para("下文", 300, 330)
        img = _fake_image(cy=200)  # 两段之间空隙
        owner = _attach_image(img, [above, below], [])
        assert owner is not None
        assert owner.text == "上文"

    def test_inside_table_zone(self):
        para = _fake_para("正文", 400, 500)
        zone = PdfTableZone(page_number=1, bbox=(100, 100, 300, 200),
                            rows=[["列1", "列2"]])
        table_item = ParagraphItem(text="列1 | 列2", is_table=True, page_number=1)
        img = _fake_image(cy=150)
        owner = _attach_image(img, [para], [(zone, table_item)])
        assert owner is table_item

    def test_no_owner_returns_none(self):
        img = _fake_image(cy=50)
        assert _attach_image(img, [], []) is None


# ============================================================
# 合成 PDF 集成：标题/页码/扫描页/分块
# ============================================================

def _make_text_pdf(path) -> None:
    """两页：第X章/1.x 标题 + 正文 + 页脚页码。"""
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((60, 90), "第一章 招标公告", fontsize=20, fontname="china-s")
    p1.insert_text((60, 130), "本招标项目已具备招标条件，现对本项目进行公开招标。", fontsize=12, fontname="china-s")
    p1.insert_text((60, 152), "招标内容详见招标文件第三章采购需求。", fontsize=12, fontname="china-s")
    p1.insert_text((300, 810), "1", fontsize=10, fontname="china-s")
    p2 = doc.new_page()
    p2.insert_text((60, 90), "1.1 项目概况", fontsize=14, fontname="china-s")
    p2.insert_text((60, 122), "本项目位于杭州市，服务期三年。", fontsize=12, fontname="china-s")
    p2.insert_text((60, 144), "（1）投标人须具有有效的营业执照；", fontsize=12, fontname="china-s")
    p2.insert_text((300, 810), "2", fontsize=10, fontname="china-s")
    doc.save(path)
    doc.close()


def _make_blank_pdf(path) -> None:
    doc = fitz.open()
    doc.new_page()
    doc.save(path)
    doc.close()


def _make_scan_pdf(path) -> None:
    """单页 PDF：页面只有一张“整页扫描图”（无文本层）。

    先渲染一张带中文的位图，再作为整页图片插入到无文本层的空白页，模拟扫描件。
    """
    tmp = fitz.open()
    tp = tmp.new_page(width=400, height=560)
    tp.insert_text((40, 60), "身份证 OCR 测试", fontsize=20, fontname="china-s")
    tp.insert_text((40, 120), "姓名：张三", fontsize=14, fontname="china-s")
    png = tp.get_pixmap(dpi=120).tobytes("png")
    tmp.close()

    doc = fitz.open()
    page = doc.new_page(width=400, height=560)
    page.insert_image(page.rect, stream=png)
    doc.save(path)
    doc.close()


def _make_split_scan_pdf(path) -> None:
    """无文本层页，两张图各占约 19% 页面积（< 旧阈值 0.2）——身份证扫描典型布局。

    旧 SCAN_IMAGE_MIN_COVER=0.2 会把这些图全部排除 → 整页被判“无可解析文本”（回归用例）。
    """
    def render(label: str) -> bytes:
        tmp = fitz.open()
        tp = tmp.new_page(width=400, height=560)
        tp.insert_text((40, 60), label, fontsize=20, fontname="china-s")
        png = tp.get_pixmap(dpi=120).tobytes("png")
        tmp.close()
        return png

    doc = fitz.open()
    page = doc.new_page(width=400, height=560)
    # 每张 320×133 = 42560/224000 ≈ 0.19 页面积（keep_proportion=False 保证盖满 bbox）
    page.insert_image(fitz.Rect(40, 40, 360, 173), stream=render("正面"),
                      keep_proportion=False)
    page.insert_image(fitz.Rect(40, 210, 360, 343), stream=render("反面"),
                      keep_proportion=False)
    doc.save(path)
    doc.close()


class TestExtractPdfItems:
    def test_structured_items_and_headings(self):
        pdf = _new_pdf_path()
        try:
            _make_text_pdf(pdf)
            result = extract_pdf_items(pdf, extract_images=False)
            assert result.page_count == 2
            assert result.skipped_pages == []
            assert len(result.items) >= 4

            headings = [(i.text, i.heading_level, i.page_number) for i in result.items if i.is_heading]
            assert ("第一章 招标公告", 1, 1) in headings
            assert ("1.1 项目概况", 3, 2) in headings
            # 页脚纯页码 "1"/"2" 被过滤，不作为正文
            bodies = [i.text for i in result.items if not i.is_heading and not i.is_table]
            assert all("1" != b.strip() and "2" != b.strip() for b in bodies)
            # 列表行（以；结尾）不是标题
            assert all("（1）投标人" not in t for t, _lvl, _p in headings)
        finally:
            pdf.unlink(missing_ok=True)

    def test_scan_like_page_skipped(self):
        pdf = _new_pdf_path()
        try:
            _make_blank_pdf(pdf)
            result = extract_pdf_items(pdf, extract_images=False)
            assert result.page_count == 1
            assert result.items == []
            assert result.skipped_pages == [1]
        finally:
            pdf.unlink(missing_ok=True)

    def test_blank_page_skipped_even_with_extract_images(self):
        """空白页（无文本且无整页图）即使开启识别也不 OCR、维持跳过。"""
        pdf = _new_pdf_path()
        try:
            _make_blank_pdf(pdf)
            result = extract_pdf_items(pdf, extract_images=True)
            assert result.page_count == 1
            assert result.items == []
            assert result.skipped_pages == [1]
        finally:
            pdf.unlink(missing_ok=True)

    def test_scan_page_ocr_item_when_extract_images(self):
        """无文本层页（整页扫描图）在 extract_images=True 时产出“空文本 + 整页图”项。"""
        pdf = _new_pdf_path()
        try:
            _make_scan_pdf(pdf)
            result = extract_pdf_items(pdf, extract_images=True)
            assert result.page_count == 1
            assert result.skipped_pages == []          # 扫描页不再跳过
            assert len(result.items) == 1
            item = result.items[0]
            assert item.page_number == 1
            assert not item.is_heading and not item.is_table
            assert item.text == ""
            assert len(item.images) >= 1
            assert item.images[0].image_bytes          # 整页图字节可供 OCR
        finally:
            pdf.unlink(missing_ok=True)

    def test_scan_page_skipped_without_extract_images(self):
        """未配置识别模型（extract_images=False）→ 扫描页维持跳过（回归）。"""
        pdf = _new_pdf_path()
        try:
            _make_scan_pdf(pdf)
            result = extract_pdf_items(pdf, extract_images=False)
            assert result.page_count == 1
            assert result.items == []
            assert result.skipped_pages == [1]
        finally:
            pdf.unlink(missing_ok=True)

    def test_split_scan_images_just_below_old_cover_threshold_ocr(self):
        """两张各约 19% 页面积的证件扫描图（< 0.2 旧阈值）也应产出 OCR 项。

        2026-09-08 线上失败回归：真实 身份证.pdf 两张图覆盖 ~19.7%~20.0%，
        旧 SCAN_IMAGE_MIN_COVER=0.2（严格小于）把它们排除 → “PDF 无可解析文本”。
        """
        pdf = _new_pdf_path()
        try:
            _make_split_scan_pdf(pdf)
            result = extract_pdf_items(pdf, extract_images=True)
            assert result.page_count == 1
            assert result.skipped_pages == []            # 扫描页不再跳过
            assert len(result.items) == 2                 # 每张图一个 OCR 候选
            assert all(len(it.images) == 1 and it.images[0].image_bytes for it in result.items)
            assert all(it.page_number == 1 for it in result.items)
        finally:
            pdf.unlink(missing_ok=True)

    def test_scan_page_ocr_text_merges_into_chunk(self):
        """整页图 OCR 文字写入 image.ocr_text 后 → 文本 chunk + 页码正确。"""
        pdf = _new_pdf_path()
        try:
            _make_scan_pdf(pdf)
            result = extract_pdf_items(pdf, extract_images=True)
            assert len(result.items) == 1
            # 模拟块级 OCR 结果（process_word_images 写回）
            for img in result.items[0].images:
                img.ocr_text = "身份证 OCR 测试 姓名：张三"

            blocks = aggregate_paragraphs(result.items)
            assert blocks and blocks[0].start_page == 1
            chunks, metas, _ = word_chunk_blocks(blocks, "doc-1", pdf.name)
            assert chunks
            assert "姓名：张三" in chunks[0]
            assert metas[0]["page_number"] == 1
            assert metas[0]["image_keys"] == []  # object_key 由 MinIO 上传后填充（本单测不上传）
        finally:
            pdf.unlink(missing_ok=True)

    def test_scan_page_without_ocr_text_yields_no_empty_chunk(self):
        """整页图 OCR 无文字（纯图片）→ 不产出空 chunk（分块器语义回归）。"""
        pdf = _new_pdf_path()
        try:
            _make_scan_pdf(pdf)
            result = extract_pdf_items(pdf, extract_images=True)
            blocks = aggregate_paragraphs(result.items)
            chunks, metas, _ = word_chunk_blocks(blocks, "doc-1", pdf.name)
            assert chunks == []
        finally:
            pdf.unlink(missing_ok=True)

    def test_chunks_carry_heading_and_page(self):
        pdf = _new_pdf_path()
        try:
            _make_text_pdf(pdf)
            result = extract_pdf_items(pdf, extract_images=False)
            blocks = aggregate_paragraphs(result.items)
            assert any(b.start_page == 1 for b in blocks)
            assert any(b.start_page == 2 for b in blocks)

            chunks, metas, _ = word_chunk_blocks(blocks, "doc-1", pdf.name)
            assert chunks
            # 块起始页写入 chunk 元数据（chapter 块在第 1 页）
            page_one = [m for m in metas if m["heading"] == "第一章 招标公告"]
            assert page_one and page_one[0]["page_number"] == 1
            page_two = [m for m in metas if m["heading"] == "1.1 项目概况"]
            assert page_two and page_two[0]["page_number"] == 2
            assert page_two[0]["heading_path"] == "第一章 招标公告 > 1.1 项目概况"
        finally:
            pdf.unlink(missing_ok=True)


class TestWordChunkPagePassthrough:
    """word_chunker 页码透传：Word 项（无 page_number）行为不变。"""

    def test_word_items_without_page_defaults_to_1(self):
        items = [
            ParagraphItem(text="第一章 采购内容", is_heading=True, heading_level=1),
            ParagraphItem(text="正文段落内容"),
        ]
        blocks = aggregate_paragraphs(items)
        assert blocks[0].start_page is None
        chunks, metas, _ = word_chunk_blocks(blocks, "doc-1", "a.docx")
        assert chunks
        assert all(m["page_number"] == 1 for m in metas)

    def test_pdf_items_carry_start_page(self):
        items = [
            ParagraphItem(text="第一章 采购内容", is_heading=True, heading_level=1, page_number=3),
            ParagraphItem(text="正文段落内容", page_number=3),
        ]
        blocks = aggregate_paragraphs(items)
        assert blocks[0].start_page == 3
        chunks, metas, _ = word_chunk_blocks(blocks, "doc-1", "a.pdf")
        assert chunks
        assert all(m["page_number"] == 3 for m in metas)
