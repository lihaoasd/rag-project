# Langfuse Trace 点重新设计方案

> 评审结论（2026-08-20）：① 采纳 CHAIN/SPAN/GENERATION 类型分类；② **不新增** `prepare`/`config_resolve`/`kb_name_resolve` 三个 span（保留现有 `embed_ms`/`search_ms`/`filter_ms` 环节耗时字段）；③ 采纳统一装饰器 `traced_span`；④ 命名小写蛇形。

## 一、现状与问题

现有埋点是逐轮零散添加的，存在以下问题：

| # | 问题 | 现状 |
|---|---|---|
| 1 | **span 类型语义不准确** | 所有 span 一律 `as_type="retriever"`：embedding/rerank 是**外部模型调用**（应为 GENERATION），Milvus/ES 是外部服务（应为 SPAN），RRF 是本地计算（SPAN），search 是编排（CHAIN） |
| 2 | **命名不统一** | span 名混杂：`_recall_milvus`、`_recall_fulltext`、`_embed_query`、`retrieve`、`rrf_merge`、`search`、`es_search`（部分取函数名含下划线，部分显式命名） |
| 3 | **耗时口径不完整** | `retrieve` 只统计 embed_ms/search_ms/filter_ms，**中间有空档**：模型配置解析（每 kb 2 次 DB select）、collection 检查、index/retriever 构建均未计时——无法回答"时间花在哪" |
| 4 | **缺失环节无 span** | `resolve_model_config`（DB）、`_get_kb_name`（DB）无任何记录；dense/sparse 单 kb 检索失败只 `logger.warning` 无 span（fulltext 失败有 span，不一致） |
| 5 | **观测代码散落业务** | `get_client().update_current_span` 手写散落在 4 个模块，metadata 结构靠人肉维护，易漂移 |
| 6 | **无统一 metadata schema** | method/kb_id/hit_count/elapsed_ms 各 span 字段不一，Langfuse 界面难以统一筛选对比 |

## 二、目标：trace 调用树

### 检索链路（search）

```
search (CHAIN)                             ← 一次检索请求
├── recall_dense / recall_sparse (SPAN)    ← 每路召回（逐 kb 并发，失败记 error）
│   └── retrieve (SPAN)                    ← 逐 kb 检索
│       ├── embedding (GENERATION)         ← query 向量化（外部模型调用）
│       └── milvus_query (SPAN)            ← Milvus ANN 查询
├── recall_fulltext (SPAN)
│   └── es_query (SPAN)                    ← ES 查询
├── rrf_merge (SPAN)                       ← 融合（多选时）
└── rerank (GENERATION)                    ← 重排（外部模型调用，有配置时）
```

> 注：评审确认不新增 `prepare` / `config_resolve` / `kb_name_resolve` 环节 span；`retrieve` span metadata 保留 `embed_ms` / `search_ms` / `filter_ms` 环节耗时字段（现有能力）。

### 问答链路（rag_qa）

```
rag_qa (CHAIN)
└── retrieve / rerank（同上结构）
└── llm (GENERATION)                       ← LLM 生成（已有，保持）
```

## 三、span 命名与类型规范

| span 名 | 类型 | 含义 | 关键 metadata |
|---|---|---|---|
| `search` / `rag_qa` | CHAIN | 一次请求编排 | kb_ids, retrieval_methods, top_k, per_method_hits, total |
| `recall_dense` / `recall_sparse` | SPAN | 单路召回汇总（逐 kb 并发） | method, kb_ids, top_k, hit_count |
| `recall_fulltext` | SPAN | ES 全文召回汇总 | method, kb_ids, top_k, hit_count |
| `retrieve` | SPAN | 逐 kb 检索 | kb_id, collection, dim, embed_ms, search_ms, filter_ms |
| `embedding` | GENERATION | query 向量化 | model, api_base, dim, embedding_dim |
| `milvus_query` | SPAN | Milvus ANN 查询 | collection, mode, top_k, similarity_threshold, hit_count |
| `es_query` | SPAN | ES 查询 | index, top_k, highlight, hit_count |
| `rrf_merge` | SPAN | 融合 | k, per_method_hits |
| `rerank` | GENERATION | 重排 | model, api_base, top_n, hit_count |

**通用字段（每个 span 必有）**：`elapsed_ms`（自动计时）、异常分支记 `error` + `hit_count=0`。
**命名规范**：统一小写蛇形（`recall_dense` / `milvus_query`），不再取函数名。

## 四、实现方式

### 4.1 `core/observability.py` 增加统一 span 装饰器

```python
def traced_span(name: str, span_type: str = "SPAN"):
    """统一 span 装饰器：固定命名/类型、自动计时 elapsed_ms、异常自动记 error。"""
    def deco(fn):
        @observe(name=name, as_type=span_type, capture_input=False, capture_output=False)
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
                get_client().update_current_span(metadata={"elapsed_ms": ...})
                return result
            except Exception as e:
                get_client().update_current_span(metadata={"error": str(e)})
                raise
        return wrapper
    return deco
```

业务函数只负责组装自己的 metadata/output，由装饰器统一补 `elapsed_ms`/`error`，消除手写计时与遗漏。

### 4.2 各模块改造

| 模块 | 改动 |
|---|---|
| `core/retrieval.py` | `_embed_query` → `@traced_span("embedding", "GENERATION")`；`_milvus_search` → `@traced_span("milvus_query")`；`retrieve` → `@traced_span("retrieve")`；`rerank` → `@traced_span("rerank", "GENERATION")` |
| `services/search_service.py` | `_recall_milvus` → `@traced_span(lambda query, kb_ids, method, ...: f"recall_{method}")`（动态名）；`_recall_fulltext` → `@traced_span("recall_fulltext")`；`_rrf_merge_traced` → `@traced_span("rrf_merge")`（同步函数，装饰器支持 sync/async）；`search` → `@traced_span("search", "CHAIN")` |
| `core/es_store.py` | `es_search` → `@traced_span("es_query")` |
| `services/chat_service.py` | `rag_qa` → `@traced_span("rag_qa", "CHAIN")`（LLM generation span 保持） |

### 4.3 异常记录统一

- `_recall_milvus` 的 `_one` 失败分支：不再只 `logger.warning`，异常随 `recall_dense/sparse` span 的 `error` 字段记录（子 retrieve span 由装饰器自动记 error）。
- collection 不存在：retrieve 正常返回空（span 照常记录），不记 error。

## 五、兼容性与代价

- 历史 trace 的 span 名变化（`_recall_milvus` → `recall_dense` 等），Langfuse 界面按旧名建的面板需重命名；metadata 字段大部分保留，`per_method_hits`、`elapsed_ms` 语义不变。
- `langfuse_enabled=False` 时装饰器路径仍为零开销（observe 为 no-op）。
- 测试需同步：`test_retrieval.py` / `test_search_es.py` / `test_es_store.py` 的 span 断言按新名/新类型更新，并补 `prepare`/`config_resolve`/`kb_name_resolve`/异常 error 字段用例。

## 六、落地步骤与验证

1. `observability.py`：加 `traced_span` 装饰器（支持 sync/async、静态/动态名、自动 elapsed_ms/error）→ 单测装饰器行为
2. 重构 `core/retrieval.py`（span 命名/类型）→ `tests/test_retrieval.py` 验证
3. 重构 `services/search_service.py`（recall 动态名/rrf/search）→ `tests/test_search_es.py` 验证
4. `core/es_store.py` 统一 `es_query` → `tests/test_es_store.py` 验证
5. `services/chat_service.py` 统一 `rag_qa`
6. 更新 `docs/08` §7.6（替换为本文档结构）+ `docs/changes.md`
7. 验证：后端全量测试通过；`langfuse_enabled=True` 环境手工检索一次，Langfuse 界面核对调用树

## 七、评审结论（已确认）

1. 采纳 span 类型分类（CHAIN/SPAN/GENERATION）。
2. **不新增** `prepare` / `config_resolve` / `kb_name_resolve` 三个环节 span；`retrieve` 保留现有 `embed_ms` / `search_ms` / `filter_ms` 字段。
3. 采纳统一装饰器 `traced_span`。
4. 命名采用小写蛇形（`recall_dense` / `milvus_query` / `es_query` / `rrf_merge`）。
