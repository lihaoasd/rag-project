"""intent_detect 默认提示词模板文案升级（主题查询不再误判为普通聊天）

把 0018 种子的 intent_detect 默认模板文案升级：
- 反转为"默认需要检索"：凡想查询/了解任何主题资料（含纯主题名词查询、
  "查询冠状动脉粥样硬化性心脏病""X 是什么"等）都算检索请求，不按投标主题圈定；
- "普通聊天"仅限纯社交表达（问候/感谢/身份闲聊/讲笑话/日常寒暄）；
- 输入不含明显社交信号一律按"需要检索"。
仅在行内容仍为 0018 旧文案时更新（用户在提示词管理自行编辑过的行不覆盖）。

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-04
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 0018 种子旧文案（downgrade 回退目标）
_INTENT_DETECT_PROMPT_0018 = """你是一名投标文档问答系统的问题分类器。请判断本次用户输入是否需要检索投标文档知识库来回答：
- 与招标/投标/资质/评分/金额/条款等文档内容相关，或指代前文检索问题的（如"那这个呢""继续""展开讲讲"）→ 需要检索
- 与文档无关的日常寒暄、问候、身份/能力闲聊（如"你好""谢谢""你是谁""讲个笑话"）→ 普通聊天
- 不确定时按"需要检索"处理。
只输出"需要检索"或"普通聊天"中的一个词，不要输出其他内容。
{history}本次输入：{query}"""

# 新文案（与 core/intent_detect.FALLBACK_INTENT_DETECT_PROMPT 保持一致）
_INTENT_DETECT_PROMPT_V2 = """你是一名问答意图分类器。用户的输入绝大多数是资料检索请求，只有纯社交表达才算"普通聊天"：
- 需要检索：凡是想查询/了解/查看任何主题资料的问题都要检索——无论主题是否熟悉、是否与投标行业相关；纯主题名词的查询（如"查询冠状动脉粥样硬化性心脏病""冠状动脉粥样硬化性心脏病是什么"）也是检索"该主题资料"，需要检索；指代前文检索问题的（如"那这个呢""继续""展开讲讲"）同样需要检索；
- 普通聊天：仅限纯社交表达——问候、感谢、自我介绍/询问助手身份、讲笑话、日常寒暄（如"你好""谢谢""你是谁""讲个笑话""今天天气不错"）；
- 输入不含明显社交信号时一律按"需要检索"处理。
只输出"需要检索"或"普通聊天"中的一个词，不要输出其他内容。
{history}本次输入：{query}"""


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        text(
            "UPDATE prompt_templates SET system_prompt = :new_prompt "
            "WHERE scenario = 'intent_detect' AND is_default = TRUE "
            "AND system_prompt = :old_prompt"
        ),
        {"new_prompt": _INTENT_DETECT_PROMPT_V2, "old_prompt": _INTENT_DETECT_PROMPT_0018},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        text(
            "UPDATE prompt_templates SET system_prompt = :old_prompt "
            "WHERE scenario = 'intent_detect' AND is_default = TRUE "
            "AND system_prompt = :new_prompt"
        ),
        {"old_prompt": _INTENT_DETECT_PROMPT_0018, "new_prompt": _INTENT_DETECT_PROMPT_V2},
    )
