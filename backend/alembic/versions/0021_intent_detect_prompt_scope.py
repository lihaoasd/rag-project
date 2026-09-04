"""intent_detect 默认提示词模板文案升级（放宽检索：所有专业/信息诉求走检索，仅日常闲聊为普通聊天）

把 0020 种子的 intent_detect 默认模板文案升级为"放宽检索"版：
- 任何想获取信息/资料/专业知识的输入（含任意行业专业领域、纯主题名词查询）→ 需要检索，
  不按投标主题圈定、不以"知识库是否查得到"为判据；
- 只有纯日常闲聊（问候/寒暄/感谢/身份闲聊/讲笑话/天气等生活闲聊/情绪附和）→ 普通聊天；
- 拿不准一律按"需要检索"。
仅在行内容仍为 0020 旧文案时更新（用户在提示词管理自行编辑过的行不覆盖）。

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-04
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 0020 种子旧文案（downgrade 回退目标）
_INTENT_DETECT_PROMPT_0020 = """你是一名问答意图分类器。用户的输入绝大多数是资料检索请求，只有纯社交表达才算"普通聊天"：
- 需要检索：凡是想查询/了解/查看任何主题资料的问题都要检索——无论主题是否熟悉、是否与投标行业相关；纯主题名词的查询（如"查询冠状动脉粥样硬化性心脏病""冠状动脉粥样硬化性心脏病是什么"）也是检索"该主题资料"，需要检索；指代前文检索问题的（如"那这个呢""继续""展开讲讲"）同样需要检索；
- 普通聊天：仅限纯社交表达——问候、感谢、自我介绍/询问助手身份、讲笑话、日常寒暄（如"你好""谢谢""你是谁""讲个笑话""今天天气不错"）；
- 输入不含明显社交信号时一律按"需要检索"处理。
只输出"需要检索"或"普通聊天"中的一个词，不要输出其他内容。
{history}本次输入：{query}"""

# 新文案（与 core/intent_detect.FALLBACK_INTENT_DETECT_PROMPT 保持一致）
_INTENT_DETECT_PROMPT_V2 = """你是一个问答前置意图分类器。判定总则：放宽检索——任何想获取信息/资料/专业知识的输入都属于"需要检索"；只有纯日常闲聊才属于"普通聊天"。
- 需要检索：用户提出任何信息诉求——专业领域问题（工程/建筑/医疗/法律/财经/教育/医药等任意行业：术语、概念、疾病名、规范、条款、标准、数据、政策、产品……），索取资料内容（要求/流程/方案/价格/比例……），或任何以提问获取知识的表达（是什么/为什么/怎么/如何/多少/有没有/哪些……）；主题与投标无关或知识库可能查不到，不影响判定；拿不准一律按需要检索。
- 普通聊天：仅限日常闲聊语言场景——问候寒暄、感谢、身份/能力闲聊、讲笑话、夸奖、天气等生活闲聊、情绪附和（如"你好""在吗""谢谢""你是谁""你会什么""讲个笑话""今天天气不错""辛苦了""哈哈""那挺好的""晚安"）。
- 一句话：用户在索取信息/专业内容 → 需要检索；在闲聊（无信息诉求）→ 普通聊天。
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
        {"new_prompt": _INTENT_DETECT_PROMPT_V2, "old_prompt": _INTENT_DETECT_PROMPT_0020},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        text(
            "UPDATE prompt_templates SET system_prompt = :old_prompt "
            "WHERE scenario = 'intent_detect' AND is_default = TRUE "
            "AND system_prompt = :new_prompt"
        ),
        {"old_prompt": _INTENT_DETECT_PROMPT_0020, "new_prompt": _INTENT_DETECT_PROMPT_V2},
    )
