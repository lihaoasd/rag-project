"""
文档 Repository — documents + document_chunks 表 CRUD。
"""
import uuid

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from models.document import Document, DocumentChunk
from utils.logging import get_logger

logger = get_logger(__name__)


class DocumentRepository:
    """封装 documents 和 document_chunks 表的常见操作。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        kb_id: str,
        file_name: str,
        file_size: int,
        file_type: str,
        object_key: str,
    ) -> Document:
        """创建文档记录（初始状态 uploading）。"""
        doc = Document(
            id=uuid.uuid4(),
            kb_id=uuid.UUID(kb_id),
            file_name=file_name,
            file_size=file_size,
            file_type=file_type,
            object_key=object_key,
            status="uploading",
        )
        self.session.add(doc)
        await self.session.commit()
        await self.session.refresh(doc)
        logger.info("文档记录写入 PG: id=%s, kb_id=%s, file=%s", doc.id, kb_id, file_name)
        return doc

    async def update_status(
        self,
        doc_id: str,
        status: str,
        error_message: str | None = None,
        page_count: int | None = None,
        chunk_count: int | None = None,
    ) -> None:
        """更新文档状态和相关字段。"""
        uid = uuid.UUID(doc_id)
        values = {"status": status}
        if error_message is not None:
            values["error_message"] = error_message
        if page_count is not None:
            values["page_count"] = page_count
        if chunk_count is not None:
            values["chunk_count"] = chunk_count
        stmt = update(Document).where(Document.id == uid).values(**values)
        await self.session.execute(stmt)
        await self.session.commit()
        logger.debug("文档状态更新: doc_id=%s, status=%s", doc_id, status)

    async def list_by_kb(self, kb_id: str) -> list[Document]:
        """获取知识库下的所有文档，按创建时间倒序。"""
        try:
            uid = uuid.UUID(kb_id)
        except ValueError:
            return []
        stmt = (
            select(Document)
            .where(Document.kb_id == uid)
            .order_by(Document.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, doc_id: str) -> Document | None:
        """按 ID 获取文档。"""
        try:
            uid = uuid.UUID(doc_id)
        except ValueError:
            return None
        stmt = select(Document).where(Document.id == uid)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def delete_chunks_by_doc_id(self, doc_id: str) -> None:
        """手动删除文档的所有切片（无 FK，需手动级联）。"""
        uid = uuid.UUID(doc_id)
        stmt = delete(DocumentChunk).where(DocumentChunk.doc_id == uid)
        result = await self.session.execute(stmt)
        await self.session.commit()
        logger.debug("切片已删除: doc_id=%s, count=%d", doc_id, result.rowcount)

    async def delete_chunks_by_kb_id(self, kb_id: str) -> None:
        """手动删除知识库下所有文档的切片。"""
        try:
            uid = uuid.UUID(kb_id)
        except ValueError:
            return
        stmt = delete(DocumentChunk).where(DocumentChunk.kb_id == uid)
        result = await self.session.execute(stmt)
        await self.session.commit()
        logger.debug("切片已删除: kb_id=%s, count=%d", kb_id, result.rowcount)

    async def delete_documents_by_kb_id(self, kb_id: str) -> list[Document]:
        """手动删除知识库下所有文档（先删切片）。返回被删除的文档列表以获取 object_keys。"""
        try:
            uid = uuid.UUID(kb_id)
        except ValueError:
            return []
        # 获取文档列表（用于后续 MinIO 清理）
        stmt = select(Document).where(Document.kb_id == uid)
        result = await self.session.execute(stmt)
        docs = list(result.scalars().all())
        # 删除文档记录
        stmt = delete(Document).where(Document.kb_id == uid)
        await self.session.execute(stmt)
        await self.session.commit()
        logger.info("文档已从 PG 删除: kb_id=%s, count=%d", kb_id, len(docs))
        return docs

    async def delete(self, doc_id: str) -> Document | None:
        """删除文档记录（手动先删切片）。返回被删除的文档以获取 object_key。"""
        doc = await self.get_by_id(doc_id)
        if doc is None:
            return None
        # 手动删除关联切片
        await self.delete_chunks_by_doc_id(doc_id)
        await self.session.delete(doc)
        await self.session.commit()
        logger.info("文档已从 PG 删除: doc_id=%s", doc_id)
        return doc

    async def batch_create_chunks(
        self,
        doc_id: str,
        kb_id: str,
        metadatas: list[dict],
        milvus_pks: list[int],
    ) -> None:
        """批量写入切片记录。"""
        uid = uuid.UUID(doc_id)
        kb_uid = uuid.UUID(kb_id)
        chunks = []
        for i, (meta, pk) in enumerate(zip(metadatas, milvus_pks)):
            chunk = DocumentChunk(
                id=uuid.uuid4(),
                doc_id=uid,
                kb_id=kb_uid,
                chunk_index=meta.get("chunk_index", i),
                page_number=meta.get("page_number", 1),
                char_count=len(meta.get("text", "")),
                text_content=meta.get("text", ""),
                milvus_pk=pk,
            )
            chunks.append(chunk)
        self.session.add_all(chunks)
        await self.session.commit()
        logger.info("切片记录写入 PG: doc_id=%s, count=%d", doc_id, len(chunks))
