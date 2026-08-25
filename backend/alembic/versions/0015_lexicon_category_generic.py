"""词库分类枚举化（通用本体）：location→place、qualification→concept，历史中文值归一

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 历史自由文本分类 → 通用枚举（docs/09 §5.2.1；location/qualification 为旧投标类型映射）
_MAPPINGS = [
    ("organization", ("公司", "企业", "单位", "机构")),
    ("project", ("项目", "工程")),
    ("person", ("人员", "人名")),
    ("place", ("地点", "位置", "location")),
    ("concept", ("资质", "证书", "标准", "技能", "概念", "qualification")),
]

_VALID = ("organization", "person", "place", "project", "product", "concept", "other")


def upgrade() -> None:
    for enum_value, legacy_values in _MAPPINGS:
        placeholders = ", ".join(f"'{v}'" for v in legacy_values)
        op.execute(f"UPDATE lexicon_terms SET category = '{enum_value}' WHERE category IN ({placeholders})")
    # 其余非枚举值一律归 other（含空串兜底）
    placeholders = ", ".join(f"'{v}'" for v in _VALID)
    op.execute(
        f"UPDATE lexicon_terms SET category = 'other' "
        f"WHERE category = '' OR category IS NULL OR category NOT IN ({placeholders})"
    )


def downgrade() -> None:
    # 数据迁移不可逆（枚举值无原始中文可回退），downgrade 为空操作
    pass
