"""
智能问答 API（SSE 流式）。
"""
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from models.schemas import ChatRequest
from services import chat_service
from utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/knowledge-bases/{kb_id}/chat", tags=["智能问答"])


@router.post("")
async def chat(kb_id: str, body: ChatRequest):
    """发送问题，SSE 流式返回回答"""
    logger.info(
        "智能问答: kb_id=%s, session=%s, message=%s",
        kb_id,
        body.session_id,
        body.message[:80],
    )
    return StreamingResponse(
        chat_service.chat_stream(
            kb_id=kb_id,
            message=body.message,
            session_id=body.session_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
