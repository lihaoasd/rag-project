"""intent_detect / chat_normal 默认提示词模板（问答意图识别接入提示词管理）

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-04
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 与 core/intent_detect.FALLBACK_INTENT_DETECT_PROMPT 保持一致
_INTENT_DETECT_PROMPT = """你是一名投标文档问答系统的问题分类器。请判断本次用户输入是否需要检索投标文档知识库来回答：
- 与招标/投标/资质/评分/金额/条款等文档内容相关，或指代前文检索问题的（如"那这个呢""继续""展开讲讲"）→ 需要检索
- 与文档无关的日常寒暄、问候、身份/能力闲聊（如"你好""谢谢""你是谁""讲个笑话"）→ 普通聊天
- 不确定时按"需要检索"处理。
只输出"需要检索"或"普通聊天"中的一个词，不要输出其他内容。
{history}本次输入：{query}"""

# 与 services/chat_service.FALLBACK_CHAT_NORMAL_PROMPT 保持一致
_CHAT_NORMAL_PROMPT = """你是一个友好专业的投标领域 AI 助手。用户当前问题不需要查阅投标文档知识库，请直接、自然地回答（寒暄、身份/能力说明、开放闲聊等）：
- 不编造、不引用任何文档内容；
- 若用户问题实际需要查阅文档资料，请提示对方到知识库文档中检索/换个问题询问。
{history}用户问题：{query}"""

_VARIABLES_QUERY_HISTORY = """[
  {"name": "query", "label": "用户输入", "required": true, "description": "用户本轮输入的问题/消息"},
  {"name": "history", "label": "对话历史", "required": false, "description": "最近几轮对话（用于理解指代）"}
]"""


def upgrade() -> None:
    # 场景枚举加入 intent_detect / chat_normal（保留既有 query_rewrite 等场景）
    op.drop_constraint("chk_prompt_templates_scenario", "prompt_templates", type_="check")
    op.create_check_constraint(
        "chk_prompt_templates_scenario",
        "prompt_templates",
        "scenario IN ('rag_qa', 'entity_extraction', 'document_summary', 'custom', 'image_recognition', 'query_rewrite', 'intent_detect', 'chat_normal')",
    )
    op.execute(
        """
        INSERT INTO prompt_templates (
            id, name, description, scenario, system_prompt, variables,
            is_active, is_default, sort_order
        )
        VALUES
        (
            uuid_generate_v4()::varchar,
            '意图识别（默认）',
            '判断问答输入是否需要检索投标文档（需要检索/普通聊天）',
            'intent_detect',
            '"""
        + _INTENT_DETECT_PROMPT
        + """',
            '"""
        + _VARIABLES_QUERY_HISTORY
        + """'::jsonb,
            TRUE,
            TRUE,
            0
        ),
        (
            uuid_generate_v4()::varchar,
            '普通聊天（默认）',
            '普通聊天（不检索文档）时的助手人设与回答口径',
            'chat_normal',
            '"""
        + _CHAT_NORMAL_PROMPT
        + """',
            '"""
        + _VARIABLES_QUERY_HISTORY
        + """'::jsonb,
            TRUE,
            TRUE,
            0
        );
        """
    )


def downgrade() -> None:
    op.execute("""
        DELETE FROM prompt_templates
        WHERE scenario IN ('intent_detect', 'chat_normal') AND is_default = TRUE;
    """)
    op.drop_constraint("chk_prompt_templates_scenario", "prompt_templates", type_="check")
    op.create_check_constraint(
        "chk_prompt_templates_scenario",
        "prompt_templates",
        "scenario IN ('rag_qa', 'entity_extraction', 'document_summary', 'custom', 'image_recognition', 'query_rewrite')",
    )
