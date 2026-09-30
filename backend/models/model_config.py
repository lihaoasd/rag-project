"""
ModelProvider + ModelConfig + KBModelBinding ORM 模型 — 供应商、统一模型配置与知识库模型绑定。

ModelProvider 为模型供应商（名称/平台/API 地址/Key），一个供应商下挂多个模型，
模型可继承供应商连接信息（模型自身非空字段覆盖）。
"""
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDMixin, TimestampMixin, UTF8JSONB


class ModelProvider(Base, UUIDMixin, TimestampMixin):
    """模型供应商 — 复用平台连接信息，一个供应商下挂多个模型。"""

    __tablename__ = "model_providers"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    api_base: Mapped[str | None] = mapped_column(String(512), nullable=True)
    api_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    params: Mapped[dict | None] = mapped_column(UTF8JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)


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
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    # 所属供应商（可空）：连接信息继承供应商，模型自身非空字段覆盖
    provider_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    provider: Mapped["ModelProvider | None"] = relationship(
        "ModelProvider",
        primaryjoin="ModelConfig.provider_id == ModelProvider.id",
        foreign_keys="ModelConfig.provider_id",
        lazy="selectin",
        viewonly=True,
    )


class KBModelBinding(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "kb_model_bindings"

    kb_id: Mapped[str] = mapped_column(String(36), nullable=False)
    model_type: Mapped[str] = mapped_column(String(20), nullable=False)
    model_config_id: Mapped[str] = mapped_column(String(36), nullable=False)
