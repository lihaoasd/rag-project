"""documents: 批次解析进度与整篇解析耗时

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("processed_chunk_count", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("parse_duration_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "parse_duration_ms")
    op.drop_column("documents", "processed_chunk_count")
