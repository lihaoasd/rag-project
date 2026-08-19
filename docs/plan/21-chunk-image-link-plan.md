# 文本 chunk 关联图片方案（OCR 文字进正文 + 图片关联）

> 版本: v1.0 | 日期: 2026-08-19 | 状态: 开发中
> 关联: [20-ocr-text-into-body-plan.md](20-ocr-text-into-body-plan.md)、[16-chunk-image-preview-plan.md](16-chunk-image-preview-plan.md)

## 1. Context

20 号方案后：Word 图片 OCR 文字并入正文分块，不再生成图片 chunk，但 chunk 与图片完全失去关联。
用户补充需求：**OCR 文字继续进正文，同时文本 chunk 要关联图片**（可查看该 chunk 对应的图片）。

用户确认的决策：

- 文本 chunk 的 metadata 记录所属章节内图片的 `object_key`（一个 chunk 可关联多张图）。
- chunk 响应返回 `image_urls` 列表；前端切片列表显示缩略图（点击放大），详情面板显示大图。
- 图片仍上传 MinIO（20 号方案已保留）。

## 2. 改动

### 2.1 `core/word_chunker.py`

`chunk_blocks`：生成文本 chunk 时，将块内图片的 `object_key`（非空）收集到 metadata：

```json
{
  "heading": "...",
  "heading_path": "...",
  "image_keys": ["kb/doc/images/a.png", "kb/doc/images/b.png"]
}
```

`word_chunk_blocks`：metadata `setdefault("image_keys", [])`。

### 2.2 `repositories/document_repository.py`

`batch_create_chunks`：chunk_metadata 增加 `"image_keys": meta.get("image_keys", [])`。

### 2.3 `services/document_service.py`

| 函数 | 改动 |
| --- | --- |
| `_chunk_image_urls(chunk) -> list[str]` | 旧 image chunk（`chunk_type=="image"`）：`[单图 URL]`；文本 chunk：按 `image_keys` 逐项生成 `/chunks/{id}/image/{index}` |
| `_chunk_to_dict` / `_chunk_to_detail_dict` | 增加 `image_urls`（保留 `image_url` 字段向后兼容） |
| `get_chunk_image(kb_id, chunk_id, image_index=0)` | 旧 image chunk 取 `object_key`；文本 chunk 取 `image_keys[image_index]` |

### 2.4 `routers/chunk.py`

- 新增 `GET /chunks/{chunk_id}/image/{image_index}`（按 index 读关联图片）。
- 保留 `GET /chunks/{chunk_id}/image`（index=0 兼容旧接口）。

### 2.5 `models/schemas.py`

`ChunkResponse` / `ChunkDetailResponse` 增加 `image_urls: list[str] = []`。

### 2.6 前端 `ChunkListView.vue`

- 列表：`row.image_urls?.length` 时显示缩略图（第一张，`preview-src-list=image_urls` 点击放大），下方保留正文预览。
- 详情：`selectedChunk.image_urls` 渲染大图列表 + 正文文本。

## 3. 测试

- `test_word_chunker.py`：块含图片 → chunk metadata 含 `image_keys`；无 object_key 的图片不进列表。
- `test_document_service.py`：`_chunk_image_urls`（文本 chunk 多图 URL、旧 image chunk 单图）、`get_chunk_image` 按 index 取图。
- `test_chunk_router.py`：`GET /chunks/{id}/image/{index}` 200/404。
- 前端构建。

## 4. 验证

1. 后端全量 pytest 通过。
2. 重启后端后上传含图片 Word：文本 chunk 的 metadata 含 `image_keys`，列表/详情返回 `image_urls`，缩略图/大图可查看。
