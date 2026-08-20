# Langfuse：dense 检索链路细化（含向量化环节）

## 需求

dense 检索目前只记录到 retrieve 级 span（method/kb_id/top_k/命中明细），无法区分「query 向量化（embedding）」与「Milvus 检索」各占多少耗时。需要把链路在 Langfuse 中细化，**向量化作为独立子 span** 可见，并在 retrieve span 记录环节级耗时（embed_ms / search_ms / filter_ms），支撑"dense 检索有时超时"的定位。

## 改动范围

| 文件 | 改动 |
| --- | --- |
| `backend/core/retrieval.py` | `retrieve()` 内用 `client.start_span` 创建嵌套子 span：`embedding`（dense 才创建）、`milvus_search`；retrieve 当前 span 的 metadata 增加环节耗时与模型/维度/集合信息 |
| `backend/tests/test_retrieval.py` | 新增 retrieve 子 span 测试（dense 有 embedding/milvus_search span + 环节耗时；sparse 无 embedding span） |
| `docs/08-es-fulltext-search-design.md` | §7.6 补充 dense 链路子 span 描述 |
| `docs/changes.md` | 追加修改记录 |

## 关键逻辑（伪代码）

```
# core/retrieval.py retrieve()
client = get_client()
... resolve config / vector_store / collection 检查（不变） ...
index = VectorStoreIndex.from_vector_store(...)
retriever = index.as_retriever(...)

if is_sparse:
    query_bundle = QueryBundle(query_str=query)
else:
    t = perf_counter()
    with client.start_span(name="embedding", input=query,
                           metadata={model, dim, api_base}) as span:
        query_embedding = await embed_model.aget_query_embedding(query)
        span.update(output={"embedding_dim": len(query_embedding)})
    embed_ms = ...

t = perf_counter()
with client.start_span(name="milvus_search", input=query,
                       metadata={collection, mode, top_k, similarity_threshold}) as span:
    nodes = await retriever.aretrieve(query_bundle)
    span.update(output={"hit_count": len(nodes)})
search_ms = ...

t = perf_counter()
filtered_nodes = 阈值过滤（现有逻辑）
filter_ms = ...

client.update_current_span(
    input=query,
    metadata={**retrieval_metadata, "embed_ms", "search_ms", "filter_ms",
              "embedding_model", "dim", "collection"},
    output=format_retrieval_observation(hits),
)
```

## 验证方式

1. 新增测试：`pytest tests/test_retrieval.py` 通过（dense/sparse 子 span 断言）。
2. 后端全量测试无回归。
3. Langfuse UI（若启用）：检索 trace 可见 search → _recall_milvus → retrieve → embedding / milvus_search 调用树。
