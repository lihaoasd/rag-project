"""image_recognition 默认提示词模板

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-19
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("chk_prompt_templates_scenario", "prompt_templates", type_="check")
    op.create_check_constraint(
        "chk_prompt_templates_scenario",
        "prompt_templates",
        "scenario IN ('rag_qa', 'entity_extraction', 'document_summary', 'custom', 'image_recognition')",
    )
    op.execute("""
        INSERT INTO prompt_templates (
            id, name, description, scenario, system_prompt, variables,
            is_active, is_default, sort_order
        )
        VALUES (
            uuid_generate_v4()::varchar,
            '图片识别（默认）',
            '用于 Word 图片 OCR 与视觉模型识别的默认提示词',
            'image_recognition',
            '请识别这张投标文档图片中的全部文字和关键信息，并用简洁中文输出结构化文本。
如果图片包含表格、流程图或架构图，请用文本描述其结构和内容；不要编造不存在的内容。',
            NULL,
            TRUE,
            TRUE,
            0
        );
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM prompt_templates
        WHERE scenario = 'image_recognition' AND is_default = TRUE;
    """)
    op.drop_constraint("chk_prompt_templates_scenario", "prompt_templates", type_="check")
    op.create_check_constraint(
        "chk_prompt_templates_scenario",
        "prompt_templates",
        "scenario IN ('rag_qa', 'entity_extraction', 'document_summary', 'custom')",
    )
