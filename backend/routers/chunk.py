"""
切片管理 API — 查看、编辑、删除、重新切片。
"""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from core import parse_queue
from core.database import async_session
from models.schemas import (
    ChunkResponse,
    ChunkDetailResponse,
    ChunkListResponse,
    ChunkUpdateRequest,
    RechunkRequest,
)
from repositories.document_repository import DocumentRepository
from services import document_service
from utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/knowledge-bases/{kb_id}/documents/{doc_id}",
    tags=["切片管理"],
)


@router.get("/chunks", response_model=ChunkListResponse)
async def list_chunks(
    kb_id: str,
    doc_id: str,
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
):
    """获取文档的切片列表（分页）。"""
    logger.debug("获取切片列表: kb_id=%s, doc_id=%s, page=%d", kb_id, doc_id, page)
    return await document_service.list_chunks(kb_id, doc_id, page, page_size)


@router.get("/chunks/{chunk_id}", response_model=ChunkDetailResponse)
async def get_chunk(kb_id: str, doc_id: str, chunk_id: str):
    """获取单个切片详情（完整文本）。"""
    logger.debug("获取切片详情: chunk_id=%s", chunk_id)
    chunk = await document_service.get_chunk(kb_id, chunk_id)
    if chunk is None:
        raise HTTPException(status_code=404, detail="切片不存在")
    return chunk


@router.get("/chunks/{chunk_id}/image")
async def get_chunk_image(kb_id: str, doc_id: str, chunk_id: str):
    """获取 chunk 关联的第一张图片（兼容旧接口，等价 index=0）。"""
    return await _serve_chunk_image(kb_id, chunk_id, image_index=0)


@router.get("/chunks/{chunk_id}/image/{image_index}")
async def get_chunk_image_by_index(kb_id: str, doc_id: str, chunk_id: str, image_index: int):
    """按下标获取 chunk 关联的图片（文本 chunk 的 metadata.image_keys）。"""
    return await _serve_chunk_image(kb_id, chunk_id, image_index=image_index)


async def _serve_chunk_image(kb_id: str, chunk_id: str, image_index: int):
    """读取并返回 chunk 关联图片（代理 MinIO，带缓存头）。"""
    logger.debug("获取切片图片: chunk_id=%s, index=%d", chunk_id, image_index)
    image = await document_service.get_chunk_image(kb_id, chunk_id, image_index=image_index)
    if image is None:
        raise HTTPException(status_code=404, detail="图片不存在")
    image_bytes, mime_type = image
    return Response(
        content=image_bytes,
        media_type=mime_type,
        headers={"Cache-Control": "max-age=3600"},
    )


@router.put("/chunks/{chunk_id}", response_model=ChunkDetailResponse)
async def update_chunk(kb_id: str, doc_id: str, chunk_id: str, body: ChunkUpdateRequest):
    """编辑切片文本（会重新向量化并更新 Milvus）。"""
    logger.info("编辑切片: chunk_id=%s, text_len=%d", chunk_id, len(body.text))
    try:
        return await document_service.update_chunk(kb_id, chunk_id, body.text)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("编辑切片失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chunks/{chunk_id}/delete", status_code=204)
async def delete_chunk(kb_id: str, doc_id: str, chunk_id: str):
    """删除单条切片（PG + Milvus 同步清理）。"""
    logger.info("删除切片: chunk_id=%s", chunk_id)
    success = await document_service.delete_chunk(kb_id, chunk_id)
    if not success:
        raise HTTPException(status_code=404, detail="切片不存在")


@router.post("/rechunk", status_code=202)
async def rechunk_document(kb_id: str, doc_id: str, body: RechunkRequest):
    """将文档加入重新切片队列，异步 FIFO 执行。"""
    logger.info(
        "触发重切片: doc_id=%s, chunk_size=%d, chunk_overlap=%d",
        doc_id, body.chunk_size, body.chunk_overlap,
    )

    # 验证文档存在且已完成解析
    doc = await document_service.get_document(kb_id, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    if doc["status"] not in ("completed", "failed"):
        raise HTTPException(
            status_code=400,
            detail="只能对已完成解析或解析失败的文档重新切片",
        )

    # 更新状态 → queued
    async with async_session() as session:
        repo = DocumentRepository(session)
        await repo.update_status(doc_id, "queued")

    # 加入队列
    position = await parse_queue.enqueue_rechunk(
        kb_id, doc_id, body.chunk_size, body.chunk_overlap,
    )

    logger.info("重切片已入队: doc_id=%s, queue_position=%d", doc_id, position)
    return {"id": doc_id, "status": "queued", "queue_position": position}
