"""
Langfuse 观测封装 — client 生命周期 + 检索 span 结构化组装 + 统一 span 装饰器。

统一入口，避免 Langfuse 逻辑散落各处。启用开关见 config.langfuse_enabled。
注意：v4 SDK 中装饰器路径（@observe / get_client）依赖单例 client，
须显式构造 Langfuse() 注册（构造器会把实例注册进 LangfuseResourceManager 单例，
供 @observe 内部的 get_client() 复用）。
"""
import asyncio
import functools
import time
from typing import Any, Callable

from langfuse import get_client, observe

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


def _update_span(span_name: str, t0: float, error: str | None = None) -> None:
    """给当前 span 补统一字段：名称（支持动态命名）+ 耗时 + 异常。"""
    metadata: dict[str, Any] = {
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
    }
    if error is not None:
        metadata["error"] = error
    get_client().update_current_span(name=span_name, metadata=metadata)


def traced_span(name: str | Callable[..., str], span_type: str = "SPAN"):
    """统一 span 装饰器（langfuse v4）：固定命名/类型、自动计时 elapsed_ms、异常记 error。

    Args:
        name: span 名；可为字符串，或接收被装饰函数实参并返回 span 名的可调用对象
              （如 recall 按 method 动态命名：lambda query, kb_ids, method, ...: f"recall_{method}"）。
        span_type: CHAIN / SPAN / GENERATION（外部模型调用用 GENERATION）。

    支持 async 与 sync 函数；业务函数只负责组装自己的 input/output/metadata，
    由装饰器统一补 elapsed_ms 与异常 error，消除手写计时与遗漏。
    """
    def deco(fn: Callable):
        @functools.wraps(fn)
        def _resolve_name(*args, **kwargs) -> str:
            return name(*args, **kwargs) if callable(name) else name

        if asyncio.iscoroutinefunction(fn):
            @observe(as_type=span_type, capture_input=False, capture_output=False)
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                span_name = _resolve_name(*args, **kwargs)
                t0 = time.perf_counter()
                try:
                    result = await fn(*args, **kwargs)
                    _update_span(span_name, t0)
                    return result
                except Exception as e:
                    _update_span(span_name, t0, error=str(e))
                    raise
            return async_wrapper

        @observe(as_type=span_type, capture_input=False, capture_output=False)
        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            span_name = _resolve_name(*args, **kwargs)
            t0 = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
                _update_span(span_name, t0)
                return result
            except Exception as e:
                _update_span(span_name, t0, error=str(e))
                raise
        return sync_wrapper
    return deco
