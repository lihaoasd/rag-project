# Word 图片识别方案：图片独立 Chunk + 文本 Chunk 关联

> 版本: v1.3 | 日期: 2026-08-18 | 状态: 待评审
> 关联: [11-remote-ocr-integration-plan.md](11-remote-ocr-integration-plan.md)、[08-model-config-plan.md](08-model-config-plan.md)

## 1. Context

当前 Word 解析链路存在两个缺口：

- `DocxParser` 只提取正文段落和表格文本，正文图片没有进入解析结果。
- 远程 PaddleOCR 客户端已接入，Vision 模型只有配置和连通性测试，没有真实图片识别调用点。

本次需求目标是：解析 Word 时提取图片，上传 MinIO，再通过视觉模型和 PaddleOCR 双路识别；图片单独生成 chunk，图片 chunk 内同时包含 OCR 识别内容和视觉描述，并关联到原文本 chunk。

## 2. 默认决策

| 决策点 | 结论 |
| --- | --- |
| chunk 归属 | 图片单独生成一个 chunk，不把识别文本拼进正文 chunk |
| 图片 chunk 内容 | OCR 识别内容和视觉描述放在同一个图片 chunk 中 |
| 文本 chunk | 正文 chunk 中保留 `[图片 N]` 占位符，标识图片位置 |
| 关联方式 | 图片 chunk 通过 `source_chunk_index` 关联原文本 chunk；文本 chunk 通过 `image_refs` 记录关联的图片 chunk |
| 图片地址 | MinIO `object_key` 写入图片 chunk 元数据，不写 signed URL，不拼进 `text_content` |
| 错误处理 | 图片上传或识别失败只记录日志，不阻断文档解析，不写入 chunk |
| 模型缺失 | 未配置 OCR/Vision 时图片仍上传 MinIO，图片 chunk 只保留占位内容 |
| 覆盖范围 | v1 覆盖 Word 正文段落和表格内图片；页眉页脚后续按需扩展 |
| Vision 协议 | 按 OpenAI 兼容 `/chat/completions` 接入，默认传 base64 data URL，也可配置 MinIO presigned URL |

## 3. 总体流程

```text
解析 Word
  -> DocxParser 提取段落、表格、图片
  -> ImageRecognitionService 将图片上传 MinIO
  -> 配置了 OCR 则调 PaddleOCRHttpClient.recognize
  -> 配置了 Vision 则调 VisionHttpClient.recognize
  -> word_chunk 先生成文本 chunk，并保留 [图片 N] 占位符
  -> 再为每张图片生成独立图片 chunk，写入 OCR 和视觉描述
  -> 文本 chunk 与图片 chunk 通过 source_chunk_index / image_refs 关联
  -> build_index 写入 Milvus
  -> batch_create_chunks 将文本内容和元数据落库到 document_chunks
```

MinIO 对象键建议：

```text
{kb_id}/{doc_id}/images/{uuid}.{ext}
```

### 3.1 文本 chunk 示例

```text
chunk_index: 2
text:
## 3.1 技术方案

正文段落... [图片 1]

metadata:
  chunk_type: text
  image_refs:
    - image_index: 1
      image_chunk_index: 5
```

### 3.2 图片 chunk 示例

```text
chunk_index: 5
text:
[图片 1]
OCR识别：投标报价：100万元
视觉描述：这是一张报价表，包含项目名称和投标金额。

metadata:
  chunk_type: image
  source_chunk_index: 2
  image_index: 1
  object_key: kb_xxx/doc_xxx/images/xxx.png
  mime_type: image/png
  heading_path: 3.1 技术方案
```

## 4. 数据模型

新增 `document_chunks.metadata JSONB`，用于保存 chunk 类型、图片关联、图片地址等信息。

```sql
ALTER TABLE document_chunks
    ADD COLUMN metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
```

文本 chunk 元数据示例：

```json
{
  "chunk_type": "text",
  "image_refs": [
    {
      "image_index": 1,
      "image_chunk_index": 5
    }
  ],
  "heading_path": "3.1 技术方案"
}
```

图片 chunk 元数据示例：

```json
{
  "chunk_type": "image",
  "source_chunk_index": 2,
  "image_index": 1,
  "object_key": "kb_xxx/doc_xxx/images/xxx.png",
  "mime_type": "image/png",
  "heading_path": "3.1 技术方案"
}
```

说明：

- `text_content` 保存最终检索文本；文本 chunk 保存正文，图片 chunk 保存 OCR 与视觉描述。
- `metadata` 保存关联和图片地址，不参与 embedding。
- 不保存 signed URL；需要展示图片时根据 `object_key` 动态生成 presigned URL。
- 图片识别错误不写入 chunk。

## 5. 核心改动

### 5.1 Word 图片提取

`backend/core/parser.py`：

- `ParagraphItem` 增加 `images: list[WordImage]`。
- 新增 `WordImage`，包含原始字节、MIME、`object_key`、OCR 文本、视觉描述。
- 从 `w:drawing` 的 `a:blip` 和旧式 `v:imagedata` 提取图片关系，通过 `doc.part.rels[rid].target_part.blob` 取字节。
- 遍历 `w:tbl` 时同时提取表格单元格内图片。
- 修复“只有图片、没有文本”的段落被 `continue` 跳过的问题。

### 5.2 图片上传与识别

新增 `backend/services/image_recognition_service.py`：

- `upload_bytes(object_key, data, content_type)` 写入 MinIO。
- 对每张图片依次执行：上传 -> OCR -> Vision。
- OCR 和 Vision 分别 try/except；识别失败只记日志，继续处理下一张图。
- 识别成功后把 OCR 文本、视觉描述写回 `WordImage`。

`backend/core/model_factory.py`：

- 新增 `OpenAICompatibleVisionClient`，复用 httpx 同步客户端模式。
- 默认请求 `POST {api_base}/chat/completions`，消息体使用 OpenAI 多模态格式。
- prompt 从模型配置 `params.prompt` 读取，默认面向投标文档识别。
- 支持 `params.image_input=url` 时改用 MinIO presigned URL。

`backend/utils/minio_client.py`：

- 新增 `upload_bytes`。
- 可选新增 `presigned_get_url`，用于后续前端预览或 Vision URL 模式。

### 5.3 Chunk 生成与关联

`backend/core/word_chunker.py`：

- `LogicalBlock` 增加 `images`。
- 先生成文本 chunk，正文中插入 `[图片 N]` 占位符。
- 再为每张图片生成独立 chunk：
  - `text` 同时包含 OCR 识别内容和视觉描述。
  - `metadata.chunk_type = "image"`。
  - `metadata.source_chunk_index` 指向原文本 chunk。
- 文本 chunk 的 `metadata.image_refs` 记录关联的图片 chunk 索引。
- 图片识别失败时只保留 `[图片 N]` 占位内容，不写入错误。

### 5.4 索引与落库

`backend/core/indexing.py`：

- Milvus `TextNode.metadata` 增加 `chunk_type`、`source_chunk_index`、`image_refs`、`object_key`。
- 图片 chunk 与文本 chunk 共用同一个 Collection。

`backend/models/document.py`、`backend/repositories/document_repository.py`：

- `DocumentChunk` 增加 `metadata JSONB`。
- `batch_create_chunks` 从 metadata 读取 `chunk_type`、`source_chunk_index`、`image_refs` 等字段并落库。

`backend/models/schemas.py`、`backend/services/document_service.py`：

- `ChunkDetailResponse` 返回 `metadata`。
- `ChunkResponse` 可增加 `chunk_type`、`source_chunk_index` 等摘要字段。

`backend/core/retrieval.py`、`backend/models/schemas.py`：

- `SearchResultItem` 增加 `chunk_type` 和 `source_chunk_index`，前端可区分图片 chunk 并回看原文本 chunk。

## 6. Chunk 组织方案对比

### 6.1 方案 1：并入原逻辑块，纯文本

图片识别文本拼进所属段落 chunk。

- 优点：改动最小，无需新增字段。
- 缺点：图片内容与正文混在一起，检索噪声高，无法做图片级管理。
- 结论：不采用。

### 6.2 方案 2：并入原逻辑块，文本 + 结构化字段

文本与结构化图片信息并存。

- 优点：可展示和审计。
- 缺点：双写一致性成本，向量检索仍依赖扁平文本。
- 结论：不采用。

### 6.3 方案 3：图片独立 chunk，无关联

每张图片单独成 chunk，但不关联原文本 chunk。

- 优点：图片内容聚焦，检索命中清晰。
- 缺点：丢失上下文，无法回看图片所在正文。
- 结论：不采用。

### 6.4 方案 4：图片独立 chunk + 关联原文本 chunk（采用）

文本 chunk 保留图片占位符，图片 chunk 保存 OCR 与视觉描述，并通过 `source_chunk_index` 关联。

- 优点：图片内容独立可检索，上下文通过关联保留，支持图片预览和图片级管理。
- 缺点：chunk 数量增加，需要维护关联元数据，索引和落库逻辑需要扩展。
- 结论：本次采用。

### 6.5 识别文本合并规则

图片 chunk 内部统一组织：

```text
[图片 N]
OCR识别：<OCR 原文>
视觉描述：<Vision 结构化描述>
```

- OCR 非空时，优先保留 OCR 原文，适合精确文字检索。
- Vision 非空时，作为“视觉描述”追加，适合语义检索和图/表理解。
- 只有一路成功，就只保留成功那一路。
- 两路都失败，chunk 中只保留 `[图片 N]`，具体失败只记日志，不写入 chunk。

## 7. 错误处理

- 图片上传、OCR 或 Vision 调用失败时记录 `logger.warning`，继续处理后续图片。
- 图片级错误不写入 chunk，也不改变文档状态；文档最终仍按解析结果进入 `completed` 或 `failed`。
- 文档级 `error_message` 仍只保留致命错误，例如文件损坏、解析结果为空。

## 8. 清理策略

| 操作 | MinIO 清理 |
| --- | --- |
| 删除文档 | `delete_prefix("{kb_id}/{doc_id}/images/")` |
| 重新解析/重新切片 | 处理前或成功后删除同一前缀，避免孤儿对象 |
| 删除单条图片 chunk | 从该 chunk 的 `metadata.object_key` 删除对应图片对象 |
| 删除知识库 | 现有 `delete_prefix("{kb_id}/")` 已覆盖图片对象 |

## 9. 测试方案

- `test_docx_image_extraction.py`：构造含正文图片、图片-only 段落、表格内图片的 docx，断言提取结果。
- `test_image_recognition_service.py`：mock MinIO、OCR、Vision，覆盖识别成功、单路失败继续处理、上传失败只记日志。
- `test_word_chunker.py`：验证文本 chunk 与图片 chunk 同时生成，`source_chunk_index` / `image_refs` 关联正确。
- `test_document_repository.py`：验证 `metadata JSONB` 落库。
- `test_indexing.py`：验证 Milvus metadata 包含 `chunk_type` 和关联字段。
- 手工验证：解析含图片 Word，检查 MinIO 对象、文本 chunk 占位符、图片 chunk 内容及关联关系。

## 10. 实施顺序

1. 实现 DocxParser 图片提取。
2. 实现 MinIO `upload_bytes` 和 ImageRecognitionService。
3. 实现 Vision 多模态客户端。
4. 修改 word_chunker，生成文本 chunk 和图片 chunk，并建立关联。
5. 修改 indexing、repository、schema、document_service。
6. 补清理逻辑。
7. 补齐单元测试和手工验证。

## 11. 本期不做

- PDF 图片上传 MinIO 和 Vision 双路识别（后续可复用同一模型）。
- 图片识别错误保存到 chunk。
- signed URL 落库。
- 前端图片预览接口。
- 同图多次引用去重。
- 多图片并行识别和限流参数调优。
