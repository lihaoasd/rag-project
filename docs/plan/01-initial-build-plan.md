# 投标文档 RAG 系统 — 初始项目构建方案

> 版本: v1.0 | 日期: 2026-08-07 | 状态: ✅ 已完成

---

## 1. 概述

基于 6 份已评审通过的设计文档，构建投标文档 RAG 智能问答系统的初始项目骨架。本次构建聚焦 **Phase 1 — Vector RAG 核心链路**，不包含 Graph RAG（Neo4j）和词库（Lexicon），这两块属于后续迭代。

**产出**：一个可启动、可连接各组件、路由骨架完整但核心逻辑为 stub 的项目基础。

---

## 2. 改动范围

### 2.1 新建文件清单

```
ai_bid_rag/
├── docker-compose.yml                    # PostgreSQL + Milvus(etcd+minio) + MinIO文档存储
├── .env.example                          # 环境变量模板
├── .gitignore                            # Python + Node + IDE
├── README.md                             # 项目说明 + 启动步骤
│
├── backend/
│   ├── pyproject.toml                    # uv 项目配置 + 依赖声明
│   ├── main.py                           # FastAPI 入口（lifespan、CORS、路由挂载）
│   ├── config.py                         # Pydantic Settings 读取环境变量
│   ├── alembic.ini                       # Alembic 基础配置
│   │
│   ├── alembic/
│   │   ├── env.py                        # 异步 Alembic 运行环境
│   │   ├── script.py.mako                # 迁移文件模板
│   │   └── versions/
│   │       └── 0001_init.py              # 初始迁移：7 张表
│   │
│   ├── models/
│   │   ├── __init__.py                   # 导出所有模型
│   │   ├── base.py                       # Base + TimestampMixin + UUIDMixin
│   │   ├── knowledge_base.py             # KnowledgeBase
│   │   ├── document.py                   # Document + DocumentChunk
│   │   ├── conversation.py               # Conversation + Message
│   │   ├── config_model.py               # LLMConfig + EmbeddingConfig
│   │   └── schemas.py                    # Pydantic 请求/响应 Schema
│   │
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── knowledge_base.py             # KB CRUD（stub → 后续实现）
│   │   ├── document.py                   # 文档上传/列表/删除（stub）
│   │   ├── search.py                     # 语义检索（stub）
│   │   └── chat.py                       # 问答 SSE 流式（stub）
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── kb_service.py                 # 知识库业务逻辑（stub）
│   │   ├── document_service.py           # 文档业务逻辑（stub）
│   │   ├── search_service.py             # 检索业务逻辑（stub）
│   │   └── chat_service.py               # 问答业务逻辑（stub）
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── database.py                   # SQLAlchemy async engine + session factory
│   │   ├── parser.py                     # 文档解析引擎（stub，接口定义）
│   │   ├── indexing.py                   # LlamaIndex 索引引擎（stub）
│   │   ├── retrieval.py                  # 检索引擎（stub）
│   │   └── llm_factory.py                # LLM 抽象工厂（完整实现）
│   │
│   └── utils/
│       ├── __init__.py
│       └── file_utils.py                 # 文件工具（格式校验、临时目录）
│
├── frontend/
│   ├── package.json                      # Vue 3 + Vite + Element Plus + Axios
│   ├── vite.config.js                    # Vite 配置（代理 /api → 后端）
│   ├── index.html                        # 入口 HTML
│   │
│   └── src/
│       ├── main.js                       # Vue 应用入口
│       ├── App.vue                       # 根组件 + 路由出口
│       ├── style.css                     # 全局样式
│       │
│       ├── router/
│       │   └── index.js                  # Vue Router（5 个路由）
│       │
│       ├── api/
│       │   ├── request.js                # Axios 实例（baseURL、拦截器）
│       │   ├── knowledgeBase.js          # 知识库 API 封装
│       │   ├── document.js               # 文档 API 封装
│       │   ├── search.js                 # 检索 API 封装
│       │   └── chat.js                   # 问答 API 封装（含 SSE）
│       │
│       ├── views/
│       │   ├── KnowledgeBaseView.vue     # 知识库管理页（stub）
│       │   ├── DocumentListView.vue      # 文档列表页（stub）
│       │   ├── SearchView.vue            # 文档检索页（stub）
│       │   └── ChatView.vue              # 智能问答页（stub）
│       │
│       └── components/
│           └── AppLayout.vue             # 通用布局（侧边栏 + 顶栏 + 内容区）
```

---

## 3. 实现步骤

### Step 1: 基础设施文件

**操作**：创建 `.gitignore`、`.env.example`、`docker-compose.yml`、`README.md`

**关键内容**：
- `.gitignore`: Python (`__pycache__`, `.venv`, `*.pyc`), Node (`node_modules`, `dist`), IDE (`.vscode`, `.idea`), 环境变量 (`.env`), 数据卷 (`volumes/`)
- `.env.example`: 所有环境变量模板（LLM、Embedding、Milvus、PG、MinIO、文档处理、服务），敏感值用 `your-xxx` 占位
- `docker-compose.yml`: 严格按[技术设计文档 6.3 节](02-technical-design.md)，包含 postgres、etcd、minio、standalone(milvus) 四个服务
- `README.md`: 项目简介 + 技术栈 + 快速启动步骤

**验证**：`docker-compose up -d` 后四个容器正常运行，`docker ps` 显示 healthy

### Step 2: 后端项目骨架

**操作**：创建 `backend/pyproject.toml` + `backend/config.py` + `backend/main.py`

**关键内容**：
- `pyproject.toml`: 严格按[技术设计文档 1.4.2 节](02-technical-design.md)，含 RAG 相关依赖 + SQLAlchemy/asyncpg/alembic
- `config.py`: 使用 `pydantic-settings`，从环境变量读取所有配置项（[技术设计文档 7.1 节](02-technical-design.md)）
- `main.py`: FastAPI 应用，CORS 中间件，lifespan 事件，四组路由挂载，健康检查端点 `/api/health`

**验证**：`cd backend && uv sync && uv run uvicorn main:app --port 8000` 启动成功，`curl localhost:8000/api/health` 返回 `{"status": "healthy"}`

### Step 3: 数据库模型 + Alembic

**操作**：创建 `backend/models/` 全部文件 + `backend/core/database.py` + Alembic 配置 + 初始迁移

**关键内容**：
- `models/base.py`: `Base(DeclarativeBase)`, `TimestampMixin`(created_at+updated_at), `UUIDMixin`(UUID PK)
- `models/knowledge_base.py`, `document.py`, `conversation.py`, `config_model.py`: 严格按[数据库设计文档](05-database-design.md)第 3 节，含所有字段、约束、relationship
- `models/__init__.py`: 导出所有 7 个模型
- `core/database.py`: `create_async_engine` + `async_sessionmaker` + `get_db` 依赖注入
- `alembic/versions/0001_init.py`: 手写初始迁移（基于[数据库设计文档](05-database-design.md) DDL + [数据库迁移文档](06-database-migration.md)），含 upgrade/downgrade、触发器、CHECK 约束、索引、默认数据

**验证**：`docker-compose up -d postgres` → `uv run alembic upgrade head` 成功创建 7 张表 → 用 psql 验证表结构

### Step 4: Pydantic Schema + LLM Factory

**操作**：创建 `backend/models/schemas.py` + `backend/core/llm_factory.py`

**关键内容**：
- `schemas.py`: 所有请求/响应 Pydantic 模型（KB Create/Response, Document Upload/List/Response, Search Request/Response, Chat Request/SSE events）——严格对应[技术设计文档第 4 节 API 设计](02-technical-design.md)
- `llm_factory.py`: 完整实现，按 provider 创建 LLM 实例（deepseek/openai/openai_compatible），基于 LlamaIndex `OpenAILike`，同时提供 `create_embedding()` 工厂函数

**验证**：Python import 无报错，LLM Factory 可正常创建实例

### Step 5: Router + Service + Core Stub

**操作**：创建 `backend/routers/`, `backend/services/`, `backend/core/`（parser, indexing, retrieval）

**关键内容**：
- 四个 Router: 实现基础 CRUD 接口（共 9 个端点），service 调用返回占位数据
- 四个 Service: 定义方法签名，返回占位数据或空列表
- `core/parser.py`: 定义 `parse_document()` 函数签名，输入文件路径，输出 `List[Document]`
- `core/indexing.py`: 定义 `build_index()`, `delete_index()` 签名
- `core/retrieval.py`: 定义 `retrieve()` 签名
- `backend/utils/file_utils.py`: `validate_file_type()`, `get_file_extension()`, `ensure_temp_dir()`

**验证**：启动 FastAPI → 访问 `/api/docs` 看到所有接口（9 个端点分布在 4 个 tag 分组中）

### Step 6: 前端项目骨架

**操作**：创建 Vue 3 + Vite + Element Plus 项目骨架

**关键内容**：
- `vite.config.js`: 代理 `/api` 到 `http://localhost:8000`
- `src/router/index.js`: 5 个路由（/, /knowledge-bases, /knowledge-bases/:kbId/documents, /search, /chat）
- `src/api/request.js`: Axios 实例，`baseURL: '/api'`，通用错误拦截
- `src/api/*.js`: 封装所有 API 调用（含 SSE 流式问答）
- `src/components/AppLayout.vue`: Element Plus 布局（el-container + el-aside + el-main），侧边栏导航
- `src/views/*.vue`: 4 个页面，每个页面含标题和占位内容
- `App.vue`: 根组件挂载 `<AppLayout><router-view /></AppLayout>`

**验证**：`npm install && npm run dev` → 浏览器打开 localhost:5173 → 侧边栏可切换页面 → DevTools Network 确认 API 代理正常

---

## 4. 依赖关系

```
Step 1 (基础设施)  ──→  Step 2 (后端骨架)  ──→  Step 3 (数据库模型)
                                                    │
                    Step 4 (Schema + LLM) ◄──────────┘
                         │
                    Step 5 (Router/Service/Core Stub)
                         │
                    Step 6 (前端骨架，可与 Step 2-5 并行)
```

---

## 5. 验证方式

### 5.1 端到端验证

```bash
# 1. 启动基础设施
docker-compose up -d

# 2. 验证 PostgreSQL + 表
docker exec bid_postgres psql -U postgres -d ai_bid_rag -c "\dt"

# 3. 启动后端
cd backend && uv run uvicorn main:app --port 8000 --reload

# 4. 验证 Swagger
curl http://localhost:8000/api/docs        # 应返回 HTML
curl http://localhost:8000/api/health      # {"status": "healthy", "version": "0.1.0"}

# 5. 验证 DB 连接
curl http://localhost:8000/api/knowledge-bases  # 应返回 []

# 6. 启动前端
cd frontend && npm install && npm run dev

# 7. 验证前端
# 浏览器打开 http://localhost:5173
# 检查：页面加载、侧边栏导航、Network 无 404
```

### 5.2 各步检查清单

| 步骤 | 检查项 | 结果 |
|------|--------|------|
| 1 | Docker 四个容器 running，MinIO console 可访问 :9001 | ⚠️ 本地环境验证 |
| 2 | uv sync 成功，FastAPI 启动无报错 | ✅ 已验证 |
| 3 | 7 张表创建成功，alembic history 显示一条迁移 | ⚠️ 需 Docker |
| 4 | schema.py import 通过，llm_factory 用 mock 验证 | ✅ 已验证 |
| 5 | /api/docs 列出所有端点（9 个） | ✅ 已验证 |
| 6 | 前端 5 个页面可切换，API 代理正常工作 | ⚠️ 需 Node.js |

---

## 6. 不做什么（本次范围外）

- ❌ 文档解析实际实现（parser.py 仅签名）
- ❌ Milvus 连接与索引构建（indexing.py 仅签名）
- ❌ LLM 问答链路（chat.py router 仅 stub）
- ❌ 文件上传到 MinIO（document.py router 仅 stub）
- ❌ Graph RAG / Neo4j 相关任何代码
- ❌ 词库（Lexicon）相关任何代码
- ❌ 用户认证 / 权限
- ❌ 前端样式美化（仅骨架布局）
- ❌ 单元测试（后续迭代统一补）
- ❌ CI/CD 配置

---

## 7. 实际产出统计

| 层次 | 文件数 | 关键内容 |
|------|--------|----------|
| 基础设施 | 4 | `.gitignore`, `.env.example`, `docker-compose.yml`, `README.md` |
| 后端配置 | 3 | `pyproject.toml`（154 个包）, `config.py`, `main.py` |
| 数据模型 | 6 | 7 个 SQLAlchemy ORM 模型 + Pydantic Schema |
| 数据库迁移 | 4 | Alembic 异步配置 + 初始迁移（7 张表，含触发器/约束/索引/默认数据） |
| 核心引擎 | 5 | `database.py`, `llm_factory.py`（完整）, `parser.py`, `indexing.py`, `retrieval.py`（stub） |
| 服务层 | 4 | 知识库 / 文档 / 检索 / 问答 业务逻辑（stub） |
| 路由层 | 4 | 4 组路由，共 9 个 API 端点 |
| 工具 | 1 | `file_utils.py`（格式校验） |
| 前端基础 | 6 | `package.json`, `vite.config.js`, `index.html`, `main.js`, `App.vue`, `style.css` |
| 前端 API | 5 | Axios 实例 + 4 组 API 封装（含 SSE 流式） |
| 前端路由 | 1 | 5 个路由（含懒加载） |
| 前端组件 | 1 | `AppLayout.vue`（侧边栏 + 顶栏布局） |
| 前端页面 | 4 | 知识库管理 / 文档管理 / 检索 / 问答（骨架页面） |
| **合计** | **~55** | |
