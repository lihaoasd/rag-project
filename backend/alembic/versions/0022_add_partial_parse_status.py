"""add_partial_parse_status: 添加 'partial' 到 documents.status 约束

解析按批次落库，中途失败时保留已成功批次并把文档标记为 partial（部分成功），
供“继续解析/续跑”从最后提交点接着写入（见 docs/plan/43-partial-parse-resume-plan.md）。

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-05
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STATUSES = "'uploaded', 'queued', 'uploading', 'processing', 'completed', 'failed', 'partial'"
STATUSES_OLD = "'uploaded', 'queued', 'uploading', 'processing', 'completed', 'failed'"


def upgrade() -> None:
    op.execute("ALTER TABLE documents DROP CONSTRAINT IF EXISTS chk_documents_status;")
    op.execute(f"ALTER TABLE documents ADD CONSTRAINT chk_documents_status CHECK (status IN ({STATUSES}));")


def downgrade() -> None:
    op.execute("ALTER TABLE documents DROP CONSTRAINT IF EXISTS chk_documents_status;")
    op.execute(f"ALTER TABLE documents ADD CONSTRAINT chk_documents_status CHECK (status IN ({STATUSES_OLD}));")
