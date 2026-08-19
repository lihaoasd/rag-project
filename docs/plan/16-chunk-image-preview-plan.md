# 切片图片预览方案：文档详情页展示图片 Chunk

> 版本: v1.0 | 日期: 2026-08-19 | 状态: 开发中
> 关联: [14-word-image-recognition-plan.md](14-word-image-recognition-plan.md)

## 1. Context

Word 图片识别已上线：图片独立生成 `chunk_type="image"` 的 chunk，`chunk_metadata` 中保存
`object_key`（MinIO 对象键）、`mime_type`、`image_index`、`source_chunk_index`。

但前端切片管理页（`ChunkListView.vue`）对图片 chunk 只展示文本
（`[图片 1] / OCR识别：… / 视觉描述：…`），**看不到图片本身**。
14 号方案将"前端图片预览接口"列为本期不做，本次补上。

需求（用户确认）：

- 文档详情（切片列表）页：图片 chunk 显示缩略图，点击可放大查看。
- 切片详情面板：显示原图大图 + OCR / 视觉描述文本。
- 图片访问方式：后端代理接口（MinIO 为内网服务，浏览器不可直达）。

## 2. 现状

| 环节 | 状态 |
| --- | --- |
| image chunk 数据 | 已有（`object_key` / `mime_type` 在 `chunk_metadata`） |
| 切片详情接口 `GET /chunks/{id}` | 已返回完整 `metadata`（含 object_key），但无图片访问 URL |
| 切片列表接口 `GET /chunks` | 只返回 `chunk_type` / `source_chunk_index`，无图片信息 |
| 图片访问接口 | 不存在 |
| 前端展示 | 纯文本，无图片 |

## 3. 接口设计

### 3.1 新增图片下载接口

```
GET /api/knowledge-bases/{kb_id}/documents/{doc_id}/chunks/{chunk_id}/image
```

- 从 PG 读取 chunk，校验 `chunk_type == "image"` 且 `object_key` 非空。
- 从 MinIO 读取图片字节，按 `mime_type` 返回。
- 响应头：`Content-Type: {mime_type}`、`Cache-Control: max-age=3600`。
- 404：chunk 不存在 / 非图片 chunk / object_key 为空 / MinIO 对象不存在。

### 3.2 响应字段扩展

`ChunkResponse`（列表项）与 `ChunkDetailResponse`（详情）均增加：

```json
{
  "image_url": "/api/knowledge-bases/{kb_id}/documents/{doc_id}/chunks/{chunk_id}/image"
}
```

- 仅 `chunk_type == "image"` 且 `object_key` 非空时返回；否则 `null`。
- URL 由后端拼相对路径，前端直接作为 `<img src>` / `<el-image>` 使用。

## 4. 主要改动

### 4.1 后端

| 文件 | 改动 |
| --- | --- |
| `backend/utils/minio_client.py` | 新增 `get_bytes(object_key) -> bytes | None`（`get_object` + `read`） |
| `backend/models/schemas.py` | `ChunkResponse` / `ChunkDetailResponse` 增加 `image_url: str | None` |
| `backend/services/document_service.py` | `_chunk_to_dict` / `_chunk_to_detail_dict` 生成 `image_url`；新增 `get_chunk_image(kb_id, chunk_id) -> tuple[bytes, str] | None` |
| `backend/routers/chunk.py` | 新增 `GET /chunks/{chunk_id}/image` 端点 |

`_chunk_to_dict` / `_chunk_to_detail_dict` 中 `DocumentChunk` 对象已携带 `kb_id` / `doc_id`，
可直接拼接 URL，无需改函数签名。

### 4.2 前端

`frontend/src/views/ChunkListView.vue`：

- 列表"文本预览"列：`chunk_type == "image"` 时显示缩略图
  （`<el-image>`，`src=image_url`，`preview-src-list` 支持点击放大，`fit=contain`），
  下方保留识别文本预览；非图片 chunk 保持现状。
- 详情面板：`selectedChunk.chunk_type == "image"` 且有 `image_url` 时，
  顶部显示大图（`<el-image>` 可缩放），下方展示 `text_content`（OCR/视觉描述）。

## 5. 测试方案

- `backend/tests/conftest.py`：`mock_minio` 增加 `get_bytes` mock。
- `backend/tests/test_document_repository.py`（或 `test_chunk_router.py`）：
  - 列表 / 详情响应对 image chunk 返回 `image_url`，对 text chunk 返回 `null`。
  - 图片下载接口：image chunk 返回字节与 `Content-Type`；text chunk / 不存在 chunk 返回 404。
- 前端：`npm run build` 通过。

## 6. 边界与异常

| 场景 | 处理 |
| --- | --- |
| chunk 为 text 类型 | `image_url = null`，前端不渲染图片 |
| object_key 为空（图片上传失败） | `image_url = null` |
| MinIO 对象不存在 / 读取失败 | 接口返回 404，日志记录 warning |
| 编辑/删除图片 chunk | 现有逻辑不变（删除时已清理 MinIO 对象） |

## 7. 本期不做

- 图片列表单独页面 / 全屏画廊。
- 图片懒加载与分片下载（图片为文档内嵌，尺寸可控）。
- 搜索 / 问答结果中的图片预览（`SearchResultItem` / `ChatSourceItem` 后续按需扩展）。
