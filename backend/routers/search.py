"""
文档检索 API — 支持单知识库和多知识库检索。
"""
from fastapi import APIRouter, HTTPException

from models.schemas import SearchRequest, SearchResponse
from services import search_service
from utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["文档检索"])


@router.post("/api/search", response_model=SearchResponse)
async def search_multi(body: SearchRequest):
    """多知识库语义检索（也支持单知识库，传 kb_ids 即可）。"""
    kb_ids = body.kb_ids or []
    logger.info(
        "多库检索: kb_ids=%s, query=%s, top_k=%s",
        kb_ids,
        body.query[:80],
        body.top_k,
    )
    try:
        result = await search_service.search(
            kb_ids=kb_ids,
            query=body.query,
            top_k=body.top_k,
            similarity_threshold=body.similarity_threshold,
            retrieval_methods=body.retrieval_methods,
            rerank_model_id=body.rerank_model_id,
            rerank_top_n=body.rerank_top_n,
            highlight=body.highlight,
            query_rewrite=body.query_rewrite,
        )
        logger.info("检索完成: total=%d", result.get("total", 0))
        return result
    except Exception as e:
        logger.error("检索失败: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/knowledge-bases/{kb_id}/search", response_model=SearchResponse)
async def search_single(kb_id: str, body: SearchRequest):
    """单知识库语义检索（兼容旧接口）。"""
    logger.info(
        "单库检索: kb_id=%s, query=%s, top_k=%s",
        kb_id,
        body.query[:80],
        body.top_k,
    )
    try:
        result = await search_service.search(
            kb_ids=[kb_id],
            query=body.query,
            top_k=body.top_k,
            similarity_threshold=body.similarity_threshold,
            retrieval_methods=body.retrieval_methods,
            rerank_model_id=body.rerank_model_id,
            rerank_top_n=body.rerank_top_n,
            highlight=body.highlight,
            query_rewrite=body.query_rewrite,
        )
        logger.info("检索完成: total=%d", result.get("total", 0))
        return result
    except Exception as e:
        logger.error("检索失败: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
