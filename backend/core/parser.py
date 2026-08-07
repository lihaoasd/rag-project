"""
文档解析引擎 — PDF/Word 文本提取、表格识别、OCR。
Phase 1: 仅定义接口签名，具体实现后续补全。
"""
from pathlib import Path
from typing import Any

from utils.logging import get_logger

logger = get_logger(__name__)


async def parse_document(file_path: Path, file_type: str) -> list[dict[str, Any]]:
    """解析文档，返回 LlamaIndex Document 列表。

    Args:
        file_path: 上传文件路径
        file_type: 文件类型 ('pdf', 'docx', 'doc')

    Returns:
        [{text, metadata: {file_name, page_number, ...}}, ...]
    """
    logger.warning(
        "parse_document 被调用但尚未实现 (Phase 2): file=%s, type=%s",
        file_path.name,
        file_type,
    )
    raise NotImplementedError("Document parser not yet implemented — Phase 2")
