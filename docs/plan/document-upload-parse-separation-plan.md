# 文档上传与解析分离方案（队列执行）

## Context

当前文档上传接口（`POST /api/knowledge-bases/{kb_id}/documents`）将上传和解析耦合在一个同步请求中：上传文件 → MinIO 存储 → 解析 → 切片 → 向量化 → Milvus 索引，整个管道串行执行完毕后才能返回响应。这导致：

- 大文档上传时请求超时（前端 axios timeout 30s）
- 用户无法控制解析时机
- 解析失败后无法单独重试
- 多个文档同时解析抢占 CPU / Embedding API 资源

**目标**：将上传和解析拆分为两个独立接口，上传后文档状态为 `uploaded`，用户手动触发将文档加入解析队列，FIFO 逐个异步解析，前端轮询同步解析状态。

## 改动范围

### 后端

| 文件 | 改动类型 |
|---|---|
| `backend/services/document_service.py` | 重构：拆分 `upload_document`，新增 `parse_document` |
| `backend/routers/document.py` | 新增 parse 端点 + 单文档查询 + 队列状态 |
| `backend/core/parse_queue.py` | **新增**：asyncio.Queue FIFO 解析队列 + worker |
| `backend/main.py` | lifespan 中启动/停止队列 worker |
| `backend/utils/minio_client.py` | 新增 `download` 函数 |
| `backend/models/document.py` | 默认状态改为 `uploaded` |
| `backend/models/schemas.py` | 新增 `ParseResponse` |
| `backend/repositories/document_repository.py` | `create()` 初始状态改为 `uploaded` |
| `backend/alembic/versions/0002_add_uploaded_queued_status.py` | 新增迁移：状态约束加入 `uploaded`, `queued` |

### 前端

| 文件 | 改动类型 |
|---|---|
| `frontend/src/api/document.js` | 新增 `parseDocument`、`getDocument` API |
| `frontend/src/views/DocumentListView.vue` | 新增解析按钮、轮询逻辑、queued 状态标签 |
| `frontend/src/style.css` | 新增 `.status-tag.uploaded`、`.status-tag.queued` 样式 |

## 架构设计

### 解析队列（asyncio.Queue + 单 worker）

```
前端点击"解析"
  → POST /{doc_id}/parse
  → 更新 PG status = "queued"
  → enqueue(kb_id, doc_id)
  → 返回 { queue_position }

Worker（FastAPI lifespan 启动）
  → while True:
      get() 阻塞等待
      → 更新 PG status = "processing"
      → 下载 MinIO → 解析 → 切片 → Embedding → Milvus
      → 更新 PG status = "completed" / "failed"
```

## 实现步骤

### Step 1：MinIO 下载函数

**文件**: `backend/utils/minio_client.py`

新增 `download(object_key, file_path)` 函数。

### Step 2：拆分 document_service

**文件**: `backend/services/document_service.py`

- `upload_document`：精简为仅上传（status=`uploaded`）
- `parse_document(kb_id, doc_id)`：下载 → 解析 → 切片 → 向量化 → 索引（由队列 worker 调用）

### Step 3：解析队列模块

**文件**: `backend/core/parse_queue.py`

- `asyncio.Queue`（maxsize=100）
- `start_worker()`：无限循环从队列取任务执行
- `stop_worker()`：停止标志位
- `enqueue(kb_id, doc_id)`：入队，返回队列位置

### Step 4：路由层

**文件**: `backend/routers/document.py`

- `POST /{doc_id}/parse`：校验状态 → 更新 `queued` → 入队 → 返回 `{queue_position}`
- `GET /{doc_id}`：查询单个文档
- `GET /queue/status`：查询队列状态

### Step 5：FastAPI 生命周期

**文件**: `backend/main.py`

lifespan 中：`asyncio.create_task(parse_queue.start_worker())`，shutdown 时取消。

### Step 6：前端

- `uploaded` / `failed` 文档显示"解析"按钮
- `queued` / `processing` 文档启动 3 秒轮询
- CSS：`queued` 紫色标签

## 状态流转

```
uploaded ──(入队)──→ queued ──(worker取出)──→ processing ──→ completed
                         │                          │
                         └──────────────────────────└──(失败)──→ failed
                                                                   │
                                              (点击解析重新入队) ←──┘
```

## 验证方式

1. **上传验证**：上传 PDF，status=`uploaded`，显示"待解析"
2. **队列验证**：点击"解析"，status=`queued` → 显示"排队中"，前端轮询
3. **顺序验证**：连续触发 3 个文档解析，确认 FIFO 逐个执行
4. **异常处理**：损坏文件解析后 status=`failed`，可重新入队
5. **队列状态**：`GET /queue/status` 返回 `queue_size` 和 `worker_running`
6. **删除验证**：删除功能不受影响
