"""chunk_md5_and_unique: document_chunks 加内容指纹 + (doc_id, chunk_index) 唯一约束

解析写库链路按 MD5 幂等（docs/plan/45）：
- 新增 chunk_md5 列（存量回填后为内容指纹，公式与 app `_chunk_md5` 一致）；
- 建 (doc_id, chunk_index) 唯一约束前，先清理历史重复行（每对保留最早一行）。

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 指纹回填：与 app services.document_service._chunk_md5 逐字节一致
#   python: md5(f"{page_number}|{text}".encode("utf-8")).hexdigest()
BACKFILL_SQL = sa.text(
    "UPDATE document_chunks SET chunk_md5 = "
    "md5(convert_to(concat_ws('|', page_number::text, text_content), 'UTF8')) "
    "WHERE chunk_md5 IS NULL"
)
# 清历史重复：同一 (doc_id, chunk_index) 保留最早（created_at, id）最小的一行
DEDUPE_SQL = sa.text(
    "DELETE FROM document_chunks a USING document_chunks b "
    "WHERE a.doc_id = b.doc_id AND a.chunk_index = b.chunk_index "
    "AND (a.created_at, a.id) > (b.created_at, b.id)"
)


def upgrade() -> None:
    op.add_column("document_chunks", sa.Column("chunk_md5", sa.String(length=32), nullable=True))
    op.execute(DEDUPE_SQL)
    op.execute(BACKFILL_SQL)
    op.create_unique_constraint(
        "uq_document_chunks_doc_index", "document_chunks", ["doc_id", "chunk_index"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_document_chunks_doc_index", "document_chunks", type_="unique")
    op.drop_column("document_chunks", "chunk_md5")
