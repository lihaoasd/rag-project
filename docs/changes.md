# 修改记录

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
