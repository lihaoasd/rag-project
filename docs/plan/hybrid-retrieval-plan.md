# 稠密 + 稀疏（BM25）混合检索接入 MilvusVectorStore

> 版本: v1.0 | 日期: 2026-08-18 | 状态: 待评审

## Context

当前检索链路（[core/vector_store.py](../backend/core/vector_store.py) + [core/retrieval.py](../backend/core/retrieval.py)）只做**稠密向量**检索。目标：利用 `MilvusVectorStore` 原生 `enable_sparse` 能力，加入 Milvus 内置 **BM25** 稀疏检索，通过 **RRF** 融合实现稠密 + 稀疏混合检索，提升关键词召回。

已确认的技术事实（`llama-index-vector-stores-milvus` 1.1.0 / `pymilvus` 2.6.17 / Milvus v3.0.0）：

- `MilvusVectorStore(enable_sparse=True, sparse_embedding_function=BM25BuiltInFunction(...))` 会在 schema 中新增 `SPARSE_FLOAT_VECTOR` 字段 + BM25 函数（服务端内置，**无需外部稀疏模型**）。
- 查询模式 `VectorStoreQueryMode.HYBRID` 触发 `client.hybrid_search`（dense + sparse 两路 `AnnSearchRequest` + RRF/Weighted 融合）。
- BM25 内置函数在 `add()` 时仅存原始 `text`，检索时由 Milvus 服务端实时计算稀疏向量（`_hybrid_search` 中 `BaseMilvusBuiltInFunction` 分支走 `query.query_str`）。
- `retrieve()` 手动预计算 `query_embedding` 并传 `QueryBundle(query_str, embedding)` 的方式，对 HYBRID 模式兼容（`_build_vector_store_query` 同时携带 `query_str` 与 `query_embedding`）。

## 决策

| 项 | 选择 | 理由 |
|----|------|------|
| 稀疏来源 | Milvus 内置 BM25（jieba 中文分词） | 无需额外模型/本地推理，符合设计文档「向量 + BM25」定位 |
| 融合方式 | `RRFRanker`（默认 k=60） | 无需调权重，开箱即用 |
| 接入方式 | 配置开关 `retrieval_hybrid_enabled`（默认 `True`） | 可回退纯向量，便于 A/B 对比与灰度 |
| 阈值语义 | 混合模式下检索阶段**跳过** `SimilarityPostprocessor` | RRF 融合分数非 [0,1] 相似度，阈值过滤交由 rerank 阶段（若启用） |

## 改动范围

| 文件 | 改动 |
|------|------|
| `backend/config.py` | 新增 `retrieval_hybrid_enabled: bool = True` |
| `backend/core/vector_store.py` | `get_vector_store()` 按开关启用 `enable_sparse` + `BM25BuiltInFunction`（jieba） |
| `backend/core/retrieval.py` | `retrieve()` 按开关切换 `HYBRID` / `DEFAULT` 查询模式；混合模式跳过阈值后处理 |
| `docs/plan/hybrid-retrieval-plan.md` | 本文档 |

**不改动**：`services/search_service.py`、`services/chat_service.py`、`routers/*`、`core/indexing.py`（`get_vector_store` 是单一事实来源，索引写入自动获得稀疏 schema）。

## 迁移（前置步骤，不可跳过）

现有 `kb_*` Collection 是**纯稠密 schema**，无法原地升级（新增的 `sparse_embedding` 字段/BM25 函数只能在建 Collection 时写入）。混合检索查询会因「字段不存在」报错。

迁移步骤：
1. 删除现有 `kb_*` Collection（调用既有 `drop_kb_collection(kb_id)`，或一次性删除所有 `kb_` 前缀集合）。
2. 重新上传/索引文档 —— `build_index()` 会按新 schema 自动重建 Collection。

> 删除/索引删除等只读操作不受影响：`_create_index_if_required` 在集合已有索引时直接返回，不会对旧集合强制补稀疏索引。

## 实现步骤（伪代码）

### 1. `config.py`

```python
# === 检索配置 ===
retrieval_hybrid_enabled: bool = True   # 混合检索开关（稠密 + BM25 稀疏融合，RRF）
```

### 2. `core/vector_store.py`

```python
from llama_index.vector_stores.milvus.utils import BM25BuiltInFunction

def get_vector_store(kb_id, dim=None):
    kwargs = dict(
        uri=..., collection_name=f"kb_{safe}", dim=dim or settings.embedding_dim,
        similarity_metric="IP", index_config={...}, search_config={...},
        scalar_field_names=SCALAR_FIELD_NAMES, scalar_field_types=SCALAR_FIELD_TYPES,
        text_key="text", overwrite=False,
    )
    if settings.retrieval_hybrid_enabled:
        kwargs["enable_sparse"] = True
        kwargs["sparse_embedding_function"] = BM25BuiltInFunction(
            input_field_names="text",
            output_field_names="sparse_embedding",
            analyzer_params={"tokenizer": "jieba"},   # 中文分词
        )
    return MilvusVectorStore(**kwargs)
```

### 3. `core/retrieval.py`

```python
from llama_index.core.vector_stores.types import VectorStoreQueryMode

async def retrieve(query, kb_id, top_k=5, similarity_threshold=0.6):
    hybrid = settings.retrieval_hybrid_enabled
    mode = VectorStoreQueryMode.HYBRID if hybrid else VectorStoreQueryMode.DEFAULT
    ...
    retriever = index.as_retriever(
        similarity_top_k=top_k, vector_store_query_mode=mode
    )
    nodes = await retriever.aretrieve(query_bundle)

    # 后处理：仅稠密模式应用相似度阈值（混合模式融合分数非 [0,1]）
    if hybrid:
        filtered_nodes = nodes
    else:
        filtered_nodes = SimilarityPostprocessor(similarity_cutoff=similarity_threshold).postprocess_nodes(nodes)
    ...
    retrieval_metadata = {"method": "hybrid" if hybrid else "vector", ...}
```

## 验证

1. **单元**：`pytest` 全量通过（现有 `test_retrieval.py` / `test_indexing.py` 均 mock `retrieve` / 只测 `chunk_documents`，不受影响）。
2. **迁移**：删除旧 `kb_*` Collection 后，上传一份 PDF，确认日志显示新 Collection 创建 + 数据插入。
3. **检索**：`POST /api/knowledge-bases/{kb_id}/search`，确认：
   - 关键词查询能命中含精确词但语义不近的片段（BM25 生效）。
   - 关闭 `retrieval_hybrid_enabled` 后回退纯向量，行为与改动前一致。
4. **问答**：SSE 流式输出正常，来源引用正常。
5. **可观测**：Langfuse 中 `retrieve` span 的 `method` 字段为 `hybrid`。

## 风险与说明

| 风险 | 说明 | 应对 |
|------|------|------|
| 融合分数非 [0,1] | 无 rerank 时前端展示的 `score` 是 RRF 分数（~0.01~0.03），与旧相似度不可比 | 有 rerank 时分数被 `relevance_score` 覆盖；无 rerank 时仅作排序依据，本次不改动前端 |
| 中文分词效果 | jieba 分词对专业术语（如「建筑工程施工总承包一级」）可能切分过细 | 后续可接入词库/自定义词典（`analyzer_params.dict`），本次不展开 |
| 旧数据迁移 | 需删除重建 Collection | 见「迁移」章节，属一次性操作 |
