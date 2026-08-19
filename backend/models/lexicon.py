"""
词库词条 ORM 模型 — 前端维护的自定义词典（分词 + 查询扩展）。
"""
from sqlalchemy import Boolean, String, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin, TimestampMixin, UTF8JSONB


class LexiconTerm(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "lexicon_terms"

    term: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    aliases: Mapped[list] = mapped_column(
        "aliases",
        UTF8JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="", server_default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
