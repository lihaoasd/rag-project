"""
Alembic 异步运行环境配置。
"""
import os
import sys
from logging.config import fileConfig

# 将 backend 目录加入 sys.path，确保可以 import models
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from models.base import Base
import models  # noqa: F401 — 确保所有模型被导入，填充 Base.metadata

# 从环境变量构建数据库 URL
DATABASE_URL = (
    f"postgresql+asyncpg://"
    f"{os.getenv('PG_USER', 'postgres')}:{os.getenv('PG_PASSWORD', 'postgres')}"
    f"@{os.getenv('PG_HOST', 'localhost')}:{os.getenv('PG_PORT', '5432')}"
    f"/{os.getenv('PG_DATABASE', 'ai_bid_rag')}"
)

config = context.config
config.set_main_option("sqlalchemy.url", DATABASE_URL)

# fileConfig is skipped to avoid strict .ini format requirements on Windows.
# Logging is handled by the application's own logging setup.

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式 — 不连接数据库，仅生成 SQL（用于预览/review）。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """在线模式 — 连接数据库执行迁移。"""
    connectable = create_async_engine(DATABASE_URL)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def do_run_migrations(connection):
    """运行迁移（同步上下文内）。"""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    import asyncio
    asyncio.run(run_migrations_online())
