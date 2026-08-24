# 32 问答检索接入三路混合（稠密 + 稀疏 + 全文）计划

> 版本: v1.0 | 日期: 2026-08-21 | 状态: 待评审
> 关联文档: [08-es-fulltext-search-design.md](../08-es-fulltext-search-design.md)（需求变更：推翻其 §1.3「问答链路保持纯稠密」的边界）

## 1. 需求（已与用户确认）

问答（chat）检索从纯稠密升级为**后端固定三路混合**：

- `dense`（Milvus 稠密向量）+ `sparse`（Milvus BM25 稀疏向量）+ `fulltext`（ES+IK 全文检索）三路独立召回；
- 多路结果 Python 端 **RRF 融合**（k=60），再衔接现有可选 rerank + rerank 阈值过滤；
- **不加前端控件**（聊天页不动），由后端固定组合；默认三路全开；
- 检索方式通过配置项 `chat_retrieval_methods` 控制（默认 `["dense", "sparse", "fulltext"]`），便于在未部署 ES 的环境回退。

## 2. 现状

| 链路 | 现状 |
|------|------|
| search（检索页） | 三路召回 + 单选直返/多选 RRF + 可选 rerank + ES 高亮，已实现（`services/search_service.py`、`core/es_store.py`） |
| chat（问答） | `ChatRequest(message, session_id)` → `chat_stream` → `_run_rag` → `retrieve_and_rank` → 仅 `retrieve()` 纯稠密 |
| 词库查询扩展 | `lexicon_service.expand_query(query) -> list[str]`（OR 语义），`es_search` 已支持列表查询词 |
| ES 可用性 | `es_search` 内部 try/except，失败返回 `[]`；写操作受 `es_sync_enabled` 控制 |

## 3. 改动范围

| 文件 | 改动 |
|------|------|
| `backend/config.py` | 新增 `chat_retrieval_methods: list[str] = ["dense", "sparse", "fulltext"]` |
| `backend/core/retrieval.py` | 新增公共纯函数 `rrf_merge()`；扩展 `retrieve_and_rank()` 支持 `retrieval_methods` / `expand_query` 参数（默认 None → 保持纯 dense 向后兼容） |
| `backend/services/search_service.py` | 删除私有 `_rrf_merge`，改为从 `core.retrieval` 导入 `rrf_merge`（消除重复实现；`_rrf_merge_traced` span 包装保留） |
| `backend/services/chat_service.py` | `_run_rag` 检索改传三路方法 + 词库查询扩展函数 |
| `backend/tests/test_retrieval.py` | 新增 `retrieve_and_rank` 多路召回 / RRF / fulltext 降级测试 |
| `backend/tests/test_search_es.py` | `_rrf_merge` 引用位置更新 |
| `docs/08-es-fulltext-search-design.md` | 更新 chat 边界描述（§1.2/§1.3/§7.1/§13） |
| `docs/changes.md` | 追加修改记录 |

**不改动**：`routers/chat.py`、`models/schemas.py`（chat 请求结构不变）、`core/es_store.py`、前端。

## 4. 实现步骤（伪代码）

### 4.1 `config.py`

```python
# === 检索方式 ===
chat_retrieval_methods: list[str] = ["dense", "sparse", "fulltext"]  # 问答检索策略（三路混合，默认全开）
```

### 4.2 `core/retrieval.py`

```python
def rrf_merge(ranked_lists: list[list[dict]], k: int = 60) -> list[dict]:
    """RRF 融合多路召回（按 (doc_id, chunk_index) 对齐 rank 分数）。"""
    # 逻辑与 search_service._rrf_merge 相同，迁移至此供检索/问答共用

async def retrieve_and_rank(query, kb_id, top_k=5, similarity_threshold=0.2,
                            rerank_config=None, rerank_threshold=0.8,
                            retrieval_methods=None, expand_query=None):
    methods = list(retrieval_methods) if retrieval_methods else ["dense"]
    # 校验 methods ⊆ {dense, sparse, fulltext}，非法抛 ValueError

    ranked_lists = []
    for m in methods:
        if m in ("dense", "sparse"):
            try:
                threshold = (0.2 if rerank_config else similarity_threshold) if m == "dense" else None
                hits = await retrieve(query, kb_id, top_k, threshold, m)   # sparse 时相似度阈值不生效
            except Exception as e:
                logger.warning(...)   # 单路失败降级为空，不阻断问答
                hits = []
            for h in hits: h.setdefault("kb_id", kb_id)
            ranked_lists.append(hits)
        else:  # fulltext
            try:
                q = expand_query(query) if expand_query else query
                hits = await es_store.es_search(q, [kb_id], top_k=top_k * settings.retrieval_es_top_k_factor, highlight=False)
            except Exception as e:
                logger.warning(...)
                hits = []
            for h in hits: h.setdefault("kb_id", kb_id)
            ranked_lists.append(hits)

    if len(ranked_lists) == 1:
        all_hits = ranked_lists[0]
        all_hits.sort(key=lambda x: x["score"], reverse=True)   # 单选直返
    else:
        all_hits = rrf_merge(ranked_lists, k=settings.retrieval_rrf_k)

    if rerank_config and all_hits:
        all_hits = await rerank(query, all_hits, rerank_config)
        all_hits = [h for h in all_hits if h["score"] >= rerank_threshold]
    return all_hits
```

### 4.3 `services/search_service.py`

```python
from core.retrieval import rrf_merge, retrieve, rerank
# 删除本地 _rrf_merge；_rrf_merge_traced 内调用 rrf_merge(ranked_lists, k)
```

### 4.4 `services/chat_service.py`

```python
from services import lexicon_service, prompt_service

# _run_rag 内：
rerank_config = await resolve_model_config(kb_id, "rerank")
try:
    await lexicon_service.ensure_cache()   # fulltext 查询扩展缓存（失败不阻断问答）
except Exception as e:
    logger.warning("词库缓存加载失败，跳过查询扩展: %s", e)
retrieved = await retrieve_and_rank(
    query=message, kb_id=kb_id,
    top_k=settings.retrieval_top_k,
    similarity_threshold=settings.retrieval_similarity_threshold,
    rerank_config=rerank_config,
    rerank_threshold=settings.retrieval_rerank_threshold,
    retrieval_methods=settings.chat_retrieval_methods,
    expand_query=lexicon_service.expand_query,
)
```

## 5. 关键设计决策

| 项 | 决策 | 理由 |
|----|------|------|
| RRF 函数位置 | 上移到 `core/retrieval.py` 公共 `rrf_merge`，search/chat 共用 | 消除跨 service 引用私有函数与重复实现 |
| 默认兼容 | `retrieve_and_rank` 不传 `retrieval_methods` 时仍 `["dense"]` | 现有测试与语义不破坏 |
| 单路失败 | 每路 try/except 降级为空，仅记日志 | 与 search 页召回一致；ES 未部署时问答仍可用 dense+sparse |
| fulltext 召回量 | `top_k × retrieval_es_top_k_factor`（同 search 页） | 给 RRF/rerank 留余量 |
| chat 高亮 | `highlight=False` | chat 来源引用无高亮字段，不产生 ES 高亮开销 |
| 词库扩展 | chat_service 负责 `ensure_cache` + 传入 `expand_query` | `core/retrieval.py` 不依赖 services 层（避免循环依赖） |
| rerank 阈值 | 沿用现有 `rerank_threshold` 过滤（RRF 融合后 rerank） | 与现有 chat 行为一致 |

## 6. 验证

1. **单元**：`pytest` 全量通过。
   - 新增：`retrieve_and_rank` 三路（dense+sparse+fulltext）各路均被召回、RRF 融合、fulltext 异常降级、默认纯 dense 兼容；
   - 更新：`test_search_es.py` 中 `_rrf_merge` 引用改为 `core.retrieval.rrf_merge`。
2. **集成**（手动，环境具备 ES 时）：
   - 起 ES + 上传文档（PG/Milvus/ES 三处落库）→ 发起问答，确认：
     - 来源引用同时包含关键词精确命中的片段（fulltext 生效）；
     - Langfuse trace `rag_qa` 下出现 dense/sparse/fulltext 三路召回 span 与 RRF 融合；
   - 停掉 ES → 再次问答：fulltext 路降级，dense+sparse 正常，回答不中断。
3. **回归**：chat SSE 流式、来源引用、rerank 行为与改动前一致（`retrieve_and_rank` 默认参数路径不变）。

## 7. 风险与应对

| 风险 | 说明 | 应对 |
|------|------|------|
| ES 未部署/数据未同步 | fulltext 路每次降级 | 单路降级不阻断问答；可通过 `chat_retrieval_methods` 去掉 fulltext |
| RRF 分数非 [0,1] | 无 rerank 时来源引用 score 为 RRF 分数 | 有 rerank 时被 relevance_score 覆盖；无 rerank 时仅作排序依据（与 search 页一致） |
| 词库缓存加载失败 | `ensure_cache` 抛异常 | chat_service try/except，跳过查询扩展 |
