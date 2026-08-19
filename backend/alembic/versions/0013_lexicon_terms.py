"""lexicon_terms: 词库词条表（前端维护的自定义词典）

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "lexicon_terms",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("term", sa.String(length=255), nullable=False),
        sa.Column("aliases", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("category", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("term", name="uq_lexicon_terms_term"),
    )
    op.create_index("idx_lexicon_terms_category", "lexicon_terms", ["category"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_lexicon_terms_category", table_name="lexicon_terms")
    op.drop_table("lexicon_terms")
