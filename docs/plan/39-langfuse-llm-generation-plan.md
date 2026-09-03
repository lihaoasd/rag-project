# 方案 39：LLM 调用统一记录到 Langfuse（generation 规范化）

> 日期：2026-09-03 · 状态：已评审（用户确认"全量规范化"）

## 1. 需求

LLM（及模型类）调用需要以 **Langfuse generation** 形式完整记录（model / input / output / usage），
与既有聊天回答（`chat_service._call_llm_stream`）的记录标准对齐。覆盖：

1. **Query 改写** LLM 调用（检索页/问答开关开启时）；
2. **图谱实体抽取** LLM 调用（文档解析后台，目前完全无记录）；
3. **顺带修正观测类型大小写缺陷**：embedding / rerank / chain 等大写类型被 SDK 降级为普通 span。

## 2. 现状与根因

| 调用点 | 现状 | 问题 |
|---|---|---|
| 聊天回答 `_call_llm_stream` | `@observe(as_type="generation")` + `update_current_generation(model/input/output/usage)` | ✅ 达标（参照标准） |
| Query 改写 `rewrite_query` | `@traced_span(..., "GENERATION")` + `update_current_span` | `as_type="GENERATION"` 大写非法 → langfuse v4 SDK **静默降级为普通 span**（observe.py L187-192）；无 model 字段、无 usage |
| 图谱抽取 `_llm_extract_batch` | 无任何观测装饰 | ❌ LLM 调用完全未记录 |
| embedding / rerank | `"GENERATION"` 大写同上 | 降级为普通 span |
| search / rag_qa | `"CHAIN"` 大写 | 降级为普通 span，UI 无 chain 层级 |

根因：langfuse v4 `observe(as_type=...)` 仅接受小写类型
`generation/span/chain/embedding/agent/tool/retriever/evaluator/guardrail`，
大写传入被 SDK 打 warning 并回退 `"span"`。
usage 取数：llama_index openai 系 LLM（OpenAILike）把 token 计数放进
`response.additional_kwargs`（`prompt_tokens/completion_tokens/total_tokens`），流式/非流式一致。

## 3. 改动范围

### 3.1 `core/observability.py`
- `traced_span`：新增**旧大写→SDK 小写映射**（GENERATION→generation、EMBEDDING→embedding、
  CHAIN→chain、SPAN→span 等，未识别值透传），杜绝再次静默降级；其余行为不变。
- 新增 `extract_usage(resp)`：从 llama_index 响应 `additional_kwargs` 提取
  `{prompt_tokens, completion_tokens, total_tokens}`，缺失返回 None。

### 3.2 调用点类型修正（小写显式化，与映射双保险）
- `core/retrieval.py`：`_embed_query` `"GENERATION"`→`"embedding"`；`rerank` `"GENERATION"`→`"generation"`。
- `services/search_service.py`：`search` `"CHAIN"`→`"chain"`。
- `services/chat_service.py`：`_run_rag` `"CHAIN"`→`"chain"`。
- `core/query_rewrite.py`：`rewrite_query` `"GENERATION"`→`"generation"`。

### 3.3 `core/query_rewrite.py` — 改写记录升级为真 generation
伪代码：
```python
@traced_span("query_rewrite", "generation")
async def rewrite_query(query, config, history=None):
    llm = create_llm(config)                  # 失败进 except
    prompt = await _get_rewrite_prompt(query, history_text)
    resp = await llm.acomplete(prompt)
    rewritten = 清洗(resp.text)                # 取首行去引号
    get_client().update_current_generation(   # 每次实际调用都记录
        model=config.get("model"),
        input=prompt,
        output=rewritten,
        usage_details=extract_usage(resp),
    )
    if not rewritten or rewritten == query:
        return query                           # 空/未变化仍回退原词（记录已含输出）
    return rewritten
# except: get_client().update_current_generation(model=…, input=query,
#         level="ERROR", status_message=str(e)) 后 return query
```
注意：原实现空输出/未变化在记录前提前 return；改为**先记录后判定**，保证每次 LLM 调用都有 generation。

### 3.4 `core/graph_extractor.py` — 实体抽取接入观测
- `extract_from_chunks` 加 `@traced_span("entity_extraction", "chain")`（单文档一次 CHAIN，
  便于批次 generation 嵌套），内部开头 `update_current_span(input={"kb_id":…, "chunk_count":…})`。
- `_llm_extract_batch` 加 `@traced_span("entity_extract_llm", "generation")`：
  成功后 `update_current_generation(model=llm_config.get("model"),
  input=[m.content for m in messages], output=原始文本, usage_details=extract_usage(resp))`；
  两次尝试均失败（返回 None）时补记 `level="ERROR", status_message=…`。
- 模块顶部新增 `from langfuse import get_client` 与 observability 导入。

## 4. 测试
- `test_observability.py`：新增 `traced_span` 大小写映射用例（patch 模块内 observe 断言
  as_type）；新增 `extract_usage` 用例（有/无 usage、缺 key）。
- `test_query_rewrite.py`：成功路径断言 `update_current_generation` 收到 model/input/output/
  usage；LLM 异常路径断言 `level="ERROR"`；空/未变化回退不变。
- `test_graph_extractor.py`：`_llm_extract_batch` 成功/失败路径补 get_client 断言
  （patch `core.graph_extractor.get_client`）。
- 回归：既有 retrieval / search_es / chat 的 Langfuse 断言不受影响（span 体不变，仅类型小写化）。

## 5. 验证
- `pytest tests/test_observability.py tests/test_query_rewrite.py tests/test_graph_extractor.py`
  及相关回归 → 全量 `pytest tests`。
- 本地起后端实测：检索页开改写 → langfuse（192.168.60.120:3000）应见
  `search`(chain) → `query_rewrite`(generation，model/usage 齐全)；
  上传文档跑实体抽取 → `entity_extraction`(chain) → `entity_extract_llm`(generation)。

## 6. 风险与备注
- `update_current_span` 对 generation/embedding 类型观测自适应（SDK 按类型取类），
  name/elapsed 补充不受影响；metadata 由装饰器统一写 elapsed_ms（本方案不再在业务侧
  metadata 里放 model，避免被覆盖——model 走 generation 专属字段）。
- 后台队列 worker 若无 trace 上下文，entity_extraction chain 会作为独立 trace 记录（v4 行为）。
- 不涉及 DB/前端改动，无迁移。
