"""
文档管理业务逻辑 — 上传与解析分离。

上传：暂存 → MinIO → PG 记录（status=uploaded）→ 清理临时文件
解析：MinIO 下载 → 解析 → 切片 → 索引 → 状态更新（异步触发）
"""
import os
import uuid
from pathlib import Path

from fastapi import UploadFile

from config import settings
from core.database import async_session
from core.indexing import build_index, chunk_documents, delete_index
from core.parser import parse_document as parse_file
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
    """文档上传 — 仅做存储，不解析。

    流程:
        1. 暂存到临时目录
        2. 上传到 MinIO
        3. PG 写入文档记录（status=uploaded）
        4. 清理临时文件

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

        # 3. PG 写入文档记录（status=uploaded）
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

        logger.info("文档上传完成: doc_id=%s, file=%s", doc_id_str, file.filename)

        return {
            "id": doc_id_str,
            "file_name": file.filename,
            "file_size": file_size,
            "file_type": file_type,
            "status": "uploaded",
            "page_count": None,
            "error_message": None,
            "chunk_count": None,
            "created_at": doc.created_at.isoformat() if doc.created_at else "",
        }

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
        # 4. 清理临时文件
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except OSError:
                pass


async def parse_document(kb_id: str, doc_id: str) -> None:
    """异步解析文档 — 下载 → 解析 → 切片 → 向量化 → 索引。

    流程:
        1. 更新 PG 状态 → processing
        2. 从 MinIO 下载文件到临时目录
        3. 解析文档
        4. 文本切片
        5. Embedding + Milvus 索引
        6. PG 写入切片记录
        7. 更新状态 → completed
        8. 更新知识库文档计数 +1

    异常处理：任一步骤失败 → status=failed，记录 error_message
    清理：finally 中删除临时文件
    """
    temp_path = None

    try:
        # 1. 获取文档记录
        async with async_session() as session:
            doc_repo = DocumentRepository(session)
            doc = await doc_repo.get_by_id(doc_id)
            if doc is None:
                logger.error("解析失败：文档不存在 doc_id=%s", doc_id)
                return

            object_key = doc.object_key
            file_type = doc.file_type

            # 更新状态 → processing，清除旧错误信息
            await doc_repo.update_status(doc_id, "processing", error_message="")

        # 2. 从 MinIO 下载文件到临时目录
        temp_dir = ensure_temp_dir(settings.temp_dir)
        temp_path = Path(temp_dir) / f"{doc_id}.{file_type}"
        minio_client.download(object_key, temp_path)
        logger.info("文档下载完成: %s → %s", object_key, temp_path.name)

        # 3. 解析文档
        llama_docs = await parse_file(temp_path, file_type)
        if not llama_docs:
            raise ValueError("文档解析结果为空，可能是不支持的格式或文件损坏")

        # 将 doc_id 写入 metadata
        for d in llama_docs:
            d.metadata["doc_id"] = doc_id

        # 4. 文本切片
        chunks, metadatas = chunk_documents(llama_docs)

        # 5. Embedding + Milvus 索引
        milvus_pks = await build_index(chunks, metadatas, kb_id)

        # 6. PG 写入切片记录 + 7. 更新状态 → completed
        async with async_session() as session:
            doc_repo = DocumentRepository(session)
            kb_repo = KnowledgeBaseRepository(session)

            await doc_repo.batch_create_chunks(doc_id, kb_id, metadatas, milvus_pks)
            page_count = len(llama_docs)
            chunk_count = len(chunks)
            await doc_repo.update_status(
                doc_id,
                "completed",
                page_count=page_count,
                chunk_count=chunk_count,
            )
            # 8. 更新知识库文档计数
            await kb_repo.increment_doc_count(kb_id)

        logger.info("文档解析完成: doc_id=%s, pages=%d, chunks=%d", doc_id, page_count, chunk_count)

    except Exception as e:
        error_msg = str(e)
        logger.error("文档解析失败: doc_id=%s, error=%s", doc_id, error_msg, exc_info=True)

        # 更新状态 → failed
        try:
            async with async_session() as session:
                doc_repo = DocumentRepository(session)
                await doc_repo.update_status(doc_id, "failed", error_message=error_msg)
        except Exception as db_err:
            logger.error("更新文档失败状态异常: %s", db_err)

    finally:
        # 清理临时文件
        if temp_path and temp_path.exists():
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


async def get_document(kb_id: str, doc_id: str) -> dict | None:
    """获取单个文档信息。"""
    async with async_session() as session:
        doc_repo = DocumentRepository(session)
        doc = await doc_repo.get_by_id(doc_id)
        if doc is None:
            return None
        return _doc_to_dict(doc)


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
