# 远程 PaddleOCR 接入 — 开发方案

> 版本: v1.0 | 日期: 2026-08-14 | 状态: 待评审
> 关联: [08-model-config-plan.md](08-model-config-plan.md)（模型配置/工厂预留）、[10-vision-ocr-test-connection-plan.md](10-vision-ocr-test-connection-plan.md)（OCR 测试连接，本方案并入其 OCR 部分）

---

## 1. Context

### 1.1 现状问题

文档解析的 OCR 目前是**本地硬编码**的 PaddleOCR：

- [parser.py](../backend/core/parser.py) `PDFParser._get_ocr()` 直接 `from paddleocr import PaddleOCR` 懒加载本地模型，`_ocr_images_sync()` 同步调用 `ocr.ocr(image_bytes, cls=True)`。
- [model_factory.py](../backend/core/model_factory.py) `create_ocr()` 也返回本地 `PaddleOCR`，但**从未被运行时调用**（parser 绕过了工厂）。
- 模型配置侧 OCR 的 `api_base` / `api_key` / `params` 字段已通用存在，但未接入解析管线。

### 1.2 用户确认的决策

| 决策点 | 结论 |
|--------|------|
| 远程服务接口 | PaddleOCR FastAPI 部署，**base64 JSON** |
| 本地回退 | **仅远程**，移除本地 PaddleOCR 依赖；未配置 OCR 时跳过图片识别（不报错） |
| 测试连接 | **仅连通性探测**（GET `api_base` 根路径，任意 HTTP 响应视为连通） |

### 1.3 远程服务契约

- 请求：`POST {api_base}/ocr`，`Content-Type: application/json`，body `{"file": "<base64 图片字节>"}`
- 响应：`{"success": true, "text": "识别文本", "rec_texts": ["..."]}`，取 `text`，回退 `rec_texts` 拼接
- `api_key` 可选，存在时加 `Authorization: Bearer` 头

---

## 2. 方案设计

### 2.1 远程 OCR 客户端（`model_factory.py`）

新增 `PaddleOCRHttpClient`（同步 `recognize`），与现有 `OpenAICompatibleReranker` 同构（httpx、`api_base.rstrip("/")` + `endpoint` 拼接、`Authorization` 头）：

- `recognize(image_bytes) -> str`：base64 编码 → `POST /ocr` → `raise_for_status()` → 解析文本。
- `_extract_text(data)`：`text` 非空用之；否则 `rec_texts` 为列表则空格拼接；否则返回空串。
- 同步 httpx（`httpx.Client`）：parser 在线程池中调用，阻塞线程池线程不阻塞事件循环。

`create_ocr(config)` 改为返回该客户端，`endpoint` 由 `params.endpoint` 覆盖（默认 `/ocr`，与 rerank 的 `params.endpoint` 一致）。

### 2.2 解析管线接入（`parser.py` + `document_service.py`）

OCR config 在 **document_service 解析**（异步、有 kb_id），通过参数传入 parser，保持 parser 与 `model_factory` 解耦：

```
document_service.parse_document / reparse_document:
    ocr_config = await resolve_model_config(kb_id, "ocr")   # 未配置 → None
    parsed = await parse_file(temp_path, file_type, ocr_config=ocr_config)
```

`PDFParser`：
- `_parse_sync(file_path, ocr_config)`：`ocr_client = create_ocr(ocr_config) if ocr_config else None`，传入 `_ocr_images_sync`。
- `_ocr_images_sync(page, page_num, ocr_client)`：`ocr_client is None → ""`；否则遍历嵌入图片，`img_text = ocr_client.recognize(image_bytes)`。
- 删除 `_get_ocr()` 与 `self._ocr`（本地懒加载）。

### 2.3 OCR 测试连接（`model_service.py`）

`test_model` 增加 `ocr` 分支 → 新增 `_test_ocr_connectivity(config)`：

- 未配 `api_base` → `success=False`，`message="未配置 API 地址…"`。
- `GET api_base`（10s 超时）→ 任意响应 `success=True`，`detail=f"HTTP {status_code}"`。
- `TimeoutException` / `ConnectError` → 失败，分别提示。

---

## 3. 改动范围

| 文件 | 改动 |
|------|------|
| `backend/core/model_factory.py` | 新增 `PaddleOCRHttpClient`；重写 `create_ocr`（移除本地 `PaddleOCR`）；更新模块 docstring |
| `backend/core/parser.py` | `PDFParser` 改为消费 `ocr_config`→`create_ocr`；删除 `_get_ocr`/`self._ocr`；`parse_document` 增加 `ocr_config` 参数；更新 docstring |
| `backend/services/document_service.py` | `parse_document`/`reparse_document` 解析 `resolve_model_config(kb_id, "ocr")` 并传入 `parse_file` |
| `backend/services/model_service.py` | `test_model` 支持 `ocr`；新增 `_test_ocr_connectivity`；import `resolve_api_key` |
| `backend/pyproject.toml` | 移除 `paddleocr`、`paddlepaddle` 依赖 |
| `backend/uv.lock` | `uv lock` 重新生成（移除 paddle 依赖） |
| `backend/tests/test_model_service.py` | 替换 `test_ocr_not_supported`；新增 `TestCreateOcr` + OCR 连通性用例 |
| `docs/plan/10-vision-ocr-test-connection-plan.md` | OCR 测试连接部分标注「已并入 11」 |

不改动：前端（OCR 的 `api_base`/`api_key`/`params` 表单已通用）、`schemas.py`、`llm_factory.py`（`create_ocr` 签名不变，re-export 无需改）。

---

## 4. 关键逻辑伪代码

### 4.1 `PaddleOCRHttpClient` + `create_ocr`

```
class PaddleOCRHttpClient:
    __init__(api_base, api_key=None, endpoint="/ocr", timeout=30):
        api_base = (api_base or "").rstrip("/")

    recognize(image_bytes) -> str:
        b64 = base64.b64encode(image_bytes)
        headers = {Authorization: Bearer api_key} if api_key else {}
        with httpx.Client(timeout) as client:
            resp = client.post(f"{api_base}{endpoint}", json={"file": b64}, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return _extract_text(data)

    _extract_text(data) -> str:
        text = data["text"]            # 非空 → 返回 strip
        rec_texts = data["rec_texts"]  # 列表 → " ".join
        return ""                      # 否则空

create_ocr(config):
    params = config["params"] or {}
    return PaddleOCRHttpClient(
        api_base=config["api_base"] or "",
        api_key=resolve_api_key(config["api_key"]),
        endpoint=params.get("endpoint", "/ocr"),
    )
```

### 4.2 `PDFParser` 改造

```
parse(file_path, ocr_config=None):
    documents = await to_thread(_parse_sync, file_path, ocr_config)

_parse_sync(file_path, ocr_config):
    ocr_client = create_ocr(ocr_config) if ocr_config else None
    for page in pages:
        ...
        ocr_text = _ocr_images_sync(page, page_num, ocr_client)
        ...

_ocr_images_sync(page, page_num, ocr_client):
    if ocr_client is None: return ""
    for img in page.get_images(full=True):
        image_bytes = extract_image(xref)["image"]
        img_text = ocr_client.recognize(image_bytes)
        if img_text: texts.append(f"[图片 {i}] {img_text}")
    return "\n".join(texts)

parse_document(file_path, file_type, ocr_config=None):
    parser = PARSER_REGISTRY[file_type]
    if file_type == "pdf": return await parser.parse(file_path, ocr_config=ocr_config)
    return await parser.parse(file_path)
```

### 4.3 `test_model` OCR 分支 + `_test_ocr_connectivity`

```
test_model(model_type, ...):
    if model_type not in ("llm","embedding","rerank","vision","ocr"): return 暂不支持
    if model_type == "ocr": return await _test_ocr_connectivity(config)
    ...

_test_ocr_connectivity(config):
    api_base = (config["api_base"] or "").strip().rstrip("/")
    if not api_base: return {success:False, message:"未配置 API 地址…"}
    headers = {Authorization: Bearer} if resolve_api_key(api_key) else {}
    try:
        with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(api_base, headers=headers)
        return {success:True, message:"连接成功", latency_ms, detail:f"HTTP {resp.status_code}"}
    except TimeoutException: return 超时
    except ConnectError:    return 连接失败
    except Exception as e:  return 测试失败
```

---

## 5. 验证方式

### 5.1 单元测试（`test_model_service.py`）

| 用例 | 断言 |
|------|------|
| `create_ocr` 解析 config | `.api_base`/`.api_key`/`.endpoint` 正确，`endpoint` 可被 `params.endpoint` 覆盖 |
| `_extract_text` | `text` 优先；回退 `rec_texts` 拼接；空 → `""` |
| ocr 未配 api_base | `success=False`、`message` 含「未配置 API 地址」 |
| ocr 连通成功 | mock `httpx.AsyncClient.get` 200 → `success=True`、`detail` 含 `HTTP 200` |
| ocr 连接失败 | get 抛 `httpx.ConnectError` → `success=False`、`message` 含「连接失败」 |

### 5.2 手工验证

```bash
cd backend && uv run pytest tests/test_model_service.py -q

# 有真实服务时：
curl -X POST http://localhost:8000/api/models/test \
  -H "Content-Type: application/json" \
  -d '{"model_type":"ocr","platform":"paddleocr","model":"ch_pp-ocrv4","api_base":"http://ocr-host:8866"}'
```

---

## 6. 不做什么（本次范围外）

- ❌ 本地 PaddleOCR 兜底（用户明确「仅远程」）
- ❌ OCR 测试连接的「真实识别往返」（用户明确「仅连通性探测」）
- ❌ 请求字段名/响应字段名的运行时动态配置（固定 `file` / `text`+`rec_texts`；仅 `endpoint` 可覆盖，与 rerank 一致）
- ❌ `httpx` 提升为主依赖（现已在 dev 依赖、且 rerank 运行时已在使用；生产缺依赖问题另行处理）
- ❌ 前端改动、`schemas.py`、`llm_factory.py` 改动
