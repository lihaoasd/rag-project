"""query_rewrite 默认提示词模板（Query 改写提示词接入提示词管理）

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-01
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 与 core/query_rewrite.FALLBACK_QUERY_REWRITE_PROMPT 保持一致（docs/plan/38）
_QUERY_REWRITE_PROMPT = """你是一名投标文档检索助手。请把下面的检索输入改写成更适合在投标文档库中检索的查询语句：
- 去除口语化表达、寒暄与无关内容；
- 补全行业缩写与关键业务要素（如资质、标准、评分办法等）；
- 保持原意，不要编造原文没有的新要求，不要回答问题；
- 只输出改写后的查询文本本身，不要引号、解释或换行。
{history}检索输入：{query}"""


def upgrade() -> None:
    # 场景枚举加入 query_rewrite（保留既有 image_recognition 等场景）
    op.drop_constraint("chk_prompt_templates_scenario", "prompt_templates", type_="check")
    op.create_check_constraint(
        "chk_prompt_templates_scenario",
        "prompt_templates",
        "scenario IN ('rag_qa', 'entity_extraction', 'document_summary', 'custom', 'image_recognition', 'query_rewrite')",
    )
    op.execute(
        """
        INSERT INTO prompt_templates (
            id, name, description, scenario, system_prompt, variables,
            is_active, is_default, sort_order
        )
        VALUES (
            uuid_generate_v4()::varchar,
            'Query 改写（默认）',
            '用于文档检索前把用户输入改写为更适合文档库检索的查询语句',
            'query_rewrite',
            '"""
        + _QUERY_REWRITE_PROMPT
        + """',
            '[
              {"name": "query", "label": "检索输入", "required": true, "description": "用户输入的检索词/问题"},
              {"name": "history", "label": "对话历史", "required": false, "description": "最近几轮对话（仅问答传入，检索页为空）"}
            ]'::jsonb,
            TRUE,
            TRUE,
            0
        );
        """
    )


def downgrade() -> None:
    op.execute("""
        DELETE FROM prompt_templates
        WHERE scenario = 'query_rewrite' AND is_default = TRUE;
    """)
    op.drop_constraint("chk_prompt_templates_scenario", "prompt_templates", type_="check")
    op.create_check_constraint(
        "chk_prompt_templates_scenario",
        "prompt_templates",
        "scenario IN ('rag_qa', 'entity_extraction', 'document_summary', 'custom', 'image_recognition')",
    )
