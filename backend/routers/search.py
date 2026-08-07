"""
文档检索 API。
"""
from fastapi import APIRouter, HTTPException

from models.schemas import SearchRequest, SearchResponse
from services import search_service

router = APIRouter(prefix="/api/knowledge-bases/{kb_id}/search", tags=["文档检索"])


@router.post("", response_model=SearchResponse)
async def search(kb_id: str, body: SearchRequest):
    """语义检索知识库中的文档片段"""
    try:
        result = await search_service.search(
            kb_id=kb_id,
            query=body.query,
            top_k=body.top_k,
            similarity_threshold=body.similarity_threshold,
            offset=body.offset,
            limit=body.limit,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
