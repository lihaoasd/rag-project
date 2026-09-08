"""
KnowledgeBase ORM 模型（无外键约束）。
"""
from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin, TimestampMixin


class KnowledgeBase(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "knowledge_bases"

    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    # 领域模板（v2）：图谱实体/关系本体来源；NULL = 未绑定（缺省按内置通用预设处理）
    domain_profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
