"""
FastAPI 应用入口 — 投标文档 RAG 智能问答系统。
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from routers import knowledge_base, document, search, chat


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时：暂无需初始化逻辑
    yield
    # 关闭时：暂无需清理逻辑


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

# 注册路由
app.include_router(knowledge_base.router)
app.include_router(document.router)
app.include_router(search.router)
app.include_router(chat.router)


@app.get("/api/health")
async def health_check():
    """健康检查端点"""
    return {"status": "healthy", "version": "0.1.0"}
