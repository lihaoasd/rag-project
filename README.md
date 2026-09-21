# 文档 RAG 智能系统

基于 RAG（Retrieval-Augmented Generation）的**通用知识库智能问答平台**。支持 PDF / Word 文档上传，构建 "向量 + 全文 + 知识图谱" 三路混合检索，并通过 SSE 流式回答、附引用来源。

## 一、核心特性

* **多知识库隔离**：知识库（KB）→ 文档 → 切片三级模型；向量、全文、图谱均按 `kb_id` 严格隔离。

* **三路混合检索**：Dense（Milvus 稠密向量 `bge-large-zh-v1.5`，1024 维）+ Sparse（Milvus BM25）+ Fulltext（Elasticsearch 8 + IK 分词），多路独立召回后 RRF 融合，可选重排序。

* **Graph RAG**：基于 Neo4j 的实体 / 关系图谱，支持全量重建、单文件增量与剥离、ECharts 力导向可视化，图检索证据可溯源到 chunk。

* **领域模板驱动（v2）**：图谱的实体 / 关系类型由页面配置的领域模板决定；内置「通用」「投标」预设（只读可复制），新建库选模板、可换绑。

* **词库系统**：全局词库服务 IK 词典导出与查询同义词扩展；在图谱侧与领域模板多对多映射。

* **问答增强**：Query 改写、意图识别（闲聊不走检索）、SSE 流式 token、Markdown 渲染、引用来源。

* **异步管线**：上传即返回，解析与图谱构建分别走独立内存队列 worker，不阻塞请求。

* **可配置 / 可观测**：LLM、Embedding、提示词、chunk 与检索参数均在界面配置；Langfuse 全链路追踪。



***

## 二、系统架构

### 2.1 总体架构



```
┌───────────────────────────────────────────────────────────────┐

│                     前端 (Vue 3 + Element Plus)                 │

│   知识库 / 文档 / 分块 / 检索 / 问答 / 图谱 / 领域模板 / 词库 /  │

│   模型配置 / 提示词 / ES 管理                                    │

│   dev: http://localhost:5173  (容器化: :80)                     │

└───────────────────────────┬───────────────────────────────────┘

&#x20;                           │ HTTP / SSE（流式）

&#x20;                           ▼

┌───────────────────────────────────────────────────────────────┐

│                  后端 API (FastAPI :8000)                       │

│  Router 层：12 个模块（KB/文档/检索/问答/分块/图谱/领域/词库/     │

│             模型/提示词/ES 管理）                                 │

│  Service 层：业务编排 + 事务                                     │

│  Core  层：解析 / 切片 / 索引 / 三路检索 / 图谱抽取 / 队列 /      │

│            LLM·Embedding 工厂 / 提示词渲染 / Langfuse           │

│  异步 worker：parse\_queue（解析）、graph\_queue（图谱）           │

└───────┬───────────────┬───────────────┬───────────────┬─────────┘

&#x20;       │               │               │               │

&#x20;       ▼               ▼               ▼               ▼

&#x20; ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────────┐

&#x20; │PostgreSQL │   │  MinIO   │   │  Milvus  │   │      ES 8    │

&#x20; │ KB/文档/   │   │ 原始文件  │   │ 稠密+稀疏 │   │ IK 全文索引  │

&#x20; │ 切片/配置  │   │ 对象存储  │   │ 向量检索  │   │ bid\_chunks\_v2│

&#x20; └──────────┘   └──────────┘   └──────────┘   └──────────────┘

&#x20;       │

&#x20;       ▼（GRAPH\_ENABLED=true）

&#x20; ┌──────────┐        ┌──────────────┐

&#x20; │  Neo4j   │        │  Xinference  │

&#x20; │ 7687 Bolt│        │ Embedding 推理│

&#x20; │ 知识图谱  │        │ bge-large-zh │

&#x20; └──────────┘        └──────────────┘
```

> 生产 / 部署机上，PostgreSQL、Milvus、MinIO、Elasticsearch、Xinference、Langfuse 均为
>
> **外部独立服务**
>
> ，地址由 
>
> `backend/.env`
>
>  指定；仓库 
>
> `docker-compose.yml`
>
>  只编排 
>
> **backend + frontend + neo4j**
>
> 。

### 2.2 分层职责



| 层          | 位置                      | 职责                                   |
| ---------- | ----------------------- | ------------------------------------ |
| Router     | `backend/routers/`      | HTTP 路由、参数校验、响应 / SSE 组装             |
| Service    | `backend/services/`     | 业务编排、事务、状态流转、跨存储一致性                  |
| Repository | `backend/repositories/` | SQLAlchemy 数据访问，屏蔽表细节                |
| Core       | `backend/core/`         | 引擎：解析、切片、向量 / 全文 / 图谱存储与检索、队列、LLM 工厂 |
| Model      | `backend/models/`       | ORM 实体 + Pydantic 请求 / 响应 Schema     |
| Utils      | `backend/utils/`        | 日志、文件等通用工具                           |



***

## 三、功能模块



| 模块（前端页面） | 能力                                       |
| -------- | ---------------------------------------- |
| 知识库管理    | 创建 / 列表 / 删除知识库，选择与更换领域模板，查看文档数 / 图谱状态   |
| 文档管理     | 上传 PDF / DOCX，列表，触发解析，删除，行内查看图谱构建状态      |
| 分块查看     | 浏览某文档的切片、页码、索引，人工纠错                      |
| 检索       | 三路（稠密 / 稀疏 / 全文）+ 图通道多选，RRF 融合，关键词高亮，分页  |
| 智能问答     | SSE 流式对话、多轮上下文、意图识别、引用来源跳转               |
| 知识图谱     | 力导向可视化、实体 / 关系统计、子图展开、全量 / 单文件构建与清除、实体编辑 |
| 领域模板     | 模板 CRUD、实体 / 关系类型可视化编辑、抽取提示词预览、预设复制      |
| 词库       | 词条 + 别名管理、领域映射、导出 IK 词典、查询同义词扩展、CSV 导入导出 |
| 模型配置     | LLM / Embedding 连接配置与启动自检，按知识库绑定         |
| 提示词管理    | 各场景提示词模板（问答 / 改写 / 意图等）；实体抽取提示词改由模板生成    |
| ES 管理    | 索引健康、PG↔ES 对账、手动重建                       |



***

## 四、技术栈



| 层次        | 技术                                                                                         |
| --------- | ------------------------------------------------------------------------------------------ |
| 前端        | Vue 3（Composition API）+ Element Plus + Vite 5 + ECharts + markdown-it + vue-router + axios |
| 后端        | FastAPI（异步）+ SQLAlchemy 2.0 async + Alembic + Pydantic v2                                  |
| RAG 框架    | LlamaIndex 0.11                                                                            |
| 向量检索      | Milvus（稠密 + BM25 稀疏）                                                                       |
| 全文检索      | Elasticsearch 8.13 + IK 分词插件                                                               |
| 知识图谱      | Neo4j 5.21（原生 driver + Cypher）                                                             |
| 关系库       | PostgreSQL（asyncpg）                                                                        |
| 对象存储      | MinIO                                                                                      |
| Embedding | Xinference 本地 `bge-large-zh-v1.5`（1024 维，OpenAI 兼容接口）                                      |
| LLM       | DeepSeek（默认，OpenAI 兼容；可在界面切换）                                                              |
| 可观测       | Langfuse 4（链路追踪 + 采样）                                                                      |
| 包管理       | uv（Python）/npm（前端）                                                                         |



***

## 五、快速启动

### 1. 环境准备



* Docker Desktop

* Python 3.10+、Node.js 18+

* [uv](https://docs.astral.sh/uv/)（`pip install uv`）

* Xinference（本地部署 `bge-large-zh-v1.5`，端口 9997）

* 外部依赖：PostgreSQL、Milvus、MinIO、Elasticsearch；Neo4j 可由本仓库 compose 提供

### 2. 配置环境变量



```
cp backend/.env.example backend/.env

\# 编辑 backend/.env：填入 LLM\_API\_KEY；按实际环境修改 PG / Milvus / MinIO / ES / Neo4j / Xinference 地址

\# v2 确认：PG\_DATABASE=ai\_bid\_rag\_v2、ES\_INDEX\_NAME=bid\_chunks\_v2
```

### 3. 初始化数据库



```
cd backend

uv sync

uv run alembic upgrade head
```

> `alembic/env.py`
>
>  通过环境变量读 PG 参数而非 
>
> `.env`
>
> ；跑迁移前请先注入 
>
> `PG_HOST/PG_PORT/PG_DATABASE/PG_USER/PG_PASSWORD`
>
> 。

### 4. 启动后端



```
cd backend

uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

启动后访问 [http://localhost:8000/docs](http://localhost:8000/docs)（Swagger）；健康检查 `GET /api/health`。

### 5. 启动前端



```
cd frontend

npm install

npm run dev
```

访问 [http://localhost:5173](http://localhost:5173) 打开应用。

### 6. 容器化部署（可选）



```
docker compose up -d

\# 起 backend(:8000) + frontend(:80) + neo4j(:7474/:7687)

\# 其余中间件需提前就绪，并在 backend/.env 中指向
```



***

## 六、运行时与异步任务

`main.py` 的启动生命周期（lifespan）会完成：日志初始化 → Langfuse → 模型连接自检 → ES 索引 ensure → 词库缓存加载 →（若开启图谱）Neo4j 索引与滞留状态重置 → 启动解析 worker、图谱 worker、每日 ES 对账循环。关闭时优雅停止各 worker 并释放 ES / Neo4j 连接。



***

## 七、API 概览

启动后端后访问 [http://localhost:8000/docs](http://localhost:8000/docs) 查看完整 Swagger。主要模块：



| 模块    | 前缀                                                | 说明                    |
| ----- | ------------------------------------------------- | --------------------- |
| 知识库   | `/api/knowledge-bases`                            | CRUD、绑定 / 更换领域模板      |
| 文档    | `/api/knowledge-bases/{kb_id}/documents`          | 上传、列表、删除、触发解析         |
| 分块    | `/api/knowledge-bases/{kb_id}/documents/{doc_id}` | 切片查看 / 编辑             |
| 检索    | `/api/search`                                     | 三路 + 图通道混合检索          |
| 问答    | `/api/knowledge-bases/{kb_id}/chat`               | SSE 流式问答              |
| 图谱    | `/api/knowledge-bases/{kb_id}/graph`              | 构建 / 清除 / 查询 /meta    |
| 领域模板  | `/api/domains`                                    | 模板 CRUD、复制、预览、删除校验    |
| 词库    | `/api/lexicons`                                   | 词条与领域映射、IK 词典导出       |
| 模型    | `/api/models`                                     | LLM / Embedding 配置与自检 |
| 提示词   | `/api/prompts`                                    | 提示词模板管理               |
| ES 管理 | `/api/es-admin`                                   | 索引健康、对账、重建            |



***

## 八、关键配置项（`backend/.env`）



* **LLM**：`LLM_PROVIDER` / `LLM_MODEL` / `LLM_API_KEY` / `LLM_API_BASE` / `LLM_TEMPERATURE`

* **Embedding**：`EMBEDDING_API_BASE` / `EMBEDDING_MODEL` / `EMBEDDING_DIM`

* **存储**：`PG_*`、`MILVUS_*`、`MINIO_*`、`ES_*`、`NEO4J_*`

* **切分 / 检索**：`CHUNK_SIZE=512`、`CHUNK_OVERLAP=64`、`RETRIEVAL_TOP_K`、`RETRIEVAL_SIMILARITY_THRESHOLD`、`RETRIEVAL_RERANK_THRESHOLD`

* **问答增强**：`QUERY_REWRITE_ENABLED`、`CHAT_QUERY_REWRITE_ENABLED`、`CHAT_INTENT_DETECT_ENABLED`

* **图谱**：`GRAPH_ENABLED`（默认关，开启后启用 Neo4j）

* **观测**：`LANGFUSE_ENABLED` 及公 / 私钥

* **上传**：`MAX_UPLOAD_SIZE`（默认 500MB）、`TEMP_DIR`



***

## License

待定