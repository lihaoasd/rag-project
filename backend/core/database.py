"""
数据库连接管理 — SQLAlchemy async engine + session factory。
"""
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from config import settings

# 异步引擎
engine = create_async_engine(
    settings.database_url,
    echo=False,
    poolclass=NullPool,
)

# 会话工厂
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db():
    """FastAPI 依赖注入 — 获取数据库会话"""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
