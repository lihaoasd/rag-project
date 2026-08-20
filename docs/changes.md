# 修改记录

## 2026-08-19

- 需求：ES 对账页增加详细统计——①集群/索引状态卡、②文档级差异明细（下钻到具体文档含文件名）、④全库三态对账展示。
- 改动：
  - `core/es_store.py`：`count_by_kb` 替换为 `kb_stats`（按库统计切片数+文档数）；新增 `count_by_doc`（按文档统计切片数）、`index_stats`（health/docs/deleted/store/segments）；
  - `repositories/document_repository.py`：新增 `count_chunks_by_doc`、`count_docs_by_kb`、`list_doc_meta`；
  - `services/es_admin_service.py`：`reconcile_status` 重构——返回 `index_stats`、`kbs`（全库三态 ok/missing/residual + pg_docs/es_docs）、`doc_diffs`（文档级差异，上限 500）、`doc_diff_count`；ES 不可达时提前返回（连接问题单独表达，不误报数据差异）；
  - `main.py`：`es_reconcile_loop` 对 ES 不可达单独告警；
  - `frontend/src/views/EsAdminView.vue`：新增索引状态统计卡、全库对账表（三态标签+文档数+按库重建）、文档级差异表、ES 连接异常提示；
  - 测试：`test_es_store` 增 stats 用例、`test_document_repository` 增 3 个统计用例、新增 `test_es_admin_service.py`（对账三态/文档级/ES 不可达）。
- 验证：后端全量测试 460 passed；前端 `npm run build` 通过；线上接口已生效（3 库全部 ok，文档级差异 0）。

## 2026-08-19

- 需求：定时对账保持"仅告警"（不自动 rebuild），前端新增 ES 对账管理页——手动检查对账状态，发现差异后由用户确认再重建索引。
- 改动：新增 `frontend/src/api/esAdmin.js`（`getEsStatus` / `rebuildEs`，支持按 kb 或全部）；新增 `frontend/src/views/EsAdminView.vue`（ES 连接/PG/ES/差异数统计卡片、差异表（缺数据/有残留标注）、"重建该库"与"重建全部"均带确认弹窗、重建后自动刷新对账）；`router/index.js` 新增 `/es-admin` 路由；`AppLayout.vue` 侧边栏新增"ES 对账"入口（`DataAnalysis` 图标）。
- 验证：前端 `npm run build` 通过（EsAdminView 已打包）；后端无改动（复用既有 `/api/es/status`、`/api/es/rebuild` 接口），全量测试 449 passed 不受影响。

## 2026-08-19

- 需求：修复 ES 对账任务异常 `NameError: name 'func' is not defined`（`document_repository.count_chunks_by_kb` 使用 `func.count` 但未导入）。
- 改动：`repositories/document_repository.py` 导入补 `func`；`tests/test_document_repository.py` 新增 `list_chunks_by_kb` / `count_chunks_by_kb` 用例（按现有 MagicMock result 风格）。
- 验证：受影响测试 43 passed；后端全量测试 449 passed。

## 2026-08-19

- 需求：在服务器 192.168.60.120 上通过 Docker 部署自有 Elasticsearch（含 IK 中文分词），后端切换至该 ES。
- 改动：
  - `elasticsearch/Dockerfile`：IK 插件下载源由 GitHub（release 资产已清空，404）改为 **InfiniLabs 官方发布服务器** `https://release.infinilabs.com/analysis-ik/stable/elasticsearch-analysis-ik-8.13.4.zip`（`docs/08` §3.1 同步更新）；
  - `elasticsearch/config/IKAnalyzer.cfg.xml`：补充启动依赖警告（remote_ext_dict 指向的后端不可达会导致 IK 词典初始化中断，需后端先行或清空该 entry）；
  - `backend/.env`：`ES_HOST=192.168.60.120`、清空认证（自建 ES 无鉴权；原 192.168.60.121 的 elastic 认证配置移除）；
  - 服务器部署（192.168.60.120）：`/opt/es`（Dockerfile + IKAnalyzer.cfg.xml，远程词典**当前禁用**——后端未运行，IK 用本地词典）、镜像 `es-ik:8.13.4`、容器 `bid-es`（9200、1g 堆、数据卷 `/opt/es/data`、`--restart unless-stopped`）、`vm.max_map_count=262144`（已达标）、预创建 `bid_chunks` 索引（`ik_index/ik_smart` 分析器）。
- 说明：IK 本地词典工作正常（analyze 验证通过）；词库"分词词典热更新"待后端稳定运行后启用 remote_ext_dict 生效，当前词库仅查询扩展（即时）路径生效。
- 验证：镜像构建成功（analysis-ik 8.13.4 安装）、IK analyze 正常切词、E2E 冒烟通过（写入→检索命中 score 2.59→高亮→清理）；`config.py` 加载新 ES 地址正确；后端测试全量 447 passed。

## 2026-08-19

- 需求：ES 连接改为远程实例（`192.168.60.121:9200`，elastic 认证）；远程 ES 未安装 IK 插件，需保证全文检索可用。
- 改动：`backend/.env` 写入 `ES_HOST/ES_PORT/ES_USERNAME/ES_PASSWORD`（并修复 .env 追加导致的 GBK 编码问题，整体转码 UTF-8）；`core/es_store.py` `ensure_index()` 增加 **IK→cjk 分析器自动降级**（探测 `unknown analyzer` 错误，降级后按 bigram 检索并记录 `_analyzer_mode`），es_search 字段加权改用 `_TEXT_FIELDS` 常量；`tests/test_es_store.py` 补降级/非分析器错误用例与 `_analyzer_mode` 重置 fixture；设计文档新增 §4.3 分析器降级说明。
- 验证：后端测试通过（es_store/search_es 24 passed）；对远程 ES 端到端冒烟通过——`ensure_index` 降级 cjk 建索引、写入、检索命中、高亮 `<em>` 片段正确、删除清理成功。

## 2026-08-19

- 需求：ES + IK 全文检索集成（`docs/08-es-fulltext-search-design.md` + `docs/plan/23-es-fulltext-search-plan.md`）——新增 `fulltext`（ES 纯全文检索，前后端默认）与 `hybrid_es`（向量 + ES 关键词 RRF）检索方式、ES 后端高亮、词库前端管理（IK 远程词典 + 查询扩展）、双写同步与对账重建。
- 改动：
  - 部署：`docker-compose.yml` 新增 `elasticsearch` 服务（8.13.4 + IK 插件 + `host.docker.internal` 远程词典访问）；新增 `elasticsearch/Dockerfile`、`elasticsearch/config/IKAnalyzer.cfg.xml`；
  - 后端：`pyproject.toml` 加 `elasticsearch>=8.0,<9`；`config.py` 新增 ES/检索/对账配置（`retrieval_default_method=fulltext`）；新增 `core/es_store.py`（索引/双写/检索高亮/删除/对账统计）、`models/lexicon.py`（LexiconTerm）、`repositories/lexicon_repository.py`、`services/lexicon_service.py`（CRUD + 缓存 + 查询扩展 + ik-dict/csv 导出）、`services/es_admin_service.py`（对账/重建）、`routers/lexicon.py`、`routers/es_admin.py`；`services/search_service.py` 重构为多路召回（fulltext/hybrid_es + RRF）；`services/document_service.py`/`kb_service.py` 双写与删除/重切/重解析 ES 清理；`repositories/document_repository.py` 补按 kb 统计/列表；`models/schemas.py` SearchRequest 四值 + highlight、SearchResultItem.highlight；`main.py` 注册路由 + 启动 ES 初始化/词库缓存 + 每日定时对账协程；迁移 `0013_lexicon_terms.py`；
  - 前端：`api/lexicon.js` 新增；`api/search.js` 透传 highlight；`SearchView.vue` 检索方式四值下拉（默认 fulltext）+ 后端高亮渲染（`<em>` 样式）；新增 `LexiconView.vue` 词库管理页（CRUD/开关/CSV 导入导出）+ 路由与侧边栏入口；
  - 测试：新增 `test_es_store.py`、`test_search_es.py`、`test_lexicon_service.py`、`test_lexicon_router.py`、`test_es_admin_router.py`，更新 `test_search_service.py`（默认方式固定 vector 聚焦向量路径）、`test_schemas.py`、`test_search_router.py`；`uv sync --extra dev` 补齐 dev 依赖。
- 说明：词库查询扩展仅作用于 ES 检索路径（设计 §6.3"双端一致"未实现，Milvus 侧保持原 query）；IK 远程词典热更新受 IK Monitor 轮询限制（约 1 小时），查询扩展即时生效兜底。
- 验证：后端全量测试通过（445 passed）；前端 `npm run build` 通过；待部署验证（docker compose build elasticsearch + alembic upgrade head + 上传文档三处落库冒烟）。

## 2026-08-19

- 需求：ES + IK 全文检索详细设计文档（`docs/08-es-fulltext-search-design.md`）评审反馈确认——新增 `fulltext` 检索方式并作为前后端默认（原 `keyword` 并入）、词库分类采用自由文本、对账采用手动 rebuild + 每日定时对账告警、ES 数据卷不纳入备份（恢复时从 PG rebuild 重建）。
- 改动：新增 `docs/08-es-fulltext-search-design.md`（ES+IK 部署、索引设计、双写同步、词库前端更新、检索链路 fulltext/hybrid_es + RRF + 高亮、对账与重建、备份恢复策略、实施步骤与验证）；设计文档按评审结论更新（§5.6 备份策略、§7.1 检索方式表、§10 配置、§11 步骤、§12 验证、§14 开放问题全部收口）。
- 验证：文档评审输出，未涉及代码。

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
