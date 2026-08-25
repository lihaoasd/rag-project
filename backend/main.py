"""
FastAPI 应用入口 — 投标文档 RAG 智能问答系统。
"""
import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from core import es_store, graph_queue, neo4j_store, parse_queue
from core.observability import setup_langfuse
from core.database import async_session
from repositories.document_repository import DocumentRepository
from routers import knowledge_base, document, search, chat, chunk, prompt, model, lexicon, es_admin, graph
from services import es_admin_service, lexicon_service, model_service
from utils.logging import get_logger, setup_logging

logger = get_logger(__name__)


async def es_reconcile_loop() -> None:
    """每日定时 ES 对账：差异仅记日志/告警，不自动 rebuild（手动触发）。"""
    while True:
        try:
            status = await es_admin_service.reconcile_status()
            if not status.get("healthy"):
                logger.warning(
                    "ES 对账：Elasticsearch 不可达（index=%s）",
                    status.get("index"),
                )
            elif status.get("diff_count", 0) > 0:
                logger.warning(
                    "ES 对账发现差异: %d 个知识库（%s）",
                    status["diff_count"],
                    status["differences"],
                )
            else:
                logger.info(
                    "ES 对账通过: pg=%d es=%d",
                    status.get("pg_total", 0),
                    status.get("es_total", 0),
                )
        except Exception as e:
            logger.error("ES 对账任务异常: %s", e, exc_info=True)
        await asyncio.sleep(settings.es_reconcile_interval_hours * 3600)


async def reset_stale_graph_status() -> None:
    """启动时重置滞留 building 的图谱状态 → none（队列为内存队列，重启丢失任务）。"""
    try:
        async with async_session() as session:
            repo = DocumentRepository(session)
            await repo.reset_stale_graph_status()
    except Exception as e:
        logger.error("重置滞留图谱状态异常（忽略）: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时：初始化日志系统、Langfuse 观测、启用模型自检、启动解析队列 worker
    setup_logging(level=settings.log_level, log_dir=settings.log_dir)
    setup_langfuse()
    logger.info("Bid Document RAG System v0.1.0 启动中...")
    logger.info("LLM: %s / %s", settings.llm_provider, settings.llm_model)
    logger.info("Embedding: %s (dim=%d)", settings.embedding_model, settings.embedding_dim)
    logger.info("Graph RAG: %s", "已启用" if settings.graph_enabled else "未启用（GRAPH_ENABLED=false）")

    # 启用状态的模型连接自检（失败仅记日志，不阻断启动）
    await model_service.startup_model_check()

    # ES 初始化（best-effort）：确保索引存在；加载词库缓存
    await es_store.ensure_index()
    await lexicon_service.ensure_cache()

    # 图谱：Neo4j 索引（best-effort）+ 重置滞留构建状态 + 启动图谱队列
    if settings.graph_enabled:
        try:
            await neo4j_store.ensure_indexes()
        except Exception as e:
            logger.warning("Neo4j 索引初始化失败（图谱功能暂不可用）: %s", e)
    await reset_stale_graph_status()
    graph_worker_task = None
    if settings.graph_enabled:
        graph_worker_task = asyncio.create_task(graph_queue.start_worker())

    worker_task = asyncio.create_task(parse_queue.start_worker())
    logger.info("解析队列 worker 已就绪")

    reconcile_task = None
    if settings.es_reconcile_enabled:
        reconcile_task = asyncio.create_task(es_reconcile_loop())
        logger.info("ES 对账任务已启动（间隔 %d 小时）", settings.es_reconcile_interval_hours)

    yield

    # 关闭时：停止 worker 与对账任务，释放 ES 客户端与 Neo4j driver
    logger.info("应用关闭中...")
    await parse_queue.stop_worker()
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    await graph_queue.stop_worker()
    if graph_worker_task is not None:
        graph_worker_task.cancel()
        try:
            await graph_worker_task
        except asyncio.CancelledError:
            pass
    if reconcile_task is not None:
        reconcile_task.cancel()
        try:
            await reconcile_task
        except asyncio.CancelledError:
            pass
    await es_store.close_client()
    await neo4j_store.close()
    logger.info("应用已关闭")


app = FastAPI(
    title="Bid Document RAG System",
    description="投标文档 RAG 智能问答系统",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — 允许前端开发服务器访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 请求日志中间件
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """记录每个 HTTP 请求的方法、路径、状态码和耗时。"""
    start_time = time.time()
    response = await call_next(request)
    elapsed = (time.time() - start_time) * 1000  # ms
    logger.info(
        "%s %s → %d (%.0fms)",
        request.method,
        request.url.path,
        response.status_code,
        elapsed,
    )
    return response


# 注册路由
app.include_router(knowledge_base.router)
app.include_router(document.router)
app.include_router(search.router)
app.include_router(chat.router)
app.include_router(chunk.router)
app.include_router(prompt.router)
app.include_router(model.router)
app.include_router(lexicon.router)
app.include_router(es_admin.router)
app.include_router(graph.router)


@app.get("/api/health")
async def health_check():
    """健康检查端点"""
    body = {"status": "healthy", "version": "0.1.0"}
    if settings.graph_enabled:
        from core.neo4j_store import Neo4jStore

        body["graph"] = "ok" if await Neo4jStore.health_check() else "unavailable"
    return body
