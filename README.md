# 投标文档 RAG 智能问答系统

基于 RAG（Retrieval-Augmented Generation）的投标文档智能问答系统，支持 PDF/Word 文档上传、语义检索和 AI 问答。

> **v2（分支 `v2-generic-kb` · 数据库 `ai_bid_rag_v2`）**：已演进为通用知识库平台——知识图谱的实体/关系本体不再硬编码，改由用户通过页面配置的**领域模板**驱动（内置『通用』『投标』预设，只读可复制；新建/编辑模板后知识库图谱按其自动抽取与过滤类型，支持在知识库/图谱页更换模板并联动失效重建）。设计见 `docs/10-generic-kb-v2-design.md`，开发方案见 `docs/plan/47-domain-config-plan.md`，逐里程碑记录见 `docs/changes.md`。
> v1 已封板于 git tag `v1.1.0`（分支 `langfuse-v4`，库 `ai_bid_rag` 数据不动）；回滚 = 切回 `langfuse-v4` 分支并还原 `backend/.env`（v1 备份 `backend/.env.v1.local`）。

## 技术栈

| 层次 | 技术 |
|------|------|
| 前端 | Vue 3 + Element Plus + Vite |
| 后端 | FastAPI (Python 异步) |
| RAG 框架 | LlamaIndex |
| 向量数据库 | Milvus (Docker) |
| 关系数据库 | PostgreSQL (Docker) |
| 对象存储 | MinIO (Docker) |
| Embedding | Xinference (本地 bge-large-zh-v1.5) |
| LLM | DeepSeek (默认，兼容 OpenAI API) |
| 包管理 | uv (Python) / npm (前端) |

## 架构总览

```
┌─────────────────────────────────────────────┐
│               前端 (Vue 3 + Vite)             │
│              http://localhost:5173           │
└──────────────────┬──────────────────────────┘
                   │ HTTP / SSE (流式)
                   ▼
┌─────────────────────────────────────────────┐
│            后端 API (FastAPI)                 │
│              http://localhost:8000           │
│                                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│  │ 文档管理  │ │ 知识库管理│ │ 文档检索  │ │  问答服务  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘
└───────┬──────────────┬──────────────┬───────────────┘
        │              │              │
        ▼              ▼              ▼
┌──────────┐  ┌──────────┐  ┌──────────────┐
│  MinIO   │  │PostgreSQL│  │   Milvus     │
│  :9000   │  │  :5432   │  │   :19530     │
└──────────┘  └──────────┘  └──────────────┘
```

## 快速启动

### 1. 环境准备

- Docker Desktop
- Python 3.10+
- Node.js 18+
- [uv](https://docs.astral.sh/uv/) (`pip install uv`)
- Xinference (本地部署 Embedding 模型)

### 2. 配置环境变量

```bash
cp backend/.env.example backend/.env
# 编辑 backend/.env，填入实际的 API Key 等配置
```

### 3. 启动基础设施

```bash
docker compose up -d
# 启动 PostgreSQL + Milvus(etcd+minio) + MinIO
```

### 4. 初始化数据库

```bash
cd backend
uv sync                          # 安装依赖
uv run alembic upgrade head      # 创建表结构
```

### 5. 启动后端

```bash
cd backend
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

访问 http://localhost:8000/docs 查看 API 文档。

### 6. 启动前端

```bash
cd frontend
npm install
npm run dev
```

访问 http://localhost:5173 打开应用界面。

## 项目结构

```
ai_bid_rag/
├── backend/
│   ├── main.py                  # FastAPI 入口
│   ├── config.py                # 配置管理
│   ├── pyproject.toml           # 依赖声明 (uv)
│   ├── alembic/                 # 数据库迁移
│   │   └── versions/
│   │       └── 0001_init.py     # 初始迁移 (7 张表)
│   ├── models/                  # SQLAlchemy ORM 模型
│   │   ├── base.py
│   │   ├── knowledge_base.py
│   │   ├── document.py
│   │   ├── conversation.py
│   │   ├── config_model.py
│   │   └── schemas.py           # Pydantic 请求/响应
│   ├── routers/                 # API 路由
│   │   ├── knowledge_base.py
│   │   ├── document.py
│   │   ├── search.py
│   │   └── chat.py
│   ├── services/                # 业务逻辑
│   ├── core/                    # 核心引擎
│   │   ├── database.py
│   │   ├── llm_factory.py
│   │   ├── parser.py            # 文档解析 (stub)
│   │   ├── indexing.py          # 索引引擎 (stub)
│   │   └── retrieval.py         # 检索引擎 (stub)
│   └── utils/
├── frontend/
│   └── src/
│       ├── views/               # 页面
│       ├── components/          # 组件
│       ├── api/                 # API 封装
│       └── router/              # 路由
├── docs/                        # 设计文档
├── docker-compose.yml           # 容器编排
├── .env.example                 # 环境变量模板
└── README.md
```

## API 概览

| 模块 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 知识库 | POST | `/api/knowledge-bases` | 创建知识库 |
| 知识库 | GET | `/api/knowledge-bases` | 知识库列表 |
| 知识库 | DELETE | `/api/knowledge-bases/{id}` | 删除知识库 |
| 文档 | POST | `/api/knowledge-bases/{id}/documents` | 上传文档 |
| 文档 | GET | `/api/knowledge-bases/{id}/documents` | 文档列表 |
| 文档 | DELETE | `/api/knowledge-bases/{id}/documents/{doc_id}` | 删除文档 |
| 检索 | POST | `/api/knowledge-bases/{id}/search` | 语义检索 |
| 问答 | POST | `/api/knowledge-bases/{id}/chat` | SSE 流式问答 |

启动后端后访问 http://localhost:8000/docs 查看完整 Swagger 文档。

## 开发阶段

| 阶段 | 状态 | 说明 |
|------|------|------|
| Phase 1 | ✅ 已完成 | 项目骨架搭建（本版本） |
| Phase 2 | 🔜 待开发 | Vector RAG 核心链路（文档解析、索引、检索、问答） |
| Phase 3 | 📋 计划中 | Graph RAG（Neo4j 知识图谱） |
| Phase 4 | 📋 计划中 | 词库系统（实体抽取加速） |

## License

待定
