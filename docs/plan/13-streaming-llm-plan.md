# 问答真流式改造方案（LLM 逐 token 输出）

## 背景

当前问答链路是「假流式」：`_call_llm` 用 `llm.achat()` 一次性拿到完整回答，再由
`chat_stream` 按 10 字符切块模拟流式。前端表现为「等待完整生成后一次性/快速蹦出」，
不是边生成边显示。本次改造为 LLM 逐 token 的真流式。

## 改动范围

仅 `backend/services/chat_service.py` 一个文件。不新增依赖，不改前端、不改 schema。

## 关键接口事实（已核实）

- `OpenAILike.astream_chat(messages)` 返回 `AsyncGenerator[ChatResponse, None]`，
  每个 `ChatResponse.delta` 是增量字符串。
- Langfuse v4 `@observe` 装饰 async generator 时：span 在 generator **完整消费后**才 end，
  `capture_output=False` 时不覆盖我们在函数体内手动写入的 output（`update_current_generation`）。
- 现有测试只覆盖纯函数与 router 层（mock 掉 `chat_stream`），本次改动不破坏它们。

## 实现步骤

### 1. `_call_llm` → `_call_llm_stream`（async generator，generation span）

伪代码：

```
@observe(as_type="generation", capture_input=False, capture_output=False)
async def _call_llm_stream(llm, messages, model):
    try:
        full = []
        async for resp in llm.astream_chat(messages=messages):
            delta = resp.delta or ""
            if delta:
                full.append(delta)
                yield delta
        get_client().update_current_generation(model=model, input=messages, output="".join(full))
    except Exception as e:
        get_client().update_current_generation(model=model, input=messages, level="ERROR", status_message=str(e) or type(e).__name__)
        raise
```

失败仍记录 generation（ERROR），保持与现在 `_call_llm` 一致的可追溯性。

### 2. `_run_rag` → async generator（yield token），保留 `@observe(name="rag_qa")`

伪代码：

```
@observe(name="rag_qa", capture_input=False, capture_output=False)
async def _run_rag(kb_id, message, conv_id, history, result=None):
    with propagate_attributes(session_id=conv_id, metadata={"kb_id": kb_id}):
        get_client().update_current_span(input=message)
        # 检索 + Prompt 构建（与现状一致，不动）
        retrieved = await retrieve_and_rank(...)
        ...
        llm = create_llm(llm_config)
        full = []
        async for delta in _call_llm_stream(llm, llm_messages, llm_config["model"]):
            full.append(delta)
            yield delta
        get_client().update_current_span(output="".join(full))
        if result is not None:
            result["retrieved"] = retrieved
```

- `_run_rag` 保持为 async generator，使 `rag_qa` span 覆盖到 LLM 流式结束；
  `_call_llm_stream` 的 generation span 自动成为其子 span，`propagate_attributes` 的
  `session_id` 正常下发。
- `retrieved` 通过调用方传入的可变 `result` dict 传回（async generator 无法用 return 传值）。

### 3. `chat_stream` 消费流式

伪代码：

```
result = {}
full_parts = []
try:
    async for delta in _run_rag(kb_id, message, conv_id, history, result):
        full_parts.append(delta)
        yield _sse_event("token", content=delta)
except RuntimeError as e:      # 模型配置失败
    get_client().flush()
    yield _sse_event("error", message=f"模型配置错误: {e}")
    return
except Exception as llm_err:   # LLM 流式中途失败
    get_client().flush()
    yield _sse_event("error", message=f"LLM 调用失败: {llm_err}")
    return

retrieved = result.get("retrieved")
full_response = "".join(full_parts)
# 后续：保存 AI 回复、sources 事件、done 事件（与现状一致，仅去掉 10 字符切块）
```

## 权衡

- **usage 记录**：现在 `_call_llm` 也不记 token 用量，本次保持一致，不新增（避免投机性开发）。
- **retrieved 传回**：用 `result` dict 作为 out-param，简单直白；相比 yield 复合事件或
  `asend()` 双向通信更贴合项目现有代码风格。
- **span 层级不变**：`rag_qa` → `retrieve`/`rerank` + `_call_llm_stream`(generation)。

## 验证方式

1. `uv run pytest tests/test_chat_service.py tests/test_chat_router.py -q` 通过。
2. 全量 `uv run pytest -q` 通过。
3. 手工验证（可选）：前端提问，观察回答逐 token 出现，而非等待后一次性蹦出；
   Langfuse 中 `rag_qa` span 下 generation span 的 output 为完整回答。
