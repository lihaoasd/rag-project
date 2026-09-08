"""
文档管理 API — 上传与解析分离，解析使用队列 FIFO 执行。
"""
from fastapi import APIRouter, File, HTTPException, UploadFile

from core import parse_queue
from core.database import async_session
from models.schemas import DocumentResponse, ParseResponse
from repositories.document_repository import DocumentRepository
from services import document_service
from utils.file_utils import validate_file_type
from utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/knowledge-bases/{kb_id}/documents", tags=["文档管理"])


@router.post("", response_model=DocumentResponse, status_code=201)
async def upload_document(kb_id: str, file: UploadFile = File(...)):
    """上传文档到指定知识库（仅上传，不解析）"""
    logger.info("上传文档: kb_id=%s, file=%s, size=%d", kb_id, file.filename, file.size or 0)

    if not file.filename:
        logger.warning("上传失败：未提供文件")
        raise HTTPException(status_code=400, detail="未提供文件")

    if not validate_file_type(file.filename):
        logger.warning("上传失败：不支持的文件格式 %s", file.filename)
        raise HTTPException(
            status_code=400,
            detail="不支持的文件格式。支持: pdf, docx, doc",
        )

    try:
        result = await document_service.upload_document(kb_id, file)
        logger.info("文档上传成功: doc_id=%s", result.get("id"))
        return result
    except Exception as e:
        logger.error("文档上传失败: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=list[DocumentResponse])
async def list_documents(kb_id: str):
    """获取知识库下的文档列表"""
    logger.debug("获取文档列表: kb_id=%s", kb_id)
    return await document_service.list_documents(kb_id)


@router.get("/queue/status")
async def queue_status():
    """获取解析队列状态"""
    return {
        "queue_size": parse_queue.queue_size(),
        "worker_running": parse_queue.is_worker_running(),
    }


@router.get("/{doc_id}", response_model=DocumentResponse)
async def get_document(kb_id: str, doc_id: str):
    """获取单个文档信息"""
    logger.debug("获取文档: kb_id=%s, doc_id=%s", kb_id, doc_id)
    doc = await document_service.get_document(kb_id, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return doc


@router.post("/{doc_id}/parse", response_model=ParseResponse)
async def parse_document(kb_id: str, doc_id: str):
    """将文档加入解析队列，异步 FIFO 执行"""
    logger.info("触发文档解析: kb_id=%s, doc_id=%s", kb_id, doc_id)

    doc = await document_service.get_document(kb_id, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    if doc["status"] == "queued":
        raise HTTPException(status_code=400, detail="文档已在解析队列中")

    if doc["status"] == "processing":
        raise HTTPException(status_code=400, detail="文档正在解析中")

    if doc["status"] == "completed":
        raise HTTPException(status_code=400, detail="文档已解析完成，无需重复解析")

    if doc["status"] == "partial":
        raise HTTPException(
            status_code=400,
            detail="文档解析部分成功，请使用「继续解析」续跑剩余部分，或「重新解析」全量重来",
        )

    # 更新状态 → queued
    async with async_session() as session:
        repo = DocumentRepository(session)
        await repo.update_status(doc_id, "queued")

    # 加入解析队列
    position = await parse_queue.enqueue(kb_id, doc_id)

    logger.info("文档已加入解析队列: doc_id=%s, queue_position=%d", doc_id, position)
    return {"id": doc_id, "status": "queued", "queue_position": position}


@router.post("/{doc_id}/resume", response_model=ParseResponse, status_code=202)
async def resume_document(kb_id: str, doc_id: str):
    """继续解析部分成功（partial）的文档：从最后已提交处续跑剩余切片。"""
    logger.info("触发继续解析(续跑): kb_id=%s, doc_id=%s", kb_id, doc_id)

    doc = await document_service.get_document(kb_id, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    if doc["status"] == "queued":
        raise HTTPException(status_code=400, detail="文档已在解析队列中")

    if doc["status"] == "processing":
        raise HTTPException(status_code=400, detail="文档正在解析中")

    if doc["status"] != "partial":
        raise HTTPException(
            status_code=400,
            detail="仅「部分成功」状态的文档可继续解析；请使用 /parse 或 /reparse",
        )

    # 更新状态 → queued
    async with async_session() as session:
        repo = DocumentRepository(session)
        await repo.update_status(doc_id, "queued")

    # 加入续跑队列
    position = await parse_queue.enqueue_resume(kb_id, doc_id)

    logger.info("文档已加入续跑队列: doc_id=%s, queue_position=%d", doc_id, position)
    return {"id": doc_id, "status": "queued", "queue_position": position}


@router.post("/{doc_id}/reparse", response_model=ParseResponse, status_code=202)
async def reparse_document(kb_id: str, doc_id: str):
    """将已完成的文档重新加入解析队列，清旧切片后重解析"""
    logger.info("触发重新解析: kb_id=%s, doc_id=%s", kb_id, doc_id)

    doc = await document_service.get_document(kb_id, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    if doc["status"] == "queued":
        raise HTTPException(status_code=400, detail="文档已在解析队列中")

    if doc["status"] == "processing":
        raise HTTPException(status_code=400, detail="文档正在解析中")

    if doc["status"] == "uploaded":
        raise HTTPException(
            status_code=400,
            detail="文档尚未首次解析，请使用 /parse 接口",
        )

    # 更新状态 → queued
    async with async_session() as session:
        repo = DocumentRepository(session)
        await repo.update_status(doc_id, "queued")

    # 加入解析队列
    position = await parse_queue.enqueue_reparse(kb_id, doc_id)

    logger.info("文档已加入重新解析队列: doc_id=%s, queue_position=%d", doc_id, position)
    return {"id": doc_id, "status": "queued", "queue_position": position}


@router.post("/{doc_id}/delete", status_code=204)
async def delete_document(kb_id: str, doc_id: str):
    """删除文档及向量数据"""
    logger.info("删除文档: kb_id=%s, doc_id=%s", kb_id, doc_id)
    success = await document_service.delete_document(kb_id, doc_id)
    if not success:
        logger.warning("删除失败，文档不存在: kb_id=%s, doc_id=%s", kb_id, doc_id)
        raise HTTPException(status_code=404, detail="文档不存在")
    logger.info("文档已删除: kb_id=%s, doc_id=%s", kb_id, doc_id)
