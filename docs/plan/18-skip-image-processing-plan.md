# 文档解析跳过图片处理方案（视觉模型未启用/未配置时）

> 版本: v1.0 | 日期: 2026-08-19 | 状态: 开发中
> 关联: [14-word-image-recognition-plan.md](14-word-image-recognition-plan.md)、[17-model-active-plan.md](17-model-active-plan.md)

## 1. Context

当前 Word 解析链路（14 号方案）中，图片处理分为：提取 → 上传 MinIO → OCR 识别 → Vision 识别 → 生成图片 chunk。
14 号方案的兜底行为是"未配置 OCR/Vision 时图片仍上传 MinIO，图片 chunk 只保留占位内容"。

新需求（用户确认方向）：**视觉模型未启用或未配置时，文档解析完全不处理图片**
——不提取图片字节、不上传 MinIO、不调用识别、不生成图片 chunk。

17 号方案已保证：`resolve_model_config(kb_id, "vision")` 在 vision 未配置或停用（is_active=False）时均返回 `None`，
因此"未启用"与"未配置"两种情况可统一由 `vision_config is None` 判定。

## 2. 行为变化

| 场景 | 现状（14 号方案） | 新行为 |
| --- | --- | --- |
| vision 已配置且启用 | 提取图片 → 上传 MinIO → OCR+Vision → 生成图片 chunk | 不变 |
| vision 未配置或停用（含 OCR 已配置） | 图片仍上传 MinIO，生成占位图片 chunk | **完全不处理图片**：不提取、不上传、不识别、不生成图片 chunk，文本 chunk 无 `[图片 N]` 占位符 |

说明：

- 图片处理开关**只由 vision 配置决定**；vision 缺失时即使 OCR 已配置也不做图片处理（"不处理图片"优先）。
- **PDF 图片 OCR 不受影响**：PDF 的图片识别走 `ocr` 模型（`PDFParser._ocr_images_sync`），与 vision 无关，保持现状。
- 已存在的图片 chunk / MinIO 图片对象不受影响（仅影响新解析）。

## 3. 改动

### 3.1 `backend/core/parser.py`

| 位置 | 改动 |
| --- | --- |
| `DocxParser.parse(file_path, extract_images=True)` | 新增参数并透传给 `_parse_sync` |
| `DocxParser._parse_sync(file_path, extract_images=True)` | `extract_images=False` 时跳过 `_extract_images_from_element`（images 恒为空列表） |
| `DocParser.parse(file_path, extract_images=True)` | 新增参数，转换后透传给 `DocxParser.parse` |
| `parse_document(file_path, file_type, ocr_config, extract_images=True)` | 新增参数；DOCX/DOC 分支透传，PDF 分支忽略（PDF 不走该开关） |

### 3.2 `backend/services/document_service.py`

`parse_document` / `rechunk_document` / `reparse_document` 三处统一：

```
vision_config = await resolve_model_config(kb_id, "vision")
extract_images = vision_config is not None
parsed = await parse_file(temp_path, file_type, ocr_config=ocr_config, extract_images=extract_images)
```

`_parse_and_store_in_batches` 无需改动：images 为空后 `process_word_images` 空转、
`word_chunk_blocks` 不生成图片 chunk，文本 chunk 自然无 `[图片 N]` 占位符。

## 4. 测试方案

- `test_docx_image_extraction.py`：
  - `_parse_sync(docx_path, extract_images=False)` → 所有 item 的 `images` 为空。
  - 现有 `extract_images=True` 默认用例保持不变。
- `test_document_service.py`（可选）：`extract_images` 参数透传（vision_config 为 None → parse_file 收到 False）。
- 前端无改动；后端全量测试 + 前端构建不受影响。

## 5. 验证方式

1. 后端测试通过（pytest）。
2. 重启后端后上传含图片 Word：
   - vision 停用/未配置 → 解析后无图片 chunk、MinIO `images/` 前缀无对象、文本 chunk 无 `[图片 N]`。
   - vision 启用 → 行为与之前一致。
