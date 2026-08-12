"""drop_all_fks: 删除剩余外键，documents.kb_id / document_chunks.doc_id,kb_id UUID → VARCHAR(36)

与 ORM 模型 "无外键约束" + String(36) 设计一致。

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-12
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 删除剩余外键约束
    op.execute("ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_kb_id_fkey")
    op.execute("ALTER TABLE document_chunks DROP CONSTRAINT IF EXISTS document_chunks_doc_id_fkey")
    op.execute("ALTER TABLE document_chunks DROP CONSTRAINT IF EXISTS document_chunks_kb_id_fkey")

    # 2. 修改列类型
    op.execute(
        "ALTER TABLE documents ALTER COLUMN kb_id TYPE VARCHAR(36) USING kb_id::text"
    )
    op.execute(
        "ALTER TABLE document_chunks ALTER COLUMN doc_id TYPE VARCHAR(36) USING doc_id::text"
    )
    op.execute(
        "ALTER TABLE document_chunks ALTER COLUMN kb_id TYPE VARCHAR(36) USING kb_id::text"
    )


def downgrade() -> None:
    # 1. 还原列类型
    op.execute(
        "ALTER TABLE documents ALTER COLUMN kb_id TYPE UUID USING kb_id::uuid"
    )
    op.execute(
        "ALTER TABLE document_chunks ALTER COLUMN doc_id TYPE UUID USING doc_id::uuid"
    )
    op.execute(
        "ALTER TABLE document_chunks ALTER COLUMN kb_id TYPE UUID USING kb_id::uuid"
    )

    # 2. 重建外键约束
    op.execute("""
        ALTER TABLE documents ADD CONSTRAINT documents_kb_id_fkey
            FOREIGN KEY (kb_id) REFERENCES knowledge_bases(id)
    """)
    op.execute("""
        ALTER TABLE document_chunks ADD CONSTRAINT document_chunks_doc_id_fkey
            FOREIGN KEY (doc_id) REFERENCES documents(id)
    """)
    op.execute("""
        ALTER TABLE document_chunks ADD CONSTRAINT document_chunks_kb_id_fkey
            FOREIGN KEY (kb_id) REFERENCES knowledge_bases(id)
    """)
