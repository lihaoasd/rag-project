"""
WordChunker 模块单元测试 — aggregate_paragraphs + chunk_blocks + word_chunk。
"""
from core.parser import ParagraphItem
from core.word_chunker import (
    aggregate_paragraphs,
    chunk_blocks,
    word_chunk,
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

    def test_table_independent_block(self):
        """表格独立成块，继承当前标题路径"""
        items = [
            ParagraphItem(text="第一章 报价", is_heading=True, heading_level=1),
            ParagraphItem(text="报价说明正文。"),
            ParagraphItem(text="名称 | 金额\n设备A | 10000", is_table=True),
            ParagraphItem(text="后续正文。"),
        ]
        blocks = aggregate_paragraphs(items)

        # 块 0: 正文块（标题 + 报价说明正文）
        # 块 1: 表格块（独立，继承标题路径）
        # 块 2: 后续正文块
        assert len(blocks) == 3

        # 表格块
        table_block = blocks[1]
        assert len(table_block.tables) == 1
        assert "设备A" in table_block.tables[0]
        assert table_block.heading == "第一章 报价"
        assert table_block.heading_path == ["第一章 报价"]
        # 表格块不含正文
        assert table_block.paragraphs == []

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
    """测试逻辑块 → TextNode 转换。"""

    def test_small_block_direct_node(self):
        """小块直接作为 1 个 TextNode"""
        block = LogicalBlock(
            heading="第一章 总则",
            heading_level=1,
            heading_path=["第一章 总则"],
            paragraphs=["这是一段简短的说明。"],
            tables=[],
        )
        nodes = chunk_blocks([block], chunk_size=512, chunk_overlap=64)

        assert len(nodes) == 1
        assert nodes[0].metadata["heading"] == "第一章 总则"
        assert nodes[0].metadata["heading_path"] == "第一章 总则"
        assert nodes[0].metadata["chunk_method"] == "paragraph_aggregate"

    def test_large_block_sentence_split(self):
        """超大块用 SentenceSplitter 细切"""
        long_text = "这是测试文本。" * 200  # 约 1200 字符
        block = LogicalBlock(
            heading="第二章 技术方案",
            heading_level=1,
            heading_path=["第二章 技术方案"],
            paragraphs=[long_text],
            tables=[],
        )
        nodes = chunk_blocks([block], chunk_size=200, chunk_overlap=20)

        # 应该有多个切块
        assert len(nodes) > 1
        for node in nodes:
            assert node.metadata["heading"] == "第二章 技术方案"
            assert node.metadata["heading_path"] == "第二章 技术方案"
            assert node.metadata["chunk_method"] == "sentence_splitter"

    def test_empty_block_skipped(self):
        """仅有标题无正文的块 — 标题本身就是内容，保留为一个节点"""
        block = LogicalBlock(
            heading="空章节",
            heading_level=1,
            heading_path=["空章节"],
            paragraphs=[],
            tables=[],
        )
        nodes = chunk_blocks([block])
        # 标题文本作为节点内容
        assert len(nodes) == 1
        assert nodes[0].text == "空章节"

    def test_mixed_block_sizes(self):
        """混合大小块"""
        small = LogicalBlock(
            heading="第一章", heading_level=1,
            heading_path=["第一章"],
            paragraphs=["短文本。"],
        )
        large = LogicalBlock(
            heading="第二章", heading_level=1,
            heading_path=["第二章"],
            paragraphs=["长文本。" * 300],
        )
        nodes = chunk_blocks([small, large], chunk_size=200, chunk_overlap=20)

        small_nodes = [n for n in nodes if n.metadata["chunk_method"] == "paragraph_aggregate"]
        split_nodes = [n for n in nodes if n.metadata["chunk_method"] == "sentence_splitter"]

        assert len(small_nodes) == 1
        assert len(split_nodes) > 1


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
            assert "chunk_method" in meta

    def test_empty_paragraphs(self):
        """空段落列表"""
        chunks, metadatas, nodes = word_chunk(
            [], doc_id="x", file_name="x.docx",
        )
        assert chunks == []
        assert metadatas == []
        assert nodes == []
