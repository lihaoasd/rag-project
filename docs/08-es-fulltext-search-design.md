# ES + IK 全文检索集成详细设计

> 版本: v1.0 | 日期: 2026-08-19 | 状态: 待评审
> 关联文档: [22-fulltext-search-options-plan.md](plan/22-fulltext-search-options-plan.md)（选型依据）、[02-technical-design.md](02-technical-design.md)

## 1. 背景与目标

### 1.1 背景

当前检索能力：Milvus 稠密向量（`vector`）+ Milvus 内置 BM25 混合检索（`hybrid`）。关键词检索存在以下不足：

- 分词为 jieba 通用词典，投标专业术语（资质名、公司名、项目编号）切分不准；
- 无后端高亮（前端仅简单子串高亮）、无模糊/短语/同义词能力；
- **词库无法通过前端维护**：Milvus BM25 的词典在建 Collection 时固化，改词典需全量重建。

### 1.2 目标

1. 引入 **Elasticsearch + IK 中文分词** 作为全文检索引擎，提供精确关键词检索、短语匹配、后端高亮；
2. **词库通过前端界面维护**，IK 远程词典热更新，实现"加词即生效"（受 IK 轮询间隔约束，见 §6.4）；
3. 保留并融合现有向量检索：新增 `fulltext`（ES 纯关键词全文检索，**前端默认**）与 `hybrid_es`（向量 + ES 关键词 RRF 融合）检索方式；
4. 对现有 `vector` / `hybrid` 检索与问答链路**零破坏**。

### 1.3 边界（本期不做）

- 不替代 Milvus 内置 BM25 在问答链路（chat）中的作用，chat 检索链路保持不变；
- 不做 ES 集群/多副本/冷热分层（单节点足够本项目量级）；
- 不做同义词词典的 ES 端 reindex 方案（词库同义词走后端查询扩展，见 §6.3）。

## 2. 总体架构

```
┌────────────────────────────────────────────────────────────┐
│                      前端 (Vue 3)                           │
│   SearchView（检索方式 + 高亮渲染）   LexiconView（词库管理）  │
└───────────────┬──────────────────────────┬─────────────────┘
                │ /api/search              │ /api/lexicon/*
                ▼                          ▼
┌────────────────────────────────────────────────────────────┐
│                   后端 (FastAPI)                             │
│  search_service（fulltext / hybrid_es / RRF 融合 / rerank）    │
│  lexicon_service（词库 CRUD + 导出 IK 词典 + 查询扩展）        │
│  document_service（解析批次 → 三处落库：PG + Milvus + ES）     │
└───────┬───────────────┬──────────────────┬─────────────────┘
        │               │                  │
        ▼               ▼                  ▼
  ┌──────────┐   ┌──────────┐   ┌──────────────────┐
  │PostgreSQL│   │  Milvus  │   │ Elasticsearch+IK │
  │document_ │   │  稠密向量  │   │  bid_chunks 索引   │
  │chunks/   │   │  (现有)   │   │  (IK 分词+高亮)    │
  │lexicon_  │   └──────────┘   └──────────────────┘
  │terms     │
  └──────────┘
```

数据流：

- **写入**：文档解析 → 切片 → 每批次同时写 PG（`document_chunks`）、Milvus（向量）、ES（`bid_chunks`）；
- **检索**：search 页按 `retrieval_method` 选择 ES / Milvus / 融合路径；
- **词库**：前端 → `lexicon_terms` 表 → 导出为 IK 远程词典（ES 轮询热更新）+ 后端内存缓存（查询扩展）。

## 3. Elasticsearch 部署

### 3.1 docker-compose 变更

新增 `elasticsearch` 服务与构建目录：

```
ai_bid_rag/
├── docker-compose.yml          # 新增 elasticsearch 服务
├── elasticsearch/
│   ├── Dockerfile              # 基础镜像 + IK 插件
│   └── config/
│       └── IKAnalyzer.cfg.xml  # IK 远程词典配置
└── volumes/elasticsearch/      # ES 数据卷
```

```yaml
# docker-compose.yml 新增
  elasticsearch:
    build: ./elasticsearch
    container_name: bid_es
    ports:
      - "9200:9200"
    environment:
      - discovery.type=single-node
      - xpack.security.enabled=false          # 本地环境；如需鉴权见 §3.3
      - ES_JAVA_OPTS=-Xms1g -Xmx1g           # 与 Milvus 叠加注意总内存
      - bootstrap.memory_lock=false
      - ingest.geoip.downloader.enabled=false # 减少启动外联
    volumes:
      - ./volumes/elasticsearch:/usr/share/elasticsearch/data
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://localhost:9200/_cluster/health || exit 1"]
      interval: 15s
      timeout: 5s
      retries: 20
    restart: unless-stopped
```

```dockerfile
# elasticsearch/Dockerfile
FROM docker.elastic.co/elasticsearch/elasticsearch:8.13.4
# IK 插件版本必须与 ES 版本严格一致。
# 注意：GitHub release 资产已清空，改用 InfiniLabs 官方发布服务器
RUN bin/elasticsearch-plugin install --batch \
    https://release.infinilabs.com/analysis-ik/stable/elasticsearch-analysis-ik-8.13.4.zip
COPY config/IKAnalyzer.cfg.xml /usr/share/elasticsearch/config/analysis-ik/IKAnalyzer.cfg.xml
```

### 3.2 IKAnalyzer.cfg.xml（远程词典）

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE properties SYSTEM "http://java.sun.com/dtd/properties.dtd">
<properties>
    <comment>IK Analyzer 扩展配置</comment>
    <!-- 远程词典：指向后端导出端点，词库更新后 IK Monitor 轮询拉取 -->
    <entry key="remote_ext_dict">
        http://backend:8000/api/lexicon/export/ik-dict
    </entry>
    <entry key="remote_ext_stopwords"></entry>
</properties>
```

> 注意：IK `remote_ext_dict` 由 Monitor 线程轮询，**间隔固定约 1 小时**。词库更新后最迟 1 小时生效（见 §6.4 应对）。

### 3.3 鉴权与安全

- 本地/内网环境：`xpack.security.enabled=false`；
- 如需暴露公网：启用基础认证（`ELASTIC_PASSWORD`），后端 `es_username` / `es_password` 配置；
- 数据卷持久化，删除知识库/文档时同步清理 ES 数据（见 §5）。

## 4. 索引设计

### 4.1 索引命名与分片策略

- **单索引 `bid_chunks`**（所有知识库共用，`kb_id` 字段过滤）：
  - 与现有"每知识库一个 Milvus Collection"不同，原因：search API 已支持 `kb_ids` 多库检索，单索引可用 `terms` 过滤一次查出，多索引则需 `msearch` 并合并；
  - 删除知识库 = `delete_by_query(kb_id)`，无需删索引；
- 单节点：1 主分片、0 副本；`refresh_interval: 1s`（默认）。

### 4.2 Mapping 与 Analyzer

```json
PUT /bid_chunks
{
  "settings": {
    "number_of_shards": 1,
    "number_of_replicas": 0,
    "index.refresh_interval": "1s",
    "analysis": {
      "analyzer": {
        "ik_search": { "type": "custom", "tokenizer": "ik_smart" },
        "ik_index":  { "type": "custom", "tokenizer": "ik_max_word" }
      }
    }
  },
  "mappings": {
    "properties": {
      "doc_id":            { "type": "keyword" },
      "kb_id":             { "type": "keyword" },
      "file_name":         { "type": "keyword" },
      "file_name_search":  { "type": "text", "analyzer": "ik_index", "search_analyzer": "ik_search" },
      "page_number":       { "type": "integer" },
      "chunk_index":       { "type": "integer" },
      "chunk_type":        { "type": "keyword" },
      "source_chunk_index":{ "type": "integer" },
      "heading":           { "type": "text", "analyzer": "ik_index", "search_analyzer": "ik_search" },
      "heading_path":      { "type": "text", "analyzer": "ik_index", "search_analyzer": "ik_search" },
      "text":              { "type": "text", "analyzer": "ik_index", "search_analyzer": "ik_search" },
      "created_at":        { "type": "date" }
    }
  }
}
```

- 索引时 `ik_max_word`（细切保证召回），查询时 `ik_smart`（粗切减少噪音）；
- IK 自定义词典（远程词典）挂在 IK 插件级，作用于所有 IK 分词器，无需改 mapping。

### 4.3 分析器降级（无 IK 插件时）

远程 ES（如 `192.168.60.121:9200`）未安装 analysis-ik 插件时，`ensure_index()` 检测到 `unknown analyzer` 错误后**自动降级为内置 `cjk` 分析器**（bigram，无需插件），保证中文检索可用。降级结果以模块状态记录（`_analyzer_mode`）。降级影响：

- 中文按双字 bigram 切分，专业术语（"建筑工程施工总承包一级"）不再整词切分，但 bigram 检索 + 后端查询扩展仍可命中；
- IK 远程词典（词库分词）不可用，**词库仅靠查询扩展生效**（即时）；
- 如需 IK 词级切分：在远程 ES 安装 `analysis-ik` 插件（版本与 ES 严格一致，当前 8.11.3），并可配置其 `IKAnalyzer.cfg.xml` 的 `remote_ext_dict` 指向后端 `/api/lexicon/export/ik-dict`。

### 4.4 文档 ID

`_id = f"{doc_id}_{chunk_index}"`（与 Milvus node_id 无耦合），保证同一 chunk 幂等覆盖。

## 5. 数据同步机制

### 5.1 写入（解析批次内）

在 `document_service._parse_and_store_in_batches` 每个批次完成 `build_index`（Milvus）后，**同一批次**写入 ES：

```
伪代码（_parse_and_store_in_batches 内，build_index 之后）：

milvus_pks = await build_index(chunks, metadatas, kb_id)     # 现有
if settings.es_sync_enabled:
    ok = await es_store.bulk_index(chunks, metadatas, kb_id) # 新增
    if not ok:
        logger.error(...)  # 记日志，由对账任务兜底（§5.5）
await _store_chunk_batch(...)                                  # 现有（PG）
```

- 三处落库顺序：Milvus → ES → PG；PG 是**权威数据源**，ES 失败不阻断解析（best-effort + 对账）；
- ES 批量写入用 `bulk` API（每批 ≤ parse_batch_size 条），异步客户端不阻塞事件循环。

### 5.2 删除文档 / 删除知识库

| 流程 | 现有动作（PG/Milvus/MinIO） | 新增 ES 动作 |
|------|-----------------------------|--------------|
| `delete_document` | `delete_index`（Milvus）+ PG 删除 + MinIO 删除 | `es_store.delete_by_doc(doc_id)` |
| `delete_kb` | `drop_kb_collection` + PG 级联清理 | `es_store.delete_by_kb(kb_id)` |
| `delete_chunk` | `delete_single_vector` + PG 删除 | `es_store.delete_by_chunk(doc_id, chunk_index)` |
| `rechunk / reparse` | 清 PG 切片 + 删 Milvus 向量 → 重索引 | 先 `delete_by_doc(doc_id)`，重索引批次再写入 |

- 删除失败同样 best-effort + 对账兜底；
- `update_chunk`（编辑切片文本）：删除旧 `_id` 文档 + 写入新文本（幂等覆盖即可，直接 `index` 同 `_id`）。

### 5.3 批量重建（对账兜底）

提供手动/定时对账与重建：

```
POST /api/es/rebuild?kb_id=xxx     # 从 PG document_chunks 全量重建该知识库的 ES 索引
GET  /api/es/status                # 对账：各知识库 PG chunk 数 vs ES count（差异列表）
```

- `rebuild` 实现：按 kb 删除 ES 数据 → 分页读取 PG `document_chunks` → bulk 写入；
- 对账口径：`(kb_id, doc_id) → count` 与 `text` 是否一致（可选内容哈希抽样）。

### 5.4 同步开关

`settings.es_sync_enabled`（默认 True），关闭后写入/删除跳过 ES（对账接口仍可用）。

### 5.6 备份与恢复策略（已确认）

- ES 数据卷（`volumes/elasticsearch`）**不纳入备份**——ES 为派生数据，PG `document_chunks` 为权威源；
- 备份范围：**PG（含 `document_chunks` 全量切片文本）+ MinIO（原文件/图片）**；
- 灾难恢复流程：恢复 PG + MinIO → 启动 ES → `POST /api/es/rebuild`（全量重建）→ 检索恢复；
- IK 词典无需备份（后端 `/api/lexicon/export/ik-dict` 实时导出）。

## 6. 词库系统（前端更新）

### 6.1 数据模型（PG 新增表 `lexicon_terms`）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | uuid PK | |
| term | varchar(255) unique | 词条（标准词） |
| aliases | jsonb default [] | 别名/同义词列表 |
| category | varchar(50) default '' | 分类：**自由文本**（如 资质/公司/术语/项目/编号，可空，不限定枚举） |
| enabled | boolean default true | 启用状态 |
| created_at / updated_at | timestamp | |

迁移：`alembic revision 0013_lexicon_terms`（参考现有迁移写法）。

### 6.2 后端 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/lexicon/terms?page=&page_size=&search=&category=` | 分页列表（支持搜索/分类过滤） |
| POST | `/api/lexicon/terms` | 新增词条（term 重复返回 409） |
| PUT | `/api/lexicon/terms/{id}` | 更新（词条/别名/分类/启用） |
| DELETE | `/api/lexicon/terms/{id}` | 删除 |
| POST | `/api/lexicon/import` | CSV 批量导入（列：term,aliases,category） |
| GET | `/api/lexicon/export/ik-dict` | **IK 远程词典**：一行一词（enabled 词条），供 ES 轮询 |
| GET | `/api/lexicon/export/csv` | 导出全部词条 |
| GET | `/api/lexicon/stats` | 统计（总数/启用数/分类分布） |

### 6.3 词库的两个消费端

1. **IK 分词词典（remote_ext_dict）**：解决"术语切分不准"。导出端点输出一行一词，IK Monitor 轮询更新；
2. **查询扩展（后端，即时生效）**：词库别名/同义词扩展 query。检索时加载启用词条到内存缓存（启动加载 + 词库变更时刷新），对 query 做替换/OR 扩展：

```
伪代码（query 扩展）：
terms = lexicon_cache.enabled_terms()          # 内存缓存：{标准词: [别名...]}
expanded = expand(query, terms)
# 例：query="甲建设集团" 且词库含 {甲公司: [甲建设集团]} → "甲建设集团 OR 甲公司"
```

- 扩展后的 query 用于 ES 检索（`match`/`multi_match`）与 Milvus 检索，双端一致；
- 为什么不同义词走 ES synonym filter：ES 端同义词在索引/查询 analyzer 中，变更需 reindex，不满足"前端即时生效"。

### 6.4 生效延迟与应对

- IK `remote_ext_dict` 轮询间隔**固定约 1 小时**：词库分词更新最迟 1 小时生效；
- 应对：文档中明确该限制；查询扩展端**即时生效**（缓存刷新，无需等 IK），弥补大部分"新词即可搜"诉求；
- 若后续要求秒级分词生效：评估自定义 IK 词典加载插件或缩短轮询（需改 IK 源码/换实现），本期不做。

### 6.5 前端词库管理页

- 新增 `LexiconView.vue` + 路由 `/lexicon` + `api/lexicon.js`：
  - 词条表格（term/aliases/category/enabled/更新时间）+ 分页 + 搜索；
  - 新增/编辑对话框（词条、别名多输入、分类下拉、启用开关）；
  - 批量导入（CSV 上传）、导出；
  - 提示条：说明"分词词典最长 1 小时生效，查询扩展即时生效"。

## 7. 检索链路设计

### 7.1 检索方式扩展

`SearchRequest.retrieval_method` 扩展为四值（前端下拉同步，**默认 `fulltext`**）：

| 值 | 含义 | 路径 | 高亮 |
|----|------|------|------|
| `vector` | Milvus 稠密（现状，不变） | `retrieve()` | 前端子串 |
| `hybrid` | Milvus 稠密+稀疏（现状，不变） | `retrieve()` | 前端子串 |
| `fulltext` | **ES 纯关键词全文检索（新增，前端默认）** | `es_search()` | **ES 高亮** |
| `hybrid_es` | **Milvus 稠密 + ES 关键词 RRF（新增）** | `retrieve(vector)` + `es_search()` + RRF | ES 高亮 |

- chat 链路（`retrieve_and_rank`）不感知新方式，`retrieve()` 内部校验逻辑不变；
- `search_service.search()` 按 `retrieval_method` 分支，`fulltext` / `hybrid_es` 不再把该参数透传给 `retrieve()`；
- 请求未传 `retrieval_method` 时沿用 `settings.retrieval_default_method`（默认 `fulltext`），前端下拉默认值同。

### 7.2 ES 查询构造（core/es_store.py）

```
es_search(query, kb_ids, top_k, highlight=True):
    body = {
      "size": top_k,
      "query": {
        "bool": {
          "must": [ { "multi_match": { "query": query,
                                        "fields": ["text^3", "heading^2", "file_name_search", "heading_path"],
                                        "type": "best_fields" } } ],
          "filter": [ { "terms": { "kb_id": kb_ids } } ]
        }
      },
      "highlight": {
        "fields": { "text": { "fragment_size": 200, "number_of_fragments": 1,
                              "pre_tags": ["<em>"], "post_tags": ["</em>"] } }
      }
    }
    resp = await es_client.search(index="bid_chunks", body=body)
    return [ { "score": round(h["_score"], 4),
               "text": h["_source"]["text"],
               "doc_id": ..., "file_name": ..., "page_number": ...,
               "chunk_index": ..., "chunk_type": ..., "source_chunk_index": ...,
               "highlight": "".join(h["highlight"]["text"]) if highlight else None } for h in hits ]
```

### 7.3 RRF 融合（hybrid_es）

复用现有 RRF 思路，Python 端实现（k=60）：

```
伪代码：
vector_hits = await retrieve(query, kb_id, top_k, retrieval_method="vector", ...)   # 逐 kb 并发
es_hits     = await es_search(query, kb_ids, top_k=top_k*factor)
merged = rrf_merge(vector_hits, es_hits, k=60)   # score = Σ 1/(k + rank_i)
# 之后与现有 rerank 逻辑衔接：可选 rerank 收口 + 阈值过滤
```

- 与现有 `search_service` 的"并发检索多 kb → 合并排序 → 可选 rerank"结构兼容，新增一路召回即可；
- fulltext 模式无 [0,1] 阈值语义：`similarity_threshold` 对 fulltext 不生效（文档注明）；hybrid_es 的阈值由 rerank/向量侧控制。

### 7.4 高亮

- `SearchResponse` 结果项新增 `highlight: str | None`（含 `<em>` 标签的片段）；
- 前端 `SearchView`：`fulltext` / `hybrid_es` 模式优先渲染后端 highlight（`v-html`，样式 `<em>` 高亮），`vector` / `hybrid` 保留现有 `highlightText` 前端逻辑。

### 7.5 检索参数

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `retrieval_default_method` | `fulltext` | 未传 `retrieval_method` 时默认的检索方式 |
| `es_top_k_factor` | 2 | fulltext 召回量 = top_k × factor（给 RRF/rerank 留余量） |
| `rrf_k` | 60 | RRF 常数 |
| `es_highlight_enabled` | True | 是否返回高亮片段 |

## 8. API 接口变更汇总

### 8.1 SearchRequest（models/schemas.py）

```python
retrieval_method: Literal["vector", "fulltext", "hybrid", "hybrid_es"] | None = None
highlight: bool = True
```

### 8.2 SearchResultItem（响应）

新增字段 `highlight: str | None = None`。

### 8.3 新增路由

| 路由文件 | 端点 |
|----------|------|
| `routers/lexicon.py` | `/api/lexicon/terms` CRUD、`/import`、`/export/ik-dict`、`/export/csv`、`/stats` |
| `routers/search.py` | `/api/search`、`/api/knowledge-bases/{kb_id}/search` 新增 `retrieval_method` 分支（同端点扩展） |
| `routers/es_admin.py` | `/api/es/status`、`/api/es/rebuild`（对账/重建） |

## 9. 数据库迁移

`0013_lexicon_terms.py`：创建 `lexicon_terms` 表（见 §6.1）。`document_chunks` 无结构变更（ES 索引为派生数据）。

## 10. 配置项（config.py）

```python
# === Elasticsearch 配置 ===
es_host: str = "localhost"
es_port: int = 9200
es_index_name: str = "bid_chunks"
es_sync_enabled: bool = True          # ES 双写开关
es_username: str = ""
es_password: str = ""

# === 检索 ===
retrieval_default_method: str = "fulltext"  # 未传 retrieval_method 时的默认方式
retrieval_es_top_k_factor: int = 2          # fulltext 召回放大系数
retrieval_rrf_k: int = 60                   # RRF 常数

# === ES 对账 ===
es_reconcile_enabled: bool = True           # 每日定时对账开关
es_reconcile_interval_hours: int = 24       # 对账间隔（小时）
```

新增依赖：`elasticsearch>=8.0`（异步客户端 `AsyncElasticsearch`）。

## 11. 实施步骤（伪代码）

| 步骤 | 内容 | 验证 |
|------|------|------|
| 1 | docker-compose 增加 ES 服务 + Dockerfile（IK 插件）+ IKAnalyzer.cfg.xml | `docker compose up -d elasticsearch`，`GET /_cat/plugins` 显示 IK，`_analyze` IK 分词正常 |
| 2 | `uv add elasticsearch`；config.py 新增 ES 配置 | 配置加载正常 |
| 3 | `core/es_store.py`：AsyncElasticsearch 单例、`ensure_index()`（mapping/settings）、`bulk_index()`、`delete_by_doc/kb/chunk()`、`es_search()`（含 highlight） | 单元测试（mock client）+ 手动起 ES 冒烟 |
| 4 | `document_service._parse_and_store_in_batches` 插入 ES 写入；delete/rechunk/reparse/update_chunk 各流程补 ES 清理 | 上传文档 → PG/Milvus/ES 三处 count 一致；删除文档 → ES 同步删除 |
| 5 | `search_service` 扩展 fulltext / hybrid_es 分支 + RRF；schemas 扩展 retrieval_method/highlight；routers/search.py 透传；`retrieval_default_method` 默认 fulltext | 三种检索方式返回正确、高亮片段返回 |
| 6 | `0013_lexicon_terms` 迁移 + model + repository + `lexicon_service` + `routers/lexicon.py`（CRUD/import/export/ik-dict）+ 内存缓存与失效 | CRUD 单测；`/api/lexicon/export/ik-dict` 输出正确 |
| 7 | 前端：`api/lexicon.js`、`LexiconView.vue`、路由 `/lexicon`；`api/search.js` + `SearchView.vue` 检索方式下拉四值 + 后端高亮渲染 | `npm run build` 通过；页面操作词库增删改查 |
| 8 | `routers/es_admin.py` 对账/重建 + **每日定时对账任务**（main.py lifespan 启动后台协程，按 `es_reconcile_interval_hours` 执行 status 对账，差异写日志/告警） | 对账接口显示差异、rebuild 后一致、定时任务按周期执行 |
| 9 | 测试与回归：pytest 全量 + 手动回归 vector/hybrid/chat | 见 §12 |

## 12. 验证方式

### 12.1 单元测试

- `es_store`：查询构造（mock `AsyncElasticsearch`）、bulk 写入、删除、highlight 解析；
- `search_service`：keyword / hybrid_es 分支、RRF 融合正确性（构造固定 rank 验证分数）、retrieval_method 透传；
- `lexicon`：CRUD、重复 term 409、CSV 导入、ik-dict 导出格式、查询扩展逻辑；
- 现有 `vector` / `hybrid` 测试保持通过（回归）。

### 12.2 集成验证

1. 上传一份 PDF：PG `document_chunks` count == Milvus count == ES count；
2. `retrieval_method=fulltext`：精确术语（如"建筑工程施工总承包一级"）命中，返回 highlight 片段（`<em>` 包裹）；不传 `retrieval_method` 时默认走 fulltext；
3. `retrieval_method=hybrid_es`：语义 + 关键词融合，top-k 相关性与单一 vector 对比不劣化；
4. 词库：前端新增词条"甲建设集团"→ `/api/lexicon/export/ik-dict` 包含该词；查询"甲建设集团"经查询扩展命中含"甲公司"的文档（即时生效）；重启/等待 IK 轮询后 `_analyze` 验证 IK 分词正确；
5. 删除文档/知识库：ES 数据同步清理；`/api/es/status` 无差异；每日定时对账任务运行后无告警；
6. 问答（chat）回归：SSE 正常，来源引用正常（确认 chat 未受影响）。

### 12.3 前端

`npm run build` 通过；检索页四模式切换、高亮渲染、词库管理页 CRUD 正常。

## 13. 风险与应对

| 风险 | 说明 | 应对 |
|------|------|------|
| IK 插件与 ES 版本不匹配 | 插件安装失败或启动失败 | 版本严格锁定（Dockerfile 固定镜像 tag 与插件 zip tag 一致）；实施时先验证最新版本号 |
| 词库分词生效延迟 ~1 小时 | IK `remote_ext_dict` 轮询间隔固定 | 查询扩展端即时生效兜底；文档明示限制；如不满足再做自定义加载（后续） |
| ES 内存占用 | 与 Milvus、PostgreSQL 叠加 | `ES_JAVA_OPTS=-Xms1g -Xmx1g`；数据量级小时单节点足够；监控堆使用 |
| 双写一致性 | ES 写入/删除失败导致数据漂移 | best-effort + 日志 + `/api/es/status` 对账 + `/api/es/rebuild` 重建 |
| 分数体系差异 | keyword 无 [0,1] 阈值；RRF 分数非相似度 | 阈值仅对 vector/rerank 生效；前端分数展示按检索方式说明 |
| 两套关键词能力并存（Milvus BM25 vs ES）语义混淆 | chat 用 Milvus hybrid，search 页新增 ES 路径 | 明确边界：chat 不动；search 页新增 keyword/hybrid_es；文档与前端标注 |
| 授权 | Elastic License（Basic 免费） | 单节点 Basic 功能足够；如需 Apache 2.0 可评估 OpenSearch+IK 移植版（本期不做） |

## 14. 开放问题（评审确认）

1. ~~前端检索方式默认值~~ → **已确认：新增 `fulltext` 类型（纯 ES 全文检索）作为前端与后端默认**（`retrieval_default_method=fulltext`，原 `keyword` 命名并入 `fulltext`）；
2. ~~词库分类固定枚举还是自由文本~~ → **已确认：自由文本**；
3. ~~`/api/es/rebuild` 触发方式~~ → **已确认：手动 rebuild + 每日定时对账告警**（`es_reconcile_enabled` / `es_reconcile_interval_hours=24`，对账差异写日志/告警，不自动 rebuild）;
4. ~~ES 数据卷是否纳入备份范围~~ → **已确认：不备份 ES 卷，恢复时从 PG rebuild 重建**（备份 = PG + MinIO，见 §5.6）。

### 14.1 新增检索方式类型（默认值）— 已定

- **类型名**：`fulltext`（纯 ES 全文检索，行为即原 `keyword`，`es_search()` + ES 高亮）；
- **默认值**：前端下拉默认 `fulltext`；后端未传 `retrieval_method` 时用 `settings.retrieval_default_method = "fulltext"`；
- 类型清单最终为：`vector` / `hybrid` / `fulltext` / `hybrid_es`（见 §7.1）。
