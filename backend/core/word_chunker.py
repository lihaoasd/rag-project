"""
Word 文档分片引擎 — 段落聚合 + 中文标题识别 + 图片独立 chunk。

流程：
    ParagraphItem 列表
        → aggregate_paragraphs()  → LogicalBlock 列表
        → chunk_blocks()          → 文本 TextNode 列表
        → word_chunk()            → 文本 chunk + 图片 chunk，并建立关联元数据
"""
from dataclasses import dataclass, field
from typing import Any

from llama_index.core.schema import TextNode

from core.parser import ParagraphItem, WordImage
from utils.logging import get_logger

logger = get_logger(__name__)


# ============================================================
# 数据模型
# ============================================================

@dataclass
class LogicalBlock:
    """聚合后的逻辑块：一个标题 + 其下正文、表格和图片。"""
    heading: str | None
    heading_level: int | None
    heading_path: list[str]
    paragraphs: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    images: list[WordImage] = field(default_factory=list)


# ============================================================
# 段落聚合
# ============================================================

def aggregate_paragraphs(paragraphs: list[ParagraphItem]) -> list[LogicalBlock]:
    """以标题为边界，将相邻正文、表格和图片聚合到同一逻辑块。"""
    blocks: list[LogicalBlock] = []
    current_block = LogicalBlock(
        heading=None,
        heading_level=None,
        heading_path=[],
    )
    heading_stack: list[tuple[str, int]] = []

    for item in paragraphs:
        if item.is_heading:
            if current_block.paragraphs or current_block.tables or current_block.images:
                blocks.append(current_block)

            current_level = item.heading_level or 0
            while heading_stack and heading_stack[-1][1] >= current_level:
                heading_stack.pop()
            heading_stack.append((item.text, current_level))

            current_block = LogicalBlock(
                heading=item.text,
                heading_level=item.heading_level,
                heading_path=[h[0] for h in heading_stack],
            )
            current_block.images.extend(item.images)
        elif item.is_table:
            current_block.tables.append(item.text)
            current_block.images.extend(item.images)
        else:
            current_block.paragraphs.append(item.text)
            current_block.images.extend(item.images)

    if current_block.paragraphs or current_block.tables or current_block.images:
        blocks.append(current_block)

    logger.debug(
        "段落聚合完成: paragraphs=%d → blocks=%d",
        len(paragraphs),
        len(blocks),
    )
    return blocks


# ============================================================
# 逻辑块 → 文本 TextNode
# ============================================================

def chunk_blocks(blocks: list[LogicalBlock]) -> list[TextNode]:
    """将每个逻辑块转换为文本 TextNode，图片识别文字并入正文。"""
    all_nodes: list[TextNode] = []

    for block_idx, block in enumerate(blocks):
        body_parts: list[str] = []
        if block.heading:
            body_parts.append(block.heading)
        body_parts.extend(block.paragraphs)

        # 图片识别文字并入正文（OCR 优先，vision 兜底；无识别文字则跳过）
        for image in block.images:
            text = (image.ocr_text or "").strip() or (image.vision_text or "").strip()
            if text:
                body_parts.append(text)

        if block.tables:
            body_parts.extend(block.tables)

        has_text_content = bool(block.heading) or any(
            p.strip() for p in block.paragraphs
        ) or any(t.strip() for t in block.tables)
        # 图片识别文字也算作内容（仅图片的块也能产出文本 chunk）
        image_texts = [
            (image.ocr_text or "").strip() or (image.vision_text or "").strip()
            for image in block.images
        ]
        if not has_text_content and not any(image_texts):
            continue

        body = "\n\n".join(body_parts)
        if not body.strip():
            continue

        heading_path_str = " > ".join(block.heading_path) if block.heading_path else ""
        # 关联图片（仅记录已上传的 object_key，供前端查看原图）
        image_keys = [
            image.object_key for image in block.images if image.object_key
        ]
        node = TextNode(
            text=body,
            metadata={
                "heading": block.heading or "",
                "heading_path": heading_path_str,
                "image_keys": image_keys,
                "source_block_index": block_idx,
            },
        )
        all_nodes.append(node)

    logger.info(
        "文本分块完成: blocks=%d → nodes=%d",
        len(blocks),
        len(all_nodes),
    )
    return all_nodes


# ============================================================
# 统一入口
# ============================================================

def word_chunk(
    paragraphs: list[ParagraphItem],
    doc_id: str,
    file_name: str,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> tuple[list[str], list[dict[str, Any]], list[TextNode]]:
    """Word 文档分片统一入口：先聚合逻辑块，再输出文本 chunk + 图片 chunk。"""
    blocks = aggregate_paragraphs(paragraphs)
    return word_chunk_blocks(
        blocks,
        doc_id,
        file_name,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )


def word_chunk_blocks(
    blocks: list[LogicalBlock],
    doc_id: str,
    file_name: str,
    start_chunk_index: int = 0,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> tuple[list[str], list[dict[str, Any]], list[TextNode]]:
    """按逻辑块批次生成文本 chunk，支持全局索引偏移。

    图片识别文字已在 chunk_blocks 中并入正文，不再生成独立图片 chunk。
    """
    text_nodes = chunk_blocks(blocks)

    all_nodes = text_nodes
    chunks: list[str] = []
    metadatas: list[dict[str, Any]] = []
    final_nodes: list[TextNode] = []

    for i, node in enumerate(all_nodes):
        node.metadata.pop("source_block_index", None)
        node.metadata.setdefault("doc_id", doc_id)
        node.metadata.setdefault("file_name", file_name)
        node.metadata.setdefault("page_number", 1)
        node.metadata.setdefault("chunk_type", "text")
        node.metadata.setdefault("image_keys", [])
        node.metadata["chunk_index"] = start_chunk_index + i
        node.metadata["text"] = node.text

        chunks.append(node.text)
        metadatas.append(dict(node.metadata))
        final_nodes.append(node)

    logger.info(
        "word_chunk_blocks 完成: blocks=%d → chunks=%d, start_index=%d",
        len(blocks),
        len(chunks),
        start_chunk_index,
    )
    return chunks, metadatas, final_nodes
