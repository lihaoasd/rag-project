# Langfuse 观测接入 — 开发方案

> 版本: v2.0 | 日期: 2026-08-16 | 状态: 已实现（langfuse v4 SDK）
> 关联: [02-technical-design.md](../02-technical-design.md)（技术栈）、[02-logging-plan.md](02-logging-plan.md)（现有日志体系，本方案为日志之上的 LLM tracing 补充）
>
> **v2.0 变更**：SDK 从 `langfuse>=2.50,<3` 升级为 `langfuse>=4,<5`（跨 v3/v4 破坏性变更）。v4 移除 `langfuse.decorators.langfuse_context` 与 `get_current_llama_index_handler()`，改由顶层 `observe`/`get_client`/`Langfuse` + 显式 `@observe(as_type=...)` 标注。全文已按 v4 重写。

---

## 1. Context

### 1.1 现状问题

系统目前只有 `utils/logging.py` 的文本日志，**没有任何 LLM 调用级观测**：

- [chat_service.py](../backend/services/chat_service.py) 的 RAG 管道（检索→Prompt→`llm.achat()`→SSE）无 trace，无法看到一次问答的检索命中质量、LLM 输入/输出、耗时、token 与成本。
- LLM 走 llama_index `OpenAILike`，Embedding 走 `OpenAIEmbedding`，均直接创建 client 调用，未挂任何 callback。
- 调试依赖 `logs/app.log` 人工 grep，无法按 session / kb 聚合、无法做线上回归对比。

### 1.2 用户确认的决策

| 决策点 | 结论 |
|--------|------|
| 部署方式 | **已有自建实例**（不新增 docker-compose 服务，host+key 由用户填入 `.env`） |
| 追踪范围 | **完整 RAG 链路**：一次问答 = 一条 trace，含 检索 + LLM 生成，附 `session_id`/`kb_id` |
| 检索方法 | **统一公用方法**：召回 + 排序（rerank）是同一个检索链路，问答与搜索共用；rerank 是其中一步，不是独立旁路（见 §2.5） |
| 检索记录 | **结构化**：记录「检索到哪些内容（hits）+ 检索方式（method/params）+ 排序数据」，schema 可扩展以容纳后续多种检索方式（见 §2.5） |
| 追踪粒度 | 问答 + 检索（search）链路；**不**覆盖 OCR、文档解析（见 §6 范围外） |

### 1.3 技术选型

- 依赖：`langfuse>=4,<5`（v4 SDK，基于 OpenTelemetry；`@observe()`、`get_client()`、`Langfuse()` 均为顶层导出）。
- 接入方式：**`@observe()` 标注管道**，用 `as_type` 区分观测类型——检索/排序标 `"retriever"`、LLM 标 `"generation"`；span 属性经 `get_client().update_current_span()` / `update_current_generation()` 显式写入，trace 属性经 `propagate_attributes()` 下发。
- **v4 已移除 llama_index callback handler**（`get_current_llama_index_handler()`），因此 LLM 调用改用 `@observe(as_type="generation")` 显式包裹；Embedding 不再单独成 span（查询向量化信息由 `retrieve` span 的 metadata 体现）。
- 非流式 LLM：当前 `chat_service.py` 实际用 `llm.achat()`（非流式），再人工按 10 字符切块伪流式输出 → Langfuse 侧每条问答只产生**一次干净的 generation span**，无流式 trace 复杂度。

---

## 2. 方案设计

### 2.1 配置（`config.py` + `.env.example`）

`Settings` 新增 Langfuse 段（`extra="ignore"` 已兼容）：

```python
# === Langfuse 观测配置 ===
langfuse_enabled: bool = False
langfuse_public_key: str = ""
langfuse_secret_key: str = ""
langfuse_base_url: str = ""      # 自建实例地址，如 http://langfuse.local:3000
langfuse_sample_rate: float = 1.0
```

`.env.example` 追加（`.env` 为机密，不入库，由用户自行填写）：

```env
LANGFUSE_ENABLED=false
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=http://your-langfuse-host
LANGFUSE_SAMPLE_RATE=1.0
```

### 2.2 观测初始化（新增 `core/observability.py`）

单一模块封装 Langfuse client 生命周期，避免散落：

- `setup_langfuse()`：应用启动时调用；`enabled=False` 直接返回。启用时显式构造 `Langfuse(public_key=..., secret_key=..., host=..., sample_rate=...)`（pydantic 不会把 `.env` 值写入 `os.environ`，故须显式传参而非依赖 SDK 自动读 env）。构造器会把实例注册进 `LangfuseResourceManager` 单例，供 `@observe()` 内部的 `get_client()` 复用。`auth_check()` **尽力而为**：失败仅 `logger.warning`，不 raise、不阻塞启动。
- `format_retrieval_observation(hits, text_limit=200)`：将检索命中组装为结构化 span 输出（schema 见 §2.5.2）。

> 注：v2 的 `get_langfuse_handler()`（返回 `langfuse_context.get_current_llama_index_handler()`）在 v4 中随 llama_index 集成一并移除，不再保留。

### 2.3 LLM / Embedding 追踪（`core/model_factory.py` + `services/chat_service.py`）

v4 无 llama_index callback handler，故不再通过 `CallbackManager` 自动追踪底层调用：

- **LLM**：`create_llm(config)` 签名保持原样（不新增 `callback_manager`）。问答链路在 `services/chat_service.py` 用 `_call_llm()`（`@observe(as_type="generation")`）包裹 `llm.achat()`，内部 `update_current_generation(model=..., input=..., output=...)` 记录 model / 输入 / 输出。
- **Embedding**：`create_embedding(config)` 签名保持原样；查询向量化在 `retrieve()` 内部完成，**不单独成 span**（其 model 信息已并入 `retrieve` span 的 metadata，后续如需可加 `@observe(as_type="embedding")` 包裹 `aget_query_embedding()`）。

### 2.4 统一检索方法 + 结构化记录

#### 2.4.1 检索方法统一（`core/retrieval.py`）

rerank 不是检索之外的旁路，而是检索链路的一步。把 rerank 从 `search_service._apply_rerank` 抽成公用函数，问答与搜索共用同一链路「召回 → 可选重排 → 阈值过滤」：

```
retrieve(query, kb_id, top_k, similarity_threshold)               # 向量召回（@observe(as_type="retriever")）
rerank(query, hits, config, top_n)                                # 排序（@observe(as_type="retriever")，从 search_service._apply_rerank 迁入）
retrieve_and_rank(query, kb_id, top_k, threshold, rerank_config)  # 单 KB 入口 = retrieve + 可选 rerank
```

- `chat_service` 走 `retrieve_and_rank`：`rerank_config = await resolve_model_config(kb_id, "rerank")`（rerank 为全局默认、不进 KB 绑定），有配置则重排、无则纯向量。
- `search_service._apply_rerank` 改为委托公用 `rerank()`；多 KB 检索仍是「先召回、合并、再 rerank」两步（召回用 `retrieve`，排序用公用 `rerank`）。

#### 2.4.2 检索记录 schema（`core/observability.py`）

检索 span 的输出结构化、可扩展：

```
retrieve span（召回）
  input      = query
  metadata   = {"method": "vector", "kb_id", "top_k", "similarity_threshold"}
  output     = {"hit_count": n,
                "hits": [{rank, score, doc_id, file_name, page_number, chunk_index, text(截断)}]}

rerank span（排序，仅当应用 rerank 时产生）
  input      = [进入排序的候选命中]
  metadata   = {"rerank_model": model, "top_n": n}
  output     = [排序后命中（rank + 覆盖后的 score）]
```

**扩展约定**：新增检索方式（hybrid / BM25 / graph 等）只需在调用方传新的 `method` + params，复用 `format_retrieval_observation(...)` 组装函数，不改 span schema；`text` 默认截断（如 200 字符）避免 trace 过大，长度可配置。

### 2.5 RAG 管道标注（`services/chat_service.py`）

将「检索 + Prompt 构建 + LLM 生成」从 `chat_stream` 抽为一个 `@observe()` 标注的协程 `_run_rag`，SSE 切块、DB 读写仍留在 `chat_stream`：

- `@observe(name="rag_qa", capture_input=False, capture_output=False)` 产出 trace；trace 属性经 `propagate_attributes(session_id=conv_id, metadata={"kb_id": kb_id})` 下发到所有子 span。
- 根 span 的 input/output 经 `get_client().update_current_span(input=message, output=full_response)` 显式写入（禁用自动 capture，避免序列化 history 等 ORM 对象）。
- LLM 经 `_call_llm()`（`@observe(as_type="generation")`）产出 generation span，作为检索 span 的兄弟节点。

**为什么不直接 `@observe()` 标注 `chat_stream` 生成器**：SSE 生成器的 yield 值无法被 Langfuse 有意义地序列化为 output，且 trace 生命周期与流式输出耦合，易漏/易错。抽 `_run_rag` 协程（非生成器）返回 `(retrieved, full_response)`，output 可干净捕获。

### 2.6 检索（search）管道标注（`services/search_service.py`）

`search()` 是普通协程（非生成器、非流式），直接 `@observe()` 标注即可，覆盖单库/多库两个端点（[routers/search.py](../backend/routers/search.py) 两处均调用 `search()`）：

```
@observe(name="search", capture_input=False, capture_output=False)
async def search(query, kb_ids, top_k, similarity_threshold,
                 rerank_model_id=None, rerank_top_n=..., rerank_vector_threshold=None):
    get_client().update_current_span(
        input=query,
        metadata={"kb_ids": kb_ids, "top_k": top_k,
                  "similarity_threshold": similarity_threshold,
                  "rerank_model_id": rerank_model_id})
    ...
    # 每个 KB 一个 retrieve span（method/kb_id/top_k/threshold + 结构化 hits）
    # 应用 rerank 时追加 rerank span（候选 → 排序后），同 §2.4.2
    get_client().update_current_span(output={"total": len(results)})
    return {"query": ..., "total": ..., "results": ...}
```

检索 trace 无 `session_id`/`user_id`（搜索无会话概念），仅 `metadata.kb_ids`；output 记为 `{"total": N}`（完整命中明细在各 `retrieve` span 的 output 中）。

---

## 3. 改动范围

| 文件 | 改动 |
|------|------|
| `backend/pyproject.toml` | 依赖新增 `langfuse>=4,<5` |
| `backend/uv.lock` | `uv lock` 重新生成 |
| `backend/config.py` | `Settings` 新增 5 个 Langfuse 字段 |
| `backend/.env.example` | 追加 Langfuse 段（`LANGFUSE_ENABLED=false` 默认关闭） |
| `backend/core/observability.py` | **新增**：`setup_langfuse()` / `format_retrieval_observation()` |
| `backend/main.py` | lifespan 启动时 `setup_langfuse()`（`setup_logging` 之后，非致命） |
| `backend/core/retrieval.py` | `retrieve` / `rerank` 加 `@observe(as_type="retriever")`；**新增** `rerank()`（从 `search_service._apply_rerank` 迁入）+ `retrieve_and_rank()` 单 KB 入口 |
| `backend/services/chat_service.py` | 抽 `_run_rag`（`@observe(name="rag_qa")`）+ `_call_llm`（`@observe(as_type="generation")`）协程；改走 `retrieve_and_rank`（解析全局默认 rerank）；trace 属性经 `propagate_attributes` 下发 |
| `backend/services/search_service.py` | `_apply_rerank` 改为委托公用 `rerank()`；`search()` 加 `@observe(name="search")` 追踪（retrieve/rerank span） |
| `backend/tests/test_config.py` | 新增 Langfuse 字段解析断言 |
| `backend/tests/test_observability.py` | **新增**：`setup_langfuse`（disabled no-op / enabled 构造 client / init 失败降级）+ `format_retrieval_observation` 结构 |
| `backend/tests/test_retrieval.py` | **新增**：`rerank`（覆盖 score + 重排 / 遵守 top_n）+ `retrieve_and_rank`（有/无 rerank 两分支） |
| `backend/tests/test_search_service.py` | 复用公用 `rerank()` 后现有 rerank 用例仍通过；`langfuse_enabled=False` 时 `search()` 追踪为空操作、行为不变 |
| `backend/tests/test_chat_service.py` | 确保 `langfuse_enabled=False` 默认路径行为不变；`retrieve_and_rank` 有/无 rerank 配置两分支 |

**不改动**：前端、`docker-compose.yml`（已有自建实例）、`schemas.py`、`model_factory.py`（`create_llm`/`create_embedding` 签名不变）。OCR、文档解析链路不追踪。

---

## 4. 关键逻辑伪代码

### 4.1 `core/observability.py`

```
setup_langfuse():
    if not settings.langfuse_enabled: return
    from langfuse import Langfuse
    try:
        client = Langfuse(public_key=settings.langfuse_public_key,
                          secret_key=settings.langfuse_secret_key,
                          base_url=settings.langfuse_base_url or None,
                          sample_rate=settings.langfuse_sample_rate)
    except Exception as e:
        logger.warning("Langfuse 初始化失败，观测已降级关闭: %s", e); return
    if client.auth_check():
        logger.info("Langfuse 已连接: base_url=%s", settings.langfuse_base_url)
    else:
        logger.warning("Langfuse 连接校验失败，观测可能不可用: base_url=%s", settings.langfuse_base_url)
```

### 4.2 `_run_rag` + `_call_llm`（`chat_service.py`）

```
@observe(as_type="generation", capture_input=False, capture_output=False)
async def _call_llm(llm, messages, model) -> str:
    response = await llm.achat(messages=messages)
    content = response.message.content if hasattr(response, "message") else str(response)
    get_client().update_current_generation(model=model, input=messages, output=content)
    return content

@observe(name="rag_qa", capture_input=False, capture_output=False)
async def _run_rag(kb_id, message, conv_id, history) -> (retrieved, full_response):
    with propagate_attributes(session_id=conv_id, metadata={"kb_id": kb_id}):
        get_client().update_current_span(input=message)

        # 检索（统一链路：召回 + 可选 rerank；span 由 retrieve/rerank 自身产出）
        rerank_config = await resolve_model_config(kb_id, "rerank")   # None = 纯向量
        retrieved = await retrieve_and_rank(query=message, kb_id=kb_id,
                                            top_k=settings.retrieval_top_k,
                                            similarity_threshold=settings.retrieval_similarity_threshold,
                                            rerank_config=rerank_config)

        # Prompt 构建（同现有 _get_system_prompt / _build_context / _build_history）
        system_prompt = await _get_system_prompt(...)
        llm_messages = [...]

        # LLM 生成（generation span 记录 input/output/model）
        llm_config = await resolve_model_config(kb_id, "llm")
        llm = create_llm(llm_config)
        full_response = await _call_llm(llm, llm_messages, llm_config["model"])

        get_client().update_current_span(output=full_response)
        return retrieved, full_response
```

`format_retrieval_observation(retrieved, text_limit=200)`（`core/observability.py`）：

```
format_retrieval_observation(hits, text_limit=200):
    return {
        "hit_count": len(hits),
        "hits": [
            {"rank": i+1, "score": h["score"], "doc_id": h["doc_id"],
             "file_name": h["file_name"], "page_number": h["page_number"],
             "chunk_index": h["chunk_index"], "text": h["text"][:text_limit]}
            for i, h in enumerate(hits)
        ],
    }
```

### 4.3 `chat_stream` 改造

```
chat_stream(kb_id, message, session_id):
    conv_id = ...（get_or_create，不变）
    history = ...（get_history，不变）
    ... 保存用户消息（不变）
    retrieved, full_response = await _run_rag(kb_id, message, conv_id, history)
    for i in range(0, len(full_response), 10):      # 切块伪流式，不变
        yield _sse_event("token", content=full_response[i:i+10])
    ... 保存 AI 回复 / sources / done（不变）
```

---

## 5. 验证方式

### 5.1 单元测试

| 用例 | 断言 |
|------|------|
| `test_config.py` Langfuse 字段 | 从环境变量正确解析 5 个字段，默认 `enabled=False`、`sample_rate=1.0` |
| `chat_service` 默认路径 | `langfuse_enabled=False` 时 `get_client()` 返回禁用 client（`update_current_span`/`update_current_generation` 为 no-op），`_run_rag` 行为与现有 pipeline 一致 |
| `setup_langfuse` | `enabled=False` 立即返回；`enabled=True` 构造 `Langfuse` 并 `auth_check`；构造失败降级返回 `None` |
| `format_retrieval_observation` | 命中列表 → `{hit_count, hits[{rank, score, doc_id, file_name, page_number, chunk_index, text(截断)}]}`；`text` 超过 `text_limit` 被截断 |

### 5.2 手工验证

```bash
cd backend && uv sync && uv run pytest -q

# 开启观测后，在 Langfuse UI 确认：
# 1) 一次问答产生 1 条 trace "rag_qa"，session_id = 会话 ID，metadata.kb_id 正确
# 2) trace 内嵌套 retrieve span（metadata.method/top_k/threshold/rerank + output.hits 结构化：rank/score/doc_id/file_name/page_number/chunk_index/text）
#    + LLM generation span（input/output/model）
#    配置全局默认 rerank 时，retrieve 与 generation 之间出现 rerank span（input=候选 / output=排序后）
# 3) 一次检索（/api/search 或单库 search）产生 trace "search"（metadata.kb_ids/top_k/threshold/rerank_model_id）
#    + 每 KB 一个 retrieve span；应用 rerank 时出现 rerank span
# 4) 关掉 enabled=false 重启，接口正常、无 Langfuse 网络请求、测试全绿
```

---

## 6. 不做什么（本次范围外）

- ❌ 远程 OCR、文档解析的追踪（解析类长任务，非请求级 trace，留待后续）
- ❌ 新增检索方式（hybrid / BM25 / graph 等）的具体实现（用户后续会另行说明，届时只需换 `method` 值复用 `format_retrieval_observation`；rerank 已作为统一链路的一步接入）
- ❌ Embedding 单独成 span（v4 移除 llama_index handler 后不再自动追踪；查询向量化信息由 `retrieve` span metadata 体现，后续如需再补 `@observe(as_type="embedding")`）
- ❌ `docker-compose.yml` 新增 Langfuse 服务（已有自建实例）
- ❌ 用户身份追踪（当前系统无鉴权/用户体系，trace 仅挂 `session_id` + `kb_id`；后续引入登录时再补 `user_id`）
- ❌ 评分（`score`）、数据集评测、prompt 版本管理（Langfuse 高级能力，后续按需）
- ❌ 每请求 `flush()`（FastAPI 长驻进程，Langfuse 后台批量上报即可，无需阻塞式 flush）
- ❌ 敏感内容脱敏（自建实例由用户掌控，文档内容不上第三方；若后续需脱敏再评估 masking）
