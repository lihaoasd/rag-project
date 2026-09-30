"""model_providers: 模型供应商表 + model_configs 挂载供应商，历史模型按 platform 归拢

- 新增 model_providers 表（供应商复用连接信息，一个供应商下挂多个模型）
- model_configs 增加 provider_id 列 + 索引（无 FK 约束，与项目惯例一致）
- 数据归拢：按 platform 分组自动生成供应商，并把该组模型 provider_id 指向它；
  组内连接信息一致的模型 api_base/api_key 置空（跟随供应商统一维护），
  不一致的保留模型自身覆盖值（不丢信息）。

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-30
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _group_models(bind):
    """读取现有模型配置，按 platform 分组（保持创建顺序）。"""
    rows = bind.execute(
        sa.text(
            "SELECT id, platform, api_base, api_key FROM model_configs "
            "ORDER BY platform, created_at"
        )
    ).fetchall()
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r.platform, []).append(r)
    return groups


def _unique_provider_name(name: str, used: set[str]) -> str:
    base = name or "provider"
    candidate = base
    i = 2
    while candidate in used:
        candidate = f"{base}-{i}"
        i += 1
    used.add(candidate)
    return candidate


def upgrade() -> None:
    # ============================================
    # 1. 建 model_providers 表
    # ============================================
    op.create_table(
        "model_providers",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("platform", sa.String(50), nullable=False),
        sa.Column("api_base", sa.String(512), nullable=True),
        sa.Column("api_key", sa.String(512), nullable=True),
        sa.Column("params", postgresql.JSONB(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("name", name="uq_model_providers_name"),
    )
    op.execute("""
        CREATE TRIGGER trg_model_providers_updated_at
            BEFORE UPDATE ON model_providers
            FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();
    """)

    # ============================================
    # 2. model_configs 增加 provider_id
    # ============================================
    op.add_column(
        "model_configs",
        sa.Column("provider_id", sa.String(36), nullable=True),
    )
    op.create_index("idx_model_configs_provider", "model_configs", ["provider_id"], unique=False)

    # ============================================
    # 3. 历史模型按 platform 归拢到自动生成的供应商
    # ============================================
    bind = op.get_bind()
    groups = _group_models(bind)
    used_names: set[str] = set()

    for platform, items in groups.items():
        # 供应商统一连接信息：取组内第一条非空 api_base / api_key
        api_base = next((r.api_base for r in items if r.api_base), None)
        api_key = next((r.api_key for r in items if r.api_key), None)
        name = _unique_provider_name(platform, used_names)
        provider_id = str(uuid.uuid4())

        bind.execute(
            sa.text(
                "INSERT INTO model_providers "
                "(id, name, platform, api_base, api_key, params, is_active, created_at, updated_at) "
                "VALUES (:id, :name, :platform, :api_base, :api_key, NULL, TRUE, NOW(), NOW())"
            ),
            {"id": provider_id, "name": name, "platform": platform,
             "api_base": api_base, "api_key": api_key},
        )

        for r in items:
            # 与供应商一致的连接信息置空（跟随供应商统一维护）；不一致的保留模型自身覆盖
            new_api_base = None if (r.api_base is None or r.api_base == api_base) else r.api_base
            new_api_key = None if (r.api_key is None or r.api_key == api_key) else r.api_key
            bind.execute(
                sa.text(
                    "UPDATE model_configs SET provider_id = :pid, api_base = :ab, api_key = :ak "
                    "WHERE id = :mid"
                ),
                {"pid": provider_id, "ab": new_api_base, "ak": new_api_key, "mid": r.id},
            )


def downgrade() -> None:
    op.drop_index("idx_model_configs_provider", table_name="model_configs")
    op.drop_column("model_configs", "provider_id")
    op.drop_table("model_providers")
