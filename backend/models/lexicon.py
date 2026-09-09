"""
词库词条 ORM 模型 — 全局自定义词典（ES 分词 IK 词典导出 + 查询同义词扩展）。
词条与图谱的关联（属于哪个领域模板的哪个实体类型）由 lexicon_domain_mappings 表达（词条×领域多对多）。
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
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
