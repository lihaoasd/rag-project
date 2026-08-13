"""
LLM 工厂 — 向后兼容 re-export（实现已迁移至 core.model_factory）。
"""
from core.model_factory import (  # noqa: F401
    create_llm,
    create_embedding,
    create_reranker,
    create_vision_llm,
    create_ocr,
)

__all__ = [
    "create_llm",
    "create_embedding",
    "create_reranker",
    "create_vision_llm",
    "create_ocr",
]
