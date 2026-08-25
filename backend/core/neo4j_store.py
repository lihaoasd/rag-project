"""
Neo4j 图存储封装 — driver 单例 + Cypher 执行（线程池）+ 健康检查 + 索引。

图谱功能由 settings.graph_enabled 开关控制：未启用（或 Neo4j 不可达）时
所有操作抛 GraphDisabledError，由 router/service 层转换为 503 友好提示。
所有同步 driver 调用经 asyncio.to_thread 放线程池，避免阻塞事件循环
（与 Milvus 同模式）。
"""
import asyncio
from typing import Any

from config import settings
from utils.logging import get_logger

logger = get_logger(__name__)


class GraphDisabledError(RuntimeError):
    """图谱功能未启用（graph_enabled=False）或 Neo4j 不可达。"""


class Neo4jStore:
    """Neo4j 图存储访问入口（类级单例 driver）。"""

    _driver: Any = None

    # ============================================================
    # 生命周期
    # ============================================================

    @classmethod
    def enabled(cls) -> bool:
        return settings.graph_enabled

    @classmethod
    def _get_driver(cls) -> Any:
        if cls._driver is None:
            import neo4j

            cls._driver = neo4j.GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_username, settings.neo4j_password),
            )
            logger.info("Neo4j driver 已创建: %s", settings.neo4j_uri)
        return cls._driver

    @classmethod
    async def close(cls) -> None:
        if cls._driver is not None:
            await asyncio.to_thread(cls._driver.close)
            cls._driver = None
            logger.info("Neo4j driver 已关闭")

    # ============================================================
    # Cypher 执行
    # ============================================================

    @classmethod
    async def execute_read(cls, cypher: str, params: dict | None = None) -> list[dict]:
        """执行只读 Cypher，返回记录字典列表。"""
        return await cls._execute("read", cypher, params)

    @classmethod
    async def execute_write(cls, cypher: str, params: dict | None = None) -> list[dict]:
        """执行写 Cypher（自动事务），返回记录字典列表。"""
        return await cls._execute("write", cypher, params)

    @classmethod
    async def execute_write_many(cls, statements: list[tuple[str, dict]]) -> None:
        """在一个事务中顺序执行多条写 Cypher（图谱批量写入/清除用）。

        Args:
            statements: [(cypher, params), ...]
        """
        if not settings.graph_enabled:
            raise GraphDisabledError("图谱功能未启用（GRAPH_ENABLED=false），请联系管理员部署 Neo4j 后开启")
        driver = cls._get_driver()
        await asyncio.to_thread(cls._run_many, driver, statements)

    @staticmethod
    def _run_many(driver: Any, statements: list[tuple[str, dict]]) -> None:
        def _tx(tx):
            for cypher, params in statements:
                tx.run(cypher, **params)

        with driver.session(database=settings.neo4j_database) as session:
            session.execute_write(_tx)

    @classmethod
    async def _execute(cls, mode: str, cypher: str, params: dict | None) -> list[dict]:
        if not settings.graph_enabled:
            raise GraphDisabledError("图谱功能未启用（GRAPH_ENABLED=false），请联系管理员部署 Neo4j 后开启")
        driver = cls._get_driver()
        return await asyncio.to_thread(cls._run, driver, mode, cypher, params or {})

    @staticmethod
    def _run(driver: Any, mode: str, cypher: str, params: dict) -> list[dict]:
        with driver.session(database=settings.neo4j_database) as session:
            if mode == "write":
                return session.execute_write(
                    lambda tx: tx.run(cypher, **params).data()
                )
            return session.execute_read(
                lambda tx: tx.run(cypher, **params).data()
            )

    # ============================================================
    # 健康检查 / 索引
    # ============================================================

    @classmethod
    async def health_check(cls) -> bool:
        """连通性检查：执行 RETURN 1。失败返回 False（记日志，不抛）。"""
        try:
            result = await cls.execute_read("RETURN 1 AS ok")
            return bool(result) and result[0].get("ok") == 1
        except Exception as e:
            logger.warning("Neo4j 健康检查失败: %s", e)
            return False

    @classmethod
    async def ensure_indexes(cls) -> None:
        """确保知识库隔离所需的复合索引（幂等）。"""
        if not settings.graph_enabled:
            return
        cypher = (
            "CREATE INDEX entity_kb_name IF NOT EXISTS "
            "FOR (n:Entity) ON (n.kb_id, n.name)"
        )
        await cls.execute_write(cypher)
        logger.info("Neo4j 索引已确保: entity_kb_name")
