"""change_uuid_to_varchar: conversations.kb_id, messages.conversation_id UUID → VARCHAR(36)

与 ORM 模型 "无外键约束" 设计一致，迁移中删除相关 FK 约束。

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-12
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 删除外键约束（与 ORM 无外键设计一致）
    op.execute("ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_kb_id_fkey")
    op.execute("ALTER TABLE messages DROP CONSTRAINT IF EXISTS messages_conversation_id_fkey")

    # 2. 修改列类型
    op.execute(
        "ALTER TABLE conversations ALTER COLUMN kb_id TYPE VARCHAR(36) USING kb_id::text"
    )
    op.execute(
        "ALTER TABLE messages ALTER COLUMN conversation_id TYPE VARCHAR(36) USING conversation_id::text"
    )


def downgrade() -> None:
    # 1. 还原列类型
    op.execute(
        "ALTER TABLE conversations ALTER COLUMN kb_id TYPE UUID USING kb_id::uuid"
    )
    op.execute(
        "ALTER TABLE messages ALTER COLUMN conversation_id TYPE UUID USING conversation_id::uuid"
    )

    # 2. 重建外键约束
    op.execute("""
        ALTER TABLE conversations ADD CONSTRAINT conversations_kb_id_fkey
            FOREIGN KEY (kb_id) REFERENCES knowledge_bases(id)
    """)
    op.execute("""
        ALTER TABLE messages ADD CONSTRAINT messages_conversation_id_fkey
            FOREIGN KEY (conversation_id) REFERENCES conversations(id)
    """)
