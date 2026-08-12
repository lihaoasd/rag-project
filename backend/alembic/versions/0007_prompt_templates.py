"""prompt_templates: 提示词模板管理

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ============================================
    # 8. prompt_templates
    # ============================================
    op.create_table(
        "prompt_templates",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("scenario", sa.String(50), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("user_prompt", sa.Text(), nullable=True),
        sa.Column("variables", postgresql.JSONB(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("name", name="uq_prompt_templates_name"),
    )
    op.execute("""
        ALTER TABLE prompt_templates ADD CONSTRAINT chk_prompt_templates_scenario
            CHECK (scenario IN ('rag_qa', 'entity_extraction', 'document_summary', 'custom'));
    """)
    op.create_index("idx_prompt_templates_scenario", "prompt_templates", ["scenario"], unique=False)
    op.create_index("idx_prompt_templates_active", "prompt_templates", ["scenario", "is_active"], unique=False,
                    postgresql_where=sa.text("is_active = TRUE"))
    op.create_index("idx_prompt_templates_default", "prompt_templates", ["scenario", "is_default"], unique=False,
                    postgresql_where=sa.text("is_default = TRUE"))
    op.execute("""
        CREATE TRIGGER trg_prompt_templates_updated_at
            BEFORE UPDATE ON prompt_templates
            FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();
    """)

    # === 默认数据：RAG 问答模板 ===
    op.execute("""
        INSERT INTO prompt_templates (id, name, description, scenario, system_prompt, variables, is_active, is_default, sort_order)
        VALUES (
            uuid_generate_v4()::varchar,
            'RAG 问答（默认）',
            '基于文档内容回答用户问题，标注来源',
            'rag_qa',
            '你是一个投标文档分析助手。请根据以下文档内容回答用户问题。

【参考文档内容】
{context_str}

【对话历史】
{history}

【用户问题】
{query}

请基于参考文档内容回答。如果文档中找不到相关信息，请明确说明。
回答时请注明信息来源（文档名、页码）。',
            '[
              {"name": "context_str", "label": "检索上下文", "required": true, "description": "从知识库检索到的文档片段"},
              {"name": "query", "label": "用户问题", "required": true, "description": "用户输入的原始问题"},
              {"name": "history", "label": "对话历史", "required": true, "description": "最近几轮对话记录"},
              {"name": "kb_name", "label": "知识库名称", "required": false, "description": "当前使用的知识库名称"}
            ]'::jsonb,
            TRUE,
            TRUE,
            0
        );
    """)


def downgrade() -> None:
    op.drop_table("prompt_templates")
