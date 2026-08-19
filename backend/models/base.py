"""
SQLAlchemy 基础模型 — DeclarativeBase + 通用 Mixin。
"""
import json
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UTF8JSONB(JSONB):
    """JSONB 类型：序列化时保留 UTF-8 原文，避免反斜杠 u 转义。"""

    def bind_processor(self, dialect):
        def process(value):
            if value is None:
                return None
            return json.dumps(value, ensure_ascii=False)

        return process


class Base(DeclarativeBase):
    """所有 ORM 模型的基类"""
    pass


class UUIDMixin:
    """UUID 主键 Mixin（存储为 VARCHAR(36)）"""

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )


class TimestampMixin:
    """自动管理 created_at / updated_at"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class CreatedAtMixin:
    """仅 created_at（用于不可变记录，如 document_chunks、messages）"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
