"""
词库词条 × 领域模板映射表：drop category 列/索引 + 建 lexicon_domain_mappings

v2 修订（docs/10 §7 / docs/plan/48）：
- 词库与图谱的关联改为“词条 × 领域模板 → 实体 code”多对多映射；
- 全局固定分类（category，决定固定实体类型）删除，历史数据弃置；
- 新表：lexicon_domain_mappings（唯一 (lexicon_term_id, domain_profile_id)）。

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("idx_lexicon_terms_category", table_name="lexicon_terms")
    op.drop_column("lexicon_terms", "category")

    op.create_table(
        "lexicon_domain_mappings",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("lexicon_term_id", sa.String(length=36), nullable=False),
        sa.Column("domain_profile_id", sa.String(length=36), nullable=False),
        sa.Column("entity_code", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_lexicon_domain_mappings_term_profile",
        "lexicon_domain_mappings",
        ["lexicon_term_id", "domain_profile_id"],
    )
    op.create_index(
        "ix_lexicon_domain_mappings_profile",
        "lexicon_domain_mappings",
        ["domain_profile_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_lexicon_domain_mappings_profile", table_name="lexicon_domain_mappings")
    op.drop_constraint(
        "uq_lexicon_domain_mappings_term_profile",
        "lexicon_domain_mappings",
        type_="unique",
    )
    op.drop_table("lexicon_domain_mappings")

    op.add_column(
        "lexicon_terms",
        sa.Column("category", sa.String(length=50), nullable=False, server_default=""),
    )
    op.create_index("idx_lexicon_terms_category", "lexicon_terms", ["category"], unique=False)
