"""
Langfuse 观测封装 — client 生命周期 + 检索 span 结构化组装。

统一入口，避免 Langfuse 逻辑散落各处。启用开关见 config.langfuse_enabled。
注意：v4 SDK 中装饰器路径（@observe / get_client）依赖单例 client，
须显式构造 Langfuse() 注册（构造器会把实例注册进 LangfuseResourceManager 单例，
供 @observe 内部的 get_client() 复用）。
"""
from typing import Any

from config import settings
from utils.logging import get_logger

logger = get_logger(__name__)


def setup_langfuse() -> None:
    """应用启动时初始化 Langfuse client（非致命）。

    enabled=False 直接返回；启用时显式构造 Langfuse() 注册单例，
    供 @observe() / get_client() 使用（显式传参，不依赖 .env 自动注入）。
    """
    if not settings.langfuse_enabled:
        return

    from langfuse import Langfuse

    try:
        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_base_url or None,
            sample_rate=settings.langfuse_sample_rate,
        )
    except Exception as e:
        logger.warning("Langfuse 初始化失败，观测已降级关闭: %s", e)
        return

    if client.auth_check():
        logger.info("Langfuse 已连接: base_url=%s", settings.langfuse_base_url)
    else:
        logger.warning("Langfuse 连接校验失败，观测可能不可用: base_url=%s", settings.langfuse_base_url)


def format_retrieval_observation(hits: list[dict], text_limit: int = 200) -> dict[str, Any]:
    """将检索命中组装为结构化 span 输出（schema 可扩展以容纳多种检索方式）。"""
    return {
        "hit_count": len(hits),
        "hits": [
            {
                "rank": i + 1,
                "score": h.get("score"),
                "doc_id": h.get("doc_id", ""),
                "file_name": h.get("file_name", ""),
                "page_number": h.get("page_number"),
                "chunk_index": h.get("chunk_index"),
                "text": (h.get("text") or "")[:text_limit],
            }
            for i, h in enumerate(hits)
        ],
    }
