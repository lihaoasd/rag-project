# Langfuse：RRF 融合环节记录

## 需求

多选检索（dense/sparse/fulltext）时 Python 端 RRF 融合（`search_service._rrf_merge`）目前无任何 Langfuse 记录。需补独立 span：输入各路召回命中数、参数 k、输出融合结果，完善检索链路观测（已有：各路召回 span、search 总 span、dense 的 embedding/milvus_search 子 span）。

## 改动范围

| 文件 | 改动 |
| --- | --- |
| `backend/services/search_service.py` | 新增 `_rrf_merge_traced`（@observe 嵌套函数，langfuse v4 无 start_span，沿用 observe 模式）：调用 `_rrf_merge` 并记录 input（per_method_hits）/ metadata（method=rrf, k）/ output（融合命中明细）；`search()` 多选分支改调 `_rrf_merge_traced` |
| `backend/tests/test_search_es.py` | 新增 RRF span 测试（method=rrf + k + 各路命中数 + 融合输出） |
| `docs/08-es-fulltext-search-design.md` | §7.6 补充 RRF 融合 span |
| `docs/changes.md` | 追加修改记录 |

## 关键逻辑（伪代码）

```
# search_service.py
@observe(name="rrf_merge", as_type="retriever", capture_input=False, capture_output=False)
def _rrf_merge_traced(ranked_lists, k=60, method_hits=None) -> list[dict]:
    get_client().update_current_span(input={"per_method_hits": method_hits or {}},
                                     metadata={"method": "rrf", "k": k})
    merged = _rrf_merge(ranked_lists, k)
    get_client().update_current_span(output=format_retrieval_observation(merged))
    return merged

# search() 多选分支
all_hits = _rrf_merge_traced(ranked_lists, k=settings.retrieval_rrf_k, method_hits=per_method_hits)
```

## 验证方式

1. 新增测试：多选检索时 rrf span 记录 method=rrf/k/各路命中数；单选不走 RRF。
2. 后端全量测试无回归。
