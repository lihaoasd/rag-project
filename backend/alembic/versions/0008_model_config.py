"""model_configs + kb_model_bindings: 统一模型配置 + 知识库模型绑定

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ============================================
    # 1. model_configs（统一模型配置）
    # ============================================
    op.create_table(
        "model_configs",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("model_type", sa.String(20), nullable=False),
        sa.Column("platform", sa.String(50), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("api_base", sa.String(512), nullable=True),
        sa.Column("api_key", sa.String(512), nullable=True),
        sa.Column("params", postgresql.JSONB(), nullable=True),
        sa.Column("is_default", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("name", name="uq_model_configs_name"),
    )
    op.execute("""
        ALTER TABLE model_configs ADD CONSTRAINT chk_model_configs_type
            CHECK (model_type IN ('llm', 'embedding', 'rerank', 'vision', 'ocr'));
    """)
    op.create_index("idx_model_configs_type", "model_configs", ["model_type"], unique=False)
    op.create_index("uq_model_configs_default", "model_configs", ["model_type"], unique=True,
                    postgresql_where=sa.text("is_default = TRUE"))
    op.execute("""
        CREATE TRIGGER trg_model_configs_updated_at
            BEFORE UPDATE ON model_configs
            FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();
    """)

    # ============================================
    # 2. kb_model_bindings（知识库模型绑定）
    # ============================================
    op.create_table(
        "kb_model_bindings",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("kb_id", sa.String(36), nullable=False),
        sa.Column("model_type", sa.String(20), nullable=False),
        sa.Column("model_config_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("kb_id", "model_type", name="uq_kb_bindings_kb_type"),
    )
    op.create_index("idx_kb_bindings_config", "kb_model_bindings", ["model_config_id"], unique=False)
    op.execute("""
        CREATE TRIGGER trg_kb_model_bindings_updated_at
            BEFORE UPDATE ON kb_model_bindings
            FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();
    """)

    # ============================================
    # 3. 迁移旧表数据 → model_configs
    # ============================================
    op.execute("""
        INSERT INTO model_configs (id, name, model_type, platform, model, api_base, api_key, params, is_default, created_at, updated_at)
        SELECT
            id, name, 'llm', provider, model, api_base, api_key,
            jsonb_build_object(
                'temperature', temperature,
                'max_tokens', max_tokens,
                'context_window', context_window
            ),
            is_active, created_at, updated_at
        FROM llm_configs;
    """)
    op.execute("""
        INSERT INTO model_configs (id, name, model_type, platform, model, api_base, api_key, params, is_default, created_at, updated_at)
        SELECT
            id, name, 'embedding', 'xinference', model, api_base, api_key,
            jsonb_build_object(
                'dimension', dimension,
                'chunk_size', chunk_size,
                'chunk_overlap', chunk_overlap
            ),
            is_active, created_at, updated_at
        FROM embedding_configs;
    """)

    # ============================================
    # 4. 删除旧表
    # ============================================
    op.drop_table("embedding_configs")
    op.drop_table("llm_configs")


def downgrade() -> None:
    op.drop_table("kb_model_bindings")
    op.drop_table("model_configs")
