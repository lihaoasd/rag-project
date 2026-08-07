"""
LLMConfig + EmbeddingConfig ORM 模型。
"""
from sqlalchemy import Boolean, Double, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin, TimestampMixin


class LLMConfig(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "llm_configs"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    api_key: Mapped[str] = mapped_column(String(512), nullable=False)
    api_base: Mapped[str | None] = mapped_column(String(512), nullable=True)
    temperature: Mapped[float] = mapped_column(Double, default=0.1, server_default="0.1", nullable=False)
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    top_k: Mapped[int] = mapped_column(Integer, default=5, server_default="5", nullable=False)
    similarity_threshold: Mapped[float] = mapped_column(Double, default=0.6, server_default="0.6", nullable=False)
    context_window: Mapped[int] = mapped_column(Integer, default=4096, server_default="4096", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)


class EmbeddingConfig(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "embedding_configs"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    api_base: Mapped[str] = mapped_column(String(512), nullable=False)
    api_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, default=512, server_default="512", nullable=False)
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=64, server_default="64", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
