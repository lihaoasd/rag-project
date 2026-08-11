"""add_uploaded_queued_status: 添加 'uploaded' 和 'queued' 到 documents.status 约束

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-08
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STATUSES = "'uploaded', 'queued', 'uploading', 'processing', 'completed', 'failed'"
STATUSES_OLD = "'uploading', 'processing', 'completed', 'failed'"


def upgrade() -> None:
    op.execute("ALTER TABLE documents DROP CONSTRAINT IF EXISTS chk_documents_status;")
    op.execute(f"ALTER TABLE documents ADD CONSTRAINT chk_documents_status CHECK (status IN ({STATUSES}));")


def downgrade() -> None:
    op.execute("ALTER TABLE documents DROP CONSTRAINT IF EXISTS chk_documents_status;")
    op.execute(f"ALTER TABLE documents ADD CONSTRAINT chk_documents_status CHECK (status IN ({STATUSES_OLD}));")
