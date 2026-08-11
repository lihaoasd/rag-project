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
            status="uploaded",
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

    # ============================================================
    # 单切片操作
    # ============================================================

    async def list_chunks_by_doc_id(
        self, doc_id: str, offset: int = 0, limit: int = 20
    ) -> tuple[list[DocumentChunk], int]:
        """分页查询文档的切片列表（按 chunk_index 升序）。"""
        try:
            uid = uuid.UUID(doc_id)
        except ValueError:
            return [], 0

        count_stmt = select(DocumentChunk).where(DocumentChunk.doc_id == uid)
        count_result = await self.session.execute(count_stmt)
        total = len(list(count_result.scalars().all()))

        stmt = (
            select(DocumentChunk)
            .where(DocumentChunk.doc_id == uid)
            .order_by(DocumentChunk.chunk_index.asc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        chunks = list(result.scalars().all())
        logger.debug(
            "切片列表查询: doc_id=%s, offset=%d, limit=%d, total=%d",
            doc_id, offset, limit, total,
        )
        return chunks, total

    async def get_chunk_by_id(self, chunk_id: str) -> DocumentChunk | None:
        """按 ID 获取单个切片。"""
        try:
            uid = uuid.UUID(chunk_id)
        except ValueError:
            return None
        stmt = select(DocumentChunk).where(DocumentChunk.id == uid)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def delete_chunk_by_id(self, chunk_id: str) -> DocumentChunk | None:
        """删除单条切片，返回被删除对象（调用方需获取 milvus_pk 和 doc_id）。"""
        chunk = await self.get_chunk_by_id(chunk_id)
        if chunk is None:
            return None
        await self.session.delete(chunk)
        await self.session.commit()
        logger.debug("切片已从 PG 删除: chunk_id=%s", chunk_id)
        return chunk

    async def update_chunk_text(
        self,
        chunk_id: str,
        new_text: str,
        new_char_count: int,
        new_milvus_pk: int | None = None,
    ) -> None:
        """更新切片文本、字符数，可选更新 Milvus PK。"""
        uid = uuid.UUID(chunk_id)
        values: dict = {"text_content": new_text, "char_count": new_char_count}
        if new_milvus_pk is not None:
            values["milvus_pk"] = new_milvus_pk
        stmt = update(DocumentChunk).where(DocumentChunk.id == uid).values(**values)
        await self.session.execute(stmt)
        await self.session.commit()
        logger.debug("切片文本已更新: chunk_id=%s, char_count=%d", chunk_id, new_char_count)

    async def update_doc_chunk_count(self, doc_id: str, delta: int) -> None:
        """原子更新文档的切片计数（+1 或 -1）。"""
        uid = uuid.UUID(doc_id)
        stmt = (
            update(Document)
            .where(Document.id == uid)
            .values(chunk_count=Document.chunk_count + delta)
        )
        await self.session.execute(stmt)
        await self.session.commit()
        logger.debug("文档切片计数更新: doc_id=%s, delta=%+d", doc_id, delta)
