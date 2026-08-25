"""
Document + DocumentChunk ORM 模型（无外键约束）。
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin, TimestampMixin, CreatedAtMixin, UTF8JSONB


class Document(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "documents"

    kb_id: Mapped[str] = mapped_column(String(36), nullable=False)
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="uploaded", server_default="uploaded", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processed_chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parse_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 图谱构建状态：none/building/completed/failed
    graph_status: Mapped[str] = mapped_column(String(20), default="none", server_default="none", nullable=False)
    graph_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    graph_built_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    graph_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class DocumentChunk(Base, UUIDMixin, CreatedAtMixin):
    __tablename__ = "document_chunks"

    doc_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kb_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    text_content: Mapped[str] = mapped_column(Text, nullable=False)
    milvus_pk: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chunk_metadata: Mapped[dict] = mapped_column(
        "metadata",
        UTF8JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
