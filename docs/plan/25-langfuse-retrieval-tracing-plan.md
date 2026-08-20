# 25 检索三路 Langfuse 追踪增强

> 状态：待评审
> 关联：`docs/08-es-fulltext-search-design.md`、`docs/plan/24-retrieval-methods-multiselect-plan.md`

## 1. 背景与目标

检索已改为三路独立策略（dense/sparse/fulltext）可多选。当前 Langfuse 追踪不完整：

| 检索路 | 现状 | 缺口 |
| --- | --- | --- |
| dense / sparse | `retrieve()` 有 `@observe(as_type="retriever")`，逐 kb span + 命中明细 | 无每路汇总（耗时/命中数在 search 层不可见） |
| fulltext | `es_search()` **无 observe** | **Langfuse 完全看不到 ES 检索 span** |
| search 总 span | 有 observe，metadata 只记参数 | 无每路命中数 / 耗时汇总，无法对比三路效果 |

目标（已与用户确认，选"最详细含耗时"）：
1. **fulltext 路补独立 retriever span**（与 dense/sparse 对齐）；
2. **每路召回 span** 记录 method、命中数（hit_count）、耗时（elapsed_ms）、命中明细；
3. **search 总 span** metadata 汇总每路命中数（per_method_hits），便于 Langfuse 界面直接对比三路。

## 2. 改动范围

| 文件 | 改动 |
| --- | --- |
| `backend/services/search_service.py` | `_recall_milvus` / `_recall_fulltext` 加 `@observe(as_type="retriever")`，内部计时并 `update_current_span`（method/hit_count/elapsed_ms + 命中明细）；`search()` 收集 `per_method_hits` 写入总 span metadata |
| `backend/core/es_store.py` | `es_search()` 加 `@observe` 与 span 记录（method=fulltext、kb_ids、top_k、hit_count、elapsed_ms、命中明细）；注意 **es_store 已有同名 `get_client()`（ES 客户端）**，langfuse 的 `get_client` 须别名导入（如 `from langfuse import get_client as lf_get_client, observe`） |
| 测试 | `test_search_service.py` / `test_search_es.py` 补 span 记录断言（patch langfuse get_client）；`test_es_store.py` 补 es_search span 断言 |
| `docs/08` | 新增追踪章节（§7.6 Langfuse 检索追踪） |
| `docs/changes.md` | 追加修改记录 |

**不改动：** 检索逻辑、前端、`core/retrieval.py`（已有 span，仅补每路汇总在 service 层）。

## 3. 关键设计（伪代码）

### 3.1 search_service.py — 每路召回 span

```python
import time
from langfuse import get_client, observe
from core.observability import format_retrieval_observation

@observe(as_type="retriever", capture_input=False, capture_output=False)
async def _recall_milvus(query, kb_ids, method, top_k, similarity_threshold):
    t0 = time.perf_counter()
    # ... 原逻辑：逐 kb 并发 retrieve（内部仍有每 kb span，形成嵌套） ...
    all_hits = ...
    get_client().update_current_span(
        input=query,
        metadata={
            "method": method,             # dense / sparse
            "kb_ids": kb_ids,
            "top_k": top_k,
            "hit_count": len(all_hits),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        },
        output=format_retrieval_observation(all_hits),
    )
    return all_hits

@observe(as_type="retriever", capture_input=False, capture_output=False)
async def _recall_fulltext(query, kb_ids, top_k, highlight):
    t0 = time.perf_counter()
    # ... 原逻辑：词库扩展 + es_search ...
    all_hits = ...
    get_client().update_current_span(
        input=query,
        metadata={"method": "fulltext", "kb_ids": kb_ids, "top_k": top_k,
                  "hit_count": len(all_hits),
                  "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)},
        output=format_retrieval_observation(all_hits),
    )
    return all_hits
```

`search()` 内改为先收集命中数再入 ranked_lists：

```python
per_method_hits = {}
if "dense" in methods:
    hits = await _recall_milvus(...)
    per_method_hits["dense"] = len(hits)
    ranked_lists.append(hits)
if "sparse" in methods:
    hits = await _recall_milvus(...)
    per_method_hits["sparse"] = len(hits)
    ranked_lists.append(hits)
if "fulltext" in methods:
    hits = await _recall_fulltext(...)
    per_method_hits["fulltext"] = len(hits)
    ranked_lists.append(hits)
get_client().update_current_span(metadata={"per_method_hits": per_method_hits})
```

### 3.2 core/es_store.py — fulltext span

```python
from langfuse import get_client as lf_get_client, observe   # 避免与 ES get_client 冲突
from core.observability import format_retrieval_observation

@observe(as_type="retriever", capture_input=False, capture_output=False)
async def es_search(query, kb_ids, top_k=10, highlight=True):
    # 空 kb/queries 提前返回分支：同样记录（hit_count=0）
    t0 = time.perf_counter()
    hits = ...   # 原逻辑
    lf_get_client().update_current_span(
        input=query,
        metadata={"method": "fulltext", "kb_ids": kb_ids, "top_k": top_k,
                  "hit_count": len(hits),
                  "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)},
        output=format_retrieval_observation(hits),
    )
    return hits
```

> 注意：`es_search` 有 2 个提前 return（无 kb/queries、ES 异常），需在返回前也记录 span（hit_count=0），保证每路都有 span 输出。

## 4. 实现步骤

1. `core/es_store.py`：别名导入 langfuse + `es_search` 加 observe/span；→ 验证：test_es_store 通过（langfuse disabled 时 update 为 no-op，不破坏既有断言）。
2. `services/search_service.py`：`_recall_milvus`/`_recall_fulltext` 加 observe + 计时 + metadata；`search()` 收集 per_method_hits；→ 验证：test_search_service/test_search_es 通过。
3. 测试补断言：patch `services.search_service.get_client` 与 `core.es_store.lf_get_client`（或 `langfuse.get_client`），断言 `update_current_span` 收到 method/hit_count/elapsed_ms/per_method_hits。
4. 全量 `pytest -p no:cacheprovider`；→ 期望全绿。
5. 更新 `docs/08`（§7.6）+ `docs/changes.md`。

## 5. 验证方式

1. 单元测试：新增 span 记录断言（mock get_client 捕获 update_current_span 调用）；
2. 全量测试通过（现有 458 用例不回归）；
3. 如 Langfuse 可达：检索页分别用单选 dense / 单选 fulltext / 三选 RRF 各查一次，Langfuse trace 中应看到：
   - `search` span 的 `per_method_hits` 汇总；
   - 每路 `retriever` span（method/hit_count/elapsed_ms）；
   - fulltext 路出现 `es_search` span（新增）。

## 6. 风险与说明

- **命名冲突**：es_store 已有 `get_client()`（ES 客户端），langfuse 必须别名导入，否则覆盖 ES client 工厂导致连锁失败；
- **langfuse disabled**：`langfuse_enabled=False` 时 `get_client()` 返回 disabled client，`update_current_span` 为 no-op，零开销、不影响测试与生产关闭态；
- **提前返回分支**：es_search 空 kb/异常分支也要记 span，避免 fulltext 路在 Langfuse 中缺失（异常分支 hit_count=0 + 原始 error 通过 metadata 可选记录，本期仅记 0）。
