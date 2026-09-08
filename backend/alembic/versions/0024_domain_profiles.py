"""domain_profiles: 领域模板表 + knowledge_bases 绑定列 + 内置预设种子

v2 通用知识库（docs/10-generic-kb-v2-design.md §2/§9 / docs/plan/47）：
- 新增 domain_profiles（实体/关系类型清单 JSONB，is_preset 标识内置预设）；
- knowledge_bases 增加 domain_profile_id（无外键，应用层校验，沿项目去外键风格）；
- 种子：内置"通用"/"投标"两枚预设（core.domain_presets 为单一事实源，供代码复用）。

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-08
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from core.domain_presets import BID_PRESET, GENERIC_PRESET

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 固定种子 ID（UUID 风格，可读、可被代码回退逻辑引用）
GENERIC_PRESET_ID = "00000000-0000-4000-8000-000000000001"
BID_PRESET_ID = "00000000-0000-4000-8000-000000000002"


def upgrade() -> None:
    op.create_table(
        "domain_profiles",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("entity_types", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("relation_types", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("is_preset", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_domain_profiles_name", "domain_profiles", ["name"])

    op.add_column(
        "knowledge_bases",
        sa.Column("domain_profile_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        "ix_knowledge_bases_domain_profile_id", "knowledge_bases", ["domain_profile_id"]
    )

    conn = op.get_bind()
    _insert_preset(conn, GENERIC_PRESET_ID, GENERIC_PRESET)
    _insert_preset(conn, BID_PRESET_ID, BID_PRESET)


def _insert_preset(conn, preset_id: str, preset: dict) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO domain_profiles "
            "(id, name, description, entity_types, relation_types, is_preset) "
            "VALUES (:id, :name, :desc, CAST(:entity_types AS jsonb), "
            "CAST(:relation_types AS jsonb), TRUE)"
        ),
        {
            "id": preset_id,
            "name": preset["name"],
            "desc": preset["description"],
            "entity_types": json.dumps(preset["entity_types"], ensure_ascii=False),
            "relation_types": json.dumps(preset["relation_types"], ensure_ascii=False),
        },
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_bases_domain_profile_id", table_name="knowledge_bases")
    op.drop_column("knowledge_bases", "domain_profile_id")
    op.drop_constraint("uq_domain_profiles_name", "domain_profiles", type_="unique")
    op.drop_table("domain_profiles")
