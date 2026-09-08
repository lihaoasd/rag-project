# 45. 切片 MD5 幂等写入方案（解析链路按内容指纹决定是否更新）

> 状态：已实现（2026-09-08）　|　类型：需求变更　|　关联：43（partial/续跑）、44（扫描页 OCR）　|　日期：2026-09-08

## 1. 需求（用户确认）

- **解析/续跑/重跑同一份文档时，切片不应被无条件重插/重写**：每个 chunk 按内容算 **MD5**，
  与 PG 中该 (doc_id, chunk_index) 已存指纹比对——
  - 已存在且 MD5 相同 → **跳过**（不重写 PG / 向量 / ES）；
  - 缺失或 MD5 不同 → **才写/更新**。
- PG 侧：DocumentChunk 新增 `chunk_md5` 列 + `(doc_id, chunk_index)` **唯一约束**（0023 迁移，建约束前先清理历史重复行）。

## 2. 现状问题（事实）

| 事实 | 后果 |
|---|---|
| `document_chunks` 无唯一约束，`batch_create_chunks` 每次 INSERT 新 uuid 行 | 同一 (doc,index) 重复解析可产生**重复行**（partial 前 failed 遗留文档允许再走 /parse 时不清旧数据） |
| 43 号续跑靠 `resume_from = PG 行数` 跳过前缀 | 依赖"前缀全在"假设；对 legacy 重复行/内容漂移无感知；仍需先删向量再插 |
| ES `_id`、Milvus pk 已确定性（`doc_index` / `doc:index`） | 可覆盖，但 PG 是无条件 INSERT，是去重断点 |
| 单块人工编辑 `update_chunk_text` 不改 md5 列 | 无影响：解析重跑按"本次解析出的文本"与存量比对，编辑过的行指纹不同 → 判定 changed → 以解析结果覆盖（解析语义正确） |

## 3. 关键设计（伪代码）

### 3.1 指纹（App 与 PG 回填公式必须逐字节一致）
```python
def chunk_md5(page_number: int, text: str) -> str:
    return hashlib.md5(f"{page_number}|{text}".encode("utf-8")).hexdigest()
# PG 等价（0023 回填存量用）：
#   md5(convert_to(concat_ws('|', page_number::text, text_content), 'UTF8'))
```
指纹 = 页码 + 正文（不含 chunk_index；索引是比对键，序号由解析全局编号决定）。

### 3.2 executor 每批新流程（替代"pending 过滤 + resume_from 前缀跳过 + 先删后插"）
```python
# 每批（batch_blocks）：
#   ① 与现在相同：process_word_images → word_chunk_blocks → metas(含 text/chunk_index/page_number)
#   ② 计算本批指纹 new_md5s = {idx: chunk_md5(page, text)}
#   ③ existing = repo.get_chunk_md5_map(doc_id, 本批 chunk_index 集合)   # SELECT chunk_index, chunk_md5 WHERE doc_id=? AND chunk_index IN (...)
#   ④ 分区：
#        unchanged = {idx | existing[idx] == new_md5s[idx]}      → 完全跳过（不删向量/不重嵌/不写 ES/PG）
#        to_write  = {idx | idx ∉ existing 或 md5 不同}          → 继续 ⑤
#   ⑤ 对 to_write（按原顺序）：
#        a. delete_vector_nodes(kb_id, [f"{doc_id}:{idx:06d}"])   # 缺失也可能有中断残留；删除幂等
#        b. build_index(子集) → milvus_pks                        # 确定性 pk 覆盖
#        c. es_store.bulk_index(子集)                              # _id 确定性覆盖
#        d. repo.batch_upsert_chunks(doc_id, kb_id, 子集 metas, pks)
#           # INSERT ... ON CONFLICT (doc_id, chunk_index) DO UPDATE SET
#           #   text_content/char_count/page_number/chunk_md5/milvus_pk/metadata
#           # 冲突即内容已变（unchanged 已在上游滤除），整行更新并刷新 chunk_md5
#   ⑥ 仅当 to_write 非空才更新 processed 进度（有写入时才推进）；unchanged-only 批不落库
# 返回 (chunk_count, page_count) 不变
```
- 移除 executor 的 `resume_from` 参数与前缀过滤（md5 全量比对天然幂等：已提交前缀 unchanged 自动跳过、中断残留缺失行自动补写）。
- `resume_document` 保留（partial → 全量解析 → 比对写差集 → completed + 文档计数规则不变），内部不再取 count 锚点。

### 3.3 PG 迁移 0023（down_revision="0022"）
```python
# upgrade:
#   1. op.add_column("document_chunks", chunk_md5 String(32), nullable=True)
#   2. 清历史重复：DELETE FROM document_chunks a USING document_chunks b
#        WHERE a.doc_id=b.doc_id AND a.chunk_index=b.chunk_index
#          AND (a.created_at, a.id) > (b.created_at, b.id)     -- 保留最早一行
#   3. 回填存量：UPDATE document_chunks SET chunk_md5 =
#        md5(convert_to(concat_ws('|', page_number::text, text_content), 'UTF8'))
#        WHERE chunk_md5 IS NULL
#   4. op.create_unique_constraint("uq_document_chunks_doc_index", "document_chunks",
#                                  ["doc_id", "chunk_index"])
# downgrade:
#   1. drop constraint  2. drop column
```
> 提示：0023 会删除历史重复行（每 (doc,index) 保留最早一行）——执行前请备份该表。

### 3.4 模型与仓储
- `models/document.py::DocumentChunk` 增 `chunk_md5: Mapped[str | None] = mapped_column(String(32), nullable=True)`。
- `document_repository.py`：
  - 新增 `get_chunk_md5_map(doc_id, chunk_indexes) -> dict[int, str]`（uuid 校验 + `IN` 查询，仅取存在行）；
  - 新增 `batch_upsert_chunks(...)`（`sqlalchemy.dialects.postgresql.insert` + `on_conflict_do_update`，index_elements=["doc_id","chunk_index"]；不设 WHERE，因为上游已保证冲突=内容变化）；
  - `batch_create_chunks` 保留（供测试与既有调用，非冲突场景仍可用）。

## 4. 改动范围

| 文件 | 改动 |
|---|---|
| `backend/models/document.py` | +`chunk_md5` 列 |
| `backend/alembic/versions/0023_chunk_md5_unique.py`（新） | 加列 → 清重 → 回填 → 唯一约束（回填公式与 app 指纹一致） |
| `backend/repositories/document_repository.py` | +`get_chunk_md5_map`、+`batch_upsert_chunks` |
| `backend/services/document_service.py` | +`chunk_md5()`；executor 改为"指纹比对→差集写"，移除 `resume_from`/前缀过滤/无条件整批 store；`_store_chunk_batch` 调 upsert；`resume_document` 不再取 count 锚点（逻辑简化） |
| `backend/tests/test_document_service.py` | executor 用例改为 md5 语义：全 unchanged 零写、部分 changed 只写差集且 delete 只对差集、缺失行补写、新文档全插；resume/mark 相关断言适配（count 锚点移除） |
| `backend/tests/test_document_repository.py` | 适配（如影响） |
| `docs/changes.md` | 修改记录 |

不需要改：core/indexing（pk 已确定性）、es_store、前端、队列/路由守卫、44 号扫描页 OCR。

## 5. 实现步骤与验证

1. 模型 + 0023 迁移（含回填 SQL 与清重）。
2. 仓储两方法（get map / upsert）。
3. service `chunk_md5()` + executor 流程改写 + resume_document 简化。
4. 单测：指纹稳定（同输入同输出/异文本异输出，且与 `md5(concat_ws(...))` 语义一致用固定样例断言）；executor 场景集（全同/部分变/缺行/全新）；`batch_upsert_chunks` 会话级断言（调用 pg insert、on_conflict 目标、index_elements）。
5. 全量 pytest 回归（基线 727 passed）。
6. 交付说明：目标环境先 `alembic upgrade head`（含 0022→0023），再重启；用真实 378 页文档重跑验证——二次跑应见 "PG 入库前检查… unchanged=全批跳过、0 写入" 且切片数不变。

## 6. 边界与风险

- **指纹一致性**：App 与 PG 回填必须同一公式（§3.1 已锁死）；任何改公式需同步迁移回填并重新比对。
- **删除历史重复**：0023 幂等清理每 (doc,index) 保留最早行；执行前备份（迁移属一次性运维）。
- **单批不变跳过时进度**：processed 字段仅在写入批推进，最终 completed 会写真实总数，UI 展示不受影响。
- **人工编辑后的行**：指纹与"从源文件解析出的文本"不同 → 下次解析按 changed 覆盖（解析语义），符合预期。
- **并发**：解析 worker 单 FIFO，同 doc 无并发写；upsert 仍保证唯一性兜底。
- **方言**：upsert 用 PostgreSQL 方言（项目仅 PG），不做跨库。
