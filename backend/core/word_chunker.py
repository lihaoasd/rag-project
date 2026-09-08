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

from config import settings
from core.parser import ParagraphItem, WordImage
from utils.logging import get_logger

logger = get_logger(__name__)


# ============================================================
# 数据模型
# ============================================================

@dataclass
class LogicalBlock:
    """聚合后的逻辑块：一个标题 + 其下正文、表格和图片。

    start_page: 块内首项来源页（PDF 由 ParagraphItem.page_number 填充；
        Word 无页码概念为 None，分块时保持默认 page_number=1）。
    """
    heading: str | None
    heading_level: int | None
    heading_path: list[str]
    paragraphs: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    images: list[WordImage] = field(default_factory=list)
    start_page: int | None = None


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

    def _note_start_page(block: LogicalBlock, item: ParagraphItem) -> None:
        """记录块起始页（取块内首个带 page_number 的项）。"""
        if block.start_page is None:
            page = getattr(item, "page_number", None)
            if page is not None:
                block.start_page = page

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
            _note_start_page(current_block, item)
            current_block.images.extend(item.images)
        elif item.is_table:
            _note_start_page(current_block, item)
            current_block.tables.append(item.text)
            current_block.images.extend(item.images)
        else:
            _note_start_page(current_block, item)
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

def _find_last_boundary(text: str, start: int, end: int) -> int:
    """在 text[start:end] 窗口内从后往前找切分边界，返回边界后一个字符的索引。

    优先级：段落分隔 \\n\\n → 换行 \\n → 句末标点 。！？； → 逗号/顿号/空格。
    找不到则返回 end（硬切）。
    """
    window = text[start:end]
    for sep in ("\n\n", "\n"):
        idx = window.rfind(sep)
        if idx > 0:
            return start + idx + len(sep)
    for ch in "。！？；":
        idx = window.rfind(ch)
        if idx > 0:
            return start + idx + 1
    for ch in "，、 ":
        idx = window.rfind(ch)
        if idx > 0:
            return start + idx + 1
    return end


def _split_by_chars(
    text: str,
    max_chars: int,
    overlap_chars: int,
) -> list[str]:
    """按字符数切分文本，优先在段落/句子边界切，相邻块重叠 overlap_chars 字符。

    - 文本 ≤ max_chars → 返回 [text]（保持整块）
    - 每块不超过 max_chars 字符（边界切分时可能略短）
    - 相邻块末尾与下一块开头重叠 overlap_chars 字符
    """
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    n = len(text)

    while start < n:
        end = min(start + max_chars, n)
        if end < n:
            # 在窗口内找最后一个边界，避免在词/句中硬切
            cut = _find_last_boundary(text, start, end)
            # 边界太靠近窗口起点（上一段遗留分隔符）时降级为硬切
            if cut - start < max_chars // 3:
                cut = end
            end = cut
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        # 下一块起点回退 overlap，保证重叠；至少前进 1 字符防死循环
        start = max(end - overlap_chars, start + 1)

    return chunks


def chunk_blocks(
    blocks: list[LogicalBlock],
    max_chars: int | None = None,
    overlap_chars: int | None = None,
) -> list[TextNode]:
    """将每个逻辑块转换为文本 TextNode，图片识别文字并入正文。

    超长逻辑块二次切分（字符口径）：
        - 不传 max_chars → 一个逻辑块 = 一个 TextNode（原行为）
        - 传 max_chars > 0  → 块内文本超过 max_chars 字符时按字符切分为多个
                              TextNode（≤ 阈值保持整块），切分后的子块继承
                              标题/图片元数据，相邻块重叠 overlap_chars 字符
    """
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

        # 超长逻辑块二次切分：≤ max_chars 保持整块，超过则按字符拆分
        if max_chars and max_chars > 0:
            body_splits = _split_by_chars(
                body, max_chars, overlap_chars or 0
            )
        else:
            body_splits = [body]

        heading_path_str = " > ".join(block.heading_path) if block.heading_path else ""
        # 关联图片（仅记录已上传的 object_key，供前端查看原图）
        image_keys = [
            image.object_key for image in block.images if image.object_key
        ]

        for part_text in body_splits:
            if not part_text.strip():
                continue
            node_meta: dict[str, Any] = {
                "heading": block.heading or "",
                "heading_path": heading_path_str,
                "image_keys": list(image_keys),
                "source_block_index": block_idx,
            }
            # PDF：chunk 页码 = 逻辑块起始页；Word 无页码概念（start_page=None）保持默认 page_number=1
            if block.start_page is not None:
                node_meta["page_number"] = block.start_page
            node = TextNode(
                text=part_text,
                metadata=node_meta,
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
    max_chars: int | None = None,
    overlap_chars: int | None = None,
) -> tuple[list[str], list[dict[str, Any]], list[TextNode]]:
    """Word 文档分片统一入口：先聚合逻辑块，再输出文本 chunk + 图片 chunk。"""
    blocks = aggregate_paragraphs(paragraphs)
    return word_chunk_blocks(
        blocks,
        doc_id,
        file_name,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
    )


def word_chunk_blocks(
    blocks: list[LogicalBlock],
    doc_id: str,
    file_name: str,
    start_chunk_index: int = 0,
    max_chars: int | None = None,
    overlap_chars: int | None = None,
) -> tuple[list[str], list[dict[str, Any]], list[TextNode]]:
    """按逻辑块批次生成文本 chunk，支持全局索引偏移。

    图片识别文字已在 chunk_blocks 中并入正文，不再生成独立图片 chunk。
    超长逻辑块（超过 max_chars 字符）按字符边界二次切分，
    默认 max_chars / overlap_chars 取 settings.word_block_max_chars /
    word_block_overlap_chars。
    """
    size = max_chars or settings.word_block_max_chars
    overlap = (
        overlap_chars
        if overlap_chars is not None
        else settings.word_block_overlap_chars
    )
    text_nodes = chunk_blocks(blocks, max_chars=size, overlap_chars=overlap)

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
