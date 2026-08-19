# Word 图片 OCR 文字并入正文分块方案

> 版本: v1.0 | 日期: 2026-08-19 | 状态: 开发中
> 关联: [14-word-image-recognition-plan.md](14-word-image-recognition-plan.md)、[19-ocr-integration-plan.md](19-ocr-integration-plan.md)

## 1. Context

当前 Word 图片识别生成**独立图片 chunk**（`chunk_type="image"`，文本 `[图片 N] / OCR识别：… / 视觉描述：…`），
文本 chunk 保留 `[图片 N]` 占位符。用户要求修改：

- **chunk 中不再显示图片相关标记**（不生成图片 chunk、无 `[图片 N]` 占位符）。
- **OCR 识别出的文字直接成为文档正文内容，参与正常分块**。
- vision 兜底：OCR 未识别到文字时，vision 识别文字同样并入正文（用户确认）。
- 图片仍上传 MinIO 存档（用户确认），删除文档时按 `{kb_id}/{doc_id}/images/` 前缀清理。
- **禁止上传 PDF**（用户确认）：上传 PDF 直接 400 拒绝。

## 2. 行为变化

| 场景 | 现状 | 新行为 |
| --- | --- | --- |
| Word 含图片 | 独立 image chunk + 文本 chunk 带 `[图片 N]` | 图片识别文字并入所在逻辑块正文，普通 text chunk |
| 图片无识别文字 | 占位图片 chunk | 该图片无文本并入（正文不含图片信息） |
| PDF 上传 | 允许 | **400 拒绝（暂不支持 PDF 解析）** |

旧数据：已存在的 image chunk 不受影响（列表/详情/预览/删除兼容逻辑保留）。

## 3. 改动

### 3.1 `core/word_chunker.py`

`chunk_blocks`：逻辑块正文组装时，将每张图片的识别文字并入正文
（`ocr_text` 优先，空则 `vision_text`），去掉 `[图片 N]` 占位符与 `image_refs`。

`word_chunk_blocks`：删除 image chunk 生成（`image_nodes`、`image_index_to_chunk_index`、
`source_chunk_index`/`image_refs` 关联）；参数去掉 `start_image_index`、`text_chunk_offset`、
`last_text_chunk_index`，保留 `start_chunk_index`（批次间全局索引）。metadata 不再输出
`chunk_type="image"`、`object_key`、`image_index`、`image_refs`。

伪代码：

```
chunk_blocks(blocks):
    for block in blocks:
        body_parts = [block.heading] + block.paragraphs
        for image in block.images:
            text = image.ocr_text 或 image.vision_text（兜底）
            if text.strip(): body_parts.append(text)
        body_parts += block.tables
        ...（后续不变）

word_chunk_blocks(blocks, doc_id, file_name, start_chunk_index=0):
    # 仅文本 chunk；chunk_index 全局连续
```

### 3.2 `services/document_service.py`

`_parse_and_store_in_batches` Word 分支：调用 `word_chunk_blocks` 时去掉 image 相关参数
（`start_image_index`/`text_chunk_offset`/`last_text_chunk_index`），删除 `current_image_index` 维护。

**保留**（兼容旧数据）：`_chunk_image_url`/`get_chunk_image`（图片预览接口）、
`delete_chunk` 中 image chunk 的 MinIO 对象清理、`_cleanup_doc_images`。

### 3.3 `utils/file_utils.py` + `routers/document.py`

`ALLOWED_EXTENSIONS` 移除 `.pdf`（`validate_file_type` 返回 False → 上传 400"不支持的文件格式"）。
更新报错文案为 "支持: docx, doc"。

### 3.4 测试

- `test_word_chunker.py`：重写图片相关用例——识别文字并入正文（无 `[图片 N]`、无 image chunk）；
  仅图片的块 → 识别文字成为 text chunk；OCR 空 vision 兜底文字进正文。
- `test_file_utils.py`：pdf 不在允许列表。
- `test_document_router.py`：上传 .pdf → 400。
- `test_document_service.py`：image_url 相关保留（兼容旧数据语义不变）。

## 4. 验证

1. 后端全量 pytest 通过。
2. 重启后端后上传含图片 Word：无 image chunk，OCR 文字出现在 text chunk 正文中；
   MinIO 仍存图片对象；删除文档时 `images/` 前缀清理。
3. 上传 PDF → 400 拒绝。
