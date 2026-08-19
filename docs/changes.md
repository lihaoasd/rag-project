# 修改记录

## 2026-08-19

- 需求：切片详情面板中关联图片改为缩略图展示，并标注图片个数。
- 改动：`ChunkListView.vue` 详情面板图片区改为"关联图片 + N 张"标注 + 缩略图网格（点击缩略图仍可放大预览全部），列表页缩略图保持不变。
- 验证：前端构建通过。

## 2026-08-19

- 需求：OCR 文字进正文分块的同时，文本 chunk 关联图片（可查看原图）。
- 改动：`word_chunker` 文本 chunk metadata 记录 `image_keys`（章节内已上传图片的 object_key）；`document_repository` 落库 `image_keys`；chunk 列表/详情响应新增 `image_urls`（文本 chunk 多图、旧 image chunk 单图，保留 `image_url` 兼容）；新增 `GET /chunks/{id}/image/{index}` 按下标读取关联图片（旧 `/image` 等价 index=0）；前端切片列表对有关联图片的 chunk 显示缩略图（点击放大预览全部），详情面板显示大图列表。
- 验证：新增 image_keys/image_urls/按 index 取图测试，后端全量测试通过（396 passed），前端构建通过。

## 2026-08-19

- 需求：Word 图片 OCR 文字直接成为文档正文参与分块，chunk 中不显示图片标记；PDF 暂时禁止上传。
- 改动：`word_chunker` 不再生成独立图片 chunk（无 `[图片 N]` 占位符、无 image_refs/source_chunk_index 关联），图片识别文字（OCR 优先、vision 兜底）并入所在逻辑块正文参与分块；`document_service` 批次分块去除 image 索引参数；`file_utils` 白名单移除 `.pdf`，上传 PDF 返回 400。
- 保留：图片仍上传 MinIO 存档（删除文档按 `images/` 前缀清理）；旧 image chunk 的列表/详情/预览/删除兼容逻辑不变。
- 验证：重写图片相关用例（OCR 文字进正文、仅图片块产出、vision 兜底），更新 PDF 拒绝用例，后端全量测试通过（386 passed）。

## 2026-08-19

- 需求：将模型配置中的 pp_ocr（PP-OCRv6）集成到文档解析。
- 改动：`PaddleOCRHttpClient` 支持 `upload_mode`（json 默认 / multipart，由 params 配置），适配 PP-OCRv6 multipart 上传协议；`create_ocr` 读取 `params.endpoint`/`params.upload_mode`；图片识别顺序改为 OCR 优先、vision 仅在 OCR 未识别到文字时兜底；Word 图片处理开关改为"OCR 或 vision 任一配置即处理"；pp_ocr 配置已修正（api_base 拆主机、params 设 endpoint/upload_mode、设为该类型默认）。
- 验证：新增 multipart 模式与 vision 兜底测试，后端全量测试通过（384 passed），并用新 client 端到端调用远程 OCR 服务识别成功。

## 2026-08-19

- 需求：视觉模型未启用或未配置时，文档解析完全不处理图片。
- 改动：`DocxParser`/`DocParser`/`parse_document` 新增 `extract_images` 参数（默认 True）；`document_service` 三处解析入口（解析/重切片/重解析）在 `resolve_model_config(kb_id, "vision")` 返回 None（未配置或停用）时传 `extract_images=False`，即不提取 Word 图片、不上传 MinIO、不调用 OCR/Vision、不生成图片 chunk，文本 chunk 无 `[图片 N]` 占位符；PDF 图片 OCR 不受影响（走 ocr 模型）。
- 验证：`test_docx_image_extraction.py` 新增 `extract_images=False` 用例，后端全量测试通过（381 passed），后端已重启生效。

## 2026-08-19

- 需求：模型配置的模型新增"是否激活启动"——启用/停用开关 + 启动自检。
- 改动：`model_configs` 新增 `is_active` 字段（迁移 0012）；停用的模型不参与解析（绑定指向停用回退默认、停用默认不被解析、不可设为默认、不可绑定，停用默认模型时自动取消默认）；应用启动时对启用模型并发连接自检（15s 超时，失败仅记日志不阻断启动）；前端模型管理页新增状态列开关与对话框"启用"开关。
- 验证：新增 `test_model_factory.py`（resolve 停用过滤）与 `test_model_service.py`（is_active CRUD/校验/自检）用例，后端全量测试通过（380 passed），前端构建通过。

## 2026-08-19

- 需求：文档详情（切片列表）页展示图片 chunk 的关联图片。
- 改动：后端 `minio_client` 新增 `get_bytes`；新增 `GET /chunks/{chunk_id}/image` 图片代理接口（带 Cache-Control）；切片列表/详情响应新增 `image_url` 字段；前端切片列表对图片 chunk 显示缩略图（点击放大），详情面板显示大图 + OCR/视觉描述。
- 验证：新增 `test_document_service.py`（image_url 序列化 + get_chunk_image）与图片下载接口测试，后端全量测试通过（369 passed），前端构建通过。

## 2026-08-18

- 需求：检索页面新增检索选项配置项，支持向量检索与混合检索，后续再扩展其他检索方式。
- 改动：后端 `SearchRequest` / `search_service` / `search router` / `retrieve` 新增并透传 `retrieval_method`、`hybrid_ranker`、`hybrid_weights`；前端检索页新增“检索方式”配置，hybrid 下支持 `RRFRanker` / `WeightedRanker` 与权重调整。
- 验证：新增 schema / service / router 测试，运行后端测试与前端构建。
