# 方案 40：Query 多意图拆解 + 分意图检索 + 结果合并

> 日期：2026-09-03 · 状态：**已实现（2026-09-03）** · 评审：用户通过后实施

## 1. 需求

用户一次输入含多个**彼此独立的检索意图**时（如"查投标人资质要求，还要保证金比例和评分办法"），
改写链路要把它们**拆成多条子查询**，**每个子查询各检索一次**，再把各意图的结果**合并**输出，
避免单条混合查询只命中其中一个主题。延续既有规则：改写/拆解只服务召回；响应 `query` 仍为原查询。

## 2. 用户决策（已确认）

| 决策点 | 结论 |
|---|---|
| 入口范围 | 检索页 + 问答都支持（开关沿用各自 Query 改写开关） |
| 触发 | LLM 判定为多意图才拆；单意图 / 改写失败 / 输出解析失败 → 回退现状（单查询） |
| 重排 | **按子查询分别 rerank**（各自用子查询判断相关性），再跨意图合并 |
| 条数 | 每个意图各自取 top_k；跨意图合并去重后一并输出（总条数 ≈ 意图数 × top_k） |
| 否定/改口 | 沿用方案 39 后新增规则：改口型否定剔除旧目标，真实排除条件保留；多意图场景同样适用 |

## 3. 现状

- 检索页 `search_service.search`：改写开关开 → `rewrite_query` 返回**单条改写词** → 各检索方式按该词召回 → 单选直返/多选 RRF → （可选）用**原查询** rerank → 截断输出。
- 问答 `core.retrieval.retrieve_and_rank`：内部 step 0 改写（单条）→ 多路召回 → RRF → 用原查询 rerank。
- `rewrite_query` 当前签名返回 `str`。

## 4. 方案设计

### 4.1 拆解产物与解析（一次 LLM 调用，不改调用次数）

沿用 `query_rewrite` 提示词（提示词管理激活模板），规则扩展为双输出格式：
- **单一意图**：输出一行改写文本（现状行为）；
- **多个独立意图**：输出 JSON 数组，每元素一条改写后的子查询（剔除被否定的旧目标；保留真实排除条件）。

`rewrite_query` 返回值类型改为 `str | list[str]`，解析规则：
1. `resp.text` 首行 trim 后以 `[` 开头 → `json.loads`；
2. 解析成功且为字符串数组、`len >= 2`、元素均非空 → 返回 `list[str]`（多意图）；
3. 数组长度 1 → 返回该元素（单意图等价的子查询）；
4. 其余（单行文本 / 解析失败 / 空）→ 返回 `str`（现状语义，调用方照旧单查询）。

兜底不变：LLM 调用异常/空输出 → 回退原词；解析失败不会引入多意图路径。

> 注：本方案沿用用户上次"同步范围=仅运行库提示词管理默认模板"的偏好——
> 拆解输出格式规则写进运行库 `query_rewrite` 默认模板文案（数据更新）；
> 代码内置 fallback 常量与迁移 0017 seed 保持单意图旧文案（无激活模板时才使用，可接受降级）。

### 4.2 检索编排（两入口共用思路，伪代码）

检索页 `search()`：
```python
original_query = query
query_rewritten = False
if rewrite_on and rewrite_config:
    result = await rewrite_query(original_query, rewrite_config)   # str | list[str]
    query_rewritten = result != original_query

if isinstance(result, list):                       # 多意图
    merged = []
    for i, sub in enumerate(result):               # 每个子查询一次完整召回
        hits = await _recall_all_methods(sub, ...) # dense/sparse/fulltext/graph，可选 RRF
        if rerank_model_id:                        # 按子查询 rerank（用户口径）
            hits = await _apply_rerank(sub, hits[:rerank_top_n], rerank_model_id)
            hits = [h for h in hits if h["score"] >= similarity_threshold]
        merged.extend(hits[:top_k])                # 每意图取 top_k
    all_hits = _dedupe_cross_intent(merged)        # 按 (doc_id, chunk_index) 去重，保留先出现意图
    # 排序：意图顺序拼接，意图内按分数降序（跨意图分数不可比）
else:                                              # 单意图：现有逻辑（含原查询 rerank）
    ...
```
问答 `retrieve_and_rank`：step 0 得 `list` 时同样逐子查询执行 step 1-3
（按子查询 rerank + `rerank_threshold` 过滤 + 取 top_k），合并去重后返回（供构建回答上下文）。

`_dedupe_cross_intent`：key = `(doc_id, chunk_index)`；重复保留首次出现；graph 证据按
`(doc_id, chunk_index)` 一致处理。

### 4.3 提示词新规则（运行库默认模板文案）

在现有文案基础上追加：
```
- 若输入包含多个彼此独立的检索意图（想同时获取不同主题/问题的结果），输出 JSON 数组，
  每个元素是一条改写后的子查询；改口被否定的意图不要出现在数组中；
- 若只有一个意图，直接输出一行改写文本，不要输出 JSON；
- 数组/文本均只输出内容本身，不要解释。
```

### 4.4 Langfuse 记录
- `query_rewrite` generation：output 为多意图时是数组（可序列化）；metadata 无变更。
- `search` chain：span metadata 增 `sub_queries: list`（多意图时）；每个子查询的
  `recall_{method}` span 以 `input=子查询` 区分（现有动态命名机制天然支持）。
- 多意图合并不新增独立 span（合并属编排层），错误仍走各自 span 的 error 记录。

## 5. 响应与影响

- 检索页响应 `results` 可多于 top_k（≈ 意图数 × top_k，去重后略少），前端列表照常渲染；
  `query` 仍为原查询。
- 问答：合并结果进 `_build_context`，回答仍生成一条（覆盖多意图检索上下文）。
- 阈值：无 rerank 时沿用相似度阈值过滤后取 top_k/意图；sparse/fulltext 无阈值语义。

## 6. 测试计划
- `rewrite_query` 解析：mock LLM 返回 JSON 数组(≥2) → list；数组(1) → 单元素；单行文本 → str；
  非法 JSON → str（回退）；空/异常 → 原词（现有用例回归）。
- `search_service.search`：多意图开启（mock rewrite 返回 list）→ 每子查询 recall 次数=意图数、
  按子查询 rerank、每意图 top_k、跨意图去重、响应 query=原词；单意图行为与现有一致（回归）。
- `retrieve_and_rank`：同上（问答侧）。
- 既有 `test_query_rewrite` / `test_search_service` / `test_retrieval` / `test_chat_service`
  单意图断言回归。

## 7. 验证
- 后端全量 pytest。
- 真实 LLM 语义实测：多意图样例（如"查投标人资质要求，还要投标保证金比例和评分办法"）
  改写应输出数组且各元素独立；改口样例（"查XXX和YY，不了只要YY"）数组不含被否定的 XXX；
  单意图样例输出仍为一行。检索页/问答各跑一次核对结果条数与命中主题。

## 8. 风险
- 模型在单意图时误输出 JSON 数组：解析为长度 1 数组时解包为单条，无功能影响；
  长度≥2 时按多意图执行（用户输入确实含多意图时才可能，语义可接受）。
- 多意图使召回/rerank 调用数 ×意图数：延迟与成本上升，属需求本身取舍。
- 跨意图分数不可比：顺序=意图顺序拼接、意图内按分降序；去重保首次出现。
- JSON 稳定性：提示词约束 + 解析失败回退单查询兜底。

## 9. 待确认（评审时定）
- 合并后总条数口径：**每意图各保留 top_k，合并去重后全部输出（总数≈意图数×top_k）** 已按用户"每个取top_k"采纳；
  如需全局再截断到总 top_k 请指出。
