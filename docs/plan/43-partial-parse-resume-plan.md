# 43. 解析部分成功（partial）状态 + 断点续跑 方案

> 状态：待评审　|　关联：42-pdf-parse-chunk-plan（PDF 批量落库场景暴露）　|　类型：需求变更（一致性增强）

## 1. 问题与目标

**问题**：解析/重切片/重解析按**批次**落库（每批：图片识别 → 分块 → Milvus 向量化 → ES 双写 → PG 落库），
任一批次中途失败（如 PG 瞬时不可达）时，异常被置为 `failed`，但**此前成功的批次已留在 PG/Milvus/ES**：

- 切片列表无状态守卫 → 可查看/编辑部分切片，与 `failed` 语义冲突；
- 再次"解析"会整文档从 0 重跑，且因 PG 无唯一约束，已提交批次会**重复写入**；
- 计数与进度含义混乱（`chunk_count` 为 None 或过期值）。

**目标（需求方已确认）**：
1. 失败时**保留已成功批次**，不做回滚删除；
2. 文档状态改为明确标注**部分成功（已入库 N）**；
3. 提供**继续解析（续跑）**入口：从最后成功提交处接着写剩余部分，成功后状态 `completed`；
4. 三个入口统一：首次解析 `parse_document` / 重切片 `rechunk_document` / 重新解析 `reparse_document`
   中途失败且已有提交 → 均进入 `partial`；续跑入口只对 `partial` 文档开放。

**非目标**：不引入"失败即回滚全清"（已被需求方否决）；不改单点切片编辑（split/merge/insert_single_vector）
的向量写入路径；不为中断批做跨文档级补偿事务。

## 2. 现状事实（已核对代码）

| 环节 | 现状 | 与方案的关系 |
|---|---|---|
| documents.status | DB CHECK 约束：`uploaded,queued,uploading,processing,completed,failed`（migration 0001/0002） | 需 migration 0022 增 `partial` |
| 批次写入 | `_parse_and_store_in_batches`：`process_word_images(blocks 批) → word_chunk_blocks(批, start_chunk_index=current) → build_index → es_store.bulk_index → _store_chunk_batch`；PG 每批 commit 并 `update_status(processing, processed_chunk_count=…)` | 续跑锚点 = `processed_chunk_count`（= 已提交 PG 行数） |
| Milvus pk | llama_index `MilvusVectorStore.add()` 用 `node.node_id` 做主键；`TextNode` 未显式给 id → 随机 uuid；`delete_nodes(node_ids)` 可按 pk 删除 | 改为**确定性 pk** 后，续跑可"先删后插"实现幂等 |
| ES _id | 已是确定性 `f"{doc_id}_{chunk_index}"`（es_store._doc_id），bulk 幂等覆盖 | 无需改动 |
| PG chunk 行 | `DocumentChunk.chunk_index` 无 (doc_id, chunk_index) 唯一约束 | 续跑从 `resume_from` 起写，天然不与已提交行重叠；重复风险靠确定性索引规避 |
| 解析入口 | parse/rechunk/reparse 三函数结构几乎一致，except 均置 `failed` | 抽公共失败判定 helper |
| 队列 | `parse_queue` 任务 (type, kb_id, doc_id, cs, co)，type ∈ parse/rechunk/reparse | 新增 `resume` 类型 |
| 路由 | `/parse`：queued/processing/completed 拒绝，其余放行；`/reparse`：仅 queued/processing/uploaded 拒绝；`/rechunk`（chunk 路由）：仅 completed/failed 放行 | `/parse` 需拦截 partial（防止整文档重跑重复写）；新增 `/resume`；`/rechunk` 放行 partial |
| 图谱 | 建图守卫 `doc.status == completed`；reparse/rechunk 开始时 `clear_file_graph_best_effort` | partial 不可建图 → 无"partial 期间建图后失效"问题，无需新增失效逻辑 |
| 切片列表 | chunk 路由无状态守卫（可查） | 保留；前端对 partial 放行查看 |
| 前端 | `DocumentListView.vue`：statusLabel 无 partial；操作列解析仅 uploaded/failed、切片/建图仅 completed、重解析 completed/failed | 增 partial 标签 + "继续解析"按钮；切片按钮对 partial（processed>0）放行 |

## 3. 改动范围

| 文件 | 改动 |
|---|---|
| `backend/alembic/versions/0022_add_partial_parse_status.py`（新增） | CHECK 约束 status 增 `'partial'`（仿 0002 写法：drop + add） |
| `backend/core/indexing.py` | `build_index`：显式 `node.node_id = f"{doc_id}:{chunk_index:06d}"`（确定性 Milvus pk）；新增 `delete_vector_nodes(kb_id, node_ids)`（best-effort，to_thread `vector_store.delete_nodes`） |
| `backend/services/document_service.py` | `_parse_and_store_in_batches` 增 `resume_from=0`（写侧跳过 < resume_from 项；resume 批先 delete_vector_nodes 再 build_index）；三个函数 except 统一走 `_mark_parse_failure`（已提交>0 → partial，否则 failed）；新增 `resume_document(kb_id, doc_id)` |
| `backend/core/parse_queue.py` | `enqueue_resume` + worker 分支调用 `resume_document`（队列项复用 5 元组，type="resume"） |
| `backend/routers/document.py` | `/parse` 拦截 partial（提示走"继续解析/重新解析"）；新增 `POST /{doc_id}/resume`（仅 partial 放行，其余 400） |
| `backend/routers/chunk.py` | `/rechunk` 放行集合加 `partial` |
| `backend/tests/*` | 见 §5 |
| `frontend/src/views/DocumentListView.vue` | statusLabel + partial；操作列逻辑；轮询无需改 |
| `docs/changes.md` | 修改记录 |

## 4. 关键逻辑（伪代码）

### 4.1 确定性向量主键（Milvus）
```python
# core/indexing.build_index(chunks, metadatas, kb_id)
for node, meta in zip(text_nodes, metadatas):
    node.node_id = f"{meta['doc_id']}:{meta['chunk_index']:06d}"   # pk 确定性
node_ids = await asyncio.to_thread(vector_store.add, text_nodes)    # add 返回的即确定性 id
```
- ES：_id = f"{doc_id}_{chunk_index}"（既有）幂等覆盖，不改。
- PG `milvus_pk` 存该确定性 id（String(64) 足够：36+1+6）。
- 正常全量解析第一次写入即可用（delete flows 按 ref_doc_id 删，不依赖 pk 值；兼容）。

### 4.2 批次执行器（续跑锚点 = 已提交数）
```python
async def _parse_and_store_in_batches(parsed, file_type, doc_id, kb_id, file_name,
                                      ocr_config, vision_config, *, resume_from=0,
                                      chunk_size=None, chunk_overlap=None):
    ... # 解析 PDF/Word → blocks（不变）
    current = 0
    for batch in chunks_of(blocks, batch_size):
        await process_word_images(batch, kb_id, doc_id, ocr_config=..., vision_config=...)  # 与现状一致
        chunks, metas, _ = word_chunk_blocks(batch, doc_id, file_name, start_chunk_index=current)
        pending = [(n, m) for n, m in zip(chunks, metas) if m["chunk_index"] >= resume_from]
        if pending:
            p_chunks, p_metas = zip(*pending)
            if resume_from:
                # 中断批可能已写 Milvus 而未提交 PG → 先按确定性 pk 精确删除本批待写范围
                await delete_vector_nodes(kb_id, [f"{doc_id}:{m['chunk_index']:06d}" for m in p_metas])
            pks = await build_index(p_chunks, p_metas, kb_id)
            await es_store.bulk_index(p_chunks, p_metas, kb_id)     # ES 幂等覆盖
            await _store_chunk_batch(doc_id, kb_id, p_metas, pks, ...)  # 提交数 = 前缀(已存在)+新增
        current += len(chunks)
    return current, page_count
```
> 说明：为保持索引编号与全量解析一致，仍对**所有批**做图片识别与分块（`word_chunk_blocks` 结果纯函数确定），
> 写侧仅跳过 `< resume_from` 的项。续跑是低频恢复路径，全量重算换取"简单且严格一致"；
> 图片会重复上传/识别（幂等可接受），后续如需优化可再按真实批次边界持久化（不纳入本次）。

### 4.3 失败状态判定（三入口统一）
```python
async def _mark_parse_failure(doc_id, error_msg, duration_ms):
    committed = await _committed_chunk_count(doc_id)          # PG 实际已入库行数（count，权威）
    new_status = "partial" if committed > 0 else "failed"      # partial 保留 chunk_count 旧值
    await doc_repo.update_status(doc_id, new_status, error_message=error_msg,
                                 processed_chunk_count=committed,
                                 parse_duration_ms=duration_ms)
    if committed > 0:
        logger.warning("解析部分成功: doc_id=%s, 已入库=%d, 错误=%s", doc_id, committed, error_msg)
```

### 4.4 续跑入口
```python
async def resume_document(kb_id, doc_id):
    doc = get doc                                   # 需 doc.status == "partial"（worker 侧校验，防呆）
    was_never_completed = doc.chunk_count is None   # 首次解析 partial → 完成后才 +1 知识库计数
    resume_from = count_chunks_by_doc_id(doc_id)    # PG 实际已入库行数（权威续跑锚点）
    update_status(processing, error_message="")
    下载文件 → 解析（同 parse_document）
    chunk_count, page_count = _parse_and_store_in_batches(parsed, ..., resume_from=resume_from)
    update_status(completed, page_count, chunk_count, processed=chunk_count, duration)
    if was_never_completed:
        await kb_repo.increment_doc_count(kb_id)
```

### 4.5 队列与路由
```python
# parse_queue
def enqueue_resume(kb_id, doc_id):  put(("resume", kb_id, doc_id, None, None))
worker:  elif task_type == "resume": await resume_document(kb_id, doc_id)

# routers/document.py
POST /parse:      现有守卫 + status == "partial" → 400 "文档部分成功，请使用继续解析或重新解析"
POST /{doc_id}/resume:
   404 if not exists; 400 if queued/processing; 400 if status != "partial"（提示走 /parse 或 /reparse）
   status → queued; enqueue_resume; 返回 {id, status: queued, queue_position}
# routers/chunk.py rechunk 守卫: doc["status"] in ("completed", "failed", "partial")
```

### 4.6 前端（DocumentListView.vue）

```vue
statusLabel: partial → '部分成功'
操作列：
  v-if="row.status === 'partial'"  → 继续解析(handleResume → POST /resume)【primary】
  v-if="row.status === 'completed' || row.status === 'partial'
       || (row.status === 'failed' && (row.processed_chunk_count ?? 0) > 0)" → 切片(跳转 chunks 页)
       # 解析中途失败（partial）可查看已入库切片；旧版本遗留 failed 且仍有残留切片时同样放行
  v-if="row.status === 'partial'"  → 重解析、删除（同 failed 布局）
  status-tag 颜色 class 沿用现有样式（无样式时保持默认）
```

### 4.7 续跑执行细化（PDF / Word 分别）

两个格式在**解析阶段**不同、在**续跑阶段共用同一执行器**（§4.2/4.4），差异仅在
解析产物与分块语义。续跑锚点一律是 **PG 已提交行数 k = `processed_chunk_count`**（每成功一批即持久化），
与格式无关。下面分别列出各自续跑时的逐步执行与一致性前提。

#### 4.7.1 续跑通用步骤（五步）
1. `resume_document` 读文档记录：`status==partial`；续跑锚点 `resume_from` = PG 实际已入库行数
   （`count_chunks_by_doc_id`，权威，不依赖 processed 进度字段的提交时序）；
2. 状态 → processing，从 MinIO 重新下载**同一文件**（object_key 不变）；
3. 重新执行"解析 → 逻辑块 → 逐批分块"整条上游（见下分格式），产出与首次**相同的全局 chunk 序列**；
4. 写侧过滤：`chunk_index < resume_from` 的节点跳过（其 PG 行/向量/ES 已存在），
   `≥ resume_from` 的节点：先按确定性 pk 删 Milvus（清中断批残留）→ build_index → ES 幂等覆盖 → PG 落库，逐批推进；
5. 全部完成 → status=completed，`chunk_count`=全局序列总数 N、`processed=N`、page_count 更新；
   若该文档从未 completed（`chunk_count` 为空）则知识库文档计数 +1。

> 一致性前提：文件未变 + 解析/分块确定性（Word/PDF 均满足，见下表各格式说明），
> 因此重跑出的 chunk 编号 0..N-1 与首次一致，前缀 0..k-1 与已提交行一一对应，续跑只补 k..N-1，无重叠无空洞。

#### 4.7.2 Word（.docx / .doc）续跑细则
- 上游：`DocxParser.parse` → `ParagraphItem[]`（无 page_number）→ `aggregate_paragraphs` 按
  heading/style 聚逻辑块 → `word_chunk_blocks` 每块产出 TextNode（metadata.chunk_index 全局递增，
  page_number 恒默认 1）。分块是**纯文本函数**（按段落文本 + heading 层级），docx 压缩包 XML 不变则结果逐字节确定。
- 图片：文档图片经 `process_word_images` 提取上传 MinIO 并把 OCR/vision 文字并入块正文——正文含识别文本，
  因此重跑时**图片块需重新 OCR**（与首次一致才保证文本相同；失败概率场景内容未变，结果稳定）。
- 续跑中途失败的窗口：任意批次 PG commit 前失败 → 已提交数不变（k 不增），再次续跑仍从 k 继续；
  若失败发生在某批 PG commit **后**（比如后续批次图片/嵌入 API 失败），k 已前移，续跑自动从新 k 起，永不重复写已提交行。
- 边界示例：文档共 N=12 个 chunk；首次解析在写入 chunk 8（索引 7）时 PG 超时 → 已提交 k=8（索引 0..7）。
  续跑重算全序列 0..11 → 写侧从索引 8 起：删除 pk doc:000008..doc:000011（防中断批残留）→
  向量化/ES/PG 落索引 8..11 → completed（chunk_count=12）。若中断批当时已写入 Milvus/ES 但未入 PG，
  其索引恰为 8..（该批范围），先删后插收敛为单份。
- 表格：Word 表格按段落块处理（无 pdfplumber 层），无去重等中间态，续跑无特殊处理。

#### 4.7.3 PDF 续跑细则
- 上游：`PDFParser.parse` → `extract_pdf_items` 版式提取（页眉/页脚/表格区文本去重/图片过滤/扫描页跳过），
  返回 `(ParagraphItem[] 含 page_number, 物理页数)` → 同一 `aggregate_paragraphs` + `word_chunk_blocks`
  （`LogicalBlock.start_page` → chunk metadata.page_number = 块起始页）。
- 确定性前提（比 Word 略强，需逐项满足）：
  1. 同一 PDF 字节（object_key 不变）→ PyMuPDF/pdfplumber 提取的行/表格区域/图片稳定；
  2. 扫描页跳过集合稳定（无文本层页面被跳过、计入物理页数但不出条目）；
  3. 表格区文本去重与"图片归位到所在段落/表格"为纯几何函数，重跑结果一致；
  4. 块内图片与 Word 同理需要重新 OCR（含 PDF 内嵌图）。
- 续跑失败窗口与 Word 相同（以 PG commit 为权威锚点），边界示例同 §4.7.2；
  页数与页码：续跑完成时 page_count = 本次解析物理页数（与首次一致），chunk.page_number 由块起始页决定，不因续跑改变。
- 已知差异：整篇无文本层（纯扫描件）在**批量前**即抛错（ValueError），此时 0 提交 → 维持 `failed`，
  不会进入 partial，无需续跑（与 42 号方案一致）。

#### 4.7.4 两格式对比速查
| 环节 | Word | PDF |
|---|---|---|
| 解析产物 | ParagraphItem[]（无页码） | (ParagraphItem[] 带页码, 物理页数) |
| chunk.page_number | 恒 1 | 逻辑块起始页 |
| 分块确定性依赖 | docx XML 不变 + OCR 结果稳定 | PDF 版式几何稳定 + OCR 结果稳定 |
| 扫描件/无文本 | 不适用 | 纯扫描件整篇失败（0 提交 → failed） |
| 图片 | 文档图片并入块正文 | 页内图归位到段落/表格后并入 |
| 续跑执行器 | 同一 `_parse_and_store_in_batches(resume_from=k)` | 同左（仅解析返回结构不同，已在执行器内解包） |
| 权威锚点 | PG processed_chunk_count（每批 commit） | 同左 |

### 4.8 失败窗口与状态汇总

| 失败时点 | 已提交 PG | 状态 | 下次操作 |
|---|---|---|---|
| 上传/下载/解析上游（未开始批量） | 0 | failed | /parse（首次）或 /reparse |
| 批量中某批 PG commit 前 | k（此前批次数） | partial | /resume 从 k 续跑；或 /reparse 全量重来 |
| 批量中某批 PG commit 后 | k+该批 | partial | /resume 从新 k 续跑（已提交不重写） |
| 纯扫描 PDF（无批量） | 0 | failed | 换文件或 /reparse |

## 5. 实现步骤与验证

1. migration 0022（仿 0002）+ 本地 `alembic upgrade` 不在测试范围（迁移只改约束，CI 无 PG）→ 冒烟靠语法/头校验。
2. `core/indexing.py` 确定性 pk + `delete_vector_nodes`。
3. `document_service`：executor `resume_from`/写侧过滤/先删后插；`_mark_parse_failure`；三个函数接入；`resume_document`。
4. `parse_queue` resume 类型 + `routers/document.py` /resume + /parse 拦截 partial；`routers/chunk.py` rechunk 放行 partial。
5. 前端 DocumentListView：标签 + 操作按钮（handleResume 调用 /resume）。
6. 测试（pytest）：
   - `test_parse_queue.py`：enqueue_resume 入队项 type=resume；
   - `test_document_router.py`：/parse partial → 400；/resume partial → 202/queued/enqueue_resume 调用；/resume 非 partial → 400；404；
   - `test_chunk_router.py`：rechunk partial → 放行入队；
   - `test_document_service.py`：
     * executor `resume_from=2`：不写 index<2、写 index≥2 且编号连续、resume 批先调 delete_vector_nodes；
     * `_mark_parse_failure`：committed>0 → partial；committed==0 → failed（回归）；
     * `resume_document`：成功 → completed + 仅 was_never_completed 时 kb 计数 +1（两条分支）；
   - 全量 pytest（基线 708 passed）回归。
7. `docs/changes.md` 记录 + diff 审计。

## 6. 风险与说明

- **索引漂移**：续跑假设同一文件重解析产出与首次一致（纯函数分块；OCR/Vision 结果理论上有细微非确定性）。
  若 OCR 差异导致前缀编号漂移，可能产生个别冗余/错位向量——极端罕见；对策：内容未变时重复 OCR 结果稳定；
  若已出现明显错位，可用"重新解析"全量重来。计划文档记录该边界。
- **partial 期间 chunk_count**：保留上次 completed 的旧值（若来自重解析）；UI"已入库"列按 `processed_chunk_count`
  展示即真实提交数，不与 chunk_count 混淆；列表/详情语义以 processed 为准（当前 UI 已如此）。
- **中断批残留向量**：首次失败时该批可能已写 Milvus/ES、未写 PG；续跑对该批范围"先删后插"（Milvus 删 pk、ES 覆盖 _id）收敛。
- 重切片/重解析在 partial 状态仍可用（全量清旧重来），作为用户兜底。
- 不做 DB 级事务/两阶段提交；PG 每批 commit 是"已入库"的权威判定点。
