# 大文档批次解析方案：视觉识别分批 + 进度 + 耗时

> 版本: v1.0 | 日期: 2026-08-19 | 状态: 开发中

## 1. Context

当前 Word 解析会先处理整篇文档的图片识别，再统一分块、向量化和入库。文档图片较多时，视觉识别耗时长，用户必须等全部识别完成才能看到第一批 chunk。

目标：

- 不处理多文档并发。
- 单篇大文档按逻辑块分批处理。
- 每批完成 OCR / Vision、生成 chunk、写 Milvus、写 PG 后立即提交。
- 记录已入库 chunk 数量。
- 记录整篇解析完成耗时。

## 2. 总体流程

```text
解析 Word
  -> 提取段落、表格、图片字节
  -> aggregate_paragraphs 得到 LogicalBlock 列表
  -> 按 parse_batch_size 分批
       -> 本批图片：MinIO 上传 + OCR + Vision
       -> word_chunk_blocks 生成文本/图片 chunk
       -> build_index 写入 Milvus
       -> batch_create_chunks 写入 PG
       -> 更新 processed_chunk_count
  -> 全部批次完成
  -> status = completed
  -> 写入 parse_duration_ms
```

PDF 不涉及图片视觉识别，但同样按 `parse_batch_size` 分批写入 Milvus 和 PG。

## 3. 批次索引规则

- `chunk_index` 全局连续，跨批次不重复。
- `image_index` 全局连续。
- 文本 chunk 的 `image_refs.image_chunk_index` 使用全局图片 chunk 下标。
- 图片 chunk 的 `source_chunk_index` 指向全局文本 chunk 下标。
- 跨批次图片只关联文本时，使用上一个已入库文本 chunk 下标。

## 4. 数据模型

`documents` 新增：

```sql
ALTER TABLE documents
    ADD COLUMN processed_chunk_count INTEGER NULL,
    ADD COLUMN parse_duration_ms INTEGER NULL;
```

`processed_chunk_count` 表示当前已提交的 chunk 数，用于前端进度展示。
`parse_duration_ms` 表示整篇文档从开始解析到最终完成的耗时。

## 5. 配置

```text
PARSE_BATCH_SIZE=16
```

Word 按逻辑块分批；PDF 按最终 chunk 分批。图片多、Vision 慢时可降到 8。

## 6. 失败语义

- 每批独立 commit，已提交批次保留。
- 未提交批次丢弃，文档状态置为 `failed`。
- 失败时保留 `processed_chunk_count` 和已运行耗时。
- 重新解析/重新切片前清理旧 chunk、Milvus 向量和 MinIO 图片。

## 7. 主要改动

- `backend/core/word_chunker.py`：新增 `word_chunk_blocks`，支持起始 chunk/image 下标和文本 chunk 偏移。
- `backend/services/document_service.py`：Word/PDF 解析均改为批次循环，并记录进度和耗时。
- `backend/services/image_recognition_service.py`：支持按 LogicalBlock 批次处理图片。
- `backend/models/document.py`、migration：新增 `processed_chunk_count`、`parse_duration_ms`。
- `backend/repositories/document_repository.py`：`update_status` 支持进度和耗时字段。
- `backend/models/schemas.py`：文档响应增加进度和耗时。
- 前端：解析中显示“已入库 chunk / 解析耗时”。

## 8. 测试

- 批次 chunk_index / image_index 连续。
- 跨批次图片关联正确。
- 第二批失败时第一批已提交。
- `processed_chunk_count` 正确更新。
- `parse_duration_ms` 在完成和失败时写入。
- 重新解析清理旧数据后批次重跑。
