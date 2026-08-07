# 投标文档 RAG 系统 — 数据库版本管理方案

> 版本: v1.0 | 日期: 2026-08-07 | 状态: 初稿

---

## 1. 方案概述

### 1.1 选型

| 维度 | 说明 |
|------|------|
| 迁移工具 | **Alembic**（SQLAlchemy 官方迁移框架） |
| ORM | **SQLAlchemy 2.0**（async 模式） |
| 异步驱动 | **asyncpg**（PostgreSQL 高性能异步驱动） |
| 版本目录 | `backend/alembic/versions/` |

### 1.2 选型依据

| 对比项 | Alembic + SQLAlchemy | Raw SQL | Flyway |
|--------|---------------------|---------|--------|
| 自动生成迁移 | ✅ `--autogenerate` | ❌ 手写 | ❌ 手写 |
| 回滚支持 | ✅ `downgrade()` | 需手写反向脚本 | ❌（社区版） |
| Python 生态 | ✅ FastAPI 官方推荐 | 一般 | 差（需 JDK） |
| 异步支持 | ✅ async SQLAlchemy | 无关 | 无关 |

---

## 2. 目录结构

```
backend/
├── alembic/
│   ├── versions/              # 迁移脚本目录
│   │   └── 0001_init.py       # 初始迁移（7 张表）
│   ├── env.py                 # Alembic 运行环境配置
│   └── script.py.mako         # 迁移文件模板
├── alembic.ini                # Alembic 基础配置
├── models/
│   ├── __init__.py            # 导出所有模型
│   ├── base.py                # SQLAlchemy Base + 通用 Mixin
│   ├── knowledge_base.py      # KnowledgeBase 模型
│   ├── document.py            # Document + DocumentChunk 模型
│   ├── conversation.py        # Conversation + Message 模型
│   └── config.py              # LLMConfig + EmbeddingConfig 模型
└── pyproject.toml             # 项目依赖
```

---

## 3. 依赖

在 `pyproject.toml` 中追加：

```toml
dependencies = [
    # ... 现有依赖
    "sqlalchemy[asyncio]>=2.0.0",
    "asyncpg>=0.29.0",
    "alembic>=1.13.0",
]
```

安装：

```bash
uv sync
```

---

## 4. Model 定义

### 4.1 Base — 通用 Mixin

```python
# backend/models/base.py

import uuid
from datetime import datetime
from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    """自动管理 created_at / updated_at"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UUIDMixin:
    """UUID 主键"""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
```

### 4.2 KnowledgeBase

```python
# backend/models/knowledge_base.py

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin


class KnowledgeBase(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "knowledge_bases"

    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    documents = relationship("Document", back_populates="knowledge_base", cascade="all, delete-orphan")
    conversations = relationship("Conversation", back_populates="knowledge_base", cascade="all, delete-orphan")
```

### 4.3 Document + DocumentChunk

```python
# backend/models/document.py

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin


class Document(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "documents"

    kb_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="uploading", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    knowledge_base = relationship("KnowledgeBase", back_populates="documents")
    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")


class DocumentChunk(Base, UUIDMixin):
    __tablename__ = "document_chunks"

    doc_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    kb_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    text_content: Mapped[str] = mapped_column(Text, nullable=False)
    milvus_pk: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at = Base.metadata.tables["document_chunks"].columns["created_at"]  # 注

    document = relationship("Document", back_populates="chunks")
```

> **注：** `DocumentChunk` 不使用 `TimestampMixin`，仅 `created_at`，无 `updated_at`。实际实现时手动定义 `created_at` 列或使用简化的 `CreatedAtMixin`。

### 4.4 Conversation + Message

```python
# backend/models/conversation.py

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin


class Conversation(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "conversations"

    kb_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    knowledge_base = relationship("KnowledgeBase", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")


class Message(Base, UUIDMixin):
    __tablename__ = "messages"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at = ...  # 仅 created_at，手动定义

    conversation = relationship("Conversation", back_populates="messages")
```

### 4.5 LLMConfig + EmbeddingConfig

```python
# backend/models/config.py

from sqlalchemy import Boolean, Double, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class LLMConfig(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "llm_configs"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    api_key: Mapped[str] = mapped_column(String(512), nullable=False)
    api_base: Mapped[str | None] = mapped_column(String(512), nullable=True)
    temperature: Mapped[float] = mapped_column(Double, default=0.1, nullable=False)
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    top_k: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    similarity_threshold: Mapped[float] = mapped_column(Double, default=0.6, nullable=False)
    context_window: Mapped[int] = mapped_column(Integer, default=4096, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class EmbeddingConfig(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "embedding_configs"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    api_base: Mapped[str] = mapped_column(String(512), nullable=False)
    api_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, default=512, nullable=False)
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=64, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
```

---

## 5. Alembic 配置

### 5.1 alembic.ini

```ini
[alembic]
script_location = alembic
sqlalchemy.url = driver://user:pass@localhost/dbname  # 占位，实际由 env.py 读取

[loggers]
keys = root,sqlalchemy,alembic

[logger_root]
level = WARN

[logger_sqlalchemy]
level = WARN

[logger_alembic]
level = INFO
```

### 5.2 env.py

```python
# backend/alembic/env.py

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from models.base import Base
import models  # noqa: F401 — 确保所有模型被导入

# 从环境变量构建数据库 URL（不使用 alembic.ini 中的硬编码值）
DATABASE_URL = (
    f"postgresql+asyncpg://"
    f"{os.getenv('PG_USER', 'postgres')}:{os.getenv('PG_PASSWORD', 'postgres')}"
    f"@{os.getenv('PG_HOST', 'localhost')}:{os.getenv('PG_PORT', '5432')}"
    f"/{os.getenv('PG_DATABASE', 'ai_bid_rag')}"
)

config = context.config
config.set_main_option("sqlalchemy.url", DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式（不连接数据库，仅生成 SQL）"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """在线模式（连接数据库执行）"""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
    from sqlalchemy import pool as sa_pool

    connectable = create_async_engine(
        DATABASE_URL,
        poolclass=sa_pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    import asyncio
    asyncio.run(run_migrations_online())
```

### 5.3 script.py.mako（迁移文件模板）

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

---

## 6. 迁移工作流

### 6.1 初始迁移（已有表结构，从头生成）

如果是从零开始的项目，直接基于模型自动生成：

```bash
cd backend
uv run alembic revision --autogenerate -m "init: 7 tables"
uv run alembic upgrade head
```

### 6.2 增量迁移（已有数据库，新增表/字段）

```bash
# 1. 修改 models/*.py 中的 SQLAlchemy 模型
# 2. 生成迁移
uv run alembic revision --autogenerate -m "add processing_tasks table"

# 3. 检查生成的 versions/0002_xxx.py，确认升级/回滚逻辑正确
# 4. 执行
uv run alembic upgrade head
```

### 6.3 回滚

```bash
# 回滚 1 个版本
uv run alembic downgrade -1

# 回滚到指定版本
uv run alembic downgrade 0001

# 回滚到初始状态（清空所有表）
uv run alembic downgrade base
```

### 6.4 查看状态

```bash
# 当前数据库版本
uv run alembic current

# 迁移历史链路
uv run alembic history

# 查看未应用的迁移（哪些还没跑）
uv run alembic heads
```

---

## 7. 最佳实践

### 7.1 迁移脚本检查清单

- [ ] `upgrade()` 和 `downgrade()` 都必须实现，禁止空函数
- [ ] `--autogenerate` 生成的脚本要逐行检查，AI 可能遗漏约束（CHECK、索引类型等）
- [ ] 涉及数据迁移的列变更（如重命名、类型转换），`autogenerate` 检测不到，需手写
- [ ] 不允许在迁移脚本中导入业务代码（`from app.models import ...`），迁移应独立运行
- [ ] 提交前在本地执行 `alembic upgrade head` + `alembic downgrade -1` 双向验证

### 7.2 不推荐的做法

| 反模式 | 原因 |
|--------|------|
| 只保留最新迁移文件，删除历史 | 无法从旧版本升级，回滚链路断裂 |
| 手动改数据库后补迁移脚本 | 模型和数据库不一致，下次 autogenerate 出问题 |
| 迁移脚本中调用外部 API | 迁移应纯 SQL，无副作用 |
| 多人同时生成迁移 | 版本号冲突，需协调 |

---

## 8. 日常命令速查

```bash
# 安装 alembic（已包含在 pyproject.toml 中）
uv sync

# 初始化 alembic 目录（首次）
uv run alembic init alembic

# 生成迁移（自动对比模型与数据库差异）
uv run alembic revision --autogenerate -m "描述"

# 应用所有未执行的迁移
uv run alembic upgrade head

# 回滚一个版本
uv run alembic downgrade -1

# 查看迁移历史
uv run alembic history

# 查看当前数据库版本
uv run alembic current

# 生成迁移 SQL 但不执行（预览）
uv run alembic upgrade head --sql
```
