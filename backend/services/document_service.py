"""
文档管理业务逻辑 — 上传 → MinIO → 解析 → 切片 → 索引 → 状态更新。
"""
import os
import uuid
from pathlib import Path

from fastapi import UploadFile

from config import settings
from core.database import async_session
from core.indexing import build_index, chunk_documents, delete_index
from core.parser import parse_document
from repositories.document_repository import DocumentRepository
from repositories.kb_repository import KnowledgeBaseRepository
from utils import minio_client
from utils.file_utils import get_file_extension, ensure_temp_dir
from utils.logging import get_logger

logger = get_logger(__name__)


def _doc_to_dict(doc) -> dict:
    """将 ORM 对象转为 API 响应字典。"""
    return {
        "id": str(doc.id),
        "file_name": doc.file_name,
        "file_size": doc.file_size,
        "file_type": doc.file_type,
        "page_count": doc.page_count,
        "status": doc.status,
        "error_message": doc.error_message,
        "chunk_count": doc.chunk_count,
        "created_at": doc.created_at.isoformat() if doc.created_at else "",
    }


async def upload_document(kb_id: str, file: UploadFile) -> dict:
    """完整文档上传管道。

    流程:
        1. 暂存到临时目录
        2. 上传到 MinIO
        3. PG 写入文档记录（status=uploading）
        4. 更新状态 → processing
        5. 解析文档
        6. 文本切片
        7. Embedding + Milvus 索引
        8. PG 写入切片记录
        9. 更新状态 → completed
        10. 清理临时文件

    异常处理：任一步骤失败 → status=failed，记录 error_message
    """
    if not file.filename:
        raise ValueError("未提供文件")

    file_type = get_file_extension(file.filename)
    temp_dir = ensure_temp_dir(settings.temp_dir)
    temp_path = Path(temp_dir) / f"{uuid.uuid4()}.{file_type}"
    doc_id = str(uuid.uuid4())

    try:
        # 1. 暂存到临时目录
        content = await file.read()
        temp_path.write_bytes(content)
        file_size = len(content)
        logger.info("文档暂存: %s (%d bytes)", temp_path.name, file_size)

        # 2. 上传到 MinIO
        object_key = f"{kb_id}/{doc_id}.{file_type}"
        minio_client.upload(object_key, temp_path)

        # 3. PG 写入文档记录
        async with async_session() as session:
            doc_repo = DocumentRepository(session)
            doc = await doc_repo.create(
                kb_id=kb_id,
                file_name=file.filename,
                file_size=file_size,
                file_type=file_type,
                object_key=object_key,
            )
            doc_id_str = str(doc.id)

            # 4. 更新状态 → processing
            await doc_repo.update_status(doc_id_str, "processing")

        # 5. 解析文档
        llama_docs = await parse_document(temp_path, file_type)
        if not llama_docs:
            raise ValueError("文档解析结果为空，可能是不支持的格式或文件损坏")

        # 将 doc_id 写入 metadata
        for d in llama_docs:
            d.metadata["doc_id"] = doc_id_str

        # 6. 文本切片
        chunks, metadatas = chunk_documents(llama_docs)

        # 7. Embedding + Milvus 索引
        milvus_pks = await build_index(chunks, metadatas, kb_id)

        # 8. PG 写入切片记录 + 更新状态 → completed
        async with async_session() as session:
            doc_repo = DocumentRepository(session)
            kb_repo = KnowledgeBaseRepository(session)

            await doc_repo.batch_create_chunks(doc_id_str, kb_id, metadatas, milvus_pks)
            page_count = len(llama_docs)
            chunk_count = len(chunks)
            await doc_repo.update_status(
                doc_id_str,
                "completed",
                page_count=page_count,
                chunk_count=chunk_count,
            )
            await kb_repo.increment_doc_count(kb_id)

        logger.info("文档上传管道完成: doc_id=%s, pages=%d, chunks=%d", doc_id_str, page_count, chunk_count)

        # 返回最终状态的文档
        async with async_session() as session:
            doc_repo = DocumentRepository(session)
            doc = await doc_repo.get_by_id(doc_id_str)
            return _doc_to_dict(doc) if doc else {"id": doc_id_str, "status": "completed"}

    except Exception as e:
        error_msg = str(e)
        logger.error("文档上传失败: doc_id=%s, error=%s", doc_id, error_msg, exc_info=True)

        # 更新状态 → failed
        try:
            async with async_session() as session:
                doc_repo = DocumentRepository(session)
                await doc_repo.update_status(doc_id, "failed", error_message=error_msg)
        except Exception as db_err:
            logger.error("更新文档失败状态异常: %s", db_err)

        raise

    finally:
        # 10. 清理临时文件
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except OSError:
                pass


async def list_documents(kb_id: str) -> list[dict]:
    """获取知识库下的文档列表。"""
    async with async_session() as session:
        doc_repo = DocumentRepository(session)
        docs = await doc_repo.list_by_kb(kb_id)
        return [_doc_to_dict(d) for d in docs]


async def delete_document(kb_id: str, doc_id: str) -> bool:
    """删除文档 — PG + Milvus + MinIO 三处清理。"""
    # 1. PG 删除
    async with async_session() as session:
        doc_repo = DocumentRepository(session)
        doc = await doc_repo.delete(doc_id)

    if doc is None:
        return False

    # 2. Milvus 删除
    try:
        await delete_index(kb_id, doc_id)
    except Exception as e:
        logger.error("删除 Milvus 向量失败: %s", e, exc_info=True)

    # 3. MinIO 删除
    try:
        minio_client.delete(doc.object_key)
    except Exception as e:
        logger.error("删除 MinIO 文件失败: %s", e, exc_info=True)

    # 4. 更新知识库文档计数
    try:
        async with async_session() as session:
            kb_repo = KnowledgeBaseRepository(session)
            await kb_repo.decrement_doc_count(kb_id)
    except Exception as e:
        logger.error("更新文档计数失败: %s", e, exc_info=True)

    return True
