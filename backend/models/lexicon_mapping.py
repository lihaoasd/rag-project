"""
词库词条 × 领域模板 映射 ORM 模型（v2 docs/10 §7 / docs/plan/48）。

语义：
- 词条在“某领域模板”下映射为该模板内的 1 个实体 code（同域唯一）；
- 无映射的领域 → 该词条在该域不进图（仍全局分词/同义词）；
- 无外键（项目去外键风格），词条/模板删除的映射清理由应用层保证。
"""
from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin, TimestampMixin


class LexiconDomainMapping(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "lexicon_domain_mappings"
    __table_args__ = (
        UniqueConstraint(
            "lexicon_term_id", "domain_profile_id",
            name="uq_lexicon_domain_mappings_term_profile",
        ),
    )

    lexicon_term_id: Mapped[str] = mapped_column(String(36), nullable=False)
    domain_profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    entity_code: Mapped[str] = mapped_column(String(64), nullable=False)
