"""all_pks_to_varchar: 所有表主键 id UUID → VARCHAR(36)

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-12
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = [
    "knowledge_bases",
    "documents",
    "document_chunks",
    "conversations",
    "messages",
    "llm_configs",
    "embedding_configs",
]


def upgrade() -> None:
    # 1. 删除主键约束（含依赖的索引）
    for t in TABLES:
        op.execute(f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {t}_pkey CASCADE")

    # 2. 修改列类型
    for t in TABLES:
        op.execute(
            f"ALTER TABLE {t} ALTER COLUMN id TYPE VARCHAR(36) USING id::text"
        )

    # 3. 重建主键约束
    for t in TABLES:
        op.execute(f"ALTER TABLE {t} ADD PRIMARY KEY (id)")


def downgrade() -> None:
    for t in TABLES:
        op.execute(f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {t}_pkey CASCADE")

    for t in TABLES:
        op.execute(
            f"ALTER TABLE {t} ALTER COLUMN id TYPE UUID USING id::uuid"
        )

    for t in TABLES:
        op.execute(f"ALTER TABLE {t} ADD PRIMARY KEY (id)")
