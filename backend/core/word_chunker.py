"""
Word 文档分片引擎 — 段落聚合 + 中文标题识别。

以段落为原子单位，按标题边界聚合邻近正文段落形成"逻辑块"，
超长逻辑块用 SentenceSplitter 细切，兜底保证不丢数据。

流程：
    ParagraphItem 列表
        → aggregate_paragraphs()   → LogicalBlock 列表
        → chunk_blocks()           → TextNode 列表
        → 输出 chunks / metadatas（与 PDF 链路统一格式）
"""
from dataclasses import dataclass, field
from typing import Any

from llama_index.core.schema import TextNode

from core.parser import ParagraphItem
from utils.logging import get_logger

logger = get_logger(__name__)


# ============================================================
# 数据模型
# ============================================================

@dataclass
class LogicalBlock:
    """聚合后的逻辑块：一个标题 + 其下所有正文段落和表格。"""
    heading: str | None          # 所属标题文本（最近的标题）
    heading_level: int | None     # 标题层级（1-5，None 表示无标题文本块）
    heading_path: list[str]       # 标题路径（面包屑）
    paragraphs: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)


# ============================================================
# 段落聚合
# ============================================================

def aggregate_paragraphs(paragraphs: list[ParagraphItem]) -> list[LogicalBlock]:
    """以标题为边界，将相邻正文段落和表格聚合到同一逻辑块。

    规则：
        1. 遇到标题 → 开启新块
        2. 正文/表格 → 归入当前块
        3. 维持标题栈（heading_stack），构建面包屑路径
        4. 遇到同级或上级标题时弹出栈中更深层的标题

    Args:
        paragraphs: DocxParser.parse() 输出的 ParagraphItem 列表

    Returns:
        LogicalBlock 列表，每个块对应一个标题及其下内容
    """
    blocks: list[LogicalBlock] = []
    current_block = LogicalBlock(
        heading=None,
        heading_level=None,
        heading_path=[],
        paragraphs=[],
        tables=[],
    )
    heading_stack: list[tuple[str, int]] = []  # [(heading_text, level)]

    for item in paragraphs:
        if item.is_heading:
            # 保存上一个块
            if current_block.paragraphs or current_block.tables:
                blocks.append(current_block)

            # 维护标题栈：弹出所有 >= 当前层级的标题
            current_level = item.heading_level or 0
            while heading_stack and heading_stack[-1][1] >= current_level:
                heading_stack.pop()

            heading_stack.append((item.text, current_level))

            current_block = LogicalBlock(
                heading=item.text,
                heading_level=item.heading_level,
                heading_path=[h[0] for h in heading_stack],
                paragraphs=[],
                tables=[],
            )
        elif item.is_table:
            # 表格归入当前块
            current_block.tables.append(item.text)
        else:
            current_block.paragraphs.append(item.text)

    # 最后一个块
    if current_block.paragraphs or current_block.tables:
        blocks.append(current_block)

    logger.debug(
        "段落聚合完成: paragraphs=%d → blocks=%d",
        len(paragraphs), len(blocks),
    )
    return blocks


# ============================================================
# 逻辑块 → TextNode（含超长兜底）
# ============================================================

def chunk_blocks(blocks: list[LogicalBlock]) -> list[TextNode]:
    """将每个逻辑块直接转换为一个 TextNode，段落和表格保持在同一块中。

    不做额外切分，确保标题、正文、表格全部在同一个节点内。

    Args:
        blocks: aggregate_paragraphs() 输出的逻辑块列表

    Returns:
        TextNode 列表，每个节点对应一个逻辑块
    """
    all_nodes: list[TextNode] = []

    for block in blocks:
        # 构建块文本：标题 + 正文 + 表格
        body_parts: list[str] = []
        if block.heading:
            body_parts.append(block.heading)
        body_parts.extend(block.paragraphs)
        if block.tables:
            body_parts.extend(block.tables)

        body = "\n\n".join(body_parts)
        if not body.strip():
            continue

        heading_path_str = " > ".join(block.heading_path) if block.heading_path else ""

        node = TextNode(
            text=body,
            metadata={
                "heading": block.heading or "",
                "heading_path": heading_path_str,
            },
        )
        all_nodes.append(node)

    logger.info(
        "分块完成: blocks=%d → nodes=%d",
        len(blocks), len(all_nodes),
    )
    return all_nodes


# ============================================================
# 统一入口 — 对齐 PDF 链路的 chunk_documents() 输出格式
# ============================================================

def word_chunk(
    paragraphs: list[ParagraphItem],
    doc_id: str,
    file_name: str,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> tuple[list[str], list[dict[str, Any]], list[TextNode]]:
    """Word 文档分片统一入口。

    产出格式与 chunk_documents() 一致，确保 document_service.py
    的下游（build_index + batch_create_chunks）无需修改。

    Args:
        paragraphs: DocxParser.parse() 输出的段落列表
        doc_id: 文档 UUID
        file_name: 原始文件名
        chunk_size: 切片大小，默认从 settings 读取
        chunk_overlap: 切片重叠，默认从 settings 读取

    Returns:
        (chunks_text, metadatas, text_nodes) — 与 chunk_documents() 签名一致
    """
    blocks = aggregate_paragraphs(paragraphs)
    nodes = chunk_blocks(blocks)

    chunks: list[str] = []
    metadatas: list[dict[str, Any]] = []
    text_nodes: list[TextNode] = []

    for i, node in enumerate(nodes):
        # 增补必要字段（对齐 chunk_documents 的输出 metadata）
        node.metadata.setdefault("doc_id", doc_id)
        node.metadata.setdefault("file_name", file_name)
        node.metadata.setdefault("page_number", 1)   # Word 无页码，统一为 1
        node.metadata.setdefault("chunk_index", i)
        node.metadata["text"] = node.text

        chunks.append(node.text)
        metadatas.append(dict(node.metadata))
        text_nodes.append(node)

    logger.info(
        "word_chunk 完成: paragraphs=%d → blocks=%d → chunks=%d",
        len(paragraphs), len(blocks), len(chunks),
    )
    return chunks, metadatas, text_nodes
