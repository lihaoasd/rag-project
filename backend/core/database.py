"""
数据库连接管理 — SQLAlchemy async engine + session factory。
"""
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from config import settings
from utils.logging import get_logger

logger = get_logger(__name__)

logger.info("创建数据库引擎: %s", settings.pg_host)

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
        except Exception:
            logger.error("数据库会话异常", exc_info=True)
            raise
        finally:
            await session.close()
