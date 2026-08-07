# 投标文档 RAG 系统 — 数据库设计文档

> 版本: v1.0 | 日期: 2026-08-07 | 状态: 初稿 | 数据库: PostgreSQL

---

## 1. 设计概述

### 1.1 定位

PostgreSQL 存储业务元数据。向量数据在 Milvus，原始文件在 MinIO。

### 1.2 表清单

| 序号 | 表名 | 说明 | 对应需求 |
|------|------|------|----------|
| 1 | `knowledge_bases` | 知识库 | F-06~F-09 |
| 2 | `documents` | 文档元数据 | F-01~F-05 |
| 3 | `document_chunks` | 文档切片元数据（Milvus 向量对应） | 索引管理 |
| 4 | `conversations` | 对话会话 | F-11（多轮对话） |
| 5 | `messages` | 对话消息 | F-11、F-12 |
| 6 | `llm_configs` | 大模型配置 | F-22 |
| 7 | `embedding_configs` | Embedding 模型配置 | F-23 |

---

## 2. 通用约定

### 2.1 命名规范

| 规则 | 示例 |
|------|------|
| 表名：小写蛇形命名（snake_case），复数形式 | `knowledge_bases`, `documents` |
| 列名：小写蛇形命名 | `file_name`, `created_at` |
| 主键：`id`，UUID 类型 | `id UUID PRIMARY KEY DEFAULT gen_random_uuid()` |
| 外键：`{表名单数}_id` | `kb_id`, `doc_id`, `conversation_id` |
| 时间戳：`created_at`, `updated_at` | TIMESTAMPTZ |
| 索引：`idx_{表名}_{列名}` | `idx_documents_kb_id` |
| 唯一约束：`uq_{表名}_{列名}` | `uq_knowledge_bases_name` |

### 2.2 字段类型约定

| 场景 | PostgreSQL 类型 |
|------|----------------|
| 主键 / ID | `UUID DEFAULT gen_random_uuid()` |
| 名称 / 标题 | `VARCHAR(255)` |
| 长文本 / 描述 | `TEXT` |
| 文件路径 / URL | `VARCHAR(1024)` |
| 状态枚举 | `VARCHAR(20)` + CHECK 约束 |
| 数字（无小数） | `INTEGER` / `BIGINT` |
| 数字（有小数） | `NUMERIC` / `DOUBLE PRECISION` |
| 布尔 | `BOOLEAN` |
| 时间戳 | `TIMESTAMPTZ DEFAULT NOW()` |
| JSON 数据 | `JSONB` |

### 2.3 通用列

每个表都包含：

```sql
id         UUID PRIMARY KEY DEFAULT gen_random_uuid()
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
```

通过触发器自动更新 `updated_at`：

```sql
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';
```

---

## 3. 表设计

### 3.1 knowledge_bases（知识库）

| 序号 | 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|------|
| 1 | `id` | UUID | PK, DEFAULT gen_random_uuid() | 知识库唯一标识 |
| 2 | `name` | VARCHAR(255) | NOT NULL, UNIQUE | 知识库名称 |
| 3 | `description` | TEXT | NULLABLE | 知识库描述 |
| 4 | `document_count` | INTEGER | NOT NULL, DEFAULT 0 | 文档数量（冗余，便于列表展示） |
| 5 | `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 创建时间 |
| 6 | `updated_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 更新时间 |

**索引：**

| 索引名 | 列 | 类型 |
|--------|-----|------|
| `pk_knowledge_bases` | id | PRIMARY KEY |
| `uq_knowledge_bases_name` | name | UNIQUE |
| `idx_knowledge_bases_created_at` | created_at | BTREE（列表排序） |

**DDL：**

```sql
CREATE TABLE knowledge_bases (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name           VARCHAR(255) NOT NULL,
    description    TEXT,
    document_count INTEGER NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_knowledge_bases_name UNIQUE (name)
);

CREATE INDEX idx_knowledge_bases_created_at ON knowledge_bases (created_at DESC);

-- 触发器
CREATE TRIGGER trg_knowledge_bases_updated_at
    BEFORE UPDATE ON knowledge_bases
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
```

**设计说明：**

- `document_count` 是冗余字段。当文档上传/删除时，通过业务层更新该计数，避免列表查询时 COUNT 关联。如果数据不一致，可通过 `SELECT COUNT(*) FROM documents WHERE kb_id = ?` 校准。
- `name` 全局唯一，防止同名知识库造成混淆。如需要同名，移除 UNIQUE 约束即可。

---

### 3.2 documents（文档）

| 序号 | 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|------|
| 1 | `id` | UUID | PK, DEFAULT gen_random_uuid() | 文档唯一标识 |
| 2 | `kb_id` | UUID | NOT NULL, FK → knowledge_bases.id | 所属知识库 |
| 3 | `file_name` | VARCHAR(512) | NOT NULL | 原始文件名 |
| 4 | `file_size` | BIGINT | NOT NULL | 文件大小（字节） |
| 5 | `file_type` | VARCHAR(10) | NOT NULL | 文件类型（pdf / docx / doc） |
| 6 | `object_key` | VARCHAR(1024) | NOT NULL | MinIO 对象 Key（存储路径） |
| 7 | `page_count` | INTEGER | NULLABLE | 总页数（解析完成后填充） |
| 8 | `status` | VARCHAR(20) | NOT NULL, DEFAULT 'uploading' | 状态 |
| 9 | `error_message` | TEXT | NULLABLE | 解析失败原因 |
| 10 | `chunk_count` | INTEGER | NULLABLE | 切片数量（解析完成后填充） |
| 11 | `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 上传时间 |
| 12 | `updated_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 更新时间 |

**status 枚举：**

| 值 | 说明 |
|-----|------|
| `uploading` | 文件上传中 |
| `processing` | 解析与索引中 |
| `completed` | 解析索引完成，可检索 |
| `failed` | 解析失败 |

**索引：**

| 索引名 | 列 | 类型 |
|--------|-----|------|
| `pk_documents` | id | PRIMARY KEY |
| `idx_documents_kb_id` | kb_id | BTREE |
| `idx_documents_kb_status` | (kb_id, status) | BTREE（按知识库+状态筛选） |
| `idx_documents_created_at` | created_at | BTREE（列表排序） |

**DDL：**

```sql
CREATE TABLE documents (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_id         UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    file_name     VARCHAR(512) NOT NULL,
    file_size     BIGINT NOT NULL,
    file_type     VARCHAR(10) NOT NULL,
    object_key    VARCHAR(1024) NOT NULL,
    page_count    INTEGER,
    status        VARCHAR(20) NOT NULL DEFAULT 'uploading',
    error_message TEXT,
    chunk_count   INTEGER,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_documents_file_type CHECK (file_type IN ('pdf', 'docx', 'doc')),
    CONSTRAINT chk_documents_status CHECK (status IN ('uploading', 'processing', 'completed', 'failed')),
    CONSTRAINT uq_documents_object_key UNIQUE (object_key)
);

CREATE INDEX idx_documents_kb_id ON documents (kb_id);
CREATE INDEX idx_documents_kb_status ON documents (kb_id, status);
CREATE INDEX idx_documents_created_at ON documents (created_at DESC);

CREATE TRIGGER trg_documents_updated_at
    BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
```

**设计说明：**

- `ON DELETE CASCADE`：删除知识库时自动删除其下所有文档记录。业务层需同步删除 Milvus Collection 和 MinIO 对象。
- `object_key` 格式为 `{kb_id}/{doc_id}.{ext}`（如 `kb_a1b2c3d4/doc_e5f6g7h8.pdf`）。MinIO 是扁平的 Key-Value 存储，用 `/` 分隔符模拟目录层级。通过 `uq_documents_object_key` 确保同一对象不会被重复引用。
- `chunk_count` 解析完成后填充，便于前端展示切片数量。
- status 流转路径：`uploading → processing → completed | failed`

---

### 3.3 document_chunks（文档切片）

存储每个文本切片的元数据，与 Milvus Collection 中的向量一一对应。

| 序号 | 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|------|
| 1 | `id` | UUID | PK, DEFAULT gen_random_uuid() | 切片唯一标识 |
| 2 | `doc_id` | UUID | NOT NULL, FK → documents.id | 所属文档 |
| 3 | `kb_id` | UUID | NOT NULL, FK → knowledge_bases.id | 所属知识库（冗余，便于查询） |
| 4 | `chunk_index` | INTEGER | NOT NULL | 切片在文档中的序号（从 0 开始） |
| 5 | `page_number` | INTEGER | NOT NULL | 所在页码（从 1 开始） |
| 6 | `char_count` | INTEGER | NOT NULL | 字符数 |
| 7 | `text_content` | TEXT | NOT NULL | 切片原始文本 |
| 8 | `milvus_pk` | BIGINT | NULLABLE | Milvus Collection 中的主键 ID |
| 9 | `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 创建时间 |

**索引：**

| 索引名 | 列 | 类型 |
|--------|-----|------|
| `pk_document_chunks` | id | PRIMARY KEY |
| `idx_chunks_doc_id` | doc_id | BTREE |
| `idx_chunks_kb_id` | kb_id | BTREE |
| `uq_chunks_doc_chunk` | (doc_id, chunk_index) | UNIQUE（同一文档内 chunk_index 唯一） |

**DDL：**

```sql
CREATE TABLE document_chunks (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_id       UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    kb_id        UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    chunk_index  INTEGER NOT NULL,
    page_number  INTEGER NOT NULL,
    char_count   INTEGER NOT NULL,
    text_content TEXT NOT NULL,
    milvus_pk    BIGINT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_chunks_doc_chunk UNIQUE (doc_id, chunk_index)
);

CREATE INDEX idx_chunks_doc_id ON document_chunks (doc_id);
CREATE INDEX idx_chunks_kb_id ON document_chunks (kb_id);

-- 注：此表不设 updated_at 触发器，因为切片创建后不再修改
```

**设计说明：**

- `text_content` 存储切片原始文本。虽然 Milvus 也存了一份，但 PG 中保留副本用于：
  - Milvus 不可用时的降级检索
  - 后续做关键词检索（BM25）时的数据源
  - 调试和数据分析
- `milvus_pk` 记录在 Milvus 中对应向量的主键，用于删除时精确匹配。
- `kb_id` 冗余字段，避免通过 doc_id JOIN documents 才能筛选知识库。

---

### 3.4 conversations（会话）

| 序号 | 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|------|
| 1 | `id` | UUID | PK, DEFAULT gen_random_uuid() | 会话唯一标识 |
| 2 | `kb_id` | UUID | NOT NULL, FK → knowledge_bases.id | 所属知识库 |
| 3 | `title` | VARCHAR(255) | NULLABLE | 会话标题（取首条用户消息摘要） |
| 4 | `message_count` | INTEGER | NOT NULL, DEFAULT 0 | 消息数量（冗余） |
| 5 | `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 会话开始时间 |
| 6 | `updated_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 最后活跃时间 |

**索引：**

| 索引名 | 列 | 类型 |
|--------|-----|------|
| `pk_conversations` | id | PRIMARY KEY |
| `idx_conversations_kb_id` | kb_id | BTREE |
| `idx_conversations_updated_at` | updated_at | BTREE（最近会话排序） |

**DDL：**

```sql
CREATE TABLE conversations (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_id         UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    title         VARCHAR(255),
    message_count INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_conversations_kb_id ON conversations (kb_id);
CREATE INDEX idx_conversations_updated_at ON conversations (updated_at DESC);

CREATE TRIGGER trg_conversations_updated_at
    BEFORE UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
```

---

### 3.5 messages（消息）

| 序号 | 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|------|
| 1 | `id` | UUID | PK, DEFAULT gen_random_uuid() | 消息唯一标识 |
| 2 | `conversation_id` | UUID | NOT NULL, FK → conversations.id | 所属会话 |
| 3 | `role` | VARCHAR(20) | NOT NULL | 角色 |
| 4 | `content` | TEXT | NOT NULL | 消息内容 |
| 5 | `sources` | JSONB | NULLABLE | 引用来源 |
| 6 | `token_count` | INTEGER | NULLABLE | Token 用量（可选） |
| 7 | `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 消息时间 |

**role 枚举：**

| 值 | 说明 |
|-----|------|
| `user` | 用户提问 |
| `assistant` | LLM 回答 |
| `system` | 系统提示（可选，记录 prompt 模板） |

**sources JSONB 结构：**

```json
[
  {
    "doc_id": "uuid",
    "file_name": "XX招标文件.pdf",
    "page_number": 12,
    "chunk_index": 3,
    "score": 0.92,
    "text": "匹配的文档片段文本..."
  }
]
```

**索引：**

| 索引名 | 列 | 类型 |
|--------|-----|------|
| `pk_messages` | id | PRIMARY KEY |
| `idx_messages_conversation_id` | conversation_id | BTREE |
| `idx_messages_created_at` | (conversation_id, created_at) | BTREE（按会话+时间排序） |

**DDL：**

```sql
CREATE TABLE messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            VARCHAR(20) NOT NULL,
    content         TEXT NOT NULL,
    sources         JSONB,
    token_count     INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_messages_role CHECK (role IN ('user', 'assistant', 'system'))
);

CREATE INDEX idx_messages_conversation_id ON messages (conversation_id);
CREATE INDEX idx_messages_created_at ON messages (conversation_id, created_at);

-- 注：消息创建后不修改，不设 updated_at 触发器
```

**设计说明：**

- 消息创建后不可修改（immutable），确保对话记录的完整性。
- `sources` 仅在 `role = 'assistant'` 时有值，存储检索到的文档片段引用（对应需求 F-12 答案溯源）。
- `token_count` 为可选字段，用于成本分析和用量监控。

---

### 3.6 llm_configs（大模型配置）

| 序号 | 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|------|
| 1 | `id` | UUID | PK, DEFAULT gen_random_uuid() | 配置唯一标识 |
| 2 | `name` | VARCHAR(100) | NOT NULL, UNIQUE | 配置名称（如 "DeepSeek V3"） |
| 3 | `provider` | VARCHAR(50) | NOT NULL | 提供商 |
| 4 | `model` | VARCHAR(100) | NOT NULL | 模型名称（如 "deepseek-chat"） |
| 5 | `api_key` | VARCHAR(512) | NOT NULL | API Key |
| 6 | `api_base` | VARCHAR(512) | NULLABLE | API 地址 |
| 7 | `temperature` | DOUBLE PRECISION | NOT NULL, DEFAULT 0.1 | 生成温度 |
| 8 | `max_tokens` | INTEGER | NULLABLE | 最大输出 Token 数 |
| 9 | `top_k` | INTEGER | NOT NULL, DEFAULT 5 | 检索返回数 |
| 10 | `similarity_threshold` | DOUBLE PRECISION | NOT NULL, DEFAULT 0.6 | 相似度阈值 |
| 11 | `context_window` | INTEGER | NOT NULL, DEFAULT 4096 | 上下文窗口大小 |
| 12 | `is_active` | BOOLEAN | NOT NULL, DEFAULT FALSE | 是否为当前使用配置 |
| 13 | `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 创建时间 |
| 14 | `updated_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 更新时间 |

**provider 枚举：**

| 值 | 说明 |
|-----|------|
| `deepseek` | DeepSeek 官方 API |
| `openai` | OpenAI 官方 API |
| `openai_compatible` | 兼容 OpenAI 接口的模型服务 |

**索引：**

| 索引名 | 列 | 类型 |
|--------|-----|------|
| `pk_llm_configs` | id | PRIMARY KEY |
| `uq_llm_configs_name` | name | UNIQUE |
| `idx_llm_configs_active` | is_active | BTREE（查询当前激活配置） |

**DDL：**

```sql
CREATE TABLE llm_configs (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                 VARCHAR(100) NOT NULL,
    provider             VARCHAR(50) NOT NULL,
    model                VARCHAR(100) NOT NULL,
    api_key              VARCHAR(512) NOT NULL,
    api_base             VARCHAR(512),
    temperature          DOUBLE PRECISION NOT NULL DEFAULT 0.1,
    max_tokens           INTEGER,
    top_k                INTEGER NOT NULL DEFAULT 5,
    similarity_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.6,
    context_window       INTEGER NOT NULL DEFAULT 4096,
    is_active            BOOLEAN NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_llm_configs_name UNIQUE (name),
    CONSTRAINT chk_llm_configs_provider CHECK (provider IN ('deepseek', 'openai', 'openai_compatible')),
    CONSTRAINT chk_llm_configs_temperature CHECK (temperature >= 0 AND temperature <= 2)
);

CREATE INDEX idx_llm_configs_active ON llm_configs (is_active) WHERE is_active = TRUE;

CREATE TRIGGER trg_llm_configs_updated_at
    BEFORE UPDATE ON llm_configs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
```

**设计说明：**

- `api_key` 存储加密后的值，或存储环境变量占位符（如 `${DEEPSEEK_API_KEY}`）。实际读取时优先尝试从环境变量解析。
- `is_active`：同时只能有一个配置处于激活状态。业务层通过先将所有记录 `is_active = FALSE`，再将目标记录设为 `TRUE` 来保证互斥。
- `top_k`、`similarity_threshold`、`context_window` 与 LLM 配置绑定，因为不同模型的最优参数不同。

---

### 3.7 embedding_configs（Embedding 模型配置）

| 序号 | 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|------|
| 1 | `id` | UUID | PK, DEFAULT gen_random_uuid() | 配置唯一标识 |
| 2 | `name` | VARCHAR(100) | NOT NULL, UNIQUE | 配置名称 |
| 3 | `model` | VARCHAR(100) | NOT NULL | 模型名称（如 "bge-large-zh-v1.5"） |
| 4 | `api_base` | VARCHAR(512) | NOT NULL | Xinference API 地址 |
| 5 | `api_key` | VARCHAR(512) | NULLABLE | API Key（Xinference 本地部署通常不需要） |
| 6 | `dimension` | INTEGER | NOT NULL | 向量维度（如 1024） |
| 7 | `chunk_size` | INTEGER | NOT NULL, DEFAULT 512 | 文本切片大小（tokens） |
| 8 | `chunk_overlap` | INTEGER | NOT NULL, DEFAULT 64 | 切片重叠大小（tokens） |
| 9 | `is_active` | BOOLEAN | NOT NULL, DEFAULT FALSE | 是否为当前使用配置 |
| 10 | `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 创建时间 |
| 11 | `updated_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 更新时间 |

**索引：**

| 索引名 | 列 | 类型 |
|--------|-----|------|
| `pk_embedding_configs` | id | PRIMARY KEY |
| `uq_embedding_configs_name` | name | UNIQUE |
| `idx_embedding_configs_active` | is_active | BTREE（查询当前激活配置） |

**DDL：**

```sql
CREATE TABLE embedding_configs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          VARCHAR(100) NOT NULL,
    model         VARCHAR(100) NOT NULL,
    api_base      VARCHAR(512) NOT NULL,
    api_key       VARCHAR(512),
    dimension     INTEGER NOT NULL,
    chunk_size    INTEGER NOT NULL DEFAULT 512,
    chunk_overlap INTEGER NOT NULL DEFAULT 64,
    is_active     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_embedding_configs_name UNIQUE (name),
    CONSTRAINT chk_embedding_dimension CHECK (dimension > 0),
    CONSTRAINT chk_embedding_chunk_size CHECK (chunk_size > 0),
    CONSTRAINT chk_embedding_chunk_overlap CHECK (chunk_overlap >= 0 AND chunk_overlap < chunk_size)
);

CREATE INDEX idx_embedding_configs_active ON embedding_configs (is_active) WHERE is_active = TRUE;

CREATE TRIGGER trg_embedding_configs_updated_at
    BEFORE UPDATE ON embedding_configs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
```

**设计说明：**

- `chunk_size` 和 `chunk_overlap` 放在 Embedding 配置而非 LLM 配置中，因为不同 Embedding 模型的最优切片参数不同。
- `is_active` 互斥逻辑同 `llm_configs`。

---

## 4. ER 图

```
┌──────────────────────┐
│   knowledge_bases    │
│──────────────────────│
│ id (PK)              │◄──────────────────────┐
│ name (UQ)            │                       │
│ description          │                       │
│ document_count       │                       │
│ created_at           │                       │
│ updated_at           │                       │
└──────┬───────────────┘                       │
       │                                       │
       │ 1:N                                   │ 1:N
       ▼                                       │
┌──────────────────────┐              ┌────────┴─────────────┐
│     documents        │              │    conversations     │
│──────────────────────│              │──────────────────────│
│ id (PK)              │◄──┐          │ id (PK)              │
│ kb_id (FK)           │   │          │ kb_id (FK)           │
│ file_name            │   │          │ title                │
│ file_size            │   │          │ message_count        │
│ file_type            │   │          │ created_at           │
│ object_key           │   │          │ updated_at           │
│ page_count           │   │          └────────┬─────────────┘
│ status               │   │                   │
│ error_message        │   │                   │ 1:N
│ chunk_count          │   │                   ▼
│ created_at           │   │          ┌──────────────────────┐
│ updated_at           │   │          │      messages        │
└──────┬───────────────┘   │          │──────────────────────│
       │                   │          │ id (PK)              │
       │ 1:N               │          │ conversation_id (FK) │
       ▼                   │          │ role                 │
┌──────────────────────┐   │          │ content              │
│   document_chunks    │   │          │ sources (JSONB)      │
│──────────────────────│   │          │ token_count          │
│ id (PK)              │   │          │ created_at           │
│ doc_id (FK) ─────────┼───┘          └──────────────────────┘
│ kb_id (FK)           │
│ chunk_index          │
│ page_number          │
│ char_count           │
│ text_content         │
│ milvus_pk            │
│ created_at           │
└──────────────────────┘

┌──────────────────────┐       ┌──────────────────────┐
│    llm_configs       │       │  embedding_configs   │
│──────────────────────│       │──────────────────────│
│ id (PK)              │       │ id (PK)              │
│ name (UQ)            │       │ name (UQ)            │
│ provider             │       │ model                │
│ model                │       │ api_base             │
│ api_key              │       │ api_key              │
│ api_base             │       │ dimension            │
│ temperature          │       │ chunk_size           │
│ max_tokens           │       │ chunk_overlap        │
│ top_k                │       │ is_active            │
│ similarity_threshold │       │ created_at           │
│ context_window       │       │ updated_at           │
│ is_active            │       └──────────────────────┘
│ created_at           │
│ updated_at           │
└──────────────────────┘
```

---

## 5. 初始化数据

### 5.1 默认 LLM 配置

```sql
INSERT INTO llm_configs (name, provider, model, api_key, api_base, is_active)
VALUES (
    'DeepSeek V3 (默认)',
    'deepseek',
    'deepseek-chat',
    '${DEEPSEEK_API_KEY}',
    'https://api.deepseek.com/v1',
    TRUE
);
```

### 5.2 默认 Embedding 配置

```sql
INSERT INTO embedding_configs (name, model, api_base, dimension, is_active)
VALUES (
    'bge-large-zh-v1.5 (默认)',
    'bge-large-zh-v1.5',
    'http://localhost:9997/v1',
    1024,
    TRUE
);
```

---

## 6. 索引策略总结

| 表 | 索引 | 用途 |
|-----|------|------|
| knowledge_bases | name (UNIQUE) | 防止同名知识库 |
| knowledge_bases | created_at DESC | 列表按时间排序 |
| documents | kb_id | 按知识库查文档列表 |
| documents | (kb_id, status) | 按知识库+状态筛选 |
| documents | created_at DESC | 列表按时间排序 |
| documents | object_key (UNIQUE) | 防止重复引用同一对象 |
| document_chunks | doc_id | 按文档查所有切片 |
| document_chunks | kb_id | 按知识库查切片 |
| document_chunks | (doc_id, chunk_index) UNIQUE | 防止重复切片 |
| conversations | kb_id | 按知识库查会话列表 |
| conversations | updated_at DESC | 最近活跃会话排序 |
| messages | conversation_id | 按会话查消息 |
| messages | (conversation_id, created_at) | 按会话+时间排序消息 |
| llm_configs | name (UNIQUE) | 防止重名配置 |
| llm_configs | is_active WHERE TRUE | 快速查当前激活配置 |
| embedding_configs | name (UNIQUE) | 防止重名配置 |
| embedding_configs | is_active WHERE TRUE | 快速查当前激活配置 |

---

## 7. 后续扩展预留

| 序号 | 扩展方向 | 预留设计 |
|------|----------|----------|
| 1 | 用户登录与权限 | 新增 `users` 表，knowledge_bases / conversations 加 `user_id` FK |
| 2 | 标签/文件夹 | 新增 `tags` 表 + `document_tags` 关联表 |
| 3 | 词库入库管理 | 新增 `lexicon_entries` 表（实体类型、名称、别名、来源、审核状态） |
| 4 | Graph RAG | Neo4j 独立存储，PG 仅存图谱统计快照（可选） |
| 5 | 异步任务追踪 | 新增 `processing_tasks` 表（task_id, doc_id, status, progress, error） |
