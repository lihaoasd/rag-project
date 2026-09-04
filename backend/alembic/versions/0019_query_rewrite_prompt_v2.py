"""query_rewrite 默认提示词模板文案升级（否定/改口处理 + 多意图 JSON 数组输出）

把 0017 种子的默认模板文案原地升级为增强版：
- 否定/改口：以最终意图为准，被否定的内容不得出现在改写结果中；真实排除条件保留；
- 多意图：多个独立意图输出 JSON 数组子查询（含示例），单意图输出一行文本。
仅在行内容仍为 0017 旧文案时更新（用户已在提示词管理自行编辑过的行不覆盖）。

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-03
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 0017 种子旧文案（downgrade 回退目标）
_QUERY_REWRITE_PROMPT_0017 = """你是一名投标文档检索助手。请把下面的检索输入改写成更适合在投标文档库中检索的查询语句：
- 去除口语化表达、寒暄与无关内容；
- 补全行业缩写与关键业务要素（如资质、标准、评分办法等）；
- 保持原意，不要编造原文没有的新要求，不要回答问题；
- 只输出改写后的查询文本本身，不要引号、解释或换行。
{history}检索输入：{query}"""

# 增强版新文案（与 core/query_rewrite.FALLBACK_QUERY_REWRITE_PROMPT 保持一致）
_QUERY_REWRITE_PROMPT_V2 = """你是一名投标文档检索助手。请把下面的检索输入改写成更适合在投标文档库中检索的查询语句：
- 去除口语化表达、寒暄与无关内容；
- 补全行业缩写与关键业务要素（如资质、标准、评分办法等）；
- 保持原意，不要编造原文没有的新要求，不要回答问题；
- 用户先提出一个查询又否定改口（如"查XXX，不了/算了/别查了，还是查AA"）时，以最终意图为准：只输出 AA，被否定的内容（如 XXX）不得出现在改写结果中；
- 若"不/别/不含/不要"等否定是真实的筛选条件（如"不含联合体投标方式的文件"），则该条件属于最终意图，必须原样保留；
- 若输入包含多个彼此独立的检索意图（想同时获取不同主题/问题的结果），必须输出 JSON 数组，每个元素是一条独立的改写后子查询：不要遗漏任何意图，不要把多个意图合并成一句话；例如：输入"查资质要求，还要保证金比例" → 输出 ["投标人资质要求","投标保证金比例"]；改口被否定的意图不要出现在数组中；
- 若只有一个意图，直接输出一行改写文本，不要输出 JSON 数组；
- 只输出改写文本本身或 JSON 数组本身，不要任何解释、代码块包裹或引号。
{history}检索输入：{query}"""


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        text(
            "UPDATE prompt_templates SET system_prompt = :new_prompt "
            "WHERE scenario = 'query_rewrite' AND is_default = TRUE "
            "AND system_prompt = :old_prompt"
        ),
        {"new_prompt": _QUERY_REWRITE_PROMPT_V2, "old_prompt": _QUERY_REWRITE_PROMPT_0017},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        text(
            "UPDATE prompt_templates SET system_prompt = :old_prompt "
            "WHERE scenario = 'query_rewrite' AND is_default = TRUE "
            "AND system_prompt = :new_prompt"
        ),
        {"old_prompt": _QUERY_REWRITE_PROMPT_0017, "new_prompt": _QUERY_REWRITE_PROMPT_V2},
    )
