"""
智能问答业务逻辑（SSE 流式）。
Phase 1: 返回占位流式响应，完整实现后续补全。
"""
import json
from typing import AsyncGenerator

from utils.logging import get_logger

logger = get_logger(__name__)


async def chat_stream(
    kb_id: str,
    message: str,
    session_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """SSE 流式问答。Phase 1 stub — 返回提示信息。"""
    logger.debug("chat_stream stub: kb_id=%s, session=%s", kb_id, session_id)
    placeholder = "问答服务尚未实现，敬请期待后续版本。"
    for char in placeholder:
        yield f"data: {json.dumps({'type': 'token', 'content': char})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"
