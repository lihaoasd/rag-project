"""
文档检索 API。
"""
from fastapi import APIRouter, HTTPException

from models.schemas import SearchRequest, SearchResponse
from services import search_service
from utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/knowledge-bases/{kb_id}/search", tags=["文档检索"])


@router.post("", response_model=SearchResponse)
async def search(kb_id: str, body: SearchRequest):
    """语义检索知识库中的文档片段"""
    logger.info(
        "文档检索: kb_id=%s, query=%s, top_k=%s",
        kb_id,
        body.query[:80],
        body.top_k,
    )
    try:
        result = await search_service.search(
            kb_id=kb_id,
            query=body.query,
            top_k=body.top_k,
            similarity_threshold=body.similarity_threshold,
            offset=body.offset,
            limit=body.limit,
        )
        logger.info("检索完成: total=%d", result.get("total", 0))
        return result
    except Exception as e:
        logger.error("检索失败: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
