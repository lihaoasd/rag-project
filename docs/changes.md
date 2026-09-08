# 修改记录

## 2026-09-08（v2 · 分支 v2-generic-kb · 库 ai_bid_rag_v2）

- v2 里程碑：v1 封板（tag v1.1.0）；新建 v2-generic-kb 分支与新库 ai_bid_rag_v2（迁移至 0023，10 表）。设计 `docs/10-generic-kb-v2-design.md`、开发方案 `docs/plan/47-domain-config-plan.md` 已按推荐默认定稿（§14/§1.1 默认采纳：内置通用+投标预设、存量不搬移、预设只读可复制、不做每类开关）。
- M1 后端数据层（方案 Step 1）：
  - 新增 `core/domain_presets.py`：领域模板种子单一事实源（通用 9 实体/12 关系、投标 9 实体/15 关系，含 code/中文名/颜色/描述/示例与方向提示）+ 校验常量（ENTITY_CODE_RE、数量边界、保留标签 Entity/Document）；
  - 新增 `models/domain.py`（DomainProfile：UTF8JSONB 存 entity_types/relation_types，is_preset 标记内置模板）；`models/knowledge_base.py` 加 `domain_profile_id` 列（无外键，应用层校验）；`models/__init__.py` 导出；
  - 迁移 `0024_domain_profiles.py`：建表 + KB 加列 + 索引 + 种子（固定 UUID 00000000-…-0001/0002，JSON ensure_ascii=False）；
  - 新增 `repositories/domain_repository.py`：模板 CRUD + count_kb_refs/list_kb_ids_by_profile（删除前引用校验）+ bind_kb（KB 绑定/换绑）。
  - 验证：迁移在 ai_bid_rag_v2 执行 exit 0；冒烟确认 alembic_version=0024、种子（通用 9/12、投标 9/15，UTF-8 精确匹配=2）、knowledge_bases.domain_profile_id 列存在；新模块导入自检通过。
- M2 模板服务（方案 Step 2）：
  - `core/domain_presets.py` 种子 ID 上收为常量（GENERIC/BID_PRESET_ID），迁移 0024 改从该模块引入（单一事实源）；
  - `repositories/domain_repository.py` 补 `get_kb_domain_profile_id`；
  - 新增 `services/domain_service.py`：模板紧凑结构转换（entity_codes/meta、relation_names/meta）、`validate_profile`（数量/正则/保留标签/去重/颜色校验）、`build_extraction_prompt`（schema 约束提示词由模板生成）、`profile_meta`（前端 meta 视图）、缓存（ensure/refresh/invalidate）+ `get_profile`/`get_profile_for_kb`（未绑定/失效回退内置通用预设，图谱链路永不空）；
  - 新增 `tests/test_domain_repository.py`（CRUD/引用计数/绑定 16 用例）与 `tests/test_domain_service.py`（校验/提示词/预设完整性 16 用例）。
  - 验证：pytest 两文件 **32 passed**（5.2s）。
- M3 抽取与写图动态化（方案 Step 3）：
  - `models/graph_schemas.py`：`ExtractedEntity.type` 由固定 Literal 改为 `str`——类型合法性移到抽取层按领域模板动态校验（模板 code 创建时已过正则/保留标签校验，无注入面）；
  - `core/graph_extractor.py`：删除 `ENTITY_TYPE_LABEL_MAP`/`FALLBACK_ENTITY_EXTRACTION_PROMPT`/DB `entity_extraction` 模板读取；提示词改由模板原始清单实时生成（`build_extraction_prompt`）；词库/正则/LLM 三类实体均按模板 code 白名单过滤，关系按模板关系名过滤（类型过滤前置于入桶，防跨领域污染）；`extract_from_chunks(kb_id, chunks, profile=None)`，None → 通用预设兜底；
  - `services/domain_service.py`：模板紧凑结构携带原始有序清单（`entity_types`/`relation_types`，供提示词生成）；新增公开 `default_profile()`；
  - `services/graph_service.py`：删除模块级 `RELATION_TYPES`/`_ENTITY_LABELS` 常量；`_build_write_statements` 增参 `entity_codes`/`valid_relations`（按 KB 模板校验）；关系端点类型缺失不再默认补 `"Event"`（v1 遗留，缺失即丢弃）；`build_file_graph`/`update_entity` 经 `domain_service.get_profile_for_kb(kb_id)` 动态解析模板；
  - 用例重构：`test_graph_extractor.py`（提示词来源改为模板断言 + 模板外类型丢弃新用例）、`test_graph_service.py`（写语句参数化 + 端点类型缺失回归）、`conftest.py` 增补 `services.domain_service.async_session` mock。
  - 验证：图谱相关 4 文件 pytest **42 passed**（3.3s）；`main` 全应用导入通过。
- M4 API 与联动失效（方案 Step 4）：
  - `models/schemas.py`：KB 建/响应携带 `domain_profile_id`；新增领域模板 Schema 族（实体/关系类型、创建/更新/详情/列表/预览/绑定请求）；
  - `repositories/kb_repository.py`：create 支持 `domain_profile_id`；`services/kb_service.py`：创建省略绑定默认内置『通用』预设，`_kb_to_dict` 输出模板 id，删除 KB 时联动清空 Neo4j 图谱（补齐 v1 缺口，best-effort）；
  - `services/domain_service.py`：新增 `DomainValidationError`/`DomainInUseError`；`list_profiles`/`create_profile`/`update_profile`/`delete_profile`/`copy_profile`/`count_profile_refs`/`bind_kb_profile`/`reset_kb_graphs_of_profile`；模板紧凑结构携带 description；模板编辑/换绑 → 引用 KB 图谱状态批量失效；
  - `routers/domain.py`（新）：`/api/domains` 列表/创建/详情/整包更新/删除/复制 + `/preview` 实时校验并生成提示词（预设只读可复制、删除被引用模板 409）；
  - `routers/knowledge_base.py`：创建 KB 显式绑定校验、`GET/PUT /{kb_id}/domain` 换绑（本体变化 → 图谱状态失效）；`routers/graph.py`：`GET /graph/meta` 返回生效模板 meta（画布图例/实体下拉单一来源）；`main.py` 注册 domain 路由；
  - 用例：`test_domain_service.py` 增模板操作 8 例、`test_kb_service.py` 增 KB 删除联动清图 1 例、`helpers.py` MockKB 增 domain_profile_id。
  - 验证：KB/图谱/领域相关 pytest **103 passed**；真实 v2 库服务级冒烟通过（默认绑通用 → 自定义 CRUD → 预设复制/保护 → 换绑投标 → 引用保护 409 → 删除，及预览校验/提示词）。
- M5 前端（方案 Step 5）：
  - `services/domain_service.profile_meta` 的实体项补充 examples（编辑器回填/提示词预览用，单一来源）；
  - 新 `api/domain.js`（模板 CRUD/复制/预览 + KB 领域 GET/PUT）；`api/graph.js` 增 `getGraphMeta`；
  - 新 `DomainConfigView.vue`：领域模板列表（内置/自定义标识 + 实体/关系数）+ 编辑器（动态增删实体/关系行、颜色选择器、示例/方向提示、实时「校验/预览」面板展示抽取提示词）+ 复制/删除（引用保护透传 409）+ 内置预设只读可复制；路由 `/domains` + 侧边栏菜单「领域模板」；
  - `KnowledgeBaseView.vue`：创建知识库选领域（省略默认通用）、卡片显示「领域：xx」；
  - `GraphView.vue`：图谱本体 meta 驱动——图例/节点配色按模板颜色、实体编辑类型下拉为模板 code+中文名（旧常量兜底）、标题展示生效领域并可弹窗更换（换绑后提示图谱状态失效需重建）；
  - 验证：`npm run build` 通过（含 DomainConfigView chunk）；后端 domain 相关 pytest **27 passed**。
- M6 收尾与验收（方案 Step 6）：
  - README 增补 v2 说明（分支/库/回滚路径 + 设计/方案文档指针）；
  - 验收：后端最终全量 pytest **777 passed**（较 v1 基线 734 净增 43，exit 0）；前端 `npm run build` 通过；真实 Neo4j 写图回归通过——KB 绑『通用』→ 真实建图（正则 Quantity/Time 落库）→ stats 类型 ⊆ 模板 code → 清图/删 KB 联动清理正常。
- 运维 · 模型配置数据迁移（v1 → v2，一次性）：
  - 需求：模型配置直接沿用 v1 原数据（方向 v1→v2，范围仅全局 model_configs，KB 级绑定不迁——v2 KB 系新建，旧 kb_id 无意义）。
  - 做法：v1（ai_bid_rag）只读；v2（ai_bid_rag_v2）`model_configs` 先清空（前置守卫：`kb_model_bindings` 为空才允许）再按原行插入（保留 id 与 created_at/updated_at），得到与 v1 完全一致的镜像。
  - 结果：v1 源 10 行 → v2 由原 2 行（v2 迁移链默认占位）变为 **10 行与 v1 一致**；默认配置每类型唯一校验通过；v1 数据未改动。
- 运维 · ES 索引版本隔离（v2 独立索引 `bid_chunks_v2`）：
  - 现状核查：全系统单一物理索引 `bid_chunks`（v1/v2 `.env` 均未设 `ES_INDEX_NAME`）；文档按 `kb_id` 字段区分、检索/删除均带 kb 条件 → **KB 级逻辑隔离已有，但 v1 与 v2 物理混存同一索引**（对账/清理/映射升级互相牵连）。
  - 处理（用户确认方案 A：仅配置 + 文档，**代码零改动**——索引名本就是配置项 `es_index_name ← ES_INDEX_NAME`）：v2 `backend/.env` 增加 `ES_INDEX_NAME=bid_chunks_v2`；`backend/.env.example` 补 ES 段示例；v1 环境文件不动（保持默认 `bid_chunks`）。
  - 验证：真实 ES 预建 `bid_chunks_v2` 成功；索引清单同时含 `bid_chunks` 与 `bid_chunks_v2`，v1 索引未受影响。
- 修复 · 领域模板「查看/预览/编辑」点击无效：`openDetail` 在 `await getDomain` 期间 `editor.form` 仍为 null，而对话框（destroy-on-close 重挂载）立即渲染 `v-model="editor.form.xxx"` → 空值解引用报错致弹窗打不开；改为打开时同步初始化 `emptyForm()`，拉取成功后回填。`npm run build` 通过。
- 简化 · 领域模板列表：原「预览」按钮与「查看」重复（同一只读弹窗），已删除，仅保留 查看/编辑（弹窗底部即校验+提示词预览）、复制、删除。

## 2026-09-08

- 日志降噪：`utils/logging.py` 的第三方压级名单加入 `pdfminer`/`pdfplumber` → WARNING（此前 DEBUG 启动时 pdfminer 逐 token/逐字符解析日志刷屏，单份大 PDF 可达数十万行）；`core/pdf_extractor.py` 在逐页解析时输出 `PDF 正在解析第 x/y 页`（INFO），解析进度一目了然且无库级噪音。验证：pdf_extractor + config 模块 58 passed。
- 解析失败状态落库容错与超时/诊断（现场：PG 批量入库超时失败后，partial 状态更新因数据库未执行 migration 0022 触发 `chk_documents_status` CheckViolation，文档卡 processing）：
  - `services/document_service.py::_mark_parse_failure`：partial 写入失败（如旧库缺 0022 约束）时**降级置 failed 并告警**（错误信息提示执行 `alembic upgrade head`），文档不再永久卡 processing；已入库切片保留可查看/重解析。
  - `config.py` 新增 `pg_connect_timeout`（默认 180s，asyncpg connect timeout；环境变量 `PG_CONNECT_TIMEOUT` 可调），`core/database.py` 引擎 `connect_args={"timeout": ...}` 生效 —— 缓解网络抖动/批量入库前的连接超时。
  - `_parse_and_store_in_batches` 在 **PG 入库前**打印明细：本批条数、chunk_index 范围、每块字符数列表与合计（`PG 入库前检查: …`），便于判断是否"数据量过大/单块超长"导致超时。
  - 验证：test_document_service + test_config 48 passed。
- 切片 MD5 幂等写（45 号方案，评审通过）：解析/续跑/重跑同一文档时不再无条件重插/重写——
  - 指纹：`chunk_md5 = MD5("{page_number}|{text}")`（公式与 0023 回填 SQL `md5(convert_to(concat_ws('|',…)))` 逐字节一致）；
  - `services/document_service.py`：新增 `_chunk_md5`/`_existing_chunk_md5s`；executor 每批先查 PG 存量指纹后分区——**unchanged 跳过**（不重写向量/ES/PG）、**changed/missing 才写**（先删确定性 pk 向量→重嵌→ES 覆盖→PG upsert）；移除 `resume_from` 行数锚点与前缀过滤，`resume_document` 简化为全量解析自动差集（partial 语义不变）；
  - `repositories/document_repository.py`：新增 `get_chunk_md5_map` 与 `batch_upsert_chunks`（`pg_insert(表级) ON CONFLICT (doc_id, chunk_index) DO UPDATE`，冲突更新并刷新 md5）；`_store_chunk_batch` 改走 upsert；
  - 模型/迁移：`DocumentChunk` 增 `chunk_md5` 列；`alembic/versions/0023_chunk_md5_unique.py`（加列 → 清历史重复（每 doc+index 保留最早行）→ 回填指纹 → 建 `uq_document_chunks_doc_index` 唯一约束）；
  - 验证：executor 幂等场景（全新全插/全 unchanged 零写/部分 changed+missing 只写差集）+ 指纹稳定 + 仓储 upsert(ON CONFLICT)/md5 map 单测；全量 pytest **732 passed**（基线 727）。部署：先备份 document_chunks → `alembic upgrade head`（0022→0023）→ 重启后端；重跑同一文档二次应见「unchanged 跳过、0 写入」。
- 知识库文档计数改「行数口径」（46 号方案，评审通过）：`knowledge_bases.document_count` 从「成功解析计数」改为与该库 `documents` 实际行数（所有状态）一致——
  - 实测漂移：投标库 count=1、行数=3（completed 2 + failed 1）；根因：上传/失败/partial/processing 不入旧计数、parse 无条件 +1 与 delete 无条件 -1 在重试/历史变更下失真；
  - `services/document_service.py`：`upload_document` 创建行后同会话 `increment_doc_count`（计数异常仅记日志不阻断上传）；`parse_document`/`resume_document`/`reparse_document` 不再加减（删除原有 completed 后 +1 与 was_never_completed 分支），删除文档仍 -1；
  - 存量一次性对账：`UPDATE knowledge_bases SET document_count=(SELECT count(*) FROM documents …)` 已执行 → 两库均 count==行数；
  - 验证：上传 +1 / 续跑不再 +1 等单测；全量 pytest **733 passed**（基线 732）。部署：仅重启后端（无结构变化）。
- 扫描页覆盖阈值修复（回归，无独立方案号，接 plan 44）：线上 `身份证.pdf`（kb c80aadef，doc 7cb1e23c）解析报「PDF 无可解析文本」——用户已配置 OCR（pp_ocr 全局默认启用，resolve 正常返回），根因在解析器：
  - `core/pdf_extractor.py`：无文本层页两张扫描图实际只占页面 **~19.7%~20.0%**，旧 `SCAN_IMAGE_MIN_COVER=0.2` 用“严格小于”把它们排除 → 整页无候选 → 抛错；改为 **0.1** 并注释说明（证件扫描图常只占页面积约 1/5）；
  - 验证：真实文件下载重跑 extract → items=2、skipped=[]（修复前 items=0、skipped=[1]）；新增回归用例 `test_split_scan_images_just_below_old_cover_threshold_ocr`（两张各约 19% 页面积图，旧阈值会被漏）；全量 pytest **734 passed**（基线 733）。部署：仅重启后端。

## 2026-09-05

- 问题：解析/重切片/重解析按**批次**落库（图片识别→分块→Milvus→ES→PG），任一批次中途失败（如 PG 瞬时不可达）会被置为 failed，但**此前成功批次已留在 PG/Milvus/ES**——可查看/编辑部分切片、计数混乱、整文档重试会重复写入。方案：`docs/plan/43-partial-parse-resume-plan.md`（评审通过）。
- 目标语义（需求方确认）：失败时**保留已成功批次**；文档状态改为 `partial`（部分成功，已入库 N）；提供**继续解析（续跑）**入口，从最后已提交处写剩余部分；三个解析入口失败统一判定；`failed`（0 提交）行为不变。
- 改动（后端）：
  - `alembic/versions/0022_add_partial_parse_status.py`（新增）：documents.status CHECK 约束增 `'partial'`（仿 0002）。
  - `core/indexing.py`：`build_index` 显式 `node.node_id = f"{doc_id}:{chunk_index:06d}"`（确定性 Milvus 主键，续跑先删后插的基础）；新增 `delete_vector_nodes(kb_id, node_ids)`（best-effort 按 pk 删除中断批残留向量）。
  - `services/document_service.py`：
    - `_parse_and_store_in_batches` 增 `resume_from`：写侧跳过 `chunk_index < resume_from`（已提交前缀），只写剩余且全局编号连续；续跑批先 `delete_vector_nodes` 再插入；
    - 新增 `_committed_chunk_count`（PG 实际行数 count，权威锚点）与 `_mark_parse_failure`（>0 → partial，==0 → failed，统一三入口 + 续跑失败）；
    - parse/rechunk/reparse 开始解析时重置 `processed_chunk_count=0`，except 统一走 `_mark_parse_failure`；
    - 新增 `resume_document(kb_id, doc_id)`：仅 partial 生效，`resume_from`=实际已入库行数，完成后置 completed；仅当从未 completed（chunk_count 为空）才知识库计数 +1。
  - `repositories/document_repository.py`：新增 `count_chunks_by_doc_id`。
  - `core/parse_queue.py`：任务类型增 `resume`（`enqueue_resume` + worker 分支调用 `resume_document`）。
  - `routers/document.py`：`/parse` 拦截 partial（提示走继续解析/重解析，防整文档重复写）；新增 `POST /{doc_id}/resume`（仅 partial 放行）。
  - `routers/chunk.py`：`/rechunk` 守卫放行集合加 `partial`（全量清旧重来兜底）。
  - 前端：`frontend/src/api/document.js` 增 `resumeDocument`；`frontend/src/views/DocumentListView.vue` statusLabel 增 `partial→部分成功`、partial 行显示**继续解析**按钮、切片入口放行 `completed`/`partial`（以及旧版本遗留 `failed` 且仍有已入库切片的文档，兼容此前“解析中途失败残留切片可查看”场景）、重解析对 partial 放行。
- 测试：`test_parse_queue`（enqueue_resume 队列项）；`test_document_router`（/parse 拦截 partial；/resume 放行/拒绝/404）；`test_chunk_router`（rechunk partial 放行）；`test_document_service`（executor resume_from 跳过前缀+先删后插、全量不删回归、`_mark_parse_failure` partial/failed 两分支、`resume_document` 完成+计数规则/非 partial 跳过）。
- 说明与边界：续跑锚点为 **PG 实际切片行数**（count，不依赖 processed 进度字段提交时序）；确定性主键仅用于 bulk 路径（单点切片编辑 insert_single_vector 不变）；续跑假设同一文件重解析产出一致（Word docx 字节稳定、PDF 版式几何稳定 + OCR 内容不变），OCR 极端非确定性可用"重新解析"全量兜底；partial 期间保留旧 chunk_count，UI"已入库"列按 processed_chunk_count 展示真实提交数。
- 验证：后端全量 pytest（tests 目录）**722 passed**（exit 0，基线 708 → +14）；本地无 PG 环境，状态机/续跑语义由单元+接口测试覆盖，真实联调待部署后验证。
- 部署提醒：先执行 `alembic upgrade head`（0022 仅改约束），再重启后端；前端需重新构建（DocumentListView 按钮/标签）。
- 需求变更（44 号方案）：PDF **扫描页（无文本层）整页 OCR**——`pdf_extractor._parse_page` 无文本层页不再一律跳过：`extract_images=True`（已配置 OCR/识别模型）时收集覆盖页面积 ≥ 20%（`SCAN_IMAGE_MIN_COVER`，过滤装饰/水印/条带）的整页图为「空文本段落 + WordImage」项，OCR/上传/分块/入库全部复用块级图片链路（`process_word_images` → `word_chunk_blocks`），纯扫描 PDF 不再误报、文字可检索；未配置模型或确无整页图（空白页）维持跳过；纯扫描未配置模型时报错文案更新为提示配置。改动：`core/pdf_extractor.py`（常量/`_scan_page_images`/`_parse_page` 分支）、`core/parser.py`（报错文案）；测试：`tests/test_pdf_extractor.py` +5（扫描页提取项/页码/skipped、未配置跳过回归、OCR 文字并入 chunk、纯图无 OCR 不产空块、空白页不 OCR）；验证：全量 pytest **727 passed**（基线 722）。

## 2026-09-04

- 需求：恢复 PDF 上传并实现**结构化解析 + 分块**（对齐 Word 链路；此前 PDF 被白名单禁用）。PDF 解析出标题层级/段落/表格/图片 → 逻辑块聚合 → 带 heading/heading_path/page_number/image_keys 的 chunk 入库（Milvus + ES 双写 + PG），图片识别文字并入正文且图片上传 MinIO 可预览；整篇无文本层（扫描件）解析失败并给出友好提示。方案：`docs/plan/42-pdf-parse-chunk-plan.md`（评审通过）。
- 改动（后端）：
  - `utils/file_utils.py`：`ALLOWED_EXTENSIONS` 加入 `.pdf`；`routers/document.py` 上传 400 文案改"支持: pdf, docx, doc"（前端本已支持 .pdf，无前端改动、无迁移）。
  - `core/pdf_extractor.py`（新增）：PDF 版式提取流水线——PyMuPDF 逐行取文本（字号/加粗/bbox，拼接时 CJK 直接续接、ASCII 两侧补空格）+ pdfplumber 表格区域（bbox + 行列文本）；页眉/页脚/纯页码过滤、表格区域文本行去重、无绘制 rect/跨页装饰/水印/条带图片过滤；正文行按纵向间距聚段；标题判定 = `is_heading` 中文标题正则主判据 +（字号 ≥ 1.25×正文 或 加粗）短行兜底（图/表题注、目录行进黑名单）；图片字节提取后归位到所在段落/表格；无文本层页面记 skipped 跳过；返回 `(ParagraphItem 列表, 物理页数)`。
  - `core/parser.py`：`ParagraphItem` 增可选 `page_number`；`PDFParser` 重写为结构化委托（不再按页拼 LlamaDocument、不再内联 OCR——图片识别下沉到块级 image_recognition_service）；`parse_document` 去掉 ocr_config 参数、PDF 返回 `(items, page_count)`，整篇无文本层抛 ValueError（"PDF 无可解析文本（疑似扫描件，暂不支持整页 OCR）"）。
  - `core/word_chunker.py`：`LogicalBlock` 增 `start_page`；`chunk_blocks`/`word_chunk_blocks` 把块起始页写入 chunk 元数据 `page_number`（PDF）；Word 项无页码 → 行为不变（仍默认 1）。
  - `services/document_service.py`：docx/doc/pdf 统一走 blocks 结构化链路（aggregate → process_word_images → word_chunk_blocks → Milvus + ES + PG），PDF 物理页数入库 `page_count`；三处解析入口（首次解析/重切片/重新解析）不再向 parse_file 传 ocr_config。
  - `core/indexing.py`：注释更新（`chunk_documents` 不再被生产调用，函数与既有测试保留）；`services/image_recognition_service.py` 注释更新为 Word/PDF 共用。
- 测试：`test_file_utils` / `test_document_router` 的 PDF 拒绝用例翻转为放行；新增 `test_pdf_extractor.py`（标题判定含 图/表题注与"（10）…；"列表项反例、字号/加粗兜底档位、文本拼接、表格区域容差、图片归位、合成中文 PDF 结构化集成、扫描页跳过、chunk 页码与 heading_path 透传、Word 无页码回归）；`test_document_service.py` 增 PDF/Word 统一分块链路用例（PDF 返回物理页数、chunk 带页码与标题；docx 回归 page_count=1）。
- 验收（真实样本 `D:\Pictures\投标`）：「02 - 图片嵌入表格.pdf」文本型 2 页——pdfplumber 表格区文本去重后产出 2 个表格项、页内图片挂到表格项（imgs=1）；「身份证.pdf / 身份证2.pdf」纯扫描 → 整页跳过并抛扫描件友好错误；合成章节目录 PDF 标题识别（第一章 L1、1.1 L3）+ chunk page_number/heading_path 正确。
- 验证：后端全量 pytest（tests 目录）**708 passed**（exit 0，基线 677 → +31）；前端无改动。

## 2026-09-04

- 需求（放宽检索）：意图识别口径从"按投标主题圈定/示例列举"进一步放开——**所有专业领域/任何想获取信息、资料、专业知识的输入都走检索，只有纯日常闲聊才算普通聊天**；重新设计提示词文案。
- 新判定口径（提示词 v4，三处同源：`core/intent_detect.py` 兜底 + 迁移 0021 seed + 运行库默认模板，实测逐字一致）：
  - 总则：放宽检索——任何想获取信息/资料/专业知识的输入 → 需要检索；仅纯日常闲聊 → 普通聊天；
  - 需要检索：专业领域问题（工程/建筑/医疗/法律/财经/教育/医药等任意行业：术语、概念、疾病名、规范、条款、标准、数据、政策、产品…）、索取资料内容（要求/流程/方案/价格/比例…）、任何提问获取知识的表达（是什么/为什么/怎么/如何/多少/有没有/哪些…）；主题与投标无关或知识库查不到不影响判定；拿不准一律需要检索；
  - 普通聊天：仅限日常闲聊语言场景（问候寒暄/感谢/身份能力闲聊/讲笑话/夸奖/天气等生活闲聊/情绪附和，如"你好/在吗/谢谢/你是谁/你会什么/讲个笑话/今天天气不错/辛苦了/哈哈/那挺好的/晚安"）；
  - 一句话判据：索取信息/专业内容 → 需要检索；闲聊（无信息诉求）→ 普通聊天。
- 迁移 `backend/alembic/versions/0021_intent_detect_prompt_scope.py`（Revises 0020）升级 intent_detect 默认模板（guard：仅当行内容为 0020 旧文案时更新，不覆盖用户编辑）；运行库已应用至 **0021 (head)**。
- 设计文档同步：`docs/plan/41-chat-intent-detect-plan.md` 判定口径与模块清单更新（含 `_is_information_request` 守卫、0021 seed 引用）。
- 测试：drift 测试改指 0021 并换用 v4 文案标记（放宽检索/日常闲聊/专业知识）；2 处"问答意图分类器"断言适配新开头"问答前置意图分类器"。
- 验证：真实 LLM 19/19——8 条专业/信息诉求（CHD 裸查询与"是什么"、高血压饮食、投标报价得分、悬挑脚手架规范、增值税税率、民法典条款、指代提问）全部"需要检索"；11 条日常闲聊（你好/在吗/谢谢/你是谁/你会什么/讲个笑话/今天天气不错/辛苦了/哈哈你太逗了/那挺好的/晚安）全部"普通聊天"；运行库行 == 代码兜底（parity True，v4 标记在位、v3 标记已不在）；后端全量 pytest **677 passed**（exit 0）。

## 2026-09-04

- 需求：问答意图识别把"查询冠状动脉粥样硬化性心脏病"（纯主题名词/陌生主题查询）误判为普通聊天、跳过检索——需修正意图识别提示词与兜底逻辑。
- 根因：旧提示词把"需要检索"按投标主题白名单圈定（"与招标/投标/资质…相关"），且模型对"查询 + 疾病名词"这类无宾语维度的请求稳定输出"普通聊天"（实测 RAW 复现；变体探测显示带"资料/是什么/我想了解"等措辞才判需要检索）。
- 改动（提示词 v3，核心/intent_detect.py + 迁移 0020 + 运行库默认模板三处同源）：
  - 反转为"**默认需要检索**"：凡想查询/了解任何主题资料（含纯主题名词查询、`X 是什么`），无论主题是否与投标相关，都判需要检索；"普通聊天"仅限纯社交表达（问候/感谢/身份闲聊/讲笑话/日常寒暄）；输入不含明显社交信号一律按需要检索；
  - 兜底守卫：`detect_intent` 中 LLM 判普通聊天但输入为明确检索请求（`_is_information_request`：查询/查一下/帮我查/找一下…前缀）时覆盖改判 True——即使提示词层仍误判也保证走检索；
  - 迁移 `backend/alembic/versions/0020_intent_detect_prompt_v2.py`（Revises 0019）升级 intent_detect 默认模板 seed（guard：仅当行内容为 0018 旧文案时更新，不覆盖用户编辑）；运行库先误用 v2 种子，已直接 UPDATE 对齐为 0020 seed（0020 head，幂等可回滚）。
- 测试：`test_intent_detect.py` 增 `TestInformationRequestGuard`（5 前缀 True / 纯闲聊与空串 False / LLM 误判被覆盖 True / 闲聊保持 False）与 `test_fallback_matches_migration_0020_seed`（逐字防漂移 + 含主题查询示例标记）；2 处旧断言"问题分类器/不确定时按"随文案更新为 v3 标记。
- 验证：真实 LLM 实测——"查询冠状动脉粥样硬化性心脏病"与"X 是什么"RAW 输出"需要检索"（5/5 稳定），"你好/谢谢/你是谁/讲个笑话/今天天气不错"仍"普通聊天"，"查投标人资质要求"仍需要检索；运行库行与代码兜底/0020 seed 逐字一致；后端全量 pytest **677 passed**（exit 0）。

## 2026-09-04

- 确认/补全：问答意图识别与普通聊天提示词均已接入提示词管理——`core/intent_detect.py`（intent_detect）与 `services/chat_service.py`（chat_normal）运行时均优先读 DB 激活模板、缺失/异常回退内置常量；0018 迁移已种两个默认模板并应用运行库（实测运行库文案与各自代码兜底逐字一致 True/True）；前端提示词管理页已含「意图识别」「普通聊天」筛选与新增选项。附带小修：`services/prompt_service.py` 中未被引用的静态枚举 `SCENARIOS` 补齐为 8 个场景（与 DB `chk_prompt_templates_scenario` 一致）。

## 2026-09-04

- 需求：把 Query 改写默认提示词的增强文案（否定/改口剔除 + 多意图 JSON 数组输出，此前仅直接更新运行库一行数据）落成 **alembic 迁移**，保证新环境/重部署 `alembic upgrade head` 后默认模板一致；代码内置兜底同步为新文案（恢复"兜底 = 迁移 seed = 提示词管理默认模板"三处同源）。
- 改动（迁移）：新增 `backend/alembic/versions/0019_query_rewrite_prompt_v2.py`（Revises 0018）——upgrade 仅当 `query_rewrite` 默认模板 `system_prompt` 仍为 0017 旧文案时原地更新为增强版 v2（不覆盖用户在提示词管理自行编辑过的行），downgrade 反向回退。注：0018 前置迁移（intent_detect/chat_normal 默认提示词接入，随早前提交入库但此前未应用）随本次 `upgrade head` 一并应用；链 0017 → 0018 → 0019。
- 改动（代码）：`core/query_rewrite.py` `FALLBACK_QUERY_REWRITE_PROMPT` 与模块注释同步为 v2 文案（含否定/改口规则、多意图 JSON 规则与 few-shot 示例）。
- 测试：`test_query_rewrite.py` 新增 `test_fallback_matches_migration_0019_seed`——导入迁移模块逐字比对兜底常量与 0019 seed（防三处漂移），并断言含"否定改口/JSON 数组"规则。
- 验证：运行库（192.168.60.120:5444）`alembic upgrade head` → **0019 (head)**；实测运行库 `query_rewrite` 默认模板与代码兜底**逐字一致**；0018 顺带生效（intent_detect/chat_normal 默认提示词两行入库）；后端全量 pytest **664 passed**（两次稳定，exit 0）。部署提醒：新环境直接 `upgrade head` 即得 v2 默认模板；回退运行库可 `alembic downgrade 0018`（还原 0017 旧文案；再 `downgrade 0017` 移除 0018 的 intent_detect/chat_normal 种子行）。

## 2026-09-04

- 需求：问答页增加**意图识别**——判断用户输入是"需要检索文档（RAG）"还是"普通聊天"；普通聊天**不走检索**（跳过改写/召回/词库/rerank），直接 LLM 流式回复且不返回引用来源，避免闲聊浪费检索链路并误答"未找到文档"。
- 方案：`docs/plan/41-chat-intent-detect-plan.md`（默认方案已确认：仅影响问答页、判定参考最近 4 轮历史、失败一律回退"需要检索"、开关 `chat_intent_detect_enabled=True` 默认开）。
- 改动（后端）：
  - `core/intent_detect.py`（新增）：LLM 意图识别（True=需要检索 / False=普通聊天）——内置 fallback 提示词 + `_get_detect_prompt()`（提示词管理 `intent_detect` 激活模板优先，模式同 query_rewrite）+ `_parse_intent_output()`（纯文本/英文/JSON 兼容；空/非法/LLM 异常一律回退 True）；
  - `services/chat_service.py`：抽公共生成尾 `_generate_llm_answer()`（`_run_rag` 行为不变）；新增 `_run_chat_normal()`（chain span `chat_normal`，无检索、无 sources）与 `_decide_retrieval()`（开关/模型解析/识别三态，异常回退检索）；`chat_stream` Stage 4 分流；
  - `config.py`：新增 `chat_intent_detect_enabled: bool = True`；`.env.example` 同步；
  - 迁移 `0018_intent_detect_scenarios.py`：`chk_prompt_templates_scenario` 追加 `intent_detect`/`chat_normal` + seed 两个默认模板（文案与代码兜底一致）；
  - `models/schemas.py`：`PromptCreate.scenario` 说明补两个新场景。
- 改动（前端）：`PromptManagementView.vue` 场景筛选 radio / 新建弹窗 select / 标签表补「意图识别」「普通聊天」。
- 测试：新增 `tests/test_intent_detect.py`（解析/历史/提示词/兜底）与 `test_chat_service.py` 增 `_decide_retrieval` 三态、`_run_chat_normal` 接线用例，共 **+44**；后端全量 **661 passed**；前端 `npm run build` 通过。
- 部署提醒：服务器需 `alembic upgrade head`（应用 0018：扩展约束 + seed 两默认模板）后重启 backend；前端需重新构建部署。默认开启后每轮问答多一次轻量 LLM 分类调用（失败自动回退既有检索链路）。
- 追踪修复：新增外层编排 `_run_qa_round()`（`@traced_span("chat_qa","chain")`，Stage 4 前开启并下发 session_id/kb_id），使 `intent_detect`（generation）与 `_run_rag`/`_run_chat_normal`（chain）挂在**同一 trace** 下，不再单独成条；`result` 记录 `intent=retrieval/chat`。新增 `_run_qa_round` 分流用例 2 例，全量 **663 passed**。
- 观测补强：`detect_intent` 的 `intent_detect` generation 改为按 `update_current_generation` 完整记录（同 query_rewrite/plan 39 口径）——`model`、实际输入 `prompt`、`output`、`usage_details`（token 用量，模型未返回时为 None）、异常分支 `level=ERROR`+`status_message`，不再是仅记录 query/metadata 的部分信息。

## 2026-09-04

- 修复：问答页 SSE 流式 token 实际逐条到达，但界面不逐字刷新，回答在结束时整块突然显示。
- 根因：`ChatView.vue` 的 `aiMsg` 是 push 进 `messages`（ref 数组）前的**裸对象**——`onToken` 里 `aiMsg.content += token` 直接改响应式代理的 raw target，不触发 Vue 重渲染；仅 `onDone` 置 `streaming=false` 时才整块渲染一次。SSE 链路（后端逐 token yield、nginx `proxy_buffering off`）本身无问题。
- 改动（前端）：`src/views/ChatView.vue`——`aiMsg` 改用 `reactive({...})` 创建后再 push（`import` 增 `reactive`），`onToken/onSources/onError` 的赋值即走响应式代理触发逐 token 渲染。
- 验证：以项目本地 vue 包最小复现——裸对象连续 4 次 content 变更渲染计数不变、reactive 对象逐次递增；`npm run build` 通过。

## 2026-09-03

- 需求：Query 改写需处理**多意图**输入——一次输入含多个独立检索意图时（如"查资质要求，还要保证金比例和评分办法"），改写/检索链路把意图**拆成多条子查询、逐条各自检索**，结果**合并**输出，避免单条混合查询只命中一个主题。方案：`docs/plan/40-multi-intent-query-plan.md`（评审通过：检索页+问答双入口、多意图才拆、按子查询分别 rerank、每意图各取 top_k）。
- 设计：`rewrite_query` 返回值扩为 `str | list[str]`——LLM 一次调用按提示词规则输出：单意图→一行改写文本；多意图→JSON 数组子查询。解析：以 `[` 开头整体 json 解析，≥2 条→list；1 条→解包；非法 JSON/单行文本→按单查询回退现状；失败/空输出仍回退原词。
- 改动（后端）：
  - `core/query_rewrite.py`：新增 `_parse_rewrite_output()` 双格式解析；`rewrite_query` 支持多意图返回 list（generation 记录 output=数组）；docstring 更新；
  - `core/retrieval.py`：`retrieve_and_rank` 重构——召回融合抽为 `_recall_and_fuse(sub_query)`；多意图（list≥2）时逐子查询召回 + **按子查询 rerank** + 阈值过滤 + 各取 top_k，新增 `merge_intent_hits()` 跨意图按 `(kb_id, doc_id, chunk_index)` 去重合并（保意图顺序、先出现意图优先）；单意图路径行为不变（回归）；
  - `services/search_service.py`：`search()` 增加多意图分支（复用 `_recall_milvus/_recall_fulltext/_recall_graph` 逐子查询召回、`_apply_rerank` 按子查询重排、各取 top_k、`merge_intent_hits` 合并）；span metadata 增 `sub_queries`；单意图分支原样保留；
  - 运行库提示词管理 `query_rewrite` 默认模板（192.168.60.120:5444）system_prompt 更新：追加"多意图必须输出 JSON 数组、每元素一条子查询、勿合并成一句话、含改口示例与反例、被否定的意图不得入数组；单意图输出一行文本不输出 JSON"（代码兜底/迁移 seed 不动，与用户口径一致）。
- 测试：`test_query_rewrite.py` 增 `TestMultiIntentParsing` 6 例（数组→list 且 generation 记数组/多行 JSON/单元素解包/空元素过滤/单行 str/非法 JSON 回退）；`test_retrieval.py` 增 `TestRetrieveAndRankMultiIntent` 3 例（逐意图召回+按子查询 rerank+去重、无 rerank 每意图截 top_k、str 单意图回归）；`test_search_service.py` 增 `TestSearchWithMultiIntent` 2 例（逐意图召回合并截断、按子查询 rerank 去重且响应 query 为原词）。
- 验证：定向 64 passed（query_rewrite/retrieval/search_service）；真实 LLM 语义实测 5/5——多意图输出 3 元素数组、改口剔除后单意图、单意图不输出 JSON、多意图含"不含联合体"排除条件数组、改口变体只剩 AAA 意图；前端无改动。部署提醒：服务器（192.168.60.120）需重新部署 backend 并重启（含运行库模板文案已随库生效）。

## 2026-09-03

- 需求：Query 改写需处理**自我纠正/改口型否定**输入（如"我要查询 XXX，不了，还是查询 AA"），改写结果只保留最终意图 AA，**不得带出被否定的 XXX**；同时保留真实排除条件（"不含/没有 XXX 的招标文件"属于最终筛选意图）。
- 口径（用户确认）：只剔"自我纠正/改口"中被放弃的目标；含否定词的**真实筛选条件原样保留**。同步范围（用户指定）：**仅更新运行库提示词管理 `query_rewrite` 默认模板文案**，代码兜底常量与迁移 0017 seed 不随本次改动（保持原文案；仅在无激活模板时兜底使用，与新 DB 文案允许不一致）。
- 改动（运行库 192.168.60.120:5444/ai_bid_rag，仅数据）：`prompt_templates` 中 scenario=`query_rewrite` 且 is_default/is_active 的默认模板（id b6788103-…）`system_prompt` 更新，新增两条规则：改口型否定以最终意图为准且被否定内容不得出现；"不/别/不含/不要"作真实筛选条件时必须保留。其余字段（name/description/variables/is_active/is_default/sort_order）不变。
- 验证：真实 LLM 语义实测 7 例全部通过——改口剔除（"…不了/算了/不不不/别查了…还是查 AA" 5 例均只输出 AA，无 XXX）；排除条件保留（"不含联合体投标方式""近三年没有重大违法记录" 均保留否定语义）；无代码改动、无迁移。
- 备注：如需新环境/重新 seed 也与新文案一致（代码兜底 + 迁移 0017 seed 同步），可另开任务处理。

## 2026-09-03

- 需求：LLM（及模型类）调用统一以 **langfuse generation** 记录（model/input/output/usage），覆盖 Query 改写、图谱实体抽取；并修正观测类型大小写缺陷。方案：`docs/plan/39-langfuse-llm-generation-plan.md`（已评审）。
- 根因：① langfuse v4 `observe(as_type=...)` 仅接受小写类型，历史代码传 `"GENERATION"/"CHAIN"` 等大写会被 SDK 打 warning 并**静默降级为普通 span**；② Query 改写虽名义 generation 却只写 input/output、无 model/usage；③ 图谱实体抽取 LLM（`graph_extractor._llm_extract_batch`）完全未接入观测；聊天回答 `_call_llm_stream` 已达标（参照标准）。usage 取数：llama_index openai 系 LLM 把 token 计数写入 `response.additional_kwargs`。
- 改动（后端）：
  - `core/observability.py`：`traced_span` 新增旧大写→SDK 小写映射（GENERATION→generation、EMBEDDING→embedding、CHAIN→chain…，未知值透传），杜绝静默降级；新增 `extract_usage(resp)` 统一提取 token 用量；
  - `core/query_rewrite.py`：`rewrite_query` 类型改 `generation`，每次实际 LLM 调用记录 `update_current_generation(model/input=提示词/output=改写词/usage)`，空输出/未变化**先记录后回退**（顺带修复空输出 `splitlines()[0]` 越界走异常分支的问题）；失败记 `level="ERROR"`；
  - `core/graph_extractor.py`：`extract_from_chunks` 加 `@traced_span("entity_extraction", "chain")` 并记录 kb/chunk 输入；`_llm_extract_batch` 加 `@traced_span("entity_extract_llm", "generation")`，成功记 model/input/output/usage，两次尝试均失败补记 ERROR 级；
  - 类型小写显式化：`retrieval.py` embedding→`"embedding"`、rerank→`"generation"`；`search_service.py` search→`"chain"`；`chat_service.py` rag_qa→`"chain"`。
- 测试：`test_observability.py` 增 `traced_span` 类型映射（7 组参数化）与 `extract_usage`（4 例）；`test_query_rewrite.py` 增成功（含 usage）/空输出/LLM 异常三条 generation 记录断言；`test_graph_extractor.py` 增 autouse get_client mock + `_llm_extract_batch` 成功/全失败两条记录断言。
- 验证：后端全量测试通过（pytest exit 0，**606 passed**）；不涉及 DB/前端改动，无迁移。部署提醒：服务器（192.168.60.120）需重新部署 backend 后，langfuse 中改写/抽取/embedding/rerank/chain 层级方以正确类型展示。

## 2026-09-03

- 需求：修复提示词管理「预览」对含 JSON 花括号模板报错（实体抽取默认提示词预览 500：`ValueError: Invalid format specifier '"...","type":"ORGANIZATION"...'`，前端表现为弹窗空白/无渲染结果）。
- 根因：`core/prompt_renderer.py` 用 `str.format`（`_SafeFormatter`）渲染模板，模板中的 JSON 示例大括号（如 `{"entities":[...]}`）被当作占位符/格式说明符解析 → `format(value, spec)` 抛 ValueError。该渲染器同时被 `prompt_service.preview`（预览）、`chat_service`（RAG 提示词）、`graph_extractor`（实体抽取）、`query_rewrite`（改写提示词）复用，含 JSON 花括号的模板在运行期同样会崩溃。
- 改动（后端）：`core/prompt_renderer.py` 重写 `render_prompt()`——按 `{变量名}` 正则仅替换占位符，其余花括号（JSON 示例等）原样保留、变量值原样插入不二次解析；必填缺失保留占位符、非必填缺失置空（与原行为一致）；删除已无用的 `_SafeFormatter` 与 `import string`。`extract_variables` / `validate_template` 不变。
- 改动（前端）：`PromptManagementView.vue` 预览弹窗打开即自动渲染一次（`showPreviewDialog` 末尾触发 `doPreview()`），不再需要手动点「刷新渲染」才见内容。
- 测试：新增 `tests/test_prompt_renderer.py`（15 例：普通替换 / 必填缺失保留 / 非必填缺失置空 / JSON 花括号原样保留 / 占位符与 JSON 并存 / 变量值含花括号 / `extract_variables` 去重与忽略带引号 JSON 键 / `validate_template`）；修复前该文件 4 例失败（含 ValueError 复现）。
- 验证：后端全量测试 **590 passed**（pytest exit 0）；前端 `npm run build` 构建成功；不涉及数据迁移。部署提醒：服务器（192.168.60.120）需重新部署修复后的 backend/frontend 并重启后端，线上预览方生效。

## 2026-09-01

- 需求：文档检索加入 **Query 改写（LLM 单查询改写）**，作为可开关选项——检索页前端开关 + 问答后端配置开关，均默认关闭；改写提示词纳入提示词管理（新场景 `query_rewrite`）。
- 方案：`docs/plan/38-query-rewrite-plan.md`（v1.1，已评审）。
- 行为规则：改写只服务召回（dense/sparse/fulltext/graph 与 fulltext 词库扩展使用改写词）；**rerank 与响应 `query` 仍用原查询**；改写失败/无默认 LLM/输出为空静默回退原词；问答改写附带最近 4 轮历史（变量 `{history}`）。
- 改动（后端）：
  - `config.py`：新增 `query_rewrite_enabled=False`、`chat_query_rewrite_enabled=False`；
  - 新增 `core/query_rewrite.py`：`FALLBACK_QUERY_REWRITE_PROMPT` + `_get_rewrite_prompt()`（提示词管理 `query_rewrite` 激活模板优先，缺失/DB 异常回退内置常量）+ `rewrite_query()`（`traced_span("query_rewrite","GENERATION")`，失败回退原词）；
  - `core/model_factory.py`：新增 `resolve_default_model_config()`（全局默认启用模型，无则 None）；`resolve_model_config` 默认兜底分支改走同 session 的 `_query_default_config()`（行为不变）；
  - `core/retrieval.py`：`retrieve_and_rank` 新增 `query_rewrite_config`/`query_rewrite_history` 参数，改写后 rerank 用原查询；
  - `services/search_service.py`：`search()` 新增 `query_rewrite: bool|None`；开启时取全局默认 LLM 改写（召回用改写词、rerank 用原词、响应 `query` 为原词、span metadata 记 `query_rewritten`）；
  - `services/chat_service.py`：`_run_rag` 按 `chat_query_rewrite_enabled` 解析 LLM 配置（失败仅 warning 跳过）并传最近 4 轮历史给 `retrieve_and_rank`；
  - `models/schemas.py`：`SearchRequest` 新增 `query_rewrite: bool|None`；Prompt 场景注释补 `query_rewrite`；`routers/search.py` 两端点透传；
  - 迁移 `0017_query_rewrite_default_prompt.py`：重建 `chk_prompt_templates_scenario`（加入 `query_rewrite`）+ seed 默认模板（变量 query 必填 / history 可选）；
  - `.env.example`：检索段追加 `QUERY_REWRITE_ENABLED` / `CHAT_QUERY_REWRITE_ENABLED`。
- 改动（前端）：`api/search.js` 透传 `query_rewrite`；`SearchView.vue` 参数面板新增「Query 改写（LLM）」开关（默认关）；`PromptManagementView.vue` 场景新增「Query 改写」（radio/下拉/标签）。
- 测试：新增 `test_query_rewrite.py`（提示词加载 3 例 + 改写行为 6 例）；`test_model_factory.py` 增 `resolve_default_model_config` 3 例；`test_retrieval.py` 增改写召回/rerank 原词/默认不改写 4 例；`test_search_service.py` 增改写开关 6 例；`test_schemas.py`/`test_search_router.py` 增 `query_rewrite` 字段与透传用例；`test_chat_service.py` 增 `_run_rag` 开关接线 2 例。
- 验证：后端全量测试 **575 passed**（pytest exit 0）；前端 `npm run build` 构建成功；改动前行为（开关默认关）由回归用例覆盖。

## 2026-09-01

- 需求：修复启动报错 `module 'core.neo4j_store' has no attribute 'ensure_indexes'`（`GRAPH_ENABLED=true` 时 lifespan 启动触发）。
- 根因：`core/neo4j_store.py` 将 `ensure_indexes`/`close` 定义为 **`Neo4jStore` 类 classmethod**，而 `main.py` 以**模块级** `neo4j_store.ensure_indexes()`（启动）/`neo4j_store.close()`（关闭）调用，属性解析失败。同类问题 2026-08-24 修复过 health_check 一处，`ensure_indexes`（仅 graph_enabled=true 且被 try/except 吞为 warning）与 `close`（需优雅关闭）漏改。
- 方案：`docs/plan/37-main-neo4j-module-call-fix-plan.md`。
- 改动：`backend/main.py`——`from core import ... neo4j_store ...` 移除、新增 `from core.neo4j_store import Neo4jStore`；启动 `await neo4j_store.ensure_indexes()` → `await Neo4jStore.ensure_indexes()`；关闭 `await neo4j_store.close()` → `await Neo4jStore.close()`；health_check 删除冗余局部导入（复用顶部导入）。`core/neo4j_store.py` 不改（类 API 为统一用法）。
- 测试：`tests/test_health.py` 新增 `test_main_neo4j_uses_class_api` 回归用例（修复前 `main` 无 `Neo4jStore` 属性 → 失败）。
- 验证：`pytest tests/test_health.py tests/test_graph_service.py tests/test_graph_router.py tests/test_graph_queue.py` **40 passed**；后端全量测试通过（pytest exit 0）。

## 2026-09-01

- 需求：修复 Milvus 报错 `(code=1100, message=only IP is the supported metric type for sparse index)` —— 新知识库 collection 创建失败（上传/删除文档触发建库即报错）。
- 根因：`backend/core/vector_store.py` 在 `retrieval_hybrid_enabled=True`（默认）时启用 `enable_sparse` + `BM25BuiltInFunction`；llama_index 建稀疏索引默认 `metric_type=BM25`，而部署的 **Milvus 2.4.x 稀疏索引仅支持 IP**（BM25 内置函数需 Milvus 2.5+）→ `create_index` 失败；且 2.4 上 BM25 函数本就不会持久化（实测 `functions: []`），sparse 检索实际从未生效。
- 方案/改动（服务器 192.168.60.120，`/opt/bid-infra`）：
  - 备份：`backup/bid-infra-volumes-20260901-172538.tar.gz`（etcd + milvus + minio-data）+ `docker-compose.yml.bak-20260901-172538`；
  - `docker-compose.yml`：`milvusdb/milvus:v2.4.17` → `v2.5.27`（2.5 线最新），`docker compose up -d milvus` 重建，数据卷保留、etcd 元数据自动迁移；
  - 升级后对现有 5 个 collection 补建 `sparse_embedding` 索引（SPARSE_INVERTED_INDEX / IP）：Milvus 2.5 加载校验要求所有向量字段均有索引，否则 `load_collection` 报 `there is no vector index on field: [sparse_embedding]`；
- 代码：无改动（探针以项目同款配置验证：2.5.27 上 BM25 函数持久化、稀疏索引创建、插入自动生成稀疏向量、BM25 检索命中，探针库已清理）。
- 验证：`get_server_version=v2.5.27`；现有 collection 全部可加载（均为空库 rows=0）；后端 `/api/health` healthy。
- 备注：当前 PG 仅 1 个知识库（0594d85e）+ 1 个 `failed` 文档（旧建库失败所致），升级后重新上传/解析即可；5 个 collection 中 4 个为已删除知识库的孤儿（kb_98b88fd5 / kb_79789ec9 / kb_28116edc / kb_fa1f4a26），可后续清理；旧数据在旧 Milvus(121) 已丢失（见 2026-08-31 记录），需重新上传文档重建。

## 2026-08-31

- 需求：将系统 **PostgreSQL / MinIO / Milvus** 从外部地址更换为 **192.168.60.120** 服务器，120 上无对应实例的服务通过 Docker 部署。
- 现状确认：120（developtest，root/developtest）已有 langfuse 专用 PG(:5432) / MinIO(:9090) / ClickHouse / ES(bid-es, :9200) / Langfuse(:3000)；投标系统所需 PG/MinIO/Milvus 均无可用实例 → 全部新部署。
- 部署：新增 `/opt/bid-infra/docker-compose.yml`（独立于 langfuse，避免污染其自身实例）：
  - `bid_pg`：postgres:16，宿主 `5444→5432`，库 `ai_bid_rag`（pgsql/pgsql）；
  - `bid_minio`：minio/minio，宿主 `9100→9000`（API）/ `9101→9001`（console）——宿主 9000 已被 clickhouse 占用，故用 9100；凭据沿用 rag_flow/RagflowMinio2026!；
  - `bid_etcd`：quay.io/coreos/etcd:v3.5.15；
  - `bid_milvus`：milvusdb/milvus:v2.4.17（tag 无 `-standalone` 后缀，已修正），宿主 `19530→19530`，通过内部网络连 etcd:2379 / minio:9000，并注入 `MINIO_ACCESS_KEY_ID/SECRET_ACCESS_KEY`（默认 minioadmin 连不上会报 a-bucket 凭据错误）。
- 配置切换（服务器 `/opt/ai_bid_rag/backend/.env` + 本地 `backend/.env` 同步）：
  - `PG_HOST 192.168.61.222 → 192.168.60.120`（端口 5444 不变）；
  - `MILVUS_HOST 192.168.60.121 → 192.168.60.120`（19530 不变）；
  - `MINIO_ENDPOINT 192.168.60.121:9000 → 192.168.60.120:9100`；
  - `docker compose up -d --force-recreate backend` 重启生效。
- 数据迁移：旧 PG（192.168.61.222:5444）`pg_dump -Fc` → 新 PG `pg_restore --clean --if-exists`（容器需 `--network host` 访问宿主 5444），迁移 4 库 / 6 文档 / 1023 chunks / 8 对话 / 3 词条 / 10 模型 / 3 prompt；后端容器启动时 `alembic upgrade head` 幂等建表。
- 验证：后端 `/api/health` healthy；`/api/knowledge-bases` 返回 4 库；ES 对账 3 库 pg=es 一致（84/84、24/24、915/915）；Milvus `get_server_version=v2.4.17`、`list_collections=[]`；MinIO 已建 `bid-documents` 桶（Milvus 自动建 `a-bucket`）。
- 局限（无法迁移，需重建）：① 旧 Milvus（192.168.60.121:19530）已不可达，向量数据/索引丢失，需重新上传文档重建；② 旧 MinIO（121:9000）`bid-documents` 中系统文档对象全部缺失（仅有 Milvus `objects/` 数据），文档源文件需重新上传；③ 旧遗留差异：kb「标书ocr」PG 无 chunk（文档 failed）但 ES 残留 262 条，可对账重建清理。
- 备注：`docs/changes.md` 追加本记录；`docker-compose.yml` 头部部署注释同步更新。
- 方案：`docs/plan/36-graph-single-chunk-extraction-plan.md`。
- 改动：
  - `config.py`：`graph_extraction_batch: int = 4` → `1`（保留配置项，.env 设 `GRAPH_EXTRACTION_BATCH=4` 可恢复批量合并省成本）；
  - `core/graph_extractor.py`：模块 docstring 措辞更新（逻辑不变，`batch_size=1` 时 `extract_from_chunks` 自然逐 chunk 触发 `_llm_extract_batch`，实体/关系 chunk 归属精确到当前 chunk）；
  - `docs/09-graph-module-design.md`：§5.1 管线图示、§5.4 成本表（逐 chunk 幻觉最低/成本最高，可调 batch 权衡）、§10.2 配置注释同步更新；
  - 新增单测 `test_single_chunk_extraction_when_batch_1`：3 个 chunk 各触发一次 LLM 调用、每次入参 1 个 chunk、实体 chunk 归属各自准确。
- 验证：后端全量测试通过（pytest exit 0）。

## 2026-08-24

- 需求：知识图谱页面新增**实体详情分页列表**（行内编辑名称/类型/描述；列含关系数、chunk 数）、**实体名搜索**、**点击渲染到画布**（实体 + 1-hop 邻居）。
- 方案：`docs/plan/35-graph-entity-list-plan.md`。
- 改动（后端）：
  - `models/graph_schemas.py` 新增 `EntityListItem`/`EntityListResponse`/`EntityUpdateRequest`；
  - `services/graph_service.py` 新增 `list_entities`（分页 + CONTAINS 模糊搜索 + 关系数/chunk 数，按关系数降序）、`update_entity`（old_name/old_type 定位；类型 label 白名单校验，变化时 REMOVE 旧 label + SET 新 label；键冲突抛 `EntityConflictError` → 409；不存在返回 None → 404）；
  - `routers/graph.py` 新增 `GET /graph/entities/list`、`PUT /graph/entities`（保留原 `GET /graph/entities?name=` 详情）；
- 改动（前端）：
  - `src/api/graph.js` 新增 `listGraphEntities`/`updateGraphEntity`；
  - `src/views/GraphView.vue`：右侧详情卡片改为「**实体列表 / 详情**」双标签页；实体列表 Tab：搜索框 + 分页表格（名称可点击渲染、类型 tag、关系数、chunk 数可点击查看该实体全部证据原文弹窗、描述、编辑/渲染按钮）；编辑弹窗（名称/类型下拉 9 类/描述，保存后刷新列表，画布含旧节点则重拉第一页）；「渲染」= expand 1-hop 邻居并入画布 + 自动切详情 Tab；`TYPE_COLORS` 补齐通用本体 9 类配色；右侧卡片加宽至 400px；
- 验证：后端新增 15 个测试（列表分页/搜索/空、更新描述/改名改类型/冲突/404/非法类型 + 路由 503/200/404/409/400），全量 **542 测试通过**（pytest exit 0）；真实 Neo4j 验证：kb 98b88fd5 列表 371 实体、搜索"甲"命中 16、临时实体改名/改类型成功且清理；前端 `npm run build` 构建成功。

## 2026-08-24

- 需求：实体抽取提示词接入提示词管理（场景 `entity_extraction`，DB 激活模板优先 + 内置 fallback 兜底）。
- 方案：`docs/plan/34-graph-extraction-prompt-plan.md`。
- 改动：
  - 迁移 `0016_entity_extraction_default_prompt`：seed `entity_extraction` 默认模板（内容 = 原硬编码 `_EXTRACTION_SYSTEM_PROMPT`，is_active + is_default），downgrade 删除该默认模板；
  - `core/graph_extractor.py`：`_EXTRACTION_SYSTEM_PROMPT` → `FALLBACK_ENTITY_EXTRACTION_PROMPT`（内置兜底常量，内容不变）；新增 `_get_extraction_prompt()`——优先读提示词管理 `entity_extraction` 激活模板并经 `render_prompt` 渲染（无变量），缺失/DB 异常回退常量；`_llm_extract_batch` 改用它作为 system 消息；
  - `docs/09-graph-module-design.md`：§5.1 管线图示 + §5.3 标注"提示词由提示词管理维护"及加载要点；
  - 前端无需改动（`PromptManagementView` 已支持 `entity_extraction` 场景筛选/下拉）。
- 验证：新增 3 个提示词加载单测（激活模板生效 / 无激活回退 / DB 异常回退）；后端全量测试通过（pytest exit 0）。

## 2026-08-24

- 需求：按 `docs/09-graph-module-design.md`（v1.1 已评审）实现知识图谱模块——每知识库一个图谱（Neo4j + kb_id 隔离）、全量/单文件增量构建、单文件清除、ECharts Web 可视化、图检索 API（chunk 级证据）、词库分类枚举化。
- 方案：`docs/plan/33-graph-module-plan.md`。
- 改动（后端）：
  - 配置/部署：`config.py` 新增 Neo4j 图谱配置段（`graph_enabled`/`neo4j_*`/`graph_extraction_batch` 等，默认关闭优雅降级）；`.env`/`.env.example` 增加 `NEO4J_*`；`docker-compose.yml` 新增 neo4j 服务；`pyproject.toml` 移除 `graph` extra（llama-index-graph-stores-neo4j 全代码零引用），`neo4j>=5.18.0` 移入主依赖；
  - 新增 `core/neo4j_store.py`（driver 单例、execute_read/write/write_many、health_check、ensure_indexes，同步调用走线程池）；`core/graph_queue.py`（build_file/build_kb/clear_file 独立 FIFO 队列，与解析队列解耦）；`core/graph_extractor.py`（词库匹配 + 金额/日期正则 + LLM 批量 Schema 约束抽取 + 合并去重 + **chunk 回原文归属** + **词库归并**）；`models/graph_schemas.py`（抽取输出校验 + 图检索请求/响应）；`services/graph_service.py`（构建/清除/查询编排 + 证据查询 + 关系类型白名单防注入）；`routers/graph.py`（图谱管理 + 图检索 API，未启用时 503）；
  - 迁移 `0014_graph_status`：documents 加 `graph_status/graph_error_message/graph_built_at/graph_duration_ms` + `(kb_id, graph_status)` 索引；lexicon_terms 空分类统一归 `other`；
  - `models/document.py`/`schemas.py`：Document 加 4 个图谱字段，DocumentResponse 补齐；`repositories/document_repository.py`：update_graph_status/list_chunks_for_graph/get_chunks_by_ids/get_by_ids/reset_stale_graph_status/invalidate_all_graphs；
  - `services/lexicon_service.py`：缓存结构扩展为 `{标准词: {aliases, category}}`（expand_query 同步改造）+ 新增 `match_entities`/`normalize_category`（中文历史值映射、未知归 other）+ CSV 导入分类自动映射 + 词条变更后联动 `invalidate_all_graphs`（风险 10）；
  - 联动清除（§4.3 调用契约）：`document_service.delete_document`/`reparse_document`/`rechunk_document` 在删 PG 前调用 `clear_file_graph_best_effort`（先取 chunk_ids 再清 Neo4j，失败不阻断主流程，状态置 none/failed）；
  - `main.py`：注册 graph router、启动/停止 graph_queue（仅 graph_enabled 时）、Neo4j 索引初始化（best-effort）、启动时重置滞留 building → none、health 返回 neo4j 状态。
- 改动（前端）：
  - 新增 `src/api/graph.js`、`src/views/GraphView.vue`（ECharts 力导向图：类型着色/degree 大小、点击节点展开详情、chunk 证据高亮 + 跳转 ChunkListView、全量构建/清空/检索、构建中提示遮罩、自动轮询刷新）；`src/router/index.js` 加 `/knowledge-bases/:kbId/graph`；
  - `DocumentListView.vue`：图谱状态列（已建图/构建中/建图失败/未建）+ 建图/重建图/清图按钮（二次确认）+ 查看图谱入口；
  - `LexiconView.vue`：分类改为固定枚举下拉（公司/机构、项目、人员、地点、资质、其他/术语），列表/筛选/编辑/CSV 导入提示同步枚举化，旧中文分类编辑时自动映射；
  - `npm install echarts`。
- v1.1 评审决策落实：①LLM 实体 chunk 归属（回原文定位，失败降级首 chunk）②词库缓存结构扩展 ③词库变更联动置 none ④去 PropertyGraphIndex ⑤关系类型白名单 12+ 种（09 文档为单一事实源）⑥空串/未知分类归 other ⑦词库归并（精确+包含，同类型才合并）⑧联动清除 best-effort ⑨纯 Cypher 去重（reduce，去 APOC 运行时依赖）⑩写入按语句批拆事务（每批 100 条）⑪mentioned_chunk_ids 不设上限；低优先级项：description 先到先得、GET /graph 按 degree 降序采样、entities 接口改 query 参数、中文关系类型反引号、检索侧补正则层、health 扩展、构建中提示。
- 验证：后端全量 **520 测试通过**（新增 55 个图模块/词库用例）；前端 `npm run build` 构建成功；GraphView/文档列表/词库枚举均编译通过。本机无 docker CLI，Neo4j 集成验证待部署机（192.168.60.120，GRAPH_ENABLED=true + compose neo4j）执行。

## 2026-08-24

- 需求：接入真实 Neo4j（192.168.60.121:7687，neo4j/111111_a），完成知识图谱模块真实环境端到端验证与修复。
- 改动：
  - `backend/.env`：`GRAPH_ENABLED=true`，NEO4J_URI/USERNAME/PASSWORD 指向 192.168.60.121；
  - 执行 `alembic upgrade head`（0014）：documents 图谱状态列 + lexicon_terms 空分类归 other（env.py 用 os.getenv 不读 .env，命令前需注入 PG_* 环境变量）；
  - 修复 4 个真实环境 Cypher/接口 bug：
    1. `graph_extractor._llm_extract_batch`：dict 消息列表 → `ChatMessage`（llama-index achat 校验失败）；
    2. Neo4j 5 兼容性：stats 查询去掉 `GROUP BY`（Cypher 隐式聚合）；所有 `MATCH (x:Entity {参数 map})` 改 WHERE 形式（42N32）；`size((n)--())` 改 `COUNT { (n)--() }`（42I52）；变量长度关系跳数 `[r*0..$hops]` 改字符串拼接（max_hops clamp 1..3，42N32）；
    3. `$query` 参数名与 neo4j driver `tx.run(query=...)` 关键字冲突 → 改名 `$q`；
    4. `get_graph_stats` 构建进度统计 bug：`completed` 状态未映射 `built`（被计入 none）；`main.py` health 误用模块级 `health_check`（应为 `Neo4jStore.health_check()`）；
  - `tests/conftest.py`：新增 autouse fixture 隔离 `.env`（测试默认 graph_enabled=False，防 GRAPH_ENABLED=true 污染单测）。
- 真实环境端到端验证（kb 98b88fd5，24-chunk 文档）：
  - 构建：**122 实体 + 123 关系**（Project 35/Qualification 57/Organization 3/Location 3/Money 1/Event 23），graph_status=completed，built=1；
  - 图检索：子图 32 节点 + 30 边 + **31 组 chunk 证据**（页码/文本正确，证据查 document_chunks 实时关联）；
  - 实体详情：Project 类型、degree 31、30 邻居、3 条证据；
  - 清除单文件（G-05）：122 → 0 实体精确剥离，graph_status → none；重建恢复 146 实体；
  - 全图数据（ECharts）：按 degree 降序采样 100 节点 + 107 边。
- 验证：后端全量 **520 测试通过**（pytest exit 0）；前端 `npm run build` 构建成功。

## 2026-08-24

- 需求：图谱可视化优化——大图卡顿、关系不显示、chunk 证据查看方式调整。
- 改动：
  - **关系不显示修复**：后端节点 id 为 `类型_名称` 而边 source/target 为 `_名称`（格式不一致，ECharts link 匹配不上）——边查询补两端类型，构造与节点一致的 id（`get_graph_data`/`search_subgraph`），真实数据验证 100 节点/105 边/0 不匹配；
  - **chunk 证据改弹窗**：`GraphView.vue` 移除跳转 ChunkListView，"查看原文"改为 el-dialog 展示完整切片文本（文件名/页码/切片序号 + 实体全文高亮，`highlightFull`）；
  - **图大卡顿优化**：
    - 后端 `get_graph_data` 分页化（`limit` 默认 500→150 + `offset`/`exclude_names` + 返回 `total_nodes`/`has_more`；增量加载返回「已加载 ∪ 新节点」之间边，跨批边不断裂），router 传 `Query` 参数；
    - 前端：默认只渲染核心子图（按 degree 降序前 150），"加载更多"按钮增量合并（节点/边按 id 去重）；ECharts 配置优化——`animation: false`（关动画）、标签默认隐藏（degree≥5 核心节点常显，悬停 emphasis 显示）、`labelLayout.hideOverlap`、force 物理参数调优（friction 0.6/repulsion 160/gravity 0.08）、节点 size 上限收紧、`lazyUpdate`；
  - 附带修复：GraphView `detail` 初始 null 的 `detail.entity` 空指针（改可选链 `detail?.entity`）。
- 验证：后端分页接口真实数据验证（311 节点分页、跨批边正确）；后端全量 520 测试通过；前端 `npm run build` 构建成功。

## 2026-08-24

- 需求：图谱可视化改为「**节点数分级、逐层展开**」浏览方式（可行性确认：以实体为中心的交互式局部探索，节点数始终可控，天然规避大图卡顿）。
- 改动：
  - 后端新增 `expand_node(kb_id, name, limit)`：以指定实体为中心返回 1-hop 邻居节点 + 「实体 ∪ 邻居」之间边（实体不存在返回 None）；router `POST /graph/expand`（`GraphExpandRequest` schema，limit ≤ 300）；
  - 前端 `GraphView.vue`：详情面板新增「**展开邻居**」按钮——调 expand API 将 1-hop 邻居合并入画布（`mergeGraphData` 节点/边去重，loadMore 复用同一合并函数）；已展开节点以深色边框标记；展开/加载更多两条浏览路径并存；
  - 真实数据验证：degree 36 的中心实体展开返回 35 邻居 + 35 边；不存在的实体返回 404。
- 验证：后端 graph 相关 17 测试通过；前端 `npm run build` 构建成功。

## 2026-08-24

- 需求：实体/关系类型改为**通用本体**（9 类实体 + 12 种关系，不绑定投标场景），旧投标数据清空重建。
- 设计确认：实体 9 类（Organization/Person/Place/Event/Time/Project/Product/Quantity/Concept，覆盖原 Money→Quantity、Qualification→Concept、Location→Place、Date→Time）；关系 12 种（属于/位于/发生在/参与/负责/合作/关联/包含/要求/拥有/数量/日期，含兜底"关联"）；旧数据清空重建。
- 改动：
  - `core/graph_extractor.py`：`ENTITY_TYPE_LABEL_MAP` 更新为 9 类通用映射；抽取 prompt 重写（通用实体/关系 + 方向类型约束 + 兜底"关联"）；正则 `_MONEY_RE` 扩为数量（含吨/kg/年等单位）、类型改 Quantity/Time；
  - `services/graph_service.py`：`RELATION_TYPES` 更新为通用 12 种白名单（旧中标/招标/分包等自动丢弃）；
  - `services/lexicon_service.py`：`LEXICON_CATEGORY_ENTITY_TYPES`（place/product/concept）+ `_CATEGORY_LEGACY_MAP`（location→place、qualification→concept、中文值映射）；
  - `models/graph_schemas.py`：`ENTITY_TYPES` Literal 更新为 9 类；
  - 迁移 `0015_lexicon_category_generic.py`：历史分类数据归一（中文值/旧枚举→通用枚举，无法识别归 other）；
  - 前端 `LexiconView.vue`：分类下拉更新为 7 枚举（公司/机构、人员、地点、项目、产品、概念/资质、其他/术语）+ 历史映射；
  - 文档 `docs/09`：§1.3 决策表、§3.1 类型标签、§5.2.1 词库分类表、§5.3 prompt（通用本体）、§6.1/§6.3 检索示例、§7.3 映射说明同步更新。
- 真实环境验证（kb 98b88fd5 清空重建）：实体类型 **Quantity 56 / Concept 467 / Product 206 / Time 1 / Organization 4**，旧类型（Money/Qualification/Date/Location）**零残留**；关系全部为新白名单类型。
- 验证：后端全量 **520 测试通过**（pytest exit 0）；前端 `npm run build` 构建成功。

## 2026-08-24

- 需求：检索页集成知识图谱检索（范围确认：仅检索页，不做 chat 双通道；图证据并入结果列表参与融合）。
- 改动：
  - `models/schemas.py`：`SearchRequest.retrieval_methods` 增加 `graph` 选项；`SearchResultItem` 增加 `graph_entity` 字段（图检索来源实体标记）；
  - `services/search_service.py`：`VALID_METHODS` 增加 `graph`；新增 `_recall_graph`——逐 kb 调 `graph_service.search_subgraph`（1-hop），实体证据切片转结果项（score=1.0，RRF 只看排序位置），同一切片多实体去重合并 graph_entity，图谱未启用/检索失败降级为空不阻断；单选直返/多选 RRF 融合，与 dense/sparse/fulltext 完全同构；结果组装输出 `graph_entity`；
  - 前端 `SearchView.vue`：检索策略多选增加「知识图谱」；结果项显示绿色「图谱·实体名」tag，点击新窗口跳转图谱页并自动检索该实体（`?entity=` 参数）；`GraphView.vue` onMounted 读取 `?entity` 自动执行实体检索；
  - 测试：新增 `tests/test_search_graph.py`（单选返回证据/未启用降级/dense+graph RRF 融合/同切片去重）；既有 4 处 "graph 作为非法方法" 用例改为真正非法值（bm25）；
  - 文档：`docs/09` §1.4 边界更新（检索页图检索已集成，chat 双通道仍为下阶段）。
- 验证：真实环境 graph 通道检索返回 3 条图证据切片；后端全量 **524 测试通过**（pytest exit 0）；前端 `npm run build` 构建成功。

## 2026-08-22

- 需求：`docs/09-graph-module-design.md`（知识图谱模块详细设计）评审，确认 11 项决策并修订至 v1.1。
- 评审确认决策：
  - 高优先级：① LLM 实体 chunk 归属——prompt 约束实体名与原文一致 + 回原文定位，失败降级该批第一个 chunk；② 词库缓存结构扩展为 `{标准词: {aliases, category}}`；③ 词库变更 → 已建文档 graph_status 置 none/stale 提示重建；④ 移除 `llama-index-graph-stores-neo4j`，后续实体向量检索用 Neo4j 原生 vector index + `create_embedding` 自建；
  - 中优先级：⑤ 关系类型维持 12 种（09 文档为单一事实源，注明与 03 文档差异）；⑥ 空串/未知分类归 `other`，CHECK 约束不含空串；⑦ LLM 实体名在合并阶段词库归并（精确+包含匹配）；⑧ 联动清除 best-effort（先取 chunk_ids 再删 PG、失败不阻断、delete/reparse/rechunk 后置 none）；⑨ 纯 Cypher 去重，去掉 APOC 运行时依赖；⑩ 写入按 chunk 批次拆分事务；⑪ `mentioned_chunk_ids` 不设上限（接受现状）；
  - 低优先级 8 项按默认处理（description 先到先得、GET /graph 按 degree 降序采样、entities 接口改 query 参数、中文关系类型反引号、检索侧补正则层、health 扩展、构建中提示、pytest 用真实 Neo4j 实例）。
- 改动（仅文档）：`docs/09-graph-module-design.md` 修订 §1.4/§2.3/§3.2/§4.3/§4.4/§5.1/§5.2/§5.2.1/§5.3/§6.1/§6.3/§6.4/§7.1/§7.3/§9.1/§9.2/§9.3/§10.1/§10.3/§11.3/§12/§13。
- 验证：章节结构完整（grep 确认）；纯文档改动，无代码。

## 2026-08-21

- 需求：问答（chat）检索接入三路——稠密向量（dense）+ 稀疏向量（sparse）+ 全文检索（fulltext）。已确认：不加前端控件，后端固定三路混合、默认三路全开（推翻 08 设计文档 §1.3「chat 保持纯稠密」边界）。
- 方案：`docs/plan/32-chat-multi-retrieval-plan.md`。
- 改动：
  - `backend/config.py`：新增 `chat_retrieval_methods: list[str] = ["dense", "sparse", "fulltext"]`（问答固定三路混合检索策略，可经环境变量调整）；
  - `backend/core/retrieval.py`：新增公共 `rrf_merge()`（从 search_service 迁移，search/chat 共用）；`retrieve_and_rank()` 扩展 `retrieval_methods` / `expand_query` 参数——多路独立召回（dense/sparse 走 `retrieve`，fulltext 走 `es_store.es_search` + 词库查询扩展 + top_k 放大 + 不高亮），单路失败降级为空不阻断问答，单选直返/多选 RRF 融合，再衔接可选 rerank + 阈值过滤；不传 `retrieval_methods` 时默认纯稠密（向后兼容）；
  - `backend/services/search_service.py`：删除私有 `_rrf_merge`，改为从 `core.retrieval` 导入 `rrf_merge`（`_rrf_merge_traced` span 包装保留）；
  - `backend/services/chat_service.py`：`_run_rag` 检索改传 `settings.chat_retrieval_methods` 三路 + `expand_query=lexicon_service.expand_query`（`ensure_cache` 失败仅记日志，不阻断问答）；
  - 测试：`tests/test_retrieval.py` 新增 4 用例（三路混合召回与 RRF 融合、fulltext 异常降级、单选 fulltext 直返、非法方法校验）；`tests/test_search_es.py` 的 `_rrf_merge` 引用改为 `core.retrieval.rrf_merge`；
  - 文档：`docs/08` §1.2/§1.3/§7.1/§13 更新 chat 边界为三路混合。
- 验证：后端全量 479 测试通过；前端无改动。

## 2026-08-20

- 需求：重新设计 Langfuse trace 点——span 类型分类（CHAIN/SPAN/GENERATION）、统一命名（小写蛇形）、统一 `traced_span` 装饰器（自动 elapsed_ms/error）。评审确认：不新增 prepare/config_resolve/kb_name_resolve 环节 span。
- 方案：`docs/plan/31-langfuse-trace-redesign-plan.md`。
- 改动：
  - `core/observability.py`：新增 `traced_span(name, span_type)` 装饰器——基于 observe（v4 无 start_span），支持 async/sync 函数、静态/可调用 name（如 `recall_{method}` 动态命名）、自动计时 `elapsed_ms`、异常自动记 `error`；
  - `core/retrieval.py`：`_embed_query` → `@traced_span("embedding", "GENERATION")`；`_milvus_search` → `@traced_span("milvus_query")`；`retrieve` → `@traced_span("retrieve")`；`rerank` → `@traced_span("rerank", "GENERATION")`；
  - `services/search_service.py`：`_recall_milvus` → 动态名 `recall_{method}`；`_recall_fulltext` → `recall_fulltext`；`_rrf_merge_traced` → `rrf_merge`；`search` → `@traced_span("search", "CHAIN")`；
  - `core/es_store.py`：`es_search` → `@traced_span("es_query")`；
  - `services/chat_service.py`：`_run_rag` → `@traced_span("rag_qa", "CHAIN")`（LLM generation span 保持）；
  - 测试：`tests/test_observability.py` 新增 `TestTracedSpan`（async/sync/动态名/异常 error 四用例）；既有 span 断言（retrieval/search_es/es_store）保持通过；
  - 文档：`docs/08` §7.6 重写（span 类型分类 + 调用树 + traced_span 约束）。
- 验证：后端全量测试通过；前端无改动。

## 2026-08-20

- 需求：RRF 融合环节（多选召回时的 Python 端融合）也要记录到 Langfuse（用户确认"RRT"指 RRF）。
- 方案：`docs/plan/30-langfuse-rrf-tracing-plan.md`。
- 改动：
  - `backend/services/search_service.py`：新增 `_rrf_merge_traced`（`@observe(name="rrf_merge")` 嵌套函数，langfuse v4 无 start_span 沿用 observe 模式）——记录 input（per_method_hits 各路命中数）/ metadata（method=rrf, k）/ output（融合结果明细），内部委托原 `_rrf_merge`；`search()` 多选分支改调 `_rrf_merge_traced`，单选直返不走 RRF；
  - 测试：`tests/test_search_es.py` 新增 2 个用例（多选时 rrf span 记录 method=rrf/k/各路命中数/融合输出；单选不出现 rrf span）；
  - 文档：`docs/08` §7.6 补充 RRF 融合 span。
- 验证：后端全量测试通过；前端无改动。

## 2026-08-20

- 问题：29 号改动上线后 sparse/dense 检索报 `'Langfuse' object has no attribute 'start_span'` —— langfuse 4.14.4 客户端**无手动 `start_span` API**（v4 已移除）。
- 修复：`backend/core/retrieval.py` 将 `start_span` 子 span 改为 **`@observe` 嵌套函数**——新增 `_embed_query`（向量化子 span，仅 dense 调用）与 `_milvus_search`（Milvus 检索子 span），observe 自动将二者挂为 `retrieve` 的子 span；`retrieve()` 内调用方式改为 `await _embed_query(...)` / `await _milvus_search(...)`（关键字参数）。
- 测试：`tests/test_retrieval.py` 子 span 用例改为断言 `_embed_query` / `_milvus_search` 调用（dense 两者都调、sparse 只调 `_milvus_search` 且 embed_ms 为 None、collection 不存在时都不调）。
- 验证：后端全量测试通过。

## 2026-08-20

- 需求：dense 检索链路 Langfuse 追踪细化——向量化（embedding）作为独立子 span 可见，retrieve span 记录环节级耗时（支撑"dense 检索有时超时"定位）。
- 方案：`docs/plan/29-langfuse-dense-tracing-plan.md`。
- 改动：
  - `backend/core/retrieval.py`：`retrieve()` 内新增嵌套子 span——`embedding`（仅 dense：输入 query，metadata 含 model/dim/api_base，输出向量维度）与 `milvus_search`（metadata 含 collection/mode/top_k/阈值，输出命中数）；retrieve 当前 span metadata 增加 `embed_ms`/`search_ms`/`filter_ms`（sparse 的 embed_ms 为 null）与 `embedding_model`/`dim`/`collection`；
  - 测试：`tests/test_retrieval.py` 新增 3 个 span 用例（dense 有 embedding+milvus_search span 且带环节耗时、sparse 无 embedding span 且 embed_ms 为 null、collection 不存在时不创建子 span）；
  - 文档：`docs/08` §7.6 补充 dense/sparse 逐 kb span 细化描述。
- 验证：后端全量测试通过；前端无改动。

## 2026-08-20

- 需求：词库管理页去掉「状态」相关 UI（用户确认全部去掉：表格「状态」列 + 新增/编辑弹窗「启用」开关）。
- 方案：`docs/plan/28-lexicon-remove-status-plan.md`。
- 改动：`frontend/src/views/LexiconView.vue`——删除表格「状态」列（el-switch 开关）与弹窗「启用」表单项；删除不再使用的 `toggleEnabled` 方法；`form` 初始值/`resetForm`/`openEdit` 去掉 `enabled` 字段。后端 `enabled` 字段与接口保持不变（新增词条默认启用，`LexiconTermCreate.enabled` 默认 True）。
- 验证：前端 `npm run build` 通过；后端无改动。

## 2026-08-20

- 需求：ES 对账页「知识库对账」表只显示 kb_id，需要同时显示知识库名称；且改为后端接口分页（已确认非前端切片）。
- 方案：`docs/plan/27-es-admin-kbname-pagination-plan.md`。
- 改动：
  - `backend/services/es_admin_service.py`：`reconcile_status(page=1, page_size=20)`——新增查询 `knowledge_bases` 名称映射，`kbs` 每行加 `kb_name`（PG 中已删除的库为空串）；`kbs` 按页切片；响应新增 `kb_total`（知识库总数，分页用）；`diff_count`/`differences` 仍按全量统计；
  - `backend/routers/es_admin.py`：`GET /api/es/status` 增加 `page`、`page_size` 查询参数；
  - `frontend/src/api/esAdmin.js`：`getEsStatus(page, pageSize)` 透传分页参数；
  - `frontend/src/views/EsAdminView.vue`：知识库对账表新增「知识库名称」列（位于「知识库 ID」右侧，名称为空回退显示 kb_id）；表下加 `el-pagination`（total 用 `kb_total`，支持 10/20/50 每页，翻页/改页大小重新请求，删除库后空页自动回第 1 页）；页头统计文案改 `kbTotal`；
  - 测试：`test_es_admin_service.py` 补 `kb_name` 断言与分页/超范围页码用例；`test_es_admin_router.py` 补分页参数透传断言。
- 验证：后端全量测试 466 passed；前端 `npm run build` 通过。

## 2026-08-20

- 需求：ES 对账页每个知识库都显示"重建"按钮（不论是否有差异），方便词库更新后随时按库重建（让存量文档按新词典重新分词）。
- 改动：`frontend/src/views/EsAdminView.vue` 知识库对账表格操作列——移除"仅差异库显示重建该库/一致显示无需操作"的条件，改为**每行常显"重建"按钮**（按 kb 重建，含确认弹窗与 loading 态）；页头提示补充"每个知识库均可手动重建（如词库更新后按新词典重新分词）"；删除不再使用的 `.muted` 样式。
- 验证：前端 `npm run build` 通过；后端无改动。

## 2026-08-20

- 需求：新增词条"冠状动脉粥样硬化性心脏病"后全文检索仍按碎片分词/高亮（冠状/动脉粥样硬化/心脏病）；同时用户指出原"ES 经 remote_ext_dict 反向轮询后端拉词库"架构不合理（基础设施依赖业务应用），并要求改为**纯人工流程**：导出词库文件 → 人工放置到 ES 词典目录 → 重启 ES 自己加载。
- 方案：`docs/plan/26-ik-dict-term-activation-plan.md`（终版：人工导出放置 + 挂载 + 重启）。
- 改动：
  - 服务器 192.168.60.120：`/opt/es/config/IKAnalyzer.cfg.xml` 改本地词典方案（`ext_dict=extra_main.dic;dicts/ik_ext.dic`，`remote_ext_dict` 清空）；词库词典文件 `/opt/es/dicts/ik_ext.dic`（一行一词，UTF-8 无 BOM）；**重建 bid-es 容器**新增挂载 `config/IKAnalyzer.cfg.xml` 与 `/opt/es/dicts`（→ 容器 `config/analysis-ik/dicts/`，数据卷保留，对账 0 差异）；**删除同步脚本** `/opt/es/sync_ik_dict.sh`（无自动链路）；
  - 按库重建 ES 索引：`POST /api/es/rebuild?kb_id=98b88fd5-...`（医学词汇库，24 切片，其他库不动）；
  - 前端：`LexiconView.vue` 新增"导出 IK 词典"按钮（下载 ik_ext.dic + 操作提示），提示文案改为"需导出并放置到 ES 词典目录后重启 ES 生效"；`api/lexicon.js` 新增 `exportIkDict`；
  - 文档：`docs/08` §1.2/§2/§3.2/§4.2/§4.3/§5.6/§6.2-§6.5/验证/风险表 → 人工导出放置流程；`elasticsearch/config/IKAnalyzer.cfg.xml` 同步（ext_dict → dicts/ik_ext.dic）。
- 验证：`_analyze` ik_smart/ik_max_word 输出整词"冠状动脉粥样硬化性心脏病[0-12]"（原为 4 个碎片）；ES 搜索整词高亮 `<em>冠状动脉粥样硬化性心脏病</em>`（原碎片高亮），total 244→1（整词精确匹配，召回更精准）；对账 4 库 pg=es diff=0；后端无代码改动；前端 `npm run build` 通过。

## 2026-08-20

- 需求：检索三路（dense/sparse/fulltext）Langfuse 追踪增强——每路独立 span（method/命中数/耗时/命中明细），search 总 span 汇总每路命中数，fulltext 路补齐缺失的 ES span。
- 方案：`docs/plan/25-langfuse-retrieval-tracing-plan.md`。
- 改动：
  - `core/es_store.py`：`es_search()` 加 `@observe(as_type="retriever")` 与 span 记录（method=fulltext/kb_ids/top_k/hit_count/elapsed_ms + 命中明细；空查询与 ES 异常分支也记录 hit_count=0）；langfuse `get_client` 以 `lf_get_client` 别名导入（避免与 ES client 工厂命名冲突）；
  - `services/search_service.py`：`_recall_milvus`/`_recall_fulltext` 加 `@observe` + 计时（elapsed_ms）+ span 记录；`search()` 收集 `per_method_hits` 写入总 span metadata；
  - 测试：`test_es_store.py` 补 es_search span（成功/异常/空查询三分支）；`test_search_es.py` 补 per_method_hits 与每路 method/hit_count/elapsed_ms 断言；
  - 文档：`docs/08` 新增 §7.6 Langfuse 检索追踪。
- 验证：后端全量测试 462 passed；前端无改动。

## 2026-08-20

- 需求：修复单选检索结果未按分数排序的问题（多知识库场景下逐 kb 拼接后未做全局降序排序）。
- 改动：`services/search_service.py` `search()` 单选分支补 `all_hits.sort(key=lambda x: x["score"], reverse=True)`（dense/sparse 跨 kb 需全局排序；fulltext 的 ES 本身有序，排序无副作用）；`tests/test_search_service.py` 新增 `test_single_method_sorts_by_score_across_kbs`（先写复现用例验证失败，修复后通过）。
- 验证：后端全量测试 458 passed；前端无改动。

## 2026-08-20

- 需求：取消检索页"自定义向量门槛"（前端勾选+滑块与后端 `rerank_vector_threshold` 参数全链路移除）。
- 改动：
  - `models/schemas.py`：`SearchRequest` 移除 `rerank_vector_threshold`；
  - `services/search_service.py`：`search()` 移除该参数；向量门槛简化为"有排序模型时 0.0（全召回交给 rerank），否则 similarity_threshold"；
  - `routers/search.py`：两端点不再透传该参数；
  - 前端 `SearchView.vue`：移除"自定义向量门槛"勾选与滑块（rerank 区保留提示文案"向量门槛 = 相似度阈值；有排序模型时全量召回交给排序模型"）；`api/search.js` 移除该字段；
  - 测试：`test_search_service.py` 删除 `test_search_rerank_vector_threshold_gate`，`test_schemas.py` 删除相关断言。
- 验证：后端全量测试 457 passed；前端 `npm run build` 通过。

## 2026-08-20

- 需求：检索参数整改——原四选一互斥模式（`vector`/`hybrid`/`fulltext`/`hybrid_es`）改为**三路独立策略可多选**（`dense` 稠密向量 / `sparse` 稀疏向量 / `fulltext` 全文检索），多选按 Python 端 RRF 融合（k=60），单选直接返回该路结果（保留原始分数语义）；默认仅勾选稠密向量；移除旧模式与 weighted 权重融合。
- 方案：`docs/plan/24-retrieval-methods-multiselect-plan.md`。
- 改动：
  - `config.py`：`retrieval_default_method` → `retrieval_default_methods: list[str] = ["dense"]`；`retrieval_hybrid_enabled` 语义更新为 Milvus 稀疏字段开关；
  - `models/schemas.py`：`SearchRequest.retrieval_method` → `retrieval_methods: list[Literal["dense","sparse","fulltext"]] | None`；移除 `hybrid_ranker`/`hybrid_weights`；
  - `core/vector_store.py`：`get_vector_store` 移除 ranker 透传；
  - `core/retrieval.py`：`_resolve_retrieval_method`/`_resolve_hybrid_ranker` → `_resolve_query_mode`（dense→DEFAULT / sparse→SPARSE）；`retrieve()` 移除 hybrid_ranker/hybrid_weights，**sparse 免 embedding 计算、跳过相似度阈值过滤**（BM25 分数非 [0,1]）；`retrieve_and_rank`（问答链路）保持纯稠密不变；
  - `services/search_service.py`：`search()` 参数改 `retrieval_methods`；抽出 `_recall_milvus`（dense/sparse 逐 kb 并发）与 `_recall_fulltext`（词库扩展+高亮）；**单选直返 / 多选 RRF**；`retrieval_methods=None` 用默认，显式空列表报错；
  - `routers/search.py`：两个端点透传 `retrieval_methods`；
  - 前端：`api/search.js` 参数改 `retrieval_methods`（数组）并移除 hybrid 字段；`SearchView.vue` 检索方式下拉改**三复选**（默认勾选稠密向量）、移除融合策略/权重 UI、结果分数展示适配（rerank/dense 显示百分比，sparse/fulltext 单选与 RRF 多选显示原始数字，修复 BM25 2400%/RRF 3% 失真）；
  - 测试：`test_schemas.py`/`test_search_router.py`/`test_search_service.py`/`test_search_es.py`/`test_retrieval.py` 全部按新参数重构，新增单选直返、多选 RRF（dense+sparse、dense+fulltext、sparse+fulltext）、sparse 免阈值、空列表报错等用例；
  - 文档：`docs/08` §1/§7/§8/§10/§11/§12/§13/§14 更新为三策略多选模型；`docs/plan/24` 新增。
- 验证：后端全量测试 458 passed；前端 `npm run build` 通过。
- 说明：sparse 依赖 Collection 已有稀疏字段（部署默认 `retrieval_hybrid_enabled=True` 创建，无需重建）；不向后兼容旧客户端参数（属预期，前后端同步整改）。

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
