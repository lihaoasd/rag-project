# 检索重排参数细化 — 开发方案

> 版本: v1.1 | 日期: 2026-08-13 | 状态: 已实现
>
> **修订 v1.1**：移除分页（offset/limit）。检索结果本就少，无需翻页；返回条数由 `top_k`（无重排）或 `rerank_top_n`（重排）决定。`fetch_top_k` 相应简化为 `max(top_k, rerank_top_n) if rerank else top_k`。

---

## 1. Context

### 1.1 现状问题

上一期（commit `7cda7b5`）已接入排序模型（rerank）运行时，但检索参数存在两处不足：

1. **最终阈值只作用于向量分数**：[retrieval.py](../backend/core/retrieval.py) 用 `SimilarityPostprocessor(similarity_cutoff=similarity_threshold)` 在**向量阶段**过滤；[search_service.py](../backend/services/search_service.py) rerank 后用 relevance_score 覆盖 `score` 并重排，但**未对 rerank 分数再做阈值过滤**。结果是低相关结果只要过了向量门槛，即使 rerank 分很低也会被返回。
2. **缺少进入排序模型的召回控制**：无法指定「多少个向量候选进入排序模型」，也无法指定「向量相似度高于多少分才进入排序模型」。当前 rerank 会重排所有召回候选，成本不可控且无向量门槛。

### 1.2 目标

1. `similarity_threshold` 语义升级为**最终过滤阈值**：用了排序模型 → 按排序模型分数过滤；未用 → 按向量分数过滤（保持现状）。
2. 新增两个检索参数：
   - `rerank_top_n`：向量召回后，前 N 个候选进入排序模型。
   - `rerank_vector_threshold`：向量相似度高于该值才进入排序模型。

### 1.3 用户确认的设计决策

| 决策 | 选项 |
|------|------|
| 改动范围 | **后端 + 前端检索页**（SearchView 加两个控件） |
| 默认值 | **固定默认值**：`rerank_top_n=20`，`rerank_vector_threshold` 默认跟随 `similarity_threshold` |

---

## 2. 参数语义定义

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `top_k` | int | 10 | 每个知识库的向量召回候选数（内部召回数取 `max(top_k, offset+limit, rerank_top_n)`） |
| `similarity_threshold` | float | 0.6 | **最终**过滤阈值：rerank 时按 rerank 分数，否则按向量分数 |
| `rerank_top_n` | int | 20 | 向量候选按分数降序取前 N 个进入排序模型 |
| `rerank_vector_threshold` | float | `None`→`similarity_threshold` | 向量相似度门槛，低于该值不进入排序模型 |

> `rerank_vector_threshold` 为 `None` 时等于 `similarity_threshold`（向后兼容：不传时向量门槛与最终阈值一致）。

---

## 3. 改动范围

### 3.1 修改文件

| 文件 | 改动内容 |
|------|----------|
| `backend/models/schemas.py` | `SearchRequest` 新增 `rerank_top_n`、`rerank_vector_threshold` 两个字段 |
| `backend/services/search_service.py` | `search()` 增加两参数；rerank 前截断 top N；rerank 后按最终阈值过滤 |
| `backend/routers/search.py` | 两个端点透传新参数 |
| `backend/tests/test_search_service.py` | 新增/调整单测覆盖新行为 |
| `frontend/src/api/search.js` | `search()` 请求体增加两个字段 |
| `frontend/src/views/SearchView.vue` | 新增「进入排序模型候选数」「向量门槛」两个控件（选中排序模型时显示） |

### 3.2 不修改

- `backend/core/retrieval.py` 保持不变：`retrieve()` 的 `similarity_threshold` 参数天然作为「向量门槛」，由 `search()` 传入不同值即可，`chat_service` 调用方不受影响。

---

## 4. 关键逻辑伪代码

```python
async def search(
    query, kb_ids,
    top_k=10, similarity_threshold=0.6,
    offset=0, limit=20,
    rerank_model_id=None,
    rerank_top_n=20,
    rerank_vector_threshold=None,
):
    if not kb_ids:
        return {"query": query, "total": 0, "results": []}

    # 向量门槛：有排序模型 → rerank_vector_threshold（缺省=similarity_threshold）；否则 similarity_threshold
    vector_gate = rerank_vector_threshold if rerank_vector_threshold is not None else similarity_threshold

    # 召回数：覆盖分页 + 重排候选
    fetch_top_k = max(top_k, offset + limit, rerank_top_n)

    # 1. 并发召回（用 vector_gate 做向量过滤）
    all_hits = gather(retrieve(query, kb_id, top_k=fetch_top_k, similarity_threshold=vector_gate))

    # 2. 按向量分数降序
    all_hits.sort(score desc)

    # 3. 重排（可选）
    if rerank_model_id:
        candidates = all_hits[:rerank_top_n]          # 只取前 N 个进入排序模型
        try:
            reranked = _apply_rerank(query, candidates, rerank_model_id)   # 用 rerank 分数覆盖 + 降序
            # 最终阈值：按排序模型分数过滤
            all_hits = [h for h in reranked if h["score"] >= similarity_threshold]
        except Exception as e:
            logger.warning("rerank 失败，回退相似度排序: %s", e)
            # 回退：向量分数 + 最终阈值
            all_hits = [h for h in all_hits if h["score"] >= similarity_threshold]

    # 4. 分页
    total = len(all_hits)
    page = all_hits[offset:offset+limit]
    ...
```

### 4.1 行为对比

| 场景 | 改动前 | 改动后 |
|------|--------|--------|
| 无排序模型 | 向量门槛 0.6 过滤 → 分页 | **不变**（vector_gate=similarity_threshold） |
| 有排序模型 | 向量门槛 0.6 过滤 → 全部 rerank → 无最终过滤 | 向量门槛（默认 0.6）过滤 → **前 20** rerank → **按 rerank 分数 ≥0.6 过滤** |
| rerank 失败 | 回退向量排序，无过滤 | 回退向量排序，**按 0.6 过滤** |

---

## 5. Schema 变更

```python
class SearchRequest(BaseModel):
    query: str = Field(...)
    top_k: int = Field(10, ge=1, le=50, ...)
    similarity_threshold: float = Field(0.6, ge=0, le=1, ...)
    offset: int = Field(0, ge=0, ...)
    limit: int = Field(20, ge=1, le=100, ...)
    kb_ids: list[str] | None = Field(None, ...)
    rerank_model_id: str | None = Field(None, ...)
    rerank_top_n: int = Field(20, ge=1, le=100, description="进入排序模型的向量候选数量")
    rerank_vector_threshold: float | None = Field(None, ge=0, le=1, description="进入排序模型的最低向量相似度，为空则等于 similarity_threshold")
```

---

## 6. 前端设计

`SearchView.vue` 在「排序模型」选择框下方，当 `rerankModelId` 非空时（`v-if="rerankModelId"`）显示：

- **进入排序模型候选数**：`el-slider`（min 1 / max 100 / step 1），绑定 `rerankTopN`（默认 20）。
- **向量门槛**：`el-slider`（min 0 / max 1 / step 0.05，百分比显示），绑定 `rerankVectorThreshold`（默认 `null`，提示「默认=相似度阈值」）。

`api/search.js` 的 `search()` 增加 `rerank_top_n`、`rerank_vector_threshold` 字段；`searchSingle()` 保持不动（旧接口不含排序）。

---

## 7. 实现步骤

### Step 1: Schema → 验证: 单测导入通过
1. `schemas.py` 加两字段。
2. 运行 `pytest tests/test_schemas.py` 确认无破坏。

### Step 2: search_service 逻辑 → 验证: 单测
1. `search()` 加两参数 + 截断 + 最终阈值过滤逻辑。
2. `test_search_service.py` 新增用例：
   - 有排序模型：只 rerank 前 `rerank_top_n`，最终按 rerank 分数 ≥ 阈值过滤（低分被丢弃）。
   - `rerank_vector_threshold` 显式传入时，向量门槛生效。
   - 不传新参数：默认 `rerank_top_n=20`、向量门槛=similarity_threshold。
   - rerank 失败：回退向量排序并按阈值过滤。
   - 无排序模型：行为与改动前一致。

### Step 3: Router 透传 → 验证: Swagger 可调用
1. `routers/search.py` 两个端点传 `body.rerank_top_n`、`body.rerank_vector_threshold`。

### Step 4: 前端 → 验证: 页面调节生效
1. `api/search.js` 加字段。
2. `SearchView.vue` 加两个条件控件，请求体带上新参数。

---

## 8. 验证方式

```bash
cd backend && uv run pytest tests/test_search_service.py tests/test_search_router.py -q
cd frontend && npm run build   # 无报错
```

| 序号 | 检查项 | 方式 |
|------|--------|------|
| 1 | 无排序模型时结果与改动前一致 | 单测 + 页面 |
| 2 | 有排序模型时只 rerank 前 20，且按 rerank 分数过滤低分 | 单测 |
| 3 | `rerank_vector_threshold` 显式传 0.8 时低向量分候选被排除 | 单测 |
| 4 | 不传新参数默认值生效 | 单测 |
| 5 | 前端选中排序模型后出现两个控件并随请求下发 | 手动 |

---

## 9. 不做什么（本次范围外）

- ❌ 按知识库配置检索参数（仍走全局 / 请求体）。
- ❌ 问答（chat）管线的 rerank 接入与参数透传（chat 路径仍走向量检索，不含排序）。
- ❌ 排序模型 `params.top_n` 与 `rerank_top_n` 的联动（二者独立，前者是模型配置，后者是本次请求参数）。
- ❌ rerank 分数归一化 / 重标定。
