# 视觉 / OCR 模型测试连接 — 开发方案

> 版本: v1.0 | 日期: 2026-08-13 | 状态: 待评审
>
> **状态更新（2026-08-14）**：OCR 运行时远程接入（`create_ocr` 重写 + `parser.py` 改造）已并入 [11-remote-ocr-integration-plan.md](11-remote-ocr-integration-plan.md)。本节仅保留「测试连接 = 连通性探测」结论；下文「不改动 `create_ocr` / 运行时仍走本地 PaddleOCR」已过期。

---

## 1. Context

### 1.1 现状问题

模型配置模块（见 [08-model-config-plan.md](08-model-config-plan.md)）已支持 5 类模型的 CRUD 与「测试连接」按钮，但后端 `model_service.test_model()` 仅实现 `llm / embedding / rerank` 三类，`vision / ocr` 直接返回「该类型模型本期暂不支持在线测试」：

- `create_vision_llm(config)` 为 `raise NotImplementedError` 占位。
- `create_ocr(config)` 仅支持本地 `paddle` 后端，无远程 OCR client。

### 1.2 用户确认的决策

| 类型 | 测试连接方式 |
|------|--------------|
| `vision` | **纯文本连通性**：复用语言模型测试逻辑，发文本「回复 OK」验证 API 地址/Key 可达，不走图像输入通道 |
| `ocr` | **远程连通性**：OCR 为远程 HTTP 服务，仅探测 `api_base` 是否可达 |

---

## 2. 方案设计

### 2.1 vision 测试

`create_vision_llm(config)` 改为返回 OpenAI 兼容 `OpenAILike`（与 `create_llm` 同构）。测试分支与 `llm` 完全一致：发一条文本消息「你好，请回复：OK」，收到非空响应即成功。

### 2.2 OCR 测试

OCR 模型配置（`api_base` + `api_key`）指向远程 HTTP 服务。测试连接做一次 `GET` 请求探测 `api_base` 根路径：

- **成功判定**：收到任意 HTTP 响应（连接已建立），`detail` 返回状态码。
- **失败判定**：连接错误 / 超时（10 秒）。

> 权衡：任意状态码（含 4xx/5xx）都视为「连通」，仅把状态码透传到 `detail`，语义严格对齐「连通性」。若后续需要把 401/403 单独提示为鉴权失败，只需在 helper 内加一行状态码分支。

---

## 3. 改动范围

| 文件 | 改动 |
|------|------|
| `backend/core/model_factory.py` | `create_vision_llm` 改为返回 `OpenAILike`（移除 `NotImplementedError`） |
| `backend/services/model_service.py` | `test_model` 增加 `vision` / `ocr` 分支；新增 `_test_ocr_connectivity` helper |
| `backend/tests/test_model_service.py` | 删除 `test_vision_ocr_not_supported`，新增 vision / ocr 测试用例 |

不改动：`create_ocr`（运行时 OCR 仍走 [parser.py](../backend/core/parser.py) 内联 PaddleOCR，与本次「测试连接」无关）、前端（「测试连接」按钮已通用）。

---

## 4. 关键逻辑伪代码

### 4.1 `create_vision_llm`

```
def create_vision_llm(config):
    from llama_index.llms.openai_like import OpenAILike
    params = config.get("params") or {}
    return OpenAILike(
        model=config["model"],
        api_base=config.get("api_base"),
        api_key=resolve_api_key(config.get("api_key")),
        temperature=params.get("temperature", 0.1),
        is_chat_model=True,
    )
```

### 4.2 `test_model` 分支扩展

```
test_model(model_type, platform, model, api_base, api_key, params):
    config = {...}
    if model_type not in ("llm", "embedding", "rerank", "vision", "ocr"):
        return {"success": False, "message": "未知模型类型"}

    start = perf_counter()
    try:
        if model_type in ("llm", "vision"):
            llm = create_llm(config) if model_type == "llm" else create_vision_llm(config)
            resp = await wait_for(llm.achat([ChatMessage(USER, "你好，请回复：OK")]), 20)
            content = resp.message.content ...
            if not content: return 失败 "模型返回空响应"
            detail = content[:100]
        elif model_type == "embedding": ...   # 不变
        elif model_type == "rerank": ...      # 不变
        elif model_type == "ocr":
            return await _test_ocr_connectivity(config)   # 内部自行计时
        latency = (perf_counter() - start) * 1000
        return 成功 + latency + detail
    except TimeoutError: return 失败 "连接超时（20 秒）"
    except Exception as e: return 失败 f"测试失败: {e}"
```

### 4.3 `_test_ocr_connectivity`

```
async def _test_ocr_connectivity(config):
    api_base = (config.get("api_base") or "").strip().rstrip("/")
    if not api_base:
        return {"success": False, "message": "未配置 API 地址，无法测试远程 OCR 连通性"}
    api_key = resolve_api_key(config.get("api_key"))
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    start = perf_counter()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(api_base, headers=headers)
        latency = round((perf_counter() - start) * 1000)
        return {"success": True, "message": "连接成功",
                "latency_ms": latency, "detail": f"HTTP {resp.status_code}"}
    except httpx.TimeoutException:
        return {"success": False, "message": "连接超时（10 秒）"}
    except httpx.ConnectError as e:
        return {"success": False, "message": f"连接失败: {e}"}
    except Exception as e:
        return {"success": False, "message": f"测试失败: {e}"}
```

---

## 5. 验证方式

### 5.1 单元测试（`test_model_service.py`）

| 用例 | 断言 |
|------|------|
| vision 成功 | mock `create_vision_llm`，achat 返回 "OK" → `success=True`、`detail=="OK"` |
| vision 空响应 | achat 返回 "" → `success=False`、`message` 含「空响应」 |
| ocr 成功 | mock `httpx.AsyncClient.get` 返回 200 → `success=True`、`detail` 含 `HTTP 200` |
| ocr 未配 api_base | api_base=None → `success=False`、`message` 含「未配置 API 地址」 |
| ocr 连接失败 | get 抛 `httpx.ConnectError` → `success=False`、`message` 含「连接失败」 |

### 5.2 手工验证

```bash
cd backend && uv run pytest tests/test_model_service.py -q

# 有真实服务时：
curl -X POST http://localhost:8000/api/models/test \
  -H "Content-Type: application/json" \
  -d '{"model_type":"ocr","platform":"aliyun-ocr","model":"general-ocr","api_base":"http://ocr-host:8080","api_key":"k"}'
```

---

## 6. 不做什么（本次范围外）

- ❌ vision 图像输入通道的真实识别测试（用户选择纯文本连通性）
- ❌ OCR 完整识别流程（POST 图片→返回文字）；本次仅连通性探测
- ❌ 远程 OCR 运行时接入解析管线（`parser.py` 仍用本地 PaddleOCR）
- ❌ OCR 探测路径可配置化（本次固定 GET 根路径）
- ❌ 前端改动（「测试连接」按钮已通用，无需调整）
