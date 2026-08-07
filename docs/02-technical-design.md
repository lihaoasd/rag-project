# 投标文档 RAG 系统 — 技术设计文档

> 版本: v1.0 | 日期: 2026-08-07 | 状态: 初稿

---

## 1. 技术选型

### 1.1 技术栈总览

| 序号 | 层次 | 技术 | 版本 / 说明 |
|------|------|------|------------|
| 1 | 前端框架 | Vue 3 | Composition API |
| 2 | UI 组件库 | Element Plus | 适合企业级后台 |
| 3 | 后端框架 | FastAPI | Python 异步 Web 框架 |
| 4 | RAG 框架 | LlamaIndex | 文档索引、检索管线 |
| 5 | 向量数据库 | Milvus | Docker 部署，Python SDK 连接 |
| 6 | Embedding 推理 | Xinference | 本地部署 Embedding 模型 |
| 7 | 大语言模型 | DeepSeek（默认） | 兼容 OpenAI API，可灵活切换 |
| 8 | 文档解析 — PDF | pdfplumber + PyMuPDF | 文本提取 + 表格识别 |
| 9 | 文档解析 — Word | python-docx | .docx 格式解析 |
| 10 | OCR | PaddleOCR | 图片文字识别（文档内嵌图片） |
| 11 | 构建工具（前端） | Vite | Vue 3 官方推荐 |
| 12 | 环境管理 | uv | Rust 编写的 Python 包管理器，替代 pip + venv |

### 1.2 LLM 灵活切换设计

通过统一 LLM 抽象层，支持多种模型：

```python
# 配置示例
LLM_CONFIG = {
    "provider": "deepseek",       # deepseek / openai / openai_compatible
    "model": "deepseek-chat",
    "api_key": "${DEEPSEEK_API_KEY}",
    "api_base": "https://api.deepseek.com/v1",
}
```

所有 LLM 调用统一走 LlamaIndex 的 `OpenAILike` 接口，只需修改配置即可切换模型。

#### 1.2.1 支持的 LLM 类型

| 序号 | Provider | 说明 |
|------|----------|------|
| 1 | deepseek | DeepSeek 官方 API（默认） |
| 2 | openai | OpenAI 官方 API |
| 3 | openai_compatible | 任意兼容 OpenAI 接口的模型服务 |

### 1.3 Embedding 集成方案

Xinference 提供本地 Embedding 服务，通过 REST API 调用：

```python
from llama_index.embeddings.openai import OpenAIEmbedding

embed_model = OpenAIEmbedding(
    model_name="bge-large-zh-v1.5",  # 通过 Xinference 部署的中文模型
    api_base="http://localhost:9997/v1",
    api_key="not-needed",
)
```

#### 1.3.1 Xinference 部署要点

| 序号 | 配置项 | 说明 |
|------|--------|------|
| 1 | 端口 | 默认 9997 |
| 2 | 模型 | bge-large-zh-v1.5（推荐中文模型） |
| 3 | 向量维度 | 1024 |
| 4 | 接口协议 | 兼容 OpenAI Embedding API |

### 1.4 环境管理（uv）

#### 1.4.1 为什么用 uv

| 维度 | pip + venv | uv |
|------|-----------|-----|
| 安装速度 | 慢（逐一下载解析） | 快（Rust 编写，并行下载解析） |
| 依赖解析 | 可能冲突，手动解决 | 自动、快速、正确 |
| 锁文件 | `pip freeze > requirements.txt` | `uv.lock` 自动生成，可复现 |
| 虚拟环境 | 手动创建 | `uv venv` / `uv sync` 一步完成 |
| 兼容性 | — | 完全兼容 pip、pyproject.toml |

#### 1.4.2 项目配置（pyproject.toml）

```toml
# backend/pyproject.toml
[project]
name = "ai-bid-rag"
version = "1.0.0"
description = "投标文档 RAG 智能问答系统"
requires-python = ">=3.10"

dependencies = [
    "fastapi>=0.110.0",
    "uvicorn>=0.27.0",
    "llama-index>=0.11.0",
    "llama-index-vector-stores-milvus>=0.2.0",
    "llama-index-embeddings-openai>=0.2.0",
    "llama-index-llms-openai-like>=0.2.0",
    "pymilvus>=2.4.0",
    "pymupdf>=1.23.0",
    "pdfplumber>=0.10.0",
    "python-docx>=1.1.0",
    "paddleocr>=2.7.0",
    "paddlepaddle>=2.6.0",
    "python-multipart>=0.0.9",
    "sse-starlette>=2.0.0",
    "pydantic>=2.6.0",
    "pydantic-settings>=2.1.0",
]

[project.optional-dependencies]
graph = [
    "llama-index-graph-stores-neo4j>=0.2.0",
    "neo4j>=5.18.0",
]
dev = [
    "pytest>=8.0.0",
    "httpx>=0.27.0",
]

[tool.uv]
dev-dependencies = []

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

#### 1.4.3 日常命令

```bash
# 安装 uv
pip install uv

# 创建虚拟环境并安装依赖
uv sync                          # 自动创建 .venv，安装所有依赖

# 安装含可选依赖（如 Graph RAG）
uv sync --extra graph

# 添加新依赖
uv add llama-index               # 自动更新 pyproject.toml 和 uv.lock

# 移除依赖
uv remove some-package

# 运行命令
uv run python main.py
uv run uvicorn main:app --port 8000 --reload

# 运行测试
uv run pytest

# 升级所有依赖
uv lock --upgrade
```

---

## 2. 系统架构

### 2.1 架构总览

```
┌─────────────────────────────────────────────────┐
│                    前端 (Vue 3)                    │
│               http://localhost:5173              │
└─────────────────────┬───────────────────────────┘
                      │ HTTP / SSE (流式)
                      ▼
┌─────────────────────────────────────────────────┐
│               后端 API (FastAPI)                  │
│               http://localhost:8000              │
│                                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────────┐   │
│  │ 文档管理  │ │ 知识库管理│ │ 文档检索  │ │   问答服务     │   │
│  │  Router  │ │  Router  │ │  Router  │ │   Router      │   │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └───────┬───────┘   │
│       │             │            │               │            │
│  ┌────┴─────────────┴────────────┴───────────────┴───────┐   │
│  │                   Service Layer                       │   │
│  │  ┌──────────┐ ┌────────┐ ┌──────────┐ ┌──────────┐   │   │
│  │  │Document  │ │Knowledge│ │  Search  │ │  Chat    │   │   │
│  │  │Service   │ │Service  │ │ Service  │ │ Service  │   │   │
│  │  └────┬─────┘ └────┬────┘ └────┬─────┘ └────┬─────┘   │   │
│  └───────┼─────────────┼──────────┼────────────┼─────────┘   │
│          │              │           │             │             │
│  ┌───────┴──────────────┴───────────┴─────────────┴─────────┐  │
│  │                    Core Layer                            │  │
│  │  ┌──────────┐ ┌──────────┐ ┌────────────┐               │  │
│  │  │ Parser   │ │Indexing  │ │ Retrieval  │               │  │
│  │  │ Engine   │ │ Engine   │ │ Engine     │               │  │
│  │  │          │ │(LlamaIdx)│ │ (LlamaIdx) │               │  │
│  │  └──────────┘ └────┬─────┘ └─────┬──────┘               │  │
│  └─────────────────────┼─────────────┼──────────────────────┘  │
└────────────────────────┼─────────────┼────────────┘
                         │             │
         ┌───────────────┼─────────────┼───────────────┐
         │               │             │               │
         ▼               ▼             ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│    MinIO     │ │ PostgreSQL   │ │   Milvus     │ │  Xinference  │
│   (Docker)   │ │  (Docker)    │ │  (Docker)    │ │   (本地)     │
│   :9000      │ │  :5432       │ │  :19530      │ │   :9997      │
└──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘
```

### 2.2 项目目录结构

```
ai_bid_rag/
├── backend/
│   ├── pyproject.toml           # 项目依赖（uv 管理）
│   ├── main.py                # FastAPI 入口
│   ├── config.py              # 配置管理（环境变量、模型配置）
│   ├── routers/
│   │   ├── document.py        # 文档上传、列表、删除 API
│   │   ├── knowledge_base.py  # 知识库 CRUD API
│   │   ├── search.py          # 文档检索 API
│   │   └── chat.py            # 问答（含流式 SSE）API
│   ├── services/
│   │   ├── document_service.py
│   │   ├── kb_service.py
│   │   ├── search_service.py
│   │   └── chat_service.py
│   ├── core/
│   │   ├── parser.py          # 文档解析引擎（PDF/Word/OCR）
│   │   ├── indexing.py        # LlamaIndex 索引构建
│   │   ├── retrieval.py       # LlamaIndex 检索
│   │   └── llm_factory.py     # LLM 抽象工厂（灵活切换模型）
│   ├── models/
│   │   └── schemas.py         # Pydantic 数据模型
│   └── utils/
│       └── file_utils.py
├── frontend/
│   ├── src/
│   │   ├── views/
│   │   │   ├── KnowledgeBase.vue    # 知识库管理页
│   │   │   ├── DocumentList.vue     # 文档列表页
│   │   │   ├── SearchView.vue       # 文档检索页
│   │   │   └── ChatView.vue         # 问答对话页
│   │   ├── components/
│   │   ├── api/                     # Axios 请求封装
│   │   └── router/                  # Vue Router
│   └── package.json
├── docker-compose.yml          # Milvus 容器编排
├── docs/
│   ├── 01-requirements.md
│   └── 02-technical-design.md
└── README.md
```

### 2.3 分层职责说明

| 序号 | 分层 | 职责 |
|------|------|------|
| 1 | Router 层 | HTTP 请求路由、参数校验、响应格式化 |
| 2 | Service 层 | 业务逻辑编排、事务管理 |
| 3 | Core 层 | 核心能力（解析、索引、检索），与框架强相关 |
| 4 | Model 层 | 数据结构定义（Pydantic Schema） |
| 5 | Utils 层 | 通用工具（文件处理、路径工具等） |

---

## 3. 核心模块设计

### 3.1 文档解析引擎

#### 3.1.1 解析流程

```
PDF/Word 文件
    │
    ▼
┌─────────────┐
│  格式判断    │
└─────┬───────┘
      │
  ┌───┴───┐
  ▼       ▼
 PDF    Word
  │       │
  ├─ PyMuPDF 提取文本
  ├─ pdfplumber 提取表格
  ├─ 提取嵌入图片 → PaddleOCR
  │
  │    python-docx 提取文本
  │    ├─ 段落文本
  │    ├─ 表格数据
  │    └─ 图片 → PaddleOCR
  │
  ▼
┌─────────────┐
│  结构化合并   │
│  (Document)  │
└─────┬───────┘
      │
      ▼
  元数据附加（文件名、页码、知识库ID...）
      │
      ▼
  输出 LlamaIndex Document 列表
```

#### 3.1.2 技术要点

| 序号 | 要点 | 说明 |
|------|------|------|
| 1 | PDF 文本提取 | PyMuPDF（fitz）用于快速提取 PDF 文本和图片 |
| 2 | PDF 表格提取 | pdfplumber 专门处理表格，保留行列结构 |
| 3 | Word 文档解析 | python-docx 提取段落文本、表格数据和嵌入图片 |
| 4 | OCR 识别 | PaddleOCR 处理文档中的嵌入图片文字 |
| 5 | 输出格式 | `List[llama_index.Document]`，每个 Document 携带页面级元数据 |

#### 3.1.3 支持的文档格式

| 序号 | 格式 | 扩展名 | 解析库 |
|------|------|--------|--------|
| 1 | PDF | .pdf | PyMuPDF + pdfplumber |
| 2 | Word（新格式） | .docx | python-docx |
| 3 | Word（旧格式） | .doc | 待后续引入 LibreOffice 转换 |

### 3.2 索引引擎（基于 LlamaIndex）

#### 3.2.1 索引构建流程

```
LlamaIndex Documents
    │
    ▼
┌─────────────────┐
│  文本分割        │
│  (SentenceSplitter)
│  chunk_size=512  │
│  overlap=64      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Embedding       │
│  Xinference API  │
│  bge-large-zh    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  写入 Milvus     │
│  Collection:     │
│  kb_{kb_id}      │
└─────────────────┘
```

#### 3.2.2 关键设计参数

| 序号 | 参数 | 值 | 说明 |
|------|------|-----|------|
| 1 | Collection 命名 | `kb_{uuid}` | 每个知识库一个独立 Collection |
| 2 | 分割策略 | SentenceSplitter | 按句子边界分割，中文友好 |
| 3 | chunk_size | 512 tokens | 每个文本块大小 |
| 4 | chunk_overlap | 64 tokens | 相邻块重叠大小，防止语义断裂 |
| 5 | 索引类型 | IVF_FLAT | 平衡精度与速度 |
| 6 | 相似度度量 | IP（内积） | 向量相似度计算方式 |
| 7 | 元数据字段 | doc_id, file_name, page_number, chunk_index | 用于溯源和过滤 |

### 3.3 检索与问答引擎

#### 3.3.1 检索问答流程

```
用户提问 + 知识库ID
    │
    ▼
┌─────────────────┐
│  问题 Embedding  │  同模型向量化
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Milvus 检索     │  top_k=5
│  向量相似度搜索  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  构建 Prompt     │  拼接检索结果 + 对话历史
│  (LlamaIndex)   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  LLM 调用        │  DeepSeek（或其他）
│  (流式输出)      │  返回 SSE 流
└────────┬────────┘
         │
         ▼
  返回答案 + 引用来源
```

#### 3.3.2 Prompt 模板

```
你是一个投标文档分析助手。请根据以下文档内容回答用户问题。

【参考文档内容】
{context_str}

【对话历史】
{chat_history}

【用户问题】
{query_str}

请基于参考文档内容回答。如果文档中找不到相关信息，请明确说明。
回答时请注明信息来源（文档名、页码）。
```

#### 3.3.3 检索关键参数

| 序号 | 参数 | 值 | 说明 |
|------|------|-----|------|
| 1 | top_k | 5 | 检索返回的最相关文档片段数 |
| 2 | 相似度阈值 | 0.6 | 低于此阈值的结果丢弃 |
| 3 | LLM Temperature | 0.1 | 低温度保证回答准确性 |
| 4 | 上下文窗口 | 4096 tokens | Prompt 中参考文档内容的最大长度 |

### 3.4 文档检索引擎（纯检索，不含 LLM）

#### 3.4.1 检索流程

```
用户查询 + 知识库ID
    │
    ▼
┌─────────────────┐
│  查询 Embedding  │  同模型向量化
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Milvus 检索     │  top_k=10（可配置）
│  向量相似度搜索  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  结果过滤        │  相似度阈值过滤
│  similarity≥0.6  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  结果组装        │  拼接元数据
│                  │  （文档名、页码、片段）
└────────┬────────┘
         │
         ▼
  返回检索结果列表（JSON）
```

#### 3.4.2 检索关键参数

| 序号 | 参数 | 值 | 说明 |
|------|------|-----|------|
| 1 | top_k | 10 | 检索返回的最相关文档片段数 |
| 2 | 相似度阈值 | 0.6 | 低于此阈值的结果丢弃 |
| 3 | 排序方式 | 相似度降序 | 按得分从高到低排列 |
| 4 | 分页 | 支持 offset / limit | 前端分页展示大量结果 |

#### 3.4.3 与问答检索的区别

| 序号 | 对比维度 | 文档检索（3.4） | 问答检索（3.3） |
|------|----------|-----------------|-----------------|
| 1 | LLM 调用 | 不调用 | 调用 |
| 2 | 输出方式 | JSON 一次性返回 | SSE 流式返回 |
| 3 | 返回内容 | 文档片段 + 元数据 | LLM 生成的自然语言答案 |
| 4 | 对话上下文 | 无 | 有（多轮对话） |
| 5 | 典型用途 | 精确查找原文内容 | 综合分析、跨文档问答 |

---

## 4. API 设计

### 4.1 知识库 API

| 序号 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 1 | POST | `/api/knowledge-bases` | 创建知识库 |
| 2 | GET | `/api/knowledge-bases` | 获取知识库列表 |
| 3 | GET | `/api/knowledge-bases/{id}` | 获取知识库详情 |
| 4 | DELETE | `/api/knowledge-bases/{id}` | 删除知识库 |

#### 4.1.1 创建知识库 — 请求体

```json
{
  "name": "2024年投标项目",
  "description": "存放2024年度所有投标相关文档"
}
```

#### 4.1.2 创建知识库 — 响应体

```json
{
  "id": "kb_a1b2c3d4",
  "name": "2024年投标项目",
  "description": "存放2024年度所有投标相关文档",
  "document_count": 0,
  "created_at": "2026-08-07T10:00:00Z"
}
```

### 4.2 文档管理 API

| 序号 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 1 | POST | `/api/knowledge-bases/{kb_id}/documents` | 上传文档到指定知识库 |
| 2 | GET | `/api/knowledge-bases/{kb_id}/documents` | 获取知识库下的文档列表 |
| 3 | DELETE | `/api/knowledge-bases/{kb_id}/documents/{doc_id}` | 删除文档及向量数据 |

#### 4.2.1 文档上传 — 请求

- Content-Type: `multipart/form-data`
- 字段: `file`（文件）
- 支持格式: `.pdf`, `.docx`, `.doc`

#### 4.2.2 文档上传 — 响应体

```json
{
  "id": "doc_e5f6g7h8",
  "file_name": "XX项目投标书.pdf",
  "file_size": 5242880,
  "page_count": 45,
  "status": "processing",
  "created_at": "2026-08-07T10:05:00Z"
}
```

#### 4.2.3 文档状态枚举

| 序号 | 状态 | 说明 |
|------|------|------|
| 1 | uploading | 文件上传中 |
| 2 | processing | 解析与索引中 |
| 3 | completed | 解析索引完成，可检索 |
| 4 | failed | 解析失败，需重新上传 |

### 4.3 问答 API

| 序号 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 1 | POST | `/api/knowledge-bases/{kb_id}/chat` | 发送问题，SSE 流式返回 |
| 2 | DELETE | `/api/knowledge-bases/{kb_id}/chat/session` | 清除当前会话上下文 |

#### 4.3.1 问答请求体

```json
{
  "message": "2024年某项目的投标金额是多少？",
  "session_id": "uuid-optional"
}
```

#### 4.3.2 SSE 流式响应事件类型

| 序号 | 事件类型 | 说明 | 示例 |
|------|----------|------|------|
| 1 | token | 逐字输出的文本片段 | `{"type": "token", "content": "根据文档"}` |
| 2 | sources | 引用来源列表（回答结束时发送） | `{"type": "sources", "sources": [...]}` |
| 3 | error | 错误信息 | `{"type": "error", "message": "..."}` |
| 4 | done | 回答完成标记 | `{"type": "done"}` |

#### 4.3.3 SSE 流式响应示例

```
data: {"type": "token", "content": "根据"}
data: {"type": "token", "content": "文档"}
...
data: {"type": "sources", "sources": [{"file": "xxx.pdf", "page": 3, "text": "..."}]}
data: {"type": "done"}
```

### 4.4 文档检索 API

| 序号 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 1 | POST | `/api/knowledge-bases/{kb_id}/search` | 语义检索文档片段 |

#### 4.4.1 检索请求体

```json
{
  "query": "投标保证金金额",
  "top_k": 10,
  "similarity_threshold": 0.6,
  "offset": 0,
  "limit": 20
}
```

| 序号 | 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|------|--------|------|
| 1 | query | string | 是 | - | 检索关键词或自然语言描述 |
| 2 | top_k | int | 否 | 10 | 向量检索返回的候选数量 |
| 3 | similarity_threshold | float | 否 | 0.6 | 相似度阈值，过滤低分结果 |
| 4 | offset | int | 否 | 0 | 分页偏移 |
| 5 | limit | int | 否 | 20 | 每页条数 |

#### 4.4.2 检索响应体

```json
{
  "query": "投标保证金金额",
  "total": 15,
  "results": [
    {
      "rank": 1,
      "score": 0.92,
      "text": "投标保证金金额为人民币伍拾万元整（¥500,000）...",
      "doc_id": "doc_e5f6g7h8",
      "file_name": "XX项目招标文件.pdf",
      "page_number": 12,
      "chunk_index": 3
    },
    {
      "rank": 2,
      "score": 0.87,
      "text": "...",
      "doc_id": "doc_xxx",
      "file_name": "XX项目招标文件.pdf",
      "page_number": 8,
      "chunk_index": 1
    }
  ]
}
```

| 序号 | 字段 | 类型 | 说明 |
|------|------|------|------|
| 1 | rank | int | 排名序号 |
| 2 | score | float | 相似度得分（0~1） |
| 3 | text | string | 匹配的文档片段文本 |
| 4 | doc_id | string | 来源文档 ID |
| 5 | file_name | string | 来源文档名称 |
| 6 | page_number | int | 来源页码 |
| 7 | chunk_index | int | 片段在文档中的索引 |

---

## 5. 数据存储设计

### 5.1 Milvus Collection 设计

| 序号 | 属性 | 值 |
|------|------|-----|
| 1 | Collection 命名规则 | `kb_{knowledge_base_id}` |
| 2 | 向量维度 | 1024（bge-large-zh-v1.5） |
| 3 | 索引类型 | IVF_FLAT |
| 4 | 相似度度量 | IP（内积） |
| 5 | 主键字段 | `id`（INT64，自增） |
| 6 | 向量字段 | `embedding`（FLOAT_VECTOR，1024维） |

### 5.2 Milvus 标量字段

| 序号 | 字段名 | 类型 | 说明 |
|------|--------|------|------|
| 1 | doc_id | VARCHAR(64) | 文档唯一标识 |
| 2 | file_name | VARCHAR(512) | 原始文件名 |
| 3 | page_number | INT64 | 页码（从 1 开始） |
| 4 | chunk_index | INT64 | 块索引（从 0 开始） |
| 5 | kb_id | VARCHAR(64) | 所属知识库 ID |

### 5.3 业务数据存储

| 序号 | 数据类型 | 存储方式 | 说明 |
|------|----------|----------|------|
| 1 | 知识库元数据 | PostgreSQL | 知识库名称、描述、创建时间等 |
| 2 | 文档元数据 | PostgreSQL | 文档名称、大小、状态、所属知识库等 |
| 3 | 会话上下文 | PostgreSQL（conversations / messages 表） | 多轮对话持久化，支持历史回溯 |
| 4 | 文档原始文件 | MinIO（对象存储） | Object Key: `{kb_id}/{doc_id}.{ext}` |
| 5 | 模型配置 | PostgreSQL（llm_configs / embedding_configs 表） | LLM 和 Embedding 配置，支持前端切换 |

详细表结构见 [05-database-design.md](05-database-design.md)。

### 5.4 跨存储数据一致性

文档涉及 PG、Milvus、MinIO 三处存储，操作需保持最终一致性。

**写入流程：**

```text
1. 文件上传 → MinIO: PUT object (key={kb_id}/{doc_id}.{ext})
2. 元数据写入 → PG: INSERT documents (status='uploading')
3. 文档解析 → PG: UPDATE documents (status='processing')
4. 切片 + Embedding → Milvus: INSERT vectors + PG: INSERT document_chunks
5. 索引完成 → PG: UPDATE documents (status='completed', chunk_count=n)
              PG: UPDATE knowledge_bases (document_count += 1)
```

**删除文档流程：**

```text
1. Milvus: DELETE entities WHERE doc_id = ?
2. PG: DELETE document_chunks WHERE doc_id = ? (CASCADE)
3. PG: DELETE documents WHERE id = ? → 获取 object_key
4. MinIO: REMOVE object (key=object_key)
5. PG: UPDATE knowledge_bases (document_count -= 1)
```

**删除知识库流程：**

```text
1. Milvus: DROP COLLECTION kb_{kb_id}
2. PG: DELETE knowledge_bases WHERE id = ? (CASCADE → documents + chunks + conversations + messages)
3. MinIO: REMOVE objects (批量删除 prefix={kb_id}/)
```

**异常处理：**

| 场景 | 处理策略 |
|------|----------|
| MinIO 上传成功，PG 写入失败 | 回删 MinIO 对象 |
| PG 写入成功，Milvus 写入失败 | documents.status='failed'，记录 error_message |
| PG 删除成功，MinIO 删除失败 | 记录日志，定时任务清理孤儿对象 |
| PG 删除成功，Milvus 删除失败 | 记录日志，Milvus Collection 残留可后续清理 |

---

## 6. 部署方案

### 6.1 环境依赖

| 序号 | 组件 | 部署方式 | 端口 |
|------|------|---------|------|
| 1 | PostgreSQL | Docker | 5432 |
| 2 | Milvus | Docker Compose（standalone） | 19530 |
| 3 | MinIO | Docker Compose（Milvus 内置 + 独立 Bucket 存文档） | 9000 |
| 4 | Xinference | 本地 pip 安装运行 | 9997 |
| 5 | uv（环境管理） | 本地安装，管理 Python 依赖和虚拟环境 | - |
| 6 | 后端 FastAPI | uv 管理虚拟环境 + 依赖 | 8000 |
| 7 | 前端 Vue 3 | Vite 开发服务器 | 5173 |

### 6.2 启动步骤

```bash
# 0. 安装 uv（首次）
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"  # Windows
# 或: pip install uv

# 1. 启动 Milvus
docker-compose up -d

# 2. 启动 Xinference
xinference-local --host 0.0.0.0 --port 9997

# 3. 启动后端
cd backend
uv venv                          # 创建虚拟环境
uv pip install -r requirements.txt   # 安装依赖（兼容 pip）
# 或直接用 pyproject.toml:
# uv sync                        # 自动创建 venv 并安装所有依赖
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 4. 启动前端
cd frontend
npm install
npm run dev
```

### 6.3 docker-compose.yml

```yaml
version: '3.8'
services:
  # === PostgreSQL ===
  postgres:
    image: postgres:16
    container_name: bid_postgres
    ports:
      - "5432:5432"
    environment:
      POSTGRES_DB: ai_bid_rag
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    volumes:
      - ./volumes/postgres:/var/lib/postgresql/data

  # === Milvus 依赖 ===
  etcd:
    image: quay.io/coreos/etcd:v3.5.5
    environment:
      - ETCD_AUTO_COMPACTION_MODE=revision
      - ETCD_AUTO_COMPACTION_RETENTION=1000
      - ETCD_QUOTA_BACKEND_BYTES=4294967296
    volumes:
      - ./volumes/etcd:/etcd

  minio:
    image: minio/minio:latest
    ports:
      - "9000:9000"   # S3 API（Milvus + 文档存储）
      - "9001:9001"   # Web Console
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    volumes:
      - ./volumes/minio:/minio_data
    command: minio server /minio_data --console-address ":9001"

  standalone:
    image: milvusdb/milvus:v2.4.0
    ports:
      - "19530:19530"
      - "9091:9091"
    environment:
      ETCD_ENDPOINTS: etcd:2379
      MINIO_ADDRESS: minio:9000
    volumes:
      - ./volumes/milvus:/var/lib/milvus
    depends_on:
      - etcd
      - minio
```

> **说明：** MinIO 实例同时供 Milvus 内部使用和投标文档原始文件存储，两者通过不同 Bucket 隔离（Milvus 自动管理其 Bucket，文档存储使用独立的 `bid-documents` Bucket，应用启动时自动创建）。

### 6.4 硬件要求

| 序号 | 资源 | 最低要求 | 推荐配置 |
|------|------|----------|----------|
| 1 | CPU | 4 核 | 8 核 |
| 2 | 内存 | 8 GB | 16 GB |
| 3 | 磁盘 | 20 GB（不含文档存储） | 100 GB |
| 4 | GPU | 无硬性要求 | 推荐用于 Xinference 推理加速 |

---

## 7. 关键配置项

### 7.1 环境变量清单

```python
# === 7.1.1 LLM 配置 ===
LLM_PROVIDER=deepseek              # deepseek | openai | openai_compatible
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-xxxxxxxx
LLM_API_BASE=https://api.deepseek.com/v1
LLM_TEMPERATURE=0.1

# === 7.1.2 Embedding 配置 ===
EMBEDDING_API_BASE=http://localhost:9997/v1
EMBEDDING_MODEL=bge-large-zh-v1.5
EMBEDDING_DIM=1024

# === 7.1.3 Milvus 配置 ===
MILVUS_HOST=localhost
MILVUS_PORT=19530

# === 7.1.4 PostgreSQL 配置 ===
PG_HOST=localhost
PG_PORT=5432
PG_DATABASE=ai_bid_rag
PG_USER=postgres
PG_PASSWORD=postgres

# === 7.1.5 MinIO 配置 ===
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=bid-documents
MINIO_SECURE=false

# === 7.1.6 文档处理配置 ===
CHUNK_SIZE=512
CHUNK_OVERLAP=64
RETRIEVAL_TOP_K=5
RETRIEVAL_SIMILARITY_THRESHOLD=0.6

# === 7.1.7 服务配置 ===
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
TEMP_DIR=./temp                   # 文档解析临时目录
MAX_UPLOAD_SIZE=104857600         # 100MB
```

### 7.2 前端环境变量

```env
# .env.development
VITE_API_BASE_URL=http://localhost:8000/api
```

---

## 8. 风险与注意事项

| 序号 | 风险 | 说明 | 应对措施 |
|------|------|------|---------|
| 1 | OCR 准确率 | PaddleOCR 对复杂排版识别有限 | 后续可接入更专业的 OCR 服务 |
| 2 | .doc 格式兼容 | python-docx 不支持旧 .doc 格式 | 后续引入 LibreOffice 转换层 |
| 3 | Milvus 内存占用 | Milvus 需要一定内存（建议 ≥ 4GB） | 使用 Standalone 模式，索引类型选 IVF_FLAT 降低内存 |
| 4 | Xinference 模型下载 | 首次启动需下载模型文件 | 提前下载至本地缓存目录 |
| 5 | DeepSeek API 限流 | 高并发下可能触发限流 | 前端会话级别控制并发，后续可加入重试机制 |

---

## 9. 后续迭代方向

| 序号 | 方向 | 说明 |
|------|------|------|
| 1 | Milvus 集群模式 | 高可用部署，支撑大规模文档 |
| 2 | 混合检索 | 向量检索 + BM25 关键词检索，提升召回率 |
| 3 | Rerank 模型 | 引入重排序模型，提升检索精度 |
| 4 | 用户登录与权限控制 | 多用户隔离，操作审计 |
| 5 | 对话历史持久化 | 历史会话保存与回溯 |
| 6 | 异步文档解析 | Celery / Background Tasks 异步处理大文档 |
| 7 | 解析进度推送 | WebSocket 实时推送文档解析进度 |
