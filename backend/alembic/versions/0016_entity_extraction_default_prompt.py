"""entity_extraction 默认提示词模板（实体抽取提示词接入提示词管理）

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 与 core/graph_extractor.FALLBACK_ENTITY_EXTRACTION_PROMPT 保持一致（docs/09 §5.3）
_ENTITY_EXTRACTION_PROMPT = """你是通用信息抽取系统。从文本中提取实体和关系，只提取明确提及的信息，不要编造。
实体 name 必须与原文完全一致（禁止改写、合并或规范化），同一实体在原文中重复出现只需输出一次。

实体类型:
ORGANIZATION(组织/机构/公司/团体), PERSON(人物), PLACE(地点/地理位置), EVENT(事件/活动),
TIME(时间点/日期), PROJECT(项目/工程/任务), PRODUCT(产品/物品/资产/系统),
QUANTITY(数量/金额/度量值,必须含单位,如 3000万元/50吨/2年), CONCEPT(概念/资质/标准/技能/制度/条款)

关系类型(方向见括号,源类型|目标类型):
(Person|Organization)-[属于]->(Organization)
(Organization|Project|Event|Product)-[位于]->(Place)
(Event|Project)-[发生在]->(Time|Place)
(Person|Organization)-[参与]->(Event|Project)
(Person|Organization)-[负责]->(Project|Product|Event)
(Organization|Person)-[合作]->(Organization|Person)
(任意业务实体)-[关联]->(任意业务实体)   ← 语义不明确的通用关系用此类型
(Project|Event|Product|Organization)-[包含]->(Project|Event|Product|Concept|Person)
(Project|Product)-[要求]->(Concept|Product|Person)
(Organization|Person)-[拥有]->(Product|Concept)
(Project|Event|Product|Organization)-[数量]->(Quantity)
(Project|Event|Document)-[日期]->(Time)

输出严格 JSON（不要其他文字，不要 markdown 代码块）:
{"entities":[{"name":"...","type":"ORGANIZATION","description":"..."}],
 "relationships":[{"source":"...","target":"...","type":"属于","description":"..."}]}"""


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO prompt_templates (
            id, name, description, scenario, system_prompt, variables,
            is_active, is_default, sort_order
        )
        VALUES (
            uuid_generate_v4()::varchar,
            '实体抽取（默认）',
            '用于知识图谱实体/关系抽取的默认提示词（词库优先 + LLM 补充）',
            'entity_extraction',
            '{_ENTITY_EXTRACTION_PROMPT}',
            NULL,
            TRUE,
            TRUE,
            0
        );
        """
    )


def downgrade() -> None:
    op.execute("""
        DELETE FROM prompt_templates
        WHERE scenario = 'entity_extraction' AND is_default = TRUE;
    """)
