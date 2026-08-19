"""
WordChunker 模块单元测试 — aggregate_paragraphs + chunk_blocks + word_chunk。
"""
from core.parser import ParagraphItem, WordImage
from core.word_chunker import (
    aggregate_paragraphs,
    chunk_blocks,
    word_chunk,
    word_chunk_blocks,
    LogicalBlock,
)


class TestAggregateParagraphs:
    """测试段落聚合逻辑。"""

    def test_basic_heading_aggregation(self):
        """基本场景：3 个标题 + 正文 → 3 个逻辑块"""
        items = [
            ParagraphItem(text="第一章 总则", is_heading=True, heading_level=1),
            ParagraphItem(text="本章说明招标项目的基本情况。"),
            ParagraphItem(text="第一节 项目概况", is_heading=True, heading_level=1),
            ParagraphItem(text="本项目为XX单位采购项目。"),
            ParagraphItem(text="1.1 项目名称", is_heading=True, heading_level=3),
            ParagraphItem(text="XX系统开发项目。"),
        ]
        blocks = aggregate_paragraphs(items)

        assert len(blocks) == 3
        assert blocks[0].heading == "第一章 总则"
        assert len(blocks[0].paragraphs) == 1
        assert blocks[1].heading == "第一节 项目概况"
        assert blocks[2].heading == "1.1 项目名称"
        assert blocks[2].heading_path == ["第一节 项目概况", "1.1 项目名称"]

    def test_no_headings(self):
        """无标题文档 → 1 个逻辑块"""
        items = [
            ParagraphItem(text="第一段正文。"),
            ParagraphItem(text="第二段正文。"),
            ParagraphItem(text="第三段正文。"),
        ]
        blocks = aggregate_paragraphs(items)

        assert len(blocks) == 1
        assert blocks[0].heading is None
        assert blocks[0].heading_path == []
        assert len(blocks[0].paragraphs) == 3

    def test_heading_path_breadcrumb(self):
        """标题路径面包屑 — 上级标题清空下级别"""
        items = [
            ParagraphItem(text="一、商务条款", is_heading=True, heading_level=2),
            ParagraphItem(text="商务正文1"),
            ParagraphItem(text="1.1 报价", is_heading=True, heading_level=3),
            ParagraphItem(text="报价正文"),
            ParagraphItem(text="（一）分项", is_heading=True, heading_level=3),
            ParagraphItem(text="分项正文"),
            ParagraphItem(text="二、技术条款", is_heading=True, heading_level=2),
            ParagraphItem(text="技术正文"),
        ]
        blocks = aggregate_paragraphs(items)

        # 第3个块 "（一）分项" — 应替换同级的 "1.1 报价"
        block_fenxiang = blocks[2]
        assert block_fenxiang.heading == "（一）分项"
        assert block_fenxiang.heading_path == ["一、商务条款", "（一）分项"]

        # 第4个块 "二、技术条款" — L2 弹出所有 >=2 的
        block_tech = blocks[3]
        assert block_tech.heading == "二、技术条款"
        assert block_tech.heading_path == ["二、技术条款"]

    def test_table_attached_to_heading(self):
        """表格归入当前标题块，与正文在同一块中"""
        items = [
            ParagraphItem(text="第一章 报价", is_heading=True, heading_level=1),
            ParagraphItem(text="报价说明正文。"),
            ParagraphItem(text="名称 | 金额\n设备A | 10000", is_table=True),
            ParagraphItem(text="后续正文。"),
        ]
        blocks = aggregate_paragraphs(items)

        assert len(blocks) == 1
        assert blocks[0].heading == "第一章 报价"
        assert len(blocks[0].paragraphs) == 2
        assert len(blocks[0].tables) == 1
        assert "设备A" in blocks[0].tables[0]

    def test_empty_input(self):
        """空输入 → 空输出"""
        blocks = aggregate_paragraphs([])
        assert blocks == []

    def test_only_headings(self):
        """只有标题没有正文 → 所有块均无内容，不产出"""
        items = [
            ParagraphItem(text="第一章", is_heading=True, heading_level=1),
            ParagraphItem(text="第二章", is_heading=True, heading_level=1),
        ]
        blocks = aggregate_paragraphs(items)
        # 无内容的标题块被丢弃
        assert len(blocks) == 0


class TestChunkBlocks:
    """测试逻辑块 → TextNode 转换 — 每个块就是一个 TextNode，不做切分。"""

    def test_block_to_node(self):
        """逻辑块转为 TextNode，标题、正文、表格在同一节点内"""
        block = LogicalBlock(
            heading="第一章 总则",
            heading_level=1,
            heading_path=["第一章 总则"],
            paragraphs=["这是段落一。", "这是段落二。"],
            tables=["名称 | 金额\n设备A | 10000"],
        )
        nodes = chunk_blocks([block])

        assert len(nodes) == 1
        assert nodes[0].metadata["heading"] == "第一章 总则"
        assert nodes[0].metadata["heading_path"] == "第一章 总则"
        assert "设备A" in nodes[0].text
        assert "段落一" in nodes[0].text

    def test_large_block_kept_intact(self):
        """大块也不切分，段落和表格保持在一起"""
        long_text = "这是测试文本。" * 200
        block = LogicalBlock(
            heading="第二章 技术方案",
            heading_level=1,
            heading_path=["第二章 技术方案"],
            paragraphs=[long_text],
            tables=["表格内容"],
        )
        nodes = chunk_blocks([block])

        # 不切分，就是一个节点
        assert len(nodes) == 1
        assert "表格内容" in nodes[0].text

    def test_empty_block_skipped(self):
        """无内容的块跳过"""
        block = LogicalBlock(
            heading="空章节",
            heading_level=1,
            heading_path=["空章节"],
            paragraphs=[],
            tables=[],
        )
        nodes = chunk_blocks([block])
        assert len(nodes) == 1

    def test_multiple_blocks(self):
        """多个块各自独立"""
        block1 = LogicalBlock(
            heading="第一章", heading_level=1,
            heading_path=["第一章"],
            paragraphs=["短文本。"],
        )
        block2 = LogicalBlock(
            heading="第二章", heading_level=1,
            heading_path=["第二章"],
            paragraphs=["另一段文本。"],
        )
        nodes = chunk_blocks([block1, block2])

        assert len(nodes) == 2
        assert nodes[0].metadata["heading"] == "第一章"
        assert nodes[1].metadata["heading"] == "第二章"


class TestWordChunk:
    """测试 word_chunk 统一入口 — 对齐 chunk_documents 输出格式。"""

    def test_output_format(self):
        """验证输出格式与 chunk_documents 一致"""
        items = [
            ParagraphItem(text="第一章 总则", is_heading=True, heading_level=1),
            ParagraphItem(text="这是第一章正文内容。包含投标的基本信息。"),
            ParagraphItem(text="1.1 项目概况", is_heading=True, heading_level=3),
            ParagraphItem(text="项目概况详细说明。" * 30),
        ]

        chunks, metadatas, nodes = word_chunk(
            items,
            doc_id="test-doc-id",
            file_name="test.docx",
            chunk_size=512,
            chunk_overlap=64,
        )

        assert len(chunks) > 0
        assert len(chunks) == len(metadatas) == len(nodes)

        for i, meta in enumerate(metadatas):
            assert meta["doc_id"] == "test-doc-id"
            assert meta["file_name"] == "test.docx"
            assert meta["page_number"] == 1
            assert meta["chunk_index"] == i
            assert "text" in meta
            assert "heading" in meta
            assert "heading_path" in meta

    def test_empty_paragraphs(self):
        """空段落列表"""
        chunks, metadatas, nodes = word_chunk(
            [], doc_id="x", file_name="x.docx",
        )
        assert chunks == []
        assert metadatas == []
        assert nodes == []


class TestWordChunkImages:
    def test_ocr_text_merged_into_body(self):
        """图片 OCR 文字直接并入正文分块，不生成图片 chunk。"""
        image = WordImage(
            image_bytes=b"fake-png",
            content_type="image/png",
            object_key="kb/doc/images/a.png",
            ocr_text="投标报价：100万元",
            vision_text="这是一张报价表",
        )
        items = [
            ParagraphItem(text="报价章节正文"),
            ParagraphItem(text="", images=[image]),
        ]

        chunks, metadatas, _ = word_chunk(
            items,
            doc_id="doc-1",
            file_name="test.docx",
        )

        # 只有文本 chunk，无图片 chunk / 占位符
        assert len(chunks) == 1
        assert "[图片" not in chunks[0]
        assert "OCR识别" not in chunks[0]
        assert "视觉描述" not in chunks[0]
        assert "投标报价：100万元" in chunks[0]
        assert "报价章节正文" in chunks[0]
        assert metadatas[0]["chunk_type"] == "text"

    def test_image_only_paragraph_text_becomes_chunk(self):
        """只有图片的段落：OCR 文字成为文本 chunk。"""
        image = WordImage(
            image_bytes=b"fake-png",
            content_type="image/png",
            object_key="kb/doc/images/a.png",
            ocr_text="附件图：组织机构图",
        )
        items = [ParagraphItem(text="", images=[image])]

        chunks, metadatas, _ = word_chunk(items, "doc-1", "test.docx")

        assert len(chunks) == 1
        assert "附件图：组织机构图" in chunks[0]
        assert metadatas[0]["chunk_type"] == "text"

    def test_vision_text_fallback_when_ocr_empty(self):
        """OCR 无结果时，vision 文字并入正文。"""
        image = WordImage(
            image_bytes=b"fake-png",
            content_type="image/png",
            ocr_text="",
            vision_text="这是一张流程图",
        )
        items = [ParagraphItem(text="", images=[image])]

        chunks, _, _ = word_chunk(items, "doc-1", "test.docx")

        assert len(chunks) == 1
        assert "这是一张流程图" in chunks[0]

    def test_image_without_text_not_in_body(self):
        """图片无识别文字时不产生内容。"""
        image = WordImage(image_bytes=b"fake-png", content_type="image/png")
        items = [ParagraphItem(text="", images=[image])]

        chunks, metadatas, _ = word_chunk(items, "doc-1", "test.docx")

        assert chunks == []

    def test_image_keys_recorded_in_metadata(self):
        """文本 chunk metadata 记录关联图片的 object_key。"""
        image_a = WordImage(
            image_bytes=b"a", content_type="image/png",
            object_key="kb/doc/images/a.png", ocr_text="图片A文字",
        )
        image_b = WordImage(
            image_bytes=b"b", content_type="image/png",
            object_key="kb/doc/images/b.png", ocr_text="图片B文字",
        )
        image_no_key = WordImage(
            image_bytes=b"c", content_type="image/png", ocr_text="未上传图片",
        )
        items = [ParagraphItem(text="章节正文", images=[image_a, image_b, image_no_key])]

        chunks, metadatas, _ = word_chunk(items, "doc-1", "test.docx")

        assert len(chunks) == 1
        # 只有已上传的图片进入 image_keys
        assert metadatas[0]["image_keys"] == [
            "kb/doc/images/a.png",
            "kb/doc/images/b.png",
        ]
        # 识别文字均并入正文
        assert "图片A文字" in chunks[0]
        assert "图片B文字" in chunks[0]
        assert "未上传图片" in chunks[0]

    def test_image_keys_empty_for_plain_text_block(self):
        """无图片的块 image_keys 为空列表。"""
        items = [ParagraphItem(text="纯文本段落")]

        chunks, metadatas, _ = word_chunk(items, "doc-1", "test.docx")

        assert metadatas[0]["image_keys"] == []

    def test_word_chunk_blocks_global_offset(self):
        block = LogicalBlock(
            heading="章节",
            heading_level=1,
            heading_path=["章节"],
            paragraphs=["正文"],
        )

        chunks, metadatas, _ = word_chunk_blocks(
            [block],
            doc_id="doc-1",
            file_name="test.docx",
            start_chunk_index=5,
        )

        assert metadatas[0]["chunk_index"] == 5
        assert "正文" in chunks[0]
