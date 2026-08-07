# 完善日志记录 — 开发方案

## Context

当前项目（投标文档 RAG 智能问答系统）**完全没有日志记录**：
- 后端所有 Python 模块均无 `import logging`、无 logger 实例、无日志调用
- 前端仅有 1 行 `console.error`（Axios 错误拦截器）
- `.gitignore` 已预留 `*.log` / `logs/` 规则，但未实现

日志是生产系统可观测性的基础。Phase 2（RAG 核心管线）即将开发，在此之前建立统一的日志基础设施，能确保后续开发中问题可追溯、可定位。

## 改动范围概览

### 后端（Python / FastAPI）

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `backend/config.py` | 修改 | 新增 `log_level`、`log_dir` 配置项 |
| `backend/utils/logging.py` | **新建** | 集中日志配置 + `get_logger()` 工厂 |
| `backend/main.py` | 修改 | 启动时初始化日志、注册请求日志中间件 |
| `backend/routers/knowledge_base.py` | 修改 | 各端点加日志 |
| `backend/routers/document.py` | 修改 | 各端点加日志 |
| `backend/routers/search.py` | 修改 | 各端点加日志 |
| `backend/routers/chat.py` | 修改 | 各端点加日志 |
| `backend/services/kb_service.py` | 修改 | 关键操作加日志 |
| `backend/services/document_service.py` | 修改 | 关键操作加日志 |
| `backend/services/search_service.py` | 修改 | 关键操作加日志 |
| `backend/services/chat_service.py` | 修改 | 关键操作加日志 |
| `backend/core/database.py` | 修改 | 引擎/会话生命周期日志 |
| `backend/core/llm_factory.py` | 修改 | 模型创建日志 |
| `backend/core/parser.py` | 修改 | 入口日志（当前为 stub） |
| `backend/core/indexing.py` | 修改 | 入口日志（当前为 stub） |
| `backend/core/retrieval.py` | 修改 | 入口日志（当前为 stub） |

### 前端（Vue 3 / JavaScript）

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `frontend/src/utils/logger.js` | **新建** | 统一前端日志工具 |
| `frontend/src/api/request.js` | 修改 | 使用新 logger 替换 `console.error` |

## 实现方案

### 1. 后端日志基础设施

#### 1.1 配置项（`backend/config.py`）

```python
# === 日志配置 ===
log_level: str = "INFO"
log_dir: str = "./logs"
```

#### 1.2 日志核心模块（`backend/utils/logging.py`）

核心设计：
- 使用 Python 标准库 `logging`（零额外依赖）
- `setup_logging(level, log_dir)` — 在应用启动时调用一次，配置根 logger
  - **控制台 Handler**：输出到 stderr，level 由配置决定，格式简洁
  - **文件 Handler**：`RotatingFileHandler`，输出到 `logs/app.log`，单文件最大 10MB，保留 5 个备份
  - 格式：`2026-08-07 10:30:00 | INFO     | routers.kb | 创建知识库: name=xxx`
- `get_logger(name)` — 返回 `logging.getLogger(name)`，各模块用它获取 logger
- 第三方库日志级别控制：`sqlalchemy.engine` → WARNING，`llama_index` → INFO

#### 1.3 应用入口集成（`backend/main.py`）

在 `lifespan` 启动阶段调用 `setup_logging()`，并注册一个轻量中间件记录每个 HTTP 请求的方法、路径、状态码、耗时。

### 2. 各模块日志埋点

统一模式：每个模块文件顶部 `logger = get_logger(__name__)`，然后按以下原则埋点：

- **Router 层**：记录请求进入（info）、参数校验失败（warning）、异常（error，带 traceback）
- **Service 层**：记录核心操作（info）、stub 返回（debug）
- **Core 层**：
  - `database.py`：引擎创建（info）、会话异常（error）
  - `llm_factory.py`：模型实例化（info，不打印 API key）
  - `parser.py` / `indexing.py` / `retrieval.py`：在 `raise NotImplementedError` 前加 warning 日志说明 stub 被调用
- **无日志场景**：正常的成功返回不打印详细日志（避免噪音），只记录关键状态变更

### 3. 前端日志工具

#### 3.1 `frontend/src/utils/logger.js`

```js
const LEVELS = { debug: 0, info: 1, warn: 2, error: 3 }
// 生产环境最低 level 设为 warn
// 开发环境最低 level 设为 debug
// 格式: [2026-08-07T10:30:00] [INFO] [module] message
```

提供 `createLogger(module)` 工厂函数，返回 `{ debug, info, warn, error }` 方法。

#### 3.2 `frontend/src/api/request.js`

用 `createLogger('api')` 替换裸 `console.error`。

## 验证方式

1. **启动后端**：`cd backend && uv run uvicorn main:app --reload`
2. **观察控制台输出**：应有带格式的日志输出
3. **检查日志文件**：`logs/app.log` 应生成并包含日志内容
4. **触发各 API 端点**：用 curl 或前端页面调用各个 API，确认对应模块有日志输出
5. **触发错误场景**：上传不支持的文件格式（如 .txt），确认 warning 日志
6. **前端**：打开浏览器 console，确认 API 错误以统一格式输出
