"""change_milvus_pk_to_string: document_chunks.milvus_pk BigInteger → String(64)

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-11
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import String

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "document_chunks",
        "milvus_pk",
        existing_type=None,  # BigInteger
        type_=String(64),
        existing_nullable=True,
    )


def downgrade() -> None:
    # 降级时丢失非整数数据，仅保留 NULL 和纯数字值
    op.execute(
        "UPDATE document_chunks SET milvus_pk = NULL "
        "WHERE milvus_pk IS NOT NULL AND milvus_pk !~ '^\\d+$'"
    )
    op.execute(
        "ALTER TABLE document_chunks ALTER COLUMN milvus_pk TYPE bigint USING milvus_pk::bigint"
    )
