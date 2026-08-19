"""
ModelConfig + KBModelBinding ORM 模型 — 统一模型配置与知识库模型绑定。
"""
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin, TimestampMixin, UTF8JSONB


class ModelConfig(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "model_configs"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    model_type: Mapped[str] = mapped_column(String(20), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    api_base: Mapped[str | None] = mapped_column(String(512), nullable=True)
    api_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    params: Mapped[dict | None] = mapped_column(UTF8JSONB, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)


class KBModelBinding(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "kb_model_bindings"

    kb_id: Mapped[str] = mapped_column(String(36), nullable=False)
    model_type: Mapped[str] = mapped_column(String(20), nullable=False)
    model_config_id: Mapped[str] = mapped_column(String(36), nullable=False)
