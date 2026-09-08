"""
领域模板 ORM 模型 — 用户页面可配置的图谱本体（实体/关系类型清单）。
"""
from sqlalchemy import Boolean, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin, TimestampMixin, UTF8JSONB


class DomainProfile(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "domain_profiles"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_types: Mapped[list] = mapped_column(
        "entity_types",
        UTF8JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    relation_types: Mapped[list] = mapped_column(
        "relation_types",
        UTF8JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    is_preset: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
