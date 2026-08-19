# PP-OCR 模型集成到文档解析方案

> 版本: v1.0 | 日期: 2026-08-19 | 状态: 开发中
> 关联: [11-remote-ocr-integration-plan.md](11-remote-ocr-integration-plan.md)、[18-skip-image-processing-plan.md](18-skip-image-processing-plan.md)、[14-word-image-recognition-plan.md](14-word-image-recognition-plan.md)

## 1. Context

用户在模型配置页配置了 `pp_ocr`（PP-OCRv6 OCR API，`http://192.168.60.121:8866`），要求集成到文档解析。

**诊断结果**（已实测确认）：

| # | 问题 | 详情 |
|---|------|------|
| 1 | `is_default=False` | `resolve_model_config(kb_id,"ocr")` 只取默认/绑定，返回 None → 解析链路拿不到 OCR 配置 |
| 2 | 协议不匹配 | 服务为 **multipart/form-data 上传**（字段 `file`），`PaddleOCRHttpClient` 发送 **JSON base64**（`{"file": b64}`）→ 422 |
| 3 | endpoint 拼接 | 用户 api_base 已是完整端点 `.../ocr/gpu/json`，client 默认拼 `/ocr` → 404 |
| 4 | Word 图片开关 | 上一轮实现"vision 未配置时 Word 完全不处理图片"，即使 OCR 生效 Word 图片仍被跳过 |

**用户确认的处理策略**：有图片优先用 **OCR** 解析成文本；**vision 仅在 OCR 未识别到文字时使用**（OCR 兜底缺失时才调用 vision）。

## 2. 决策

| 项 | 选择 | 理由 |
|----|------|------|
| 协议适配 | `PaddleOCRHttpClient` 增加 `upload_mode`（`json` 默认 / `multipart`），由 `params.upload_mode` 控制 | 向后兼容既有 JSON 服务，显式声明协议 |
| endpoint | 由 `params.endpoint` 配置（已有机制），用户配置拆分为主机 + 路径 | 服务文档确认端点 `/ocr/gpu/json` |
| 解析到配置 | 将 pp_ocr 设为该类型默认（`is_default=True`） | 沿用"绑定优先 > 全局默认"语义，无需改 resolve 逻辑 |
| Word 图片开关 | `extract_images = (ocr_config or vision_config) is not None` | OCR 或 vision 任一配置即处理图片；两者都无才完全跳过（更新 18 号方案行为） |
| 识别顺序 | OCR 优先；`ocr_text` 为空时才调用 vision | 用户明确策略 |
| PDF 图片 | 沿用现有 `ocr_config` 集成（修复协议后自动生效），不引入 vision 兜底 | PDF 图片识别原走 OCR，不扩大范围 |

## 3. 改动

### 3.1 `backend/core/model_factory.py`

`PaddleOCRHttpClient` 支持两种上传模式：

```
recognize(image_bytes):
    if upload_mode == "multipart":
        POST {api_base}{endpoint}  files={"file": ("image.png", image_bytes, "image/png")}
    else:  # json（默认，向后兼容）
        POST {api_base}{endpoint}  json={"file": base64(image_bytes)}
    _extract_text(resp.json())   # 已兼容 text 字段
```

`create_ocr`：`upload_mode=params.get("upload_mode", "json")`。

### 3.2 配置修正（API 更新 pp_ocr）

- `api_base` → `http://192.168.60.121:8866`
- `params` → `{"endpoint": "/ocr/gpu/json", "upload_mode": "multipart"}`
- `is_default` → `true`

### 3.3 `backend/services/image_recognition_service.py`

`_process_image` 识别顺序调整：

```
if ocr_client:  image.ocr_text = ocr_client.recognize(bytes)
if vision_client and not image.ocr_text.strip():
    image.vision_text = vision_client.recognize(bytes)   # 仅 OCR 无结果时兜底
```

### 3.4 `backend/services/document_service.py`

三处解析入口（parse/rechunk/reparse）：`extract_images = (vision_config is not None) or (ocr_config is not None)`。

### 3.5 测试

- `test_model_factory.py`：`PaddleOCRHttpClient` multipart 模式（files 发送、URL 拼接、text 提取）。
- `test_image_recognition_service.py`：OCR 有结果 → vision 不调用；OCR 空 → vision 调用。

## 4. 验证

1. 后端全量 pytest 通过。
2. 重启后端 → 启动自检应包含 pp_ocr（4 个启用模型）。
3. 上传含图片 Word：图片被 OCR 识别成文本生成图片 chunk（vision 停用状态下仍处理）。
4. 上传含扫描图片 PDF：图片 OCR 生效。
5. 前端无需改动。
