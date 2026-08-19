# ES + IK 全文检索实施计划

> 版本: v1.0 | 日期: 2026-08-19 | 状态: 待评审
> 设计依据: [08-es-fulltext-search-design.md](../08-es-fulltext-search-design.md)

## 1. 改动范围

| 文件 | 改动 |
|------|------|
| `docker-compose.yml` | 新增 `elasticsearch` 服务（build ./elasticsearch，9200，数据卷） |
| `elasticsearch/Dockerfile` | ES 8.13.4 基础镜像 + 安装 IK 插件（版本锁定） |
| `elasticsearch/config/IKAnalyzer.cfg.xml` | `remote_ext_dict` 指向后端 `/api/lexicon/export/ik-dict` |
| `backend/pyproject.toml` | 新增依赖 `elasticsearch>=8.0` |
| `backend/config.py` | ES 配置 + `retrieval_default_method` + RRF + 对账配置 |
| `backend/core/es_store.py` | **新增**：AsyncElasticsearch 单例、ensure_index、bulk_index、delete_by_doc/kb/chunk、es_search（高亮） |
| `backend/services/document_service.py` | 解析批次三处落库（+ES）；delete_document/delete_chunk/update_chunk/rechunk/reparse 补 ES 清理 |
| `backend/services/kb_service.py` | delete_kb 补 ES 清理 |
| `backend/models/schemas.py` | SearchRequest 扩展 `retrieval_method`（+fulltext/hybrid_es）与 `highlight`；SearchResultItem 增 `highlight` |
| `backend/models/lexicon.py` | **新增**：LexiconTerm ORM |
| `backend/models/__init__.py` | 注册 LexiconTerm |
| `backend/alembic/versions/0013_lexicon_terms.py` | **新增**：lexicon_terms 表 |
| `backend/repositories/lexicon_repository.py` | **新增**：词条 CRUD/分页/搜索/导入 |
| `backend/services/lexicon_service.py` | **新增**：词条业务 + 内存缓存（查询扩展）+ ik-dict 导出 |
| `backend/services/search_service.py` | fulltext / hybrid_es 分支 + RRF 融合 + highlight 透传 |
| `backend/routers/lexicon.py` | **新增**：/api/lexicon/*（terms CRUD、import、export ik-dict/csv、stats） |
| `backend/routers/es_admin.py` | **新增**：/api/es/status、/api/es/rebuild |
| `backend/main.py` | 注册新路由 + 每日定时对账协程 |
| `backend/tests/*` | es_store、RRF、lexicon CRUD/导出、对账 |
| `frontend/src/api/lexicon.js` | **新增** |
| `frontend/src/api/search.js` | 请求体携带 retrieval_method/highlight |
| `frontend/src/views/SearchView.vue` | 检索方式四值下拉（默认 fulltext）+ 后端高亮渲染 |
| `frontend/src/views/LexiconView.vue` | **新增**：词库管理页 |
| `frontend/src/router/index.js` | 新增 /lexicon 路由 |
| `docs/changes.md` | 追加修改记录 |

**不改动**：`core/retrieval.py`（chat 链路）、`routers/chat.py`、`core/vector_store.py`。

## 2. 实现步骤

### 步骤 1：ES 部署
- docker-compose 新增服务（单节点、1g 堆、healthcheck、数据卷）；
- Dockerfile 安装 IK（版本与 ES 严格一致）；IKAnalyzer.cfg.xml 配置远程词典。
- 验证：`docker compose up -d elasticsearch`；`GET /_cat/plugins` 有 IK；`_analyze` 中文分词正常。

### 步骤 2：依赖与配置
- pyproject 加 `elasticsearch>=8.0`；config.py 新增 ES/检索/对账配置项。
- 验证：`uv sync` 成功；配置加载。

### 步骤 3：core/es_store.py
```
ensure_index(): 不存在时创建 bid_chunks（settings/mappings 见设计 §4.2）
bulk_index(chunks, metadatas, kb_id) -> bool   # bulk 写入，_id=f"{doc_id}_{chunk_index}"
delete_by_doc(doc_id) / delete_by_kb(kb_id) / delete_by_chunk(doc_id, chunk_index)
es_search(query, kb_ids, top_k, highlight) -> hits   # multi_match + kb filter + highlight
reconcile_status() -> dict    # PG vs ES count 对比（供 es_admin 与定时任务）
```
- 验证：单元测试（mock client）；起 ES 冒烟（手动脚本）。

### 步骤 4：双写与清理
- `_parse_and_store_in_batches`：build_index 后调用 `es_store.bulk_index`（失败记日志，不阻断）；
- `delete_document`、`delete_chunk`、`update_chunk`、`rechunk_document`、`reparse_document`、`kb_service.delete_kb` 补 ES 删除/覆盖。
- 验证：上传文档三处 count 一致；删除文档/知识库 ES 同步清理。

### 步骤 5：检索扩展
- schemas：`retrieval_method` Literal 四值 + `highlight: bool = True`；SearchResultItem 增 `highlight`；
- `search_service.search`：按 retrieval_method 分支（vector/hybrid 走现有 retrieve；fulltext 走 es_search；hybrid_es 走 retrieve(vector) + es_search + RRF(k=60)）；未传时 `settings.retrieval_default_method`；rerank 逻辑复用。
- 验证：三种方式返回正确；highlight 返回 `<em>` 片段。

### 步骤 6：词库
- 迁移 0013 建表；LexiconTerm 模型；lexicon_repository；lexicon_service（CRUD + 内存缓存 + ik-dict/csv 导出 + 查询扩展）；routers/lexicon.py。
- 验证：CRUD 单测；导出格式正确。

### 步骤 7：对账与调度
- routers/es_admin.py（status/rebuild）；main.py lifespan 启动对账协程（es_reconcile_interval_hours）。
- 验证：status 显示差异；rebuild 后一致；定时任务按周期执行。

### 步骤 8：前端
- api/lexicon.js、SearchView（四模式默认 fulltext + 后端高亮 v-html）、LexiconView、路由。
- 验证：`npm run build` 通过；页面操作正常。

### 步骤 9：测试与回归
- pytest 全量；手动回归 vector/hybrid/chat 不变。
- 验证：全部通过 + 修改记录。

## 3. 关键伪代码

```python
# es_store.es_search（核心查询）
body = {
  "size": top_k,
  "query": {"bool": {
      "must": [{"multi_match": {"query": q, "fields": ["text^3", "heading^2", "file_name_search", "heading_path"]}}],
      "filter": [{"terms": {"kb_id": kb_ids}}]}},
  "highlight": {"fields": {"text": {"fragment_size": 200, "number_of_fragments": 1, "pre_tags": ["<em>"], "post_tags": ["</em>"]}}},
}
```

```python
# search_service hybrid_es 融合
def rrf_merge(vector_hits, es_hits, k=60):
    rank_sum = {}
    for i, h in enumerate(vector_hits): rank_sum[h["_key"]] += 1.0 / (k + i + 1)
    for i, h in enumerate(es_hits):     rank_sum[h["_key"]] += 1.0 / (k + i + 1)
    # 按融合分降序输出（_key = (doc_id, chunk_index)）
```

```python
# lexicon ik-dict 导出
@app.get("/api/lexicon/export/ik-dict")
async def export_ik_dict():
    terms = await lexicon_service.enabled_terms()
    return PlainTextResponse("\n".join(t.term for t in terms))
```

## 4. 验证方式

1. 单元：es_store 查询构造/高亮解析（mock）、RRF 分数、lexicon CRUD/重复 409/导入/导出、对账对比；
2. 集成：上传 PDF → PG/Milvus/ES 三处 count 一致；fulltext 精确术语命中 + 高亮；hybrid_es 融合正常；词库新增词条 → ik-dict 导出包含 → 查询扩展即时命中；删除文档/知识库 ES 清理；/api/es/status 无差异；
3. 回归：vector/hybrid/chat 行为不变；pytest 全量通过；前端 build 通过。

## 5. 风险

| 风险 | 应对 |
|------|------|
| IK 与 ES 版本不匹配 | 版本锁定（镜像 tag == 插件 zip tag），实施时先验证版本号 |
| 双写失败数据漂移 | best-effort + 日志 + 对账/rebuild 兜底 |
| ES 未启动时后端报错 | es_store 连接失败降级：写入记日志跳过、检索返回空（es_sync_enabled/健康检查） |
