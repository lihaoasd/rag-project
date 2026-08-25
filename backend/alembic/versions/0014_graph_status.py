"""documents: 新增图谱构建状态列（graph_status 等）

Revision ID: 0014
Revises: 0013
Create Date: 2026-02-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("graph_status", sa.String(length=20), nullable=False, server_default="none"),
    )
    op.add_column("documents", sa.Column("graph_error_message", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("graph_built_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("graph_duration_ms", sa.Integer(), nullable=True))
    op.create_index("idx_documents_kb_graph_status", "documents", ["kb_id", "graph_status"], unique=False)
    # 词库分类枚举化：空分类统一归 other（§5.2.1/§7.3，历史数据一次修正）
    op.execute("UPDATE lexicon_terms SET category = 'other' WHERE category = '' OR category IS NULL")


def downgrade() -> None:
    op.drop_index("idx_documents_kb_graph_status", table_name="documents")
    op.drop_column("documents", "graph_duration_ms")
    op.drop_column("documents", "graph_built_at")
    op.drop_column("documents", "graph_error_message")
    op.drop_column("documents", "graph_status")
