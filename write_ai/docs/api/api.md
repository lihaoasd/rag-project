# WriteAI API 约定（MVP）

> 版本：v1.0（2026-08-26）

- Base：`http://127.0.0.1:3000/api`
- 请求/响应：JSON（上传除外，multipart）。
- 错误统一：`{ "error": { "code": "<CODE>", "message": "<中文描述>" } }`
- 状态码：200 / 201 / 204 / 400 / 404 / 409 / 500

## 项目

### GET /projects

项目列表（含每个项目当前状态与阶段摘要）。

### POST /projects

```json
{ "title": "string(必填)", "topic": "string(必填)", "requirements": "string(可选)" }
```

201 → 项目对象。

### GET /projects/:id

项目详情：`{ project, stages: [...], latestArtifacts: {...} }`。

### DELETE /projects/:id

204，级联删除 stages / artifacts / 产物文件目录。

### POST /projects/:id/start

启动/继续流水线。项目已在 running 或 finished → 409 `INVALID_STATE`。审核点被 approve 后由内部 `advance` 自动继续，无需再调用。

### POST /projects/:id/export

```json
{ "format": "markdown" }   // MVP 仅 markdown
```

200 → `{ filename, content }`；项目未 finished → 409。

## 阶段（人工审核）

### GET /projects/:id/stages

阶段列表（按 seq 排序，含产物摘要）。

### POST /projects/:id/stages/:stageId/approve

```json
{ "content": "可选，人工修改后的全文", "note": "可选" }
```

- 仅 `review` 状态可调用，否则 409。
- `content` 提供时先应用人工修改（更新产物与 Markdown 文件）。
- 成功后自动推进后续阶段。

### POST /projects/:id/stages/:stageId/retry

仅 `failed` 状态可调用 → 重新运行该阶段。

### POST /projects/:id/stages/:stageId/review-more

仅 `review` 状态的审校阶段可调用 → 追加一轮审校（生成新的 review 产物并更新草稿）。

## 设置

### GET /settings

返回脱敏配置：API Key 仅显示掩码（`sk-****last4`），不返回明文。

### PUT /settings

```json
{
  "llmBaseUrl": "string",
  "llmApiKey": "string(留空表示不修改)",
  "llmModel": "string",
  "tavilyApiKey": "string(留空表示不修改)",
  "reviewRounds": "number(1-10)",
  "knowledgeDir": "string"
}
```

200 → 脱敏后的设置。

## 知识库

### GET /knowledge

文档清单：`[{ docName, chunks, mtime, status }]`。

### POST /knowledge/upload

multipart，字段 `files`，多文件（md/txt）。200 → `{ indexed, skipped: [{name, reason}] }`。

### POST /knowledge/reindex

全量重索引 `data/knowledge`（先清空再重建）。200 → `{ indexed }`。

## 错误码

| code | 场景 |
| --- | --- |
| VALIDATION_ERROR | 参数缺失/格式错误（400） |
| PROJECT_NOT_FOUND / STAGE_NOT_FOUND | 不存在（404） |
| INVALID_STATE | 非法状态转移（409） |
| LLM_ERROR | LLM 调用最终失败（500，阶段置 failed） |
| SEARCH_ERROR | 搜索失败（500，可降级） |
| INTERNAL | 未归类错误（500） |
