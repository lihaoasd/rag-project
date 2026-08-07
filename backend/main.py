"""
FastAPI 应用入口 — 投标文档 RAG 智能问答系统。
"""
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from routers import knowledge_base, document, search, chat
from utils.logging import get_logger, setup_logging

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时：初始化日志系统
    setup_logging(level=settings.log_level, log_dir=settings.log_dir)
    logger.info("Bid Document RAG System v0.1.0 启动中...")
    logger.info("LLM: %s / %s", settings.llm_provider, settings.llm_model)
    logger.info("Embedding: %s (dim=%d)", settings.embedding_model, settings.embedding_dim)
    yield
    # 关闭时
    logger.info("应用关闭")


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


@app.get("/api/health")
async def health_check():
    """健康检查端点"""
    return {"status": "healthy", "version": "0.1.0"}
