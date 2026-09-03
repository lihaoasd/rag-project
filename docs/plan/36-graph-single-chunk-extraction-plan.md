# 建图 LLM 提取改为逐 chunk 提取（减少幻觉）开发方案

> 关联设计: [09-graph-module-design.md](../09-graph-module-design.md)（§5.1 管线 / §5.4 成本 / §10.2 配置） | 日期: 2026-08-24 | 状态: 已确认

## 1. 需求

建图时 LLM 实体/关系提取由「批量合并（`graph_extraction_batch=4`，4 个 chunk 一批）」改为「**逐 chunk 提取**（每 chunk 一次 LLM 调用）」，减少幻觉。

**为什么减少幻觉**：
- 单 chunk 输入下，LLM 只基于该 chunk 原文，无法跨 chunk 拼接上下文编造实体/关系；
- `_assign_chunk_ids`（实体名回原文定位）在单 chunk 下天然精确：实体要么在当前 chunk 原文中、要么 fallback 当前 chunk，不再出现批量时"实体名定位到其他 chunk"的跨批错配；
- 关系的 chunk 归属由「整批 chunk 全部标记」变为「仅当前 chunk」，来源更真实。

## 2. 现状

- `config.py:97` `graph_extraction_batch: int = 4`；`core/graph_extractor.py:244` `batch_size = settings.graph_extraction_batch or 4`；.env / .env.example 均无该配置项（默认生效）；
- `extract_from_chunks` 已按 `batch_size` 触发 `_llm_extract_batch`，`batch_size=1` 时自然逐 chunk，**代码逻辑无需改**，仅改配置默认值与文档/注释。

## 3. 改动范围

| 文件 | 改动 |
|------|------|
| `config.py` | `graph_extraction_batch: int = 4` → `1`（注释：逐 chunk，减少幻觉；可调大合并省成本） |
| `core/graph_extractor.py` | 模块 docstring「LLM 批量补充」措辞更新（逻辑不变） |
| `tests/test_graph_extractor.py` | 新增：`graph_extraction_batch=1` 时每个 chunk 各触发一次 `_llm_extract_batch`（mock 计数）+ chunk 归属为当前 chunk |
| `docs/09-graph-module-design.md` | §5.1 管线图示「批量（3~5 chunk/次）」→ 逐 chunk；§5.4 成本表「批量 LLM 调用」行更新为逐 chunk（幻觉最低/成本最高，可调 batch 权衡）；§10.2 配置注释 |
| `docs/changes.md` | 追加修改记录 |

## 4. 关键伪代码

```
# config.py
graph_extraction_batch: int = 1   # LLM 抽取：逐 chunk 提取（每 chunk 一次调用，减少跨 chunk 幻觉；可调大合并以节省成本）

# extract_from_chunks（逻辑不变，batch_size=1 生效）
batch_size = settings.graph_extraction_batch or 4   # =1
for idx, chunk in enumerate(chunks):
    词库/正则匹配 → _add_entity
    llm_batch.append(chunk)
    if len(llm_batch) >= batch_size:                # 每个 chunk 立即触发
        _llm_extract_batch(kb_id, [单 chunk]) → 实体/关系记入该 chunk
```

## 5. 验证方式

1. 新增单测：patch `settings.graph_extraction_batch=1`，3 个 chunk + mock `_llm_extract_batch`（返回实体），断言调用 3 次、每次入参 1 个 chunk、实体 `chunk_ids` 正确；
2. 全量 pytest exit 0；
3. 真实环境（可选，部署机）：单文件重建后抽查实体 `mentioned_chunk_ids` 与原文一致。

## 6. 风险与边界

- **成本上升**：LLM 调用次数与 token 翻约 4 倍（每 chunk 一次请求）；保留 `graph_extraction_batch` 可调（.env 设 `GRAPH_EXTRACTION_BATCH=4` 即恢复批量）；
- 提取质量对单 chunk 上下文敏感（跨 chunk 才显式的关系可能漏提）——这正是减少幻觉的代价，属用户接受的取舍。
