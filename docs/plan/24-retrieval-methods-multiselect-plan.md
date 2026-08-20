# 24 检索参数整改 — 三路独立召回（稠密/稀疏/全文）多选 + RRF

> 状态：待评审
> 关联：`docs/08-es-fulltext-search-design.md`、`docs/plan/23-es-fulltext-search-plan.md`、`docs/plan/hybrid-retrieval-plan.md`

## 1. 背景与目标

现状 `retrieval_method` 是**四选一互斥**模式：`vector`（Milvus 稠密）、`hybrid`（Milvus 稠密+BM25 稀疏，Milvus 内部 RRF/Weighted）、`fulltext`（ES 全文）、`hybrid_es`（Milvus 稠密 + ES，Python 端 RRF）。

整改后改为**三个独立召回策略，可多选**：

| 策略 | 值 | 实现 | 分数语义 |
| --- | --- | --- | --- |
| 稠密向量 | `dense` | Milvus `VectorStoreQueryMode.DEFAULT` | IP 相似度 [0,1] |
| 稀疏向量 | `sparse` | Milvus `VectorStoreQueryMode.SPARSE`（BM25 内置函数） | BM25 分数（非 [0,1]） |
| 全文检索 | `fulltext` | ES + IK `es_search` | BM25 分数（非 [0,1]） |

**行为规则（已与用户确认）：**
1. 单选一个策略 → 直接返回该路结果，**不做 RRF**，分数保留原始语义；
2. 多选两个及以上 → 各策略独立召回后 **Python 端 RRF 融合**（复用 `_rrf_merge`，k=60）；
3. 默认仅勾选稠密向量（`retrieval_default_methods = ["dense"]`）；
4. 旧的 4 种互斥模式与 `weighted` 权重融合全部移除；
5. rerank（可选重排）逻辑不变，作用于融合后的候选。

## 2. 改动范围

| 文件 | 改动 |
| --- | --- |
| `backend/config.py` | `retrieval_default_method: str = "fulltext"` → `retrieval_default_methods: list[str] = ["dense"]`；`retrieval_hybrid_enabled` 语义更新（Milvus 稀疏字段开关，默认 True，保留原名避免影响部署） |
| `backend/models/schemas.py` | `SearchRequest.retrieval_method` → `retrieval_methods: list[Literal["dense","sparse","fulltext"]] \| None`；移除 `hybrid_ranker`、`hybrid_weights` |
| `backend/core/vector_store.py` | `get_vector_store` 移除 `hybrid_ranker`/`hybrid_ranker_params` 透传（不再有 Milvus 内部融合）；保留 `enable_sparse`（受 `retrieval_hybrid_enabled` 控制） |
| `backend/core/retrieval.py` | `retrieve()`：`retrieval_method` 取值改为 `dense`/`sparse`（默认 `dense`），映射 `VectorStoreQueryMode.DEFAULT/SPARSE`；删除 `_resolve_retrieval_method`/`_resolve_hybrid_ranker`/`hybrid_ranker`/`hybrid_weights`；**sparse 模式跳过 embedding 计算与相似度阈值过滤**（BM25 分数无阈值语义，与现状 hybrid 一致）；`retrieve_and_rank`（问答链路）保持纯稠密不动 |
| `backend/services/search_service.py` | `search()`：参数 `retrieval_method` → `retrieval_methods: list[str] \| None`；单选直返、多选 RRF；fulltext 路保留词库扩展与高亮 |
| `backend/routers/search.py` | 两个端点透传 `retrieval_methods`，移除 `hybrid_ranker`/`hybrid_weights` |
| `frontend/src/api/search.js` | `retrieval_method` → `retrieval_methods`（数组），移除 `hybrid_ranker`/`hybrid_weights` |
| `frontend/src/views/SearchView.vue` | 检索方式下拉 → **三复选**（稠密向量/稀疏向量/全文检索，默认勾选稠密）；移除融合策略/权重 UI；结果分数展示适配（见 §5） |
| 测试 | `test_schemas.py`、`test_search_router.py`、`test_search_service.py`、`test_search_es.py`、`test_retrieval.py` 全部按新参数重构；新增多选 RRF、单选直返用例 |
| `docs/08-es-fulltext-search-design.md` | §7 检索方式表、§10 配置更新为三策略多选模型 |
| `docs/changes.md` | 追加修改记录 |

**不改动：** `core/es_store.py`、`services/lexicon_service.py`、`services/es_admin_service.py`、`services/chat_service.py`（问答链路 `retrieve_and_rank` 纯稠密行为不变）、Milvus/ES 数据与索引结构。

## 3. 关键逻辑（伪代码）

### 3.1 core/retrieval.py — retrieve()

```python
# _resolve_retrieval_method/_resolve_hybrid_ranker 删除，改为：
def _resolve_query_mode(retrieval_method: str | None) -> VectorStoreQueryMode:
    if retrieval_method in (None, "dense"):
        return VectorStoreQueryMode.DEFAULT
    if retrieval_method == "sparse":
        return VectorStoreQueryMode.SPARSE
    raise ValueError(f"不支持的检索方式: {retrieval_method}")

# retrieve() 签名：移除 hybrid_ranker / hybrid_weights
async def retrieve(query, kb_id, top_k=5, similarity_threshold=0.6,
                   retrieval_method: str | None = None) -> list[dict]:
    mode = _resolve_query_mode(retrieval_method)          # DEFAULT / SPARSE
    vector_store = get_vector_store(kb_id, dim)           # 不再传 ranker
    # ... Collection 存在性检查不变 ...
    retriever = index.as_retriever(similarity_top_k=top_k,
                                   vector_store_query_mode=mode)
    if mode == VectorStoreQueryMode.SPARSE:
        query_bundle = QueryBundle(query_str=query)       # 免 embedding
        filtered_nodes = await retriever.aretrieve(query_bundle)  # BM25 分数不设阈值
    else:
        query_embedding = await embed_model.aget_query_embedding(query)
        query_bundle = QueryBundle(query_str=query, embedding=query_embedding)
        nodes = await retriever.aretrieve(query_bundle)
        processor = SimilarityPostprocessor(similarity_cutoff=similarity_threshold)
        filtered_nodes = processor.postprocess_nodes(nodes)
    # ... 组装 hits 不变（score 保留原始值） ...
```

### 3.2 services/search_service.py — search()

```python
VALID_METHODS = ("dense", "sparse", "fulltext")

async def search(query, kb_ids, top_k=10, similarity_threshold=0.6,
                 retrieval_methods: list[str] | None = None,
                 rerank_model_id=None, rerank_top_n=5,
                 rerank_vector_threshold=None, highlight=True) -> dict:
    methods = list(dict.fromkeys(retrieval_methods or settings.retrieval_default_methods))
    if not methods:
        raise ValueError("未选择任何检索方式")
    invalid = set(methods) - set(VALID_METHODS)
    if invalid:
        raise ValueError(f"不支持的检索方式: {sorted(invalid)}")
    if not kb_ids:
        return {"query": query, "total": 0, "results": []}

    # 向量门槛 / 召回数逻辑不变（仅 dense 路生效）
    vector_gate = ...          # 原逻辑
    fetch_top_k = max(top_k, rerank_top_n) if rerank_model_id else top_k

    ranked_lists = []
    if "dense" in methods:
        ranked_lists.append(await _recall_milvus(query, kb_ids, "dense",
                                                 fetch_top_k, vector_gate))
    if "sparse" in methods:
        ranked_lists.append(await _recall_milvus(query, kb_ids, "sparse",
                                                 fetch_top_k, None))
    if "fulltext" in methods:
        await lexicon_service.ensure_cache()
        expanded = lexicon_service.expand_query(query)
        ranked_lists.append(await es_store.es_search(
            expanded, kb_ids,
            top_k=fetch_top_k * settings.retrieval_es_top_k_factor,
            highlight=highlight))

    all_hits = ranked_lists[0] if len(ranked_lists) == 1 \
        else _rrf_merge(ranked_lists, k=settings.retrieval_rrf_k)

    # rerank / kb_name / 组装响应：原逻辑不变
```

`_recall_milvus` 为原 `_retrieve_one`/`_vector_one` 的泛化（按 mode 逐 kb 并发 + try/except 降级 + `kb_id` 打标），sparse 路不传 `similarity_threshold`。

### 3.3 单选与多选的行为差异

- `methods == ["dense"]` → 结果分数为 IP 相似度（0~1），与现状 `vector` 一致；
- `methods == ["sparse"]` → 结果分数为 Milvus BM25 分数；
- `methods == ["fulltext"]` → 结果分数为 ES BM25 分数，含高亮；
- `methods == ["dense","sparse"]` → 两路 RRF（原先 `hybrid` 的 Python 端等价实现）；
- `methods == ["dense","fulltext"]` → 等价于旧 `hybrid_es`；
- 三路全选 → 三路 RRF。

## 4. 实现步骤

1. `config.py`：改默认配置；→ 验证：pydantic 加载正常。
2. `core/vector_store.py`：移除 ranker 透传；→ 验证：`get_vector_store` 调用方无残留参数。
3. `core/retrieval.py`：mode 解析 + sparse 免 embedding/免阈值；→ 验证：单测（dense/sparse mode 解析、sparse 不调 embedding）。
4. `models/schemas.py` + `routers/search.py`：参数改名与透传；→ 验证：router 单测。
5. `services/search_service.py`：三路召回 + 单选直返/多选 RRF；→ 验证：单测（单选直返分数、dense+sparse RRF、dense+fulltext 等价旧 hybrid_es、默认仅 dense）。
6. 前端 `api/search.js` + `SearchView.vue`：三复选 + 分数展示；→ 验证：`npm run build`。
7. 全量测试 `pytest -p no:cacheprovider`；→ 期望全绿。
8. 更新 `docs/08`、`docs/changes.md`。

## 5. 连带调整：结果分数展示

单选 sparse/fulltext 与多选 RRF 的分数均**非百分比**（BM25 可 >1，RRF ≈0.01~0.05），现状前端 `score*100%` 显示会失真（如 2400%、3%）。本次一并修正（遗留问题，早前已与用户讨论过方案 1）：

- 有 rerank 或单/多选均为 dense → 显示百分比（现有样式）；
- 单选 sparse / fulltext，或多选 RRF → 显示**原始分数数字**（去掉百分比条或降级为数字标签），避免误导。

## 6. 验证方式

1. 后端单测：新增/重构覆盖——
   - `_resolve_query_mode`：dense/sparse/非法值；
   - sparse 路不调用 embedding（mock embed_model 断言）；
   - 单选各策略分数语义直返（不 RRF）；
   - 多选 RRF 融合正确性（固定 rank 验证分数，k=60）；
   - 默认 `retrieval_default_methods == ["dense"]`；
   - router 透传 `retrieval_methods`；
2. 前端 `npm run build` 通过；
3. 手工冒烟（如可达）：dense 单选 / fulltext 单选（高亮）/ 三选 RRF 各查一次，核对分数与顺序合理性。

## 7. 风险与说明

- **sparse 依赖 Collection 有稀疏字段**：现有部署 `retrieval_hybrid_enabled=True`（默认），Collection 创建时已含 `sparse_embedding` 字段，sparse 策略直接可用；若某环境该配置为 False 创建过 Collection，sparse 检索会报错（service 层降级为空结果，与向量路一致）。
- **配置名保留**：`retrieval_hybrid_enabled` 不改名（避免影响部署与 .env），语义更新为"Milvus 稀疏字段开关"。
- **问答链路不变**：`chat_service` 走 `retrieve_and_rank`（纯稠密 + rerank），不受本次整改影响。
- **不向后兼容**：旧客户端传 `retrieval_method`/`hybrid_ranker` 会 422，属预期（前后端同步整改）。
