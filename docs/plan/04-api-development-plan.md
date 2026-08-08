# Phase 2 — API 开发与前端对接方案

> 版本: v1.0 | 日期: 2026-08-08 | 状态: 待评审

---

## Context

Phase 1 已完成项目骨架搭建：数据库模型（7 张表）、路由框架（4 组 API、9 个端点）、前端 UI（4 个页面、主题系统、API 封装层）。但所有 `services/` 和 `core/`（除 `llm_factory.py` 和 `database.py`）均为 stub 实现，返回占位数据或直接抛 `NotImplementedError`。

本次 Phase 2 目标：实现 RAG 核心链路，对外提供真实 API，前端完成对接。

---

## 1. 当前状态检查

### 已完成 ✅

| 层次 | 文件 | 状态 |
|------|------|------|
| 配置 | `config.py` | 完整的 pydantic-settings，覆盖所有环境变量 |
| 数据库 | `core/database.py`、`models/`、`alembic/` | 异步引擎 + 7 张表 + 初始迁移 |
| LLM | `core/llm_factory.py` | LLM/Embedding 工厂函数完整实现 |
| 路由 | `routers/*.py` | 4 组路由，参数校验、错误处理、日志完备 |
| 工具 | `utils/logging.py`、`utils/file_utils.py` | 集中日志 + 文件格式校验 |
| 前端 UI | `views/*.vue`、`components/AppLayout.vue` | 完整页面，含交互逻辑 |
| 前端 API | `api/*.js` | Axios 封装 + SSE 流式问答 |
| 前端主题 | `style.css` | CSS 变量体系 + Element Plus 覆写 |

### 需要实现 ❌

| 文件 | 当前状态 | 目标状态 |
|------|----------|----------|
| `core/parser.py` | `raise NotImplementedError` | PDF/Word 解析，返回 LlamaIndex Document 列表 |
| `core/indexing.py` | `raise NotImplementedError` | 向量化 → Milvus 写入 |
| `core/retrieval.py` | `raise NotImplementedError` | Milvus 向量检索 + 元数据回填 |
| `services/kb_service.py` | 返回硬编码占位数据 | PostgreSQL CRUD，操作 knowledge_bases 表 |
| `services/document_service.py` | 返回硬编码占位数据 | 上传 → MinIO → 解析 → 索引 完整流程 |
| `services/search_service.py` | 返回空结果 | 调用 retrieval + 分页组装 |
| `services/chat_service.py` | 逐字返回占位文本 | RAG 管道 + LLM 流式生成 + 来源引用 |

---

## 2. 设计模式优化

### 2.1 当前架构评估

当前已有分层架构 (Router → Service → Core)，总体合理。但存在以下可优化点：

| 问题 | 现状 | 影响 |
|------|------|------|
| Service 直接操作 ORM | 散落 `async_session` 调用 | 测试困难，SQL 逻辑重复 |
| Parser 无抽象 | `parse_document()` 一个函数处理所有格式 | 添加新格式需改核心逻辑 |
| Chat 管道耦合 | 检索→Prompt→LLM 混在一起 | 中间环节不可单独测试/替换 |
| MinIO 操作散落 | 各 Service 自行连接 MinIO | 连接管理重复 |

### 2.2 推荐设计模式

#### 2.2.1 Repository 模式（数据访问层）

**做什么**：在 Service 和 ORM 之间加一层薄薄的 Repository，封装常见 CRUD。

**不做什么**：不搞泛型基类、不搞工作单元（UoW）。当前项目规模不需要。

```
# 新增文件：backend/repositories/kb_repository.py
class KnowledgeBaseRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, name: str, description: str | None) -> KnowledgeBase: ...
    async def list_all(self) -> list[KnowledgeBase]: ...
    async def get_by_id(self, kb_id: str) -> KnowledgeBase | None: ...
    async def delete(self, kb_id: str) -> bool: ...
    async def increment_doc_count(self, kb_id: str) -> None: ...
    async def decrement_doc_count(self, kb_id: str) -> None: ...
```

同样为 Document、Conversation、Message 各建一个最小 Repository。

**收益**：Service 层不直接写 SQLAlchemy 查询，单 Service 可 mock Repository 测试。

#### 2.2.2 Strategy 模式（文档解析）

**做什么**：每种文档格式一个 Parser 类，通过注册表按扩展名查找。

```
# 改造文件：backend/core/parser.py
class BaseParser(ABC):
    @abstractmethod
    async def parse(self, file_path: Path) -> list[Document]: ...

class PDFParser(BaseParser): ...    # PyMuPDF + pdfplumber
class DocxParser(BaseParser): ...  # python-docx

# 注册表（模块级字典）
PARSER_REGISTRY: dict[str, BaseParser] = {
    "pdf": PDFParser(),
    "docx": DocxParser(),
}

async def parse_document(file_path: Path, file_type: str) -> list[Document]:
    parser = PARSER_REGISTRY.get(file_type)
    if parser is None:
        raise ValueError(f"Unsupported file type: {file_type}")
    return await parser.parse(file_path)
```

**收益**：加新格式（如 `.txt`、`.md`）只需新增 Parser 类 + 注册，不影响现有代码。

#### 2.2.3 Pipeline 模式（问答管道）

**做什么**：将问答过程拆为独立阶段，每个阶段可独立替换/测试。

```
# 改造：chat_service.py 内部结构
async def chat_stream(kb_id, message, session_id) -> AsyncGenerator:
    # Stage 1: 检索
    retrieved = await retrieval.retrieve(query=message, kb_id=kb_id)
    # Stage 2: 构建 Prompt
    prompt = build_rag_prompt(query=message, context=retrieved, history=history)
    # Stage 3: LLM 生成 (streaming)
    async for token in llm.astream_chat(prompt):
        yield sse_event("token", token)
    # Stage 4: 来源引用
    yield sse_event("sources", extract_sources(retrieved))
    yield sse_event("done")
```

**收益**：Prompt 模板、检索参数、来源提取逻辑均可独立调整。

#### 2.2.4 MinIO 存储抽象

**做什么**：一个 `MinIOClient` 工具类封装 Bucket 操作。

```
# 新增文件：backend/utils/minio_client.py
class MinIOClient:
    async def upload(self, object_key: str, file_path: Path) -> None: ...
    async def delete(self, object_key: str) -> None: ...
    async def delete_prefix(self, prefix: str) -> None: ...
    def get_object_url(self, object_key: str) -> str: ...
```

**收益**：MinIO 连接管理集中一处，换 S3 兼容存储只改此文件。

### 2.3 不做过度设计

| 不做 | 原因 |
|------|------|
| 通用 Repository 泛型基类 | 7 张表不值得 |
| 工作单元 (Unit of Work) | 当前无跨 Repository 事务需求 |
| 事件总线 / CQRS | 系统规模小，直接调用足够 |
| DI 容器 | FastAPI `Depends` 已足够 |
| Interface 满天飞 | 仅 Parser 确实有多态需求，其余 YAGNI |

---

## 3. 实现步骤

### Step 1: 基础设施 — MinIO 客户端 + Repository 层

**新增文件**：
- `backend/utils/minio_client.py` — MinIO 客户端封装
- `backend/repositories/__init__.py`
- `backend/repositories/kb_repository.py`
- `backend/repositories/document_repository.py`
- `backend/repositories/conversation_repository.py`

**依赖**：`core/database.py`（已存在）

**验证**：
```bash
# Python import 无报错
uv run python -c "from repositories.kb_repository import KnowledgeBaseRepository; print('OK')"
```

### Step 2: 核心引擎 — 文档解析 (core/parser.py)

**修改文件**：`backend/core/parser.py`

**关键逻辑（伪代码）**：
```
class PDFParser:
    parse(file_path):
        doc = fitz.open(file_path)
        pages = []
        for page in doc:
            text = page.get_text()
            tables = pdfplumber.extract_tables(page)  # 可选
            images = page.get_images()
            for img in images:
                ocr_text = PaddleOCR.recognize(extract_image(img))
                text += ocr_text
            pages.append(Document(text=text, metadata={page_number: ...}))
        return pages

class DocxParser:
    parse(file_path):
        doc = Document(file_path)
        # 段落 + 表格 + 图片 OCR
        return [Document(...)]

parse_document(file_path, file_type):
    parser = PARSER_REGISTRY[file_type]
    return parser.parse(file_path)
```

**依赖**：`utils/minio_client.py`（Step 1）、`utils/logging.py`（已有）

**验证**：
```bash
# 用测试文件验证
uv run python -c "
from core.parser import parse_document
import asyncio
docs = asyncio.run(parse_document(Path('test.pdf'), 'pdf'))
assert len(docs) > 0
"
```

### Step 3: 核心引擎 — 索引 (core/indexing.py)

**修改文件**：`backend/core/indexing.py`

**关键逻辑（伪代码）**：
```
build_index(chunks, metadata_list, kb_id, embedding_dim):
    # 1. 获取 Embedding 模型
    embed_model = create_embedding(settings)

    # 2. 对每个 chunk 向量化
    embeddings = [embed_model.get_text_embedding(c) for c in chunks]

    # 3. 连接 Milvus
    connections.connect(host=settings.milvus_host, port=settings.milvus_port)

    # 4. 获取或创建 Collection
    collection_name = f"kb_{kb_id}"
    if not utility.has_collection(collection_name):
        create_collection(collection_name, embedding_dim)

    # 5. 插入数据
    collection = Collection(collection_name)
    mr = collection.insert([embeddings, doc_ids, file_names, page_numbers, chunk_indices])
    collection.flush()
    return mr.primary_keys

delete_index(kb_id, doc_id):
    collection = Collection(f"kb_{kb_id}")
    collection.delete(expr=f'doc_id == "{doc_id}"')
```

**依赖**：`core/llm_factory.py`（已有）、Milvus 连接

**验证**：本地启动 Milvus，调用 `build_index` 插入测试向量 → 通过 `pymilvus` 查询确认

### Step 4: 核心引擎 — 检索 (core/retrieval.py)

**修改文件**：`backend/core/retrieval.py`

**关键逻辑（伪代码）**：
```
retrieve(query, kb_id, top_k=5, similarity_threshold=0.6):
    # 1. 获取 Embedding 模型
    embed_model = create_embedding(settings)

    # 2. 查询向量化
    query_embedding = embed_model.get_query_embedding(query)

    # 3. Milvus 搜索
    collection = Collection(f"kb_{kb_id}")
    collection.load()
    results = collection.search(
        data=[query_embedding],
        anns_field="embedding",
        param={"metric_type": "IP", "params": {"nprobe": 10}},
        limit=top_k,
        output_fields=["doc_id", "file_name", "page_number", "chunk_index", "text"]
    )

    # 4. 过滤 + 组装
    return [
        {score, text, doc_id, file_name, page_number, chunk_index}
        for r in results[0] if r.score >= similarity_threshold
    ]
```

**依赖**：`core/llm_factory.py`、`core/indexing.py`（需确认 Collection Schema）

**验证**：先建索引，再检索 → 确认返回结果含分数和元数据

### Step 5: Service 层 — 知识库 (services/kb_service.py)

**修改文件**：`backend/services/kb_service.py`

**变更**：将硬编码数据替换为 `KnowledgeBaseRepository` 调用

**验证**：
```bash
curl -X POST http://localhost:8000/api/knowledge-bases \
  -H "Content-Type: application/json" \
  -d '{"name":"测试知识库","description":"test"}'
# 应返回 201，id 为真实 UUID

curl http://localhost:8000/api/knowledge-bases
# 应返回包含刚创建的知识库的列表
```

### Step 6: Service 层 — 文档管理 (services/document_service.py)

**修改文件**：`backend/services/document_service.py`

**关键逻辑（伪代码）**：
```
upload_document(kb_id, file):
    # 1. 暂存到临时目录
    temp_path = save_temp(file)

    # 2. 上传到 MinIO
    object_key = f"{kb_id}/{uuid4()}.{ext}"
    minio_client.upload(object_key, temp_path)

    # 3. PG 写入文档记录（status='uploading'）
    doc = doc_repo.create(kb_id, file_name, file_size, file_type, object_key)

    # 4. 更新状态 → processing
    doc_repo.update_status(doc.id, 'processing')

    # 5. 解析文档
    llama_docs = parser.parse_document(temp_path, file_type)

    # 6. 切片 + 向量化 + 索引
    chunks, metadatas = slice_documents(llama_docs, chunk_size, overlap)
    milvus_pks = indexing.build_index(chunks, metadatas, kb_id)

    # 7. PG 写入 chunk 记录
    chunk_repo.batch_create(doc.id, kb_id, metadatas, milvus_pks)

    # 8. 更新状态 → completed
    doc_repo.update_status(doc.id, 'completed', chunk_count=len(chunks))
    kb_repo.increment_doc_count(kb_id)

    # 9. 清理临时文件
    os.remove(temp_path)

    # 异常处理：任一步骤失败 → status='failed'，记录 error_message
```

**依赖**：Step 1-4 全部完成

**验证**：上传真实 PDF/DOCX 文件 → 检查 MinIO 有文件、PG 状态为 completed、Milvus 可检索

### Step 7: Service 层 — 检索 (services/search_service.py)

**修改文件**：`backend/services/search_service.py`

**变更**：调用 `core.retrieval.retrieve()` + 分页组装，替换空结果返回

**验证**：
```bash
curl -X POST http://localhost:8000/api/knowledge-bases/{kb_id}/search \
  -H "Content-Type: application/json" \
  -d '{"query":"投标保证金","top_k":10}'
# 应返回含 score、text、file_name、page_number 的结果
```

### Step 8: Service 层 — 问答 (services/chat_service.py)

**修改文件**：`backend/services/chat_service.py`

**关键逻辑（伪代码）**：
```
async chat_stream(kb_id, message, session_id):
    # 1. 获取或创建会话
    conv = await conv_repo.get_or_create(session_id, kb_id)

    # 2. 检索相关文档
    retrieved = await retrieve(query=message, kb_id=kb_id, top_k=5)

    # 3. 加载对话历史
    history = await msg_repo.get_history(conv.id, limit=10)

    # 4. 构建 Prompt
    prompt = build_rag_prompt(
        query=message,
        context_str=format_context(retrieved),
        chat_history=format_history(history),
    )

    # 5. 保存用户消息
    await msg_repo.create(conv.id, role='user', content=message)

    # 6. LLM 流式生成
    llm = create_llm(settings)
    full_response = ""
    async for chunk in llm.astream_chat(prompt):
        full_response += chunk
        yield sse_event("token", chunk)

    # 7. 保存 AI 回复
    await msg_repo.create(conv.id, role='assistant', content=full_response, sources=retrieved)

    # 8. 发送来源
    yield sse_event("sources", extract_sources(retrieved))
    yield sse_event("done")
```

**依赖**：Step 4（检索）、Step 1（Repository）

**验证**：
```bash
curl -X POST http://localhost:8000/api/knowledge-bases/{kb_id}/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"投标金额是多少？"}'
# 应返回 SSE 流，逐字输出 token，最终包含 sources 和 done 事件
```

### Step 9: 前端对接

**修改范围**：4 个 View 组件

| 页面 | 当前行为 | 改造内容 |
|------|----------|----------|
| `KnowledgeBaseView.vue` | API 失败时用空数组兜底 | 移除空数据兜底，API 失败显示错误提示 |
| `DocumentListView.vue` | API 失败时用空数组兜底 | 同上，上传成功后刷新列表 |
| `SearchView.vue` | API 失败时用空数组兜底 | 同上，检索无结果显示正确空状态 |
| `ChatView.vue` | **完全使用 mock 数据模拟流式** | 替换为 `chatStream()` 真实 SSE 调用 |

**ChatView 改造关键点**（伪代码）：
```
// 当前：用 setInterval 逐字输出 mockReply
// 改后：调用 api/chat.js 的 chatStream()

import { chatStream } from '@/api/chat'

async handleSend() {
  messages.value.push({ role: 'user', content: text })
  const aiMsg = { role: 'assistant', content: '', sources: [] }
  messages.value.push(aiMsg)
  streaming.value = true

  chatStream(kbId.value, text, sessionId.value, {
    onToken: (token) => { aiMsg.content += token; scrollToBottom() },
    onSources: (sources) => { aiMsg.sources = sources },
    onDone: () => { streaming.value = false; scrollToBottom() },
    onError: (msg) => { aiMsg.content = '错误: ' + msg; streaming.value = false },
  })
}
```

**验证**：上传文档 → 索引完成 → 前端各页面操作 → 确认数据来自真实 API，不再依赖兜底逻辑

---

## 4. 新增/修改文件清单

### 新增文件

```
backend/repositories/__init__.py
backend/repositories/kb_repository.py
backend/repositories/document_repository.py
backend/repositories/conversation_repository.py
backend/utils/minio_client.py
```

### 修改文件

```
backend/core/parser.py              # stub → 完整实现（Strategy 模式）
backend/core/indexing.py            # stub → 完整实现
backend/core/retrieval.py           # stub → 完整实现
backend/services/kb_service.py      # stub → Repository 调用
backend/services/document_service.py # stub → 完整上传管道
backend/services/search_service.py  # stub → 检索调用
backend/services/chat_service.py    # stub → RAG + SSE Pipeline
frontend/src/views/ChatView.vue     # mock → 真实 SSE
frontend/src/views/KnowledgeBaseView.vue  # 兜底逻辑 → 错误提示
frontend/src/views/DocumentListView.vue   # 兜底逻辑 → 错误提示
frontend/src/views/SearchView.vue         # 兜底逻辑 → 错误提示
```

### 不修改

- `config.py`、`main.py`、`routers/*.py` — 当前接口签名和错误处理足够
- `models/` — ORM 模型和 Pydantic Schema 无需改动
- `core/database.py`、`core/llm_factory.py` — 已完成
- `utils/logging.py`、`utils/file_utils.py` — 已完成
- `frontend/src/api/*.js`、`router/*.js` — API 层封装已正确
- `alembic/` — 表结构无需变更

---

## 5. 依赖关系图

```
Step 1 (MinIO + Repository)
  │
  ▼
Step 2 (Parser) ────────────────────┐
  │                                 │
  ▼                                 ▼
Step 3 (Indexing)              Step 4 (Retrieval)
  │                                 │
  ▼                                 │
Step 5 (KB Service)                 │
  │                                 │
  ▼                                 │
Step 6 (Document Service) ◄─────────┘
  │
  ▼
Step 7 (Search Service) ◄── Step 4
  │
  ▼
Step 8 (Chat Service) ◄── Step 4 + Step 1 (Repo)
  │
  ▼
Step 9 (Frontend Integration)
```

---

## 6. 验证方式

### 6.1 端到端验证流程

```bash
# 1. 启动基础设施
docker-compose up -d

# 2. 启动 Xinference（手动，需提前部署模型）
xinference-local --host 0.0.0.0 --port 9997

# 3. 启动后端
cd backend && uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 4. 验证健康检查
curl http://localhost:8000/api/health
# {"status": "healthy", "version": "0.1.0"}

# 5. 创建知识库
curl -X POST http://localhost:8000/api/knowledge-bases \
  -H "Content-Type: application/json" \
  -d '{"name":"E2E测试","description":"端到端验证"}'
# → 201, 返回带 UUID 的知识库对象

# 6. 上传文档
curl -X POST http://localhost:8000/api/knowledge-bases/{kb_id}/documents \
  -F "file=@test_document.pdf"
# → 201, status=processing → 等待后 status=completed

# 7. 检索文档
curl -X POST http://localhost:8000/api/knowledge-bases/{kb_id}/search \
  -H "Content-Type: application/json" \
  -d '{"query":"测试关键词","top_k":5}'
# → 200, results 非空

# 8. 流式问答
curl -X POST http://localhost:8000/api/knowledge-bases/{kb_id}/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"文档的主要内容是什么？"}'
# → SSE 流，逐字输出，末尾包含 sources 和 done

# 9. 前端验证
cd frontend && npm run dev
# 浏览器打开 http://localhost:5173
# → 知识库列表显示真实数据
# → 文档上传 → 列表显示
# → 检索返回真实结果
# → 问答流式输出真实内容
```

### 6.2 各步骤检查清单

| 步骤 | 检查项 | 验证方式 |
|------|--------|----------|
| 1 | MinIO 连接、Repository 正常工作 | Python import + 单元调用 |
| 2 | PDF/Word 解析正确 | 测试文件解析，检查文本完整性 |
| 3 | 向量写入 Milvus | `pymilvus` 查询验证 |
| 4 | 向量检索返回结果 | 已知查询返回预期结果 |
| 5 | KB CRUD 走数据库 | curl 创建/查询/删除 |
| 6 | 文档完整管道 | 上传 → MinIO → PG → Milvus 三处一致 |
| 7 | 检索 API | curl 返回真实结果 |
| 8 | 问答 SSE | curl 流式输出 + 来源引用 |
| 9 | 前端对接 | UI 操作，DevTools Network 无 stub |

---

## 7. 不做什么（范围边界）

- ❌ 不实现 Graph RAG / Neo4j（Phase 3）
- ❌ 不实现词库管理（后续迭代）
- ❌ 不实现用户认证/权限
- ❌ 不实现批量上传
- ❌ 不实现 .doc 格式支持（python-docx 不支持旧格式）
- ❌ 不写单元测试（后续迭代统一补，当前用 curl + 手动验证）
- ❌ 不修改已有路由接口签名（前端已按当前 API 对接）
- ❌ 不引入 Celery/后台任务队列（同步解析即可，大文件后续优化）
