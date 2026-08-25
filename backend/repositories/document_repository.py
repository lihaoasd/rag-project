"""
文档 Repository — documents + document_chunks 表 CRUD。
"""
import uuid

from sqlalchemy import delete, func, select, update
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
            id=str(uuid.uuid4()),
            kb_id=kb_id,
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
        processed_chunk_count: int | None = None,
        parse_duration_ms: int | None = None,
    ) -> None:
        """更新文档状态和相关字段。"""
        uuid.UUID(doc_id)  # 验证格式
        values = {"status": status}
        if error_message is not None:
            values["error_message"] = error_message
        if page_count is not None:
            values["page_count"] = page_count
        if chunk_count is not None:
            values["chunk_count"] = chunk_count
        if processed_chunk_count is not None:
            values["processed_chunk_count"] = processed_chunk_count
        if parse_duration_ms is not None:
            values["parse_duration_ms"] = parse_duration_ms
        stmt = update(Document).where(Document.id == doc_id).values(**values)
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
            .where(Document.kb_id == str(uid))
            .order_by(Document.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, doc_id: str) -> Document | None:
        """按 ID 获取文档。"""
        try:
            uuid.UUID(doc_id)  # 验证格式
        except ValueError:
            return None
        stmt = select(Document).where(Document.id == doc_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_ids(self, doc_ids: list[str]) -> list[Document]:
        """按 ID 列表批量获取文档（图谱证据文件名映射用）。"""
        if not doc_ids:
            return []
        try:
            ids = [str(uuid.UUID(d)) for d in doc_ids]
        except ValueError:
            return []
        stmt = select(Document).where(Document.id.in_(ids))
        return list((await self.session.scalars(stmt)).all())

    async def delete_chunks_by_doc_id(self, doc_id: str) -> None:
        """手动删除文档的所有切片（无 FK，需手动级联）。"""
        uuid.UUID(doc_id)  # 验证格式
        stmt = delete(DocumentChunk).where(DocumentChunk.doc_id == doc_id)
        result = await self.session.execute(stmt)
        await self.session.commit()
        logger.debug("切片已删除: doc_id=%s, count=%d", doc_id, result.rowcount)

    async def delete_chunks_by_kb_id(self, kb_id: str) -> None:
        """手动删除知识库下所有文档的切片。"""
        try:
            uid = uuid.UUID(kb_id)
        except ValueError:
            return
        stmt = delete(DocumentChunk).where(DocumentChunk.kb_id == str(uid))
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
        stmt = select(Document).where(Document.kb_id == str(uid))
        result = await self.session.execute(stmt)
        docs = list(result.scalars().all())
        # 删除文档记录
        stmt = delete(Document).where(Document.kb_id == str(uid))
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
        milvus_pks: list[str],
    ) -> None:
        """批量写入切片记录。"""
        uuid.UUID(doc_id)  # 验证格式
        uuid.UUID(kb_id)   # 验证格式
        chunks = []
        for i, (meta, pk) in enumerate(zip(metadatas, milvus_pks)):
            chunk = DocumentChunk(
                id=str(uuid.uuid4()),
                doc_id=doc_id,
                kb_id=kb_id,
                chunk_index=meta.get("chunk_index", i),
                page_number=meta.get("page_number", 1),
                char_count=len(meta.get("text", "")),
                text_content=meta.get("text", ""),
                milvus_pk=pk,
                chunk_metadata={
                    "chunk_type": meta.get("chunk_type", "text"),
                    "source_chunk_index": meta.get("source_chunk_index", -1),
                    "image_index": meta.get("image_index", 0),
                    "object_key": meta.get("object_key", ""),
                    "mime_type": meta.get("mime_type", ""),
                    "image_refs": meta.get("image_refs", []),
                    "image_keys": meta.get("image_keys", []),
                    "heading": meta.get("heading", ""),
                    "heading_path": meta.get("heading_path", ""),
                },
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
            uuid.UUID(doc_id)  # 验证格式
        except ValueError:
            return [], 0

        count_stmt = select(DocumentChunk).where(DocumentChunk.doc_id == doc_id)
        count_result = await self.session.execute(count_stmt)
        total = len(list(count_result.scalars().all()))

        stmt = (
            select(DocumentChunk)
            .where(DocumentChunk.doc_id == doc_id)
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

    async def list_chunks_by_kb(self, kb_id: str) -> list[DocumentChunk]:
        """查询知识库下全部切片（ES rebuild 用，按 chunk_index 升序）。"""
        stmt = (
            select(DocumentChunk)
            .where(DocumentChunk.kb_id == kb_id)
            .order_by(DocumentChunk.chunk_index.asc())
        )
        return list((await self.session.scalars(stmt)).all())

    async def list_chunks_for_graph(self, doc_id: str) -> list[DocumentChunk]:
        """查询文档全部切片（图谱构建用，含 id/text_content/page_number，按 chunk_index 升序）。"""
        try:
            uuid.UUID(doc_id)  # 验证格式
        except ValueError:
            return []
        stmt = (
            select(DocumentChunk)
            .where(DocumentChunk.doc_id == doc_id)
            .order_by(DocumentChunk.chunk_index.asc())
        )
        return list((await self.session.scalars(stmt)).all())

    async def get_chunks_by_ids(self, kb_id: str, chunk_ids: list[str]) -> list[DocumentChunk]:
        """按 chunk ID 列表批量查询切片（图谱检索证据用，含文件名 join 需调用方补充）。"""
        if not chunk_ids:
            return []
        stmt = (
            select(DocumentChunk)
            .where(DocumentChunk.kb_id == kb_id, DocumentChunk.id.in_(chunk_ids))
        )
        return list((await self.session.scalars(stmt)).all())

    async def count_chunks_by_kb(self) -> dict[str, int]:
        """按知识库统计切片数（ES 对账用）。"""
        rows = (
            await self.session.execute(
                select(DocumentChunk.kb_id, func.count(DocumentChunk.id)).group_by(
                    DocumentChunk.kb_id
                )
            )
        ).all()
        return {kb_id: cnt for kb_id, cnt in rows}

    async def count_chunks_by_doc(self) -> dict[str, int]:
        """按文档统计切片数：{doc_id: count}（文档级对账用）。"""
        rows = (
            await self.session.execute(
                select(DocumentChunk.doc_id, func.count(DocumentChunk.id)).group_by(
                    DocumentChunk.doc_id
                )
            )
        ).all()
        return {str(doc_id): cnt for doc_id, cnt in rows}

    async def count_docs_by_kb(self) -> dict[str, int]:
        """按知识库统计文档数（ES 对账用）。"""
        rows = (
            await self.session.execute(
                select(Document.kb_id, func.count(Document.id)).group_by(Document.kb_id)
            )
        ).all()
        return {kb_id: cnt for kb_id, cnt in rows}

    async def list_doc_meta(self) -> dict[str, dict]:
        """全部文档元数据：{doc_id: {"kb_id": ..., "file_name": ...}}（文档级对账用）。"""
        rows = (
            await self.session.execute(select(Document.id, Document.kb_id, Document.file_name))
        ).all()
        return {
            str(doc_id): {"kb_id": kb_id, "file_name": file_name}
            for doc_id, kb_id, file_name in rows
        }

    async def get_chunk_by_id(self, chunk_id: str) -> DocumentChunk | None:
        """按 ID 获取单个切片。"""
        try:
            uuid.UUID(chunk_id)  # 验证格式
        except ValueError:
            return None
        stmt = select(DocumentChunk).where(DocumentChunk.id == chunk_id)
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
        new_milvus_pk: str | None = None,
    ) -> None:
        """更新切片文本、字符数，可选更新 Milvus PK。"""
        uuid.UUID(chunk_id)  # 验证格式
        values: dict = {"text_content": new_text, "char_count": new_char_count}
        if new_milvus_pk is not None:
            values["milvus_pk"] = new_milvus_pk
        stmt = update(DocumentChunk).where(DocumentChunk.id == chunk_id).values(**values)
        await self.session.execute(stmt)
        await self.session.commit()
        logger.debug("切片文本已更新: chunk_id=%s, char_count=%d", chunk_id, new_char_count)

    async def update_doc_chunk_count(self, doc_id: str, delta: int) -> None:
        """原子更新文档的切片计数（+1 或 -1）。"""
        uuid.UUID(doc_id)  # 验证格式
        stmt = (
            update(Document)
            .where(Document.id == doc_id)
            .values(chunk_count=Document.chunk_count + delta)
        )
        await self.session.execute(stmt)
        await self.session.commit()
        logger.debug("文档切片计数更新: doc_id=%s, delta=%+d", doc_id, delta)

    # ============================================================
    # 图谱状态
    # ============================================================

    async def update_graph_status(
        self,
        doc_id: str,
        graph_status: str,
        error_message: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """更新文档图谱构建状态。graph_status: none/building/completed/failed"""
        uuid.UUID(doc_id)  # 验证格式
        values: dict = {"graph_status": graph_status}
        if error_message is not None:
            values["graph_error_message"] = error_message
        if duration_ms is not None:
            values["graph_duration_ms"] = duration_ms
        if graph_status == "completed":
            from datetime import datetime, timezone

            values["graph_built_at"] = datetime.now(timezone.utc)
        stmt = update(Document).where(Document.id == doc_id).values(**values)
        await self.session.execute(stmt)
        await self.session.commit()
        logger.info("图谱状态更新: doc_id=%s, graph_status=%s", doc_id, graph_status)

    async def reset_graph_status_by_kb(self, kb_id: str, status: str = "none") -> int:
        """批量重置知识库下文档的图谱状态（清空图谱后调用）。"""
        try:
            uid = uuid.UUID(kb_id)
        except ValueError:
            return 0
        stmt = (
            update(Document)
            .where(Document.kb_id == str(uid))
            .values(graph_status=status, graph_error_message=None)
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        logger.info("图谱状态批量重置: kb_id=%s, status=%s, count=%d", kb_id, status, result.rowcount)
        return result.rowcount or 0

    async def reset_stale_graph_status(self) -> int:
        """启动时重置滞留 building 的图谱状态 → none（队列为内存队列，重启丢失任务）。"""
        stmt = (
            update(Document)
            .where(Document.graph_status == "building")
            .values(graph_status="none", graph_error_message=None)
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        if result.rowcount:
            logger.warning("滞留 building 图谱状态已重置为 none: %d 条", result.rowcount)
        return result.rowcount or 0

    async def invalidate_all_graphs(self) -> int:
        """词库变更联动（风险 10）：所有已构建文档图谱状态置 none（stale 标记，提示重建）。"""
        stmt = (
            update(Document)
            .where(Document.graph_status == "completed")
            .values(graph_status="none", graph_error_message=None)
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        if result.rowcount:
            logger.info("词库变更联动：%d 个已建图文档状态置 none（提示重建）", result.rowcount)
        return result.rowcount or 0
