# 知识图谱模块详细设计 — 构建 / 管理 / 可视化 / 图检索

> 版本: v1.1 | 日期: 2026-08-22 | 状态: 已评审（评审确认决策见 docs/changes.md）
> 关联文档: [03-graph-rag-design.md](03-graph-rag-design.md)（Graph RAG 总体初稿）、[04-lexicon-design.md](04-lexicon-design.md)（词库）、[05-database-design.md](05-database-design.md)、[08-es-fulltext-search-design.md](08-es-fulltext-search-design.md)

## 1. 背景与目标

### 1.1 背景

系统已具备 Vector RAG 全链路（文档解析 → 切片 → Milvus 稠密/稀疏 + ES 全文 → 三路混合检索）。现有 `docs/03-graph-rag-design.md` 规划了 Graph RAG 总体方向（Neo4j 存储、实体抽取、双通道问答），本设计将其中"图谱构建与检索"落地为**可实施、可交付的第一阶段**，聚焦图谱的构建、管理、可视化和图检索 API。

### 1.2 本期需求（用户确认）

| 编号   | 需求             | 说明                         |
| ---- | -------------- | -------------------------- |
| G-01 | 每个知识库一个知识图谱    | 图谱按知识库严格隔离，互不可见            |
| G-02 | Web 查看图谱内容     | 前端可视化浏览图谱节点/关系             |
| G-03 | 全量构建图谱         | 对知识库内全部已解析文档构建图谱（异步）       |
| G-04 | 单文件增量构建        | 用户对单个文件触发构建，追加合并进知识库图谱（幂等） |
| G-05 | 清除单文件图谱        | 删除某文件对图谱的全部贡献，不影响其他文件贡献的实体 |
| G-06 | 图检索，**证据溯源到 chunk** | 图检索结果（实体/关系/子图）附带 chunk 级证据：切片文本 + 页码 + 可跳转原文 |

### 1.3 已确认的技术决策

| 决策点    | 结论                                 | 理由                                |
| ------ | ---------------------------------- | --------------------------------- |
| 图存储引擎  | **Neo4j**（Community 版，Docker/外部服务） | 与 03 方案一致，Cypher 多跳查询与聚合能力强       |
| 实体抽取方式 | **词库优先 + LLM 补充**（完全复用系统词库 `lexicon_terms`） | 高频实体零成本匹配，长尾实体与关系用 LLM，符合 04 词库策略 |
| 词库分类   | **前端固定枚举下拉**（公司/机构、人员、地点、项目、产品、概念/资质、其他） | 分类是词库→图谱实体类型的唯一桥梁，自由文本无法可靠映射    |
| 本阶段范围  | 图谱管理 + 可视化 + 图检索 API               | 问答双通道（query router）集成放下一阶段        |
| 可视化    | **ECharts 力导向图**                   | 成熟、可定制，与 Element Plus 风格统一        |

### 1.4 边界（本期不做）

- **检索页已集成图检索**（`graph` 检索通道，证据并入结果列表参与 RRF 融合，§6.5）；**chat 问答双通道（问题路由、图谱上下文组装进 LLM 回答）仍为下阶段**，接口预留；
- 不做实体向量索引（Neo4j vector index）——实体检索先用关键词/模糊匹配 + 邻居展开，量级足够；
- 不做图谱手动编辑（增删节点/关系）——只做构建、查看、清除；
- **图谱本体不建 chunk 节点，也不新增任何 PG 表**：实体节点用 `mentioned_chunk_ids` 属性记录来源切片 ID，检索时凭 chunk_id **直接查询现有 `document_chunks` 表**取证据文本/页码（§6.2）——证据永远与切片实时一致，零冗余；
- 不迁移到 Neo4j Enterprise 多数据库——Community 版单 database，用属性隔离（见 §3.1）；
- **不引入 `llama-index-graph-stores-neo4j`（PropertyGraphIndex）**：其图结构约定（节点/关系 schema、属性命名、内部 upsert）与本方案的自定义图结构（kb_id 属性隔离、mentioned_chunk_ids/source_doc_ids 精确剥离）冲突，且其核心价值（实体向量检索）用 Neo4j 原生 vector index + 现有 `create_embedding` 即可自建；
- **下阶段实体向量检索（chat 双通道）采用 Neo4j 原生 `CREATE VECTOR INDEX` 自建**：注意 kb_id 隔离下的 top-k 稀释问题（全库召回后置过滤不可靠），届时按 KB 分索引或 top_k 放大处理，不引入 LlamaIndex 图框架。

***

## 2. 总体架构

### 2.1 架构图

```
┌───────────────────────────────────────────────────────────────┐
│                       前端 (Vue 3)                             │
│   GraphView.vue（ECharts 力导向图 + 统计 + 构建/清除操作）        │
│   DocumentListView（文档行内 图谱状态 + 构建/清除按钮）            │
└───────────────┬───────────────────────────────┬───────────────┘
                │ /api/knowledge-bases/{id}/graph/*              
                ▼                               │
┌───────────────────────────────────────────────▼───────────────┐
│                       后端 (FastAPI)                           │
│  routers/graph.py          (图谱管理 + 图检索 API)              │
│  services/graph_service.py (构建/清除/查询编排, 状态落库)        │
│  core/graph_queue.py       (图谱任务队列：build_file/build_kb/  │
│                             clear_file，独立于解析队列)          │
│  core/graph_extractor.py   (词库匹配 + LLM 补充 + 实体去重)      │
│  core/neo4j_store.py       (Neo4j driver 连接池 + Cypher 封装)   │
│  core/lexicon_service.py   (复用：词库缓存/别名→标准词)           │
│  core/model_factory.py     (复用：create_llm / resolve_model_config)│
└──────────┬─────────────────────────────────────────────────────┘
           │
           ▼
   ┌───────────────┐        ┌─────────────────────┐
   │    Neo4j      │        │   PostgreSQL        │
   │  (:7687)      │        │   documents 表       │
   │  图谱本体      │        │   +graph_status 等   │
   │  节点/关系     │        │   document_chunks   │
   │  (含 mentioned │        │   (切片原文,证据数据源)│
   │   _chunk_ids)  │        │                     │
   └───────────────┘        └─────────────────────┘
```

### 2.2 与现有模块的关系

| 现有模块                                        | 关系                                                                   |
| ------------------------------------------- | -------------------------------------------------------------------- |
| `core/parse_queue.py`                       | **不复用**，新建独立 `graph_queue`。图谱构建依赖"文档已解析"（读 PG chunks），与解析不抢同一 worker |
| `core/model_factory.py`                     | 复用 `create_llm` + `resolve_model_config(kb_id, "llm")` 获取抽取用 LLM     |
| `services/lexicon_service.py`               | 复用词库缓存做高频实体匹配；新增 `match_entities` 公共函数，配合分类枚举化（§5.2） |
| `models/document.py`                        | 新增图谱状态列（alembic 迁移，§7）                                               |
| `core/es_store.py` / `core/vector_store.py` | 不参与图谱链路（图谱独立于向量/全文）                                                  |

### 2.3 新增依赖

| 依赖                | 用途                         | 方式                                          |
| ----------------- | -------------------------- | ------------------------------------------- |
| `neo4j>=5.18.0`   | 原生 Python driver，执行 Cypher | 移入 `pyproject.toml` 主依赖（当前在 `graph` extra）  |
| `neo4j:5.21.0` 容器 | 图存储                        | docker-compose 新增服务（本地开发）/ 外部服务（生产 .env 指向） |
| `echarts`         | 前端力导向图                     | `npm install echarts`                       |

> 说明：不使用 `llama-index-graph-stores-neo4j` 的 PropertyGraphIndex（**该依赖已从 `graph` extra 移除，全代码零引用**）。本项目已有自建 model\_factory / prompt 体系，用原生 driver + 自定义抽取管线更贴合现有代码风格；后续实体向量检索用 Neo4j 原生 `CREATE VECTOR INDEX` + 现有 `create_embedding` 自建，不引入该框架（§1.4）。

***

## 3. 图数据模型设计（Neo4j）

### 3.1 知识库隔离策略

Neo4j **Community 版仅支持单 database**，因此"每个知识库一个图谱"通过**属性隔离 + 索引**实现，而非多 database：

- 每个节点带 `kb_id` 属性；每个关系带 `kb_id` 属性；
- 所有实体节点带通用标签 `Entity` + 类型标签（通用本体 9 类：`Organization`/`Person`/`Place`/`Event`/`Time`/`Project`/`Product`/`Quantity`/`Concept`）；
- 建复合索引支撑隔离查询：`CREATE INDEX FOR (n:Entity) ON (n.kb_id, n.name)`；
- **所有 Cypher 查询强制带** **`WHERE n.kb_id = $kb_id`** **过滤**，从查询层保证隔离；
- 实体节点唯一键：`(kb_id, type, name)` —— 同名不同类型视为不同节点（去重逻辑见 §5.3）。

> 备选（03 文档曾提 `:KB_xxx` 标签）：kb\_id 为 UUID 含连字符，作标签名需反引号转义且不直观，放弃。

### 3.2 节点（Entity）设计

| 标签                 | 属性                                                                         | 说明                 |
| ------------------ | -------------------------------------------------------------------------- | ------------------ |
| `Entity`（通用）+ 类型标签 | `kb_id`, `name`, `type`, `description`, `mentioned_chunk_ids: []`, `created_at` | 所有实体节点             |
| `Document`         | `kb_id`, `doc_id`, `file_name`, `created_at`                               | 每个已构建文件一个节点，作为来源锚点 |

**`mentioned_chunk_ids`（实体节点）**：记录该实体出现的全部切片 ID（`document_chunks.id`），是 **chunk 级检索的证据定位**：

- 实体生命周期由其驱动：数组被清空 → 实体不再被任何切片提及 → 删除节点（§4.3）；
- MERGE 时 `SET n.mentioned_chunk_ids = reduce(acc = n.mentioned_chunk_ids, c IN $chunk_ids | CASE WHEN c IN acc THEN acc ELSE acc + c END)`（纯 Cypher 去重，不依赖 APOC，见 §4.4）；
- 证据明细（文本/页码/切片序号）**不存图谱**，检索时凭 chunk_id 查 `document_chunks`（§6.2），与切片永远一致。

> 数组规模：单实体被几百个切片提及在属性大小限制（数 MB）内，且构建为单 worker 串行、无并发写冲突。**本期不设硬上限**（接受现状）；若高频实体（跨几十份文档的常驻名称）量级验证后逼近属性上限，另定截断策略（保留最近 N 个 + 实体 `chunk_count` 总数属性）。

### 3.3 关系（Relationship）设计

| 关系类型                                        | 方向                | 属性                                                   | 说明                           |
| ------------------------------------------- | ----------------- | ---------------------------------------------------- | ---------------------------- |
| 业务关系（通用本体 12 种：属于/位于/发生在/参与/负责/合作/关联/包含/要求/拥有/数量/日期，§5.3） | 按语义               | `kb_id`, `type`, `description`, `source_doc_ids: []` | 实体间关系（**无 chunk 维度**，按文件级追踪来源） |
| `提及`                                        | Document → Entity | `kb_id`                                                 | 文件到其贡献实体的锚点边，生命周期跟随 Document |

关系唯一键：`(kb_id, type, 起点节点, 终点节点)` —— MERGE 模式匹配自动保证去重。

> 关系来源追踪用文件级 `source_doc_ids`：关系由两端实体所在 chunk 文本抽取，chunk 粒度对关系无额外价值，文件级即可支撑清除剥离。

### 3.4 数据流示意

```
Neo4j 图谱本体（节点量 = 文件数 + 实体数）：
(文件A:Document) --[提及]--> (甲公司:Organization{name, mentioned_chunk_ids:[c3,c5]})
(文件B:Document) --[提及]--> (甲公司)     ← 同实体多文件共享，节点唯一
(甲公司) -[参与{source_doc_ids:[A]}]-> (XX项目:Project{...})

PostgreSQL（证据数据源，零冗余）：
document_chunks 表：c3 → {doc_id:A, chunk_index:2, page_number:5, text:"…中标单位：甲公司…"}
                    c5 → {doc_id:A, chunk_index:4, page_number:7, text:"…甲公司…"}
检索实体「甲公司」→ 取 mentioned_chunk_ids=[c3,c5] → 查 document_chunks 得证据
```

***

## 4. 核心流程设计

### 4.1 单文件构建（G-04，增量构建的核心原语）

**定义**：对知识库内单个文件，抽取其 chunks 中的实体与关系，MERGE 进该知识库图谱。幂等：无论该文件是否已构建，都**先清除该文件旧贡献，再重建**（与文档 reparse "清旧重建"模式一致，避免 source\_doc\_ids 重复）。

```
build_file(kb_id, doc_id):
  1. 校验：文档存在且 status=completed（未解析完不能建图）
  2. PG: documents.graph_status = 'building'
  3. 清除该文件旧贡献（调用 clear_file 的 Cypher 部分，§4.3）
  4. 读取该文件全部 chunks: SELECT id, text_content, page_number FROM document_chunks
  5. 过滤短 chunk（< 20 字符跳过）
  6. 抽取（§5）：词库/规则匹配 → LLM 补充（批量） → 合并去重
     · 每个实体记录其来源 chunk_id（抽取即基于 chunk 文本，来源天然可知）
  7. 写入 Neo4j（按 chunk 批次拆分事务，§4.4）：MERGE 实体节点时合并 mentioned_chunk_ids
  8. PG: graph_status='completed', graph_built_at=NOW(), graph_duration_ms=...
  异常 → graph_status='failed', graph_error_message=...
```

### 4.2 全量构建（G-03）

```
build_kb(kb_id):
  1. 取该知识库全部 status=completed 的文档列表
  2. 清空该知识库整个图谱（Cypher: MATCH (n) WHERE n.kb_id=$kb_id DETACH DELETE n）
  3. 对每个文档依次执行 build_file 的 3~8 步（同一 worker 顺序执行）
  4. 任一文件失败不中断整体，记录到该文件 graph_status=failed，KB 构建状态汇总返回
```

> 全量构建 = 清空 + 逐文件构建，避免与已有文件贡献叠加导致 source\_doc\_ids 脏数据。

### 4.3 清除单文件图谱（G-05）

**先查 PG 取该文件全部 chunk_id，再在 Neo4j 精确剥离**（不影响其他文件贡献的实体）：

```sql
-- PG 步骤 0：取该文件全部切片 ID（证据剥离依据）
SELECT id FROM document_chunks WHERE doc_id = :doc_id AND kb_id = :kb_id;
```

```cypher
// 1. 删除该文件的 Document 节点及其 提及 关系
MATCH (d:Document {doc_id: $doc_id, kb_id: $kb_id})
OPTIONAL MATCH (d)-[m:`提及`]->()
DELETE m, d;

// 2. 从所有实体节点的 mentioned_chunk_ids 移除本文件切片，数组变空即删除节点
MATCH (n:Entity {kb_id: $kb_id})
WHERE any(c IN $chunk_ids WHERE c IN n.mentioned_chunk_ids)
WITH n, [c IN n.mentioned_chunk_ids WHERE NOT c IN $chunk_ids] AS rest
SET n.mentioned_chunk_ids = rest
WITH n, rest WHERE size(rest) = 0
DETACH DELETE n;

// 3. 业务关系按文件级 source_doc_ids 剥离，空数组即删除关系
MATCH (a)-[r]->(b)
WHERE r.kb_id = $kb_id AND $doc_id IN r.source_doc_ids
WITH r, [x IN r.source_doc_ids WHERE x <> $doc_id] AS rest
SET r.source_doc_ids = rest
WITH r, rest WHERE size(rest) = 0
DELETE r;
```

> 正确性说明：实体 E 仅被文件 A 的切片提及 → A 清除后 E.mentioned\_chunk\_ids 空 → 删除；E 同时被 A、B 的切片提及 → 保留。关系按文件剥离，同理。**证据文本无需清理**——图谱只存 chunk_id，`document_chunks` 由文档删除/重切片流程管理。

**清除的调用契约（best-effort）**：`clear_file_graph` 统一封装"取 chunk_ids → 清 Neo4j"两步，供手动清除与 `delete_document`/`reparse`/`rechunk` 联动调用：

1. **顺序**：先查 PG 取该文件全部 chunk_id（在删除 PG 行/切片**之前**），再执行上述清除 Cypher；
2. **失败不阻断**：Neo4j 不可达/清除失败时**不阻断**文档删除/重解析主流程，记日志并将该文档 `graph_status` 置 `failed`（或 stale 标记）；由 `build_file` 的"先清旧再重建"幂等性兜底（重建自然覆盖残留）；
3. **状态复位**：`delete_document`/`reparse`/`rechunk` 联动清除后，文档 `graph_status` 一律置 `none`（reparse/rechunk 后切片 id 全部变化，旧图谱已无意义，需用户重新构建）。

### 4.4 写入合并（MERGE 语义，幂等）

```cypher
// 实体节点：键 = (kb_id, type, name)；合并该实体在本文件的来源切片
MERGE (n:Entity:Organization {kb_id: $kb_id, name: $name})
ON CREATE SET n.type = $type, n.created_at = datetime(),
              n.mentioned_chunk_ids = $chunk_ids, n.description = $description
ON MATCH  SET n.mentioned_chunk_ids =
              reduce(acc = n.mentioned_chunk_ids, c IN $chunk_ids |
                     CASE WHEN c IN acc THEN acc ELSE acc + c END)
// description 先到先得：ON CREATE 设置一次，ON MATCH 不覆盖（与 §5.1 合并策略一致）

// 业务关系：先确保两端存在，再 MERGE 关系（按文件级 source_doc_ids 追踪）
MATCH (a:Entity {kb_id: $kb_id, name: $a_name, type: $a_type}),
      (b:Entity {kb_id: $kb_id, name: $b_name, type: $b_type})
MERGE (a)-[r:`参与` {kb_id: $kb_id}]->(b)
ON CREATE SET r.type = '参与', r.source_doc_ids = [$doc_id]
ON MATCH  SET r.source_doc_ids =
              reduce(acc = r.source_doc_ids, d IN [$doc_id] |
                     CASE WHEN d IN acc THEN acc ELSE acc + d END)

// 文件锚点
MERGE (d:Document {kb_id: $kb_id, doc_id: $doc_id})
ON CREATE SET d.file_name = $file_name, d.created_at = datetime()
MATCH (d:Document {kb_id: $kb_id, doc_id: $doc_id})
MATCH (e:Entity {kb_id: $kb_id, name: $e_name})
MERGE (d)-[:`提及` {kb_id: $kb_id}]->(e)
```

写入事务按 **chunk 批次拆分**（如 20~50 个 chunk/批，每批一个 `session.execute_write` 事务）：避免大文件几百个 MERGE 挤在单个事务内的内存压力；批间失败可重试（MERGE 幂等），单文件整体仍"先清旧再重建"幂等。

***

## 5. 实体与关系抽取设计（词库优先 + LLM 补充）

### 5.1 抽取管线

```
chunk 文本
   │
   ▼
┌─────────────────────┐
│ 1. 词库/规则匹配      │  零成本，毫秒级
│  - lexicon 词库      │  标准词+别名 → 标准词，category → 实体类型
│  - 内置正则          │  数量 / 时间 / 编号 等
│  输出: 实体候选集     │  {name, type, 出现次数}
└──────────┬──────────┘
           │（词库命中的实体直接入候选集，不调 LLM）
           ▼
┌─────────────────────┐
│ 2. LLM 补充抽取      │  逐 chunk（每 chunk 一次调用，`graph_extraction_batch=1` 默认，减少跨 chunk 幻觉），覆盖长尾实体 + 关系
│  - create_llm       │  复用 model_factory 的 OpenAI 兼容 LLM
│  - Schema 约束 prompt│  提示词管理 entity_extraction 激活模板（fallback 内置常量）；输出 JSON: entities[] + relationships[]
│  - 小 chunk 跳过     │  < 20 字符不调 LLM
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ 3. 合并与去重         │
│  - 词库归并          │  LLM 实体名与标准词/别名精确+包含匹配，命中则归一为标准词作节点键
│  - 全半角/空白归一化   │
│  - 同 (name,type) 合并│  description 先到先得（ON CREATE 设置一次，ON MATCH 不覆盖）
└──────────┬──────────┘
           │ 每个实体附带来源 chunk_id 列表
           │ {entity, chunk_ids: [...]}
           ▼
    写入 Neo4j（§4.4）：MERGE 节点合并 mentioned_chunk_ids
```

### 5.2 词库匹配接口（lexicon\_service 扩展）

**完全复用系统词库 `lexicon_terms`**：图谱不另建词库，直接使用启用词条（`term`/`aliases`/`category`/`enabled`）及现有内存缓存。**缓存结构扩展**：`_cache` 由 `{标准词: [别名]}` 改为 `{标准词: {aliases: [...], category: ...}}`（`refresh_cache`/`ensure_cache`/`expand_query` 同步改造，改动集中在 lexicon_service 内部），新增公共匹配函数：

```python
async def match_entities(text: str) -> list[dict]:
    """扫描 chunk 文本，最长匹配命中词条（标准词/别名），返回 [{name: 标准词, type: 实体类型, count}]。
    命中别名 → 归一到标准词作为实体名（天然去重）。"""
```

#### 5.2.1 词库分类枚举化（前端 LexiconView 改造）

`category` 由自由文本改为**固定枚举下拉**，存储英文枚举值、展示中文（通用本体，投标旧枚举 location/qualification 映射见 §7.3）：

| 存储值（category） | 前端下拉文案 | 图谱实体类型标签 | 是否建图谱节点 |
|------------------|------------|----------------|--------------|
| `organization`   | 公司/机构    | `Organization` | ✅ |
| `person`         | 人员        | `Person`       | ✅ |
| `place`          | 地点        | `Place`        | ✅ |
| `project`        | 项目        | `Project`      | ✅ |
| `product`        | 产品        | `Product`      | ✅ |
| `concept`        | 概念/资质    | `Concept`      | ✅ |
| `other`          | 其他/术语    | —              | ❌ 不进图（通用术语非实体，避免图谱噪声） |

- **`other` 类词条不建图谱节点**：通用术语（如"招标文件""保证金"）不是实体，进图只会制造噪声；`match_entities` 直接跳过该类；
- **空分类/未知分类一律归 `other`**：迁移脚本统一 `UPDATE` 为 `'other'`；CHECK 约束只含 7 个枚举值（不含空串），数据库层杜绝自由文本；
- **旧数据一次性整理**：迁移 `0015` 将历史自由文本/旧投标枚举映射为通用枚举（`location→place`、`qualification→concept`、中文值→对应枚举，无法识别归 `other`）；
- **CSV 导入校验**：导入时对已知中文分类自动映射为枚举值，未知值归 `other` 并在导入结果中提示。

内置正则规则（数量、时间、编号）在 `core/graph_extractor.py` 中作为第二层匹配（不受词库分类影响）。

### 5.3 LLM 抽取 Prompt（Schema 约束，通用本体）

> **提示词由提示词管理维护**（`prompt_templates`，场景 `entity_extraction`，迁移 0016 seed 默认模板）：抽取时优先读取该场景**激活模板**（支持 `{变量}` 渲染），缺失/异常回退代码内置 `FALLBACK_ENTITY_EXTRACTION_PROMPT` 常量（内容与本段一致，作为兜底）；在提示词管理界面修改并激活即可调整抽取行为，无需改代码。内置默认模板如下：

```text
你是通用信息抽取系统。从文本中提取实体和关系，只提取明确提及的信息，不要编造。
实体 name 必须与原文完全一致（禁止改写、合并或规范化），同一实体在原文中重复出现只需输出一次。

实体类型:
ORGANIZATION(组织/机构/公司/团体), PERSON(人物), PLACE(地点/地理位置), EVENT(事件/活动),
TIME(时间点/日期), PROJECT(项目/工程/任务), PRODUCT(产品/物品/资产/系统),
QUANTITY(数量/金额/度量值,必须含单位,如 3000万元/50吨/2年), CONCEPT(概念/资质/标准/技能/制度/条款)

关系类型(方向见括号,源类型|目标类型):
(Person|Organization)-[属于]->(Organization)
(Organization|Project|Event|Product)-[位于]->(Place)
(Event|Project)-[发生在]->(Time|Place)
(Person|Organization)-[参与]->(Event|Project)
(Person|Organization)-[负责]->(Project|Product|Event)
(Organization|Person)-[合作]->(Organization|Person)
(任意业务实体)-[关联]->(任意业务实体)   ← 语义不明确的通用关系用此类型
(Project|Event|Product|Organization)-[包含]->(Project|Event|Product|Concept|Person)
(Project|Product)-[要求]->(Concept|Product|Person)
(Organization|Person)-[拥有]->(Product|Concept)
(Project|Event|Product|Organization)-[数量]->(Quantity)
(Project|Event|Document)-[日期]->(Time)

输出严格 JSON（不要其他文字）:
{"entities":[{"name":"...","type":"ORGANIZATION","description":"..."}],
 "relationships":[{"source":"...","target":"...","type":"属于","description":"..."}]}
```

> 关系类型清单为**本期单一事实源**（通用本体 12 种，含兜底"关联"）；扩展/调整先改此处，再同步实现白名单（`graph_service.RELATION_TYPES`）与 03 文档。

实现要点：

- 抽取系统提示词经 `prompt_service.get_active_prompt("entity_extraction")` 动态获取（渲染后使用），无激活模板或 DB 异常时使用内置 `FALLBACK_ENTITY_EXTRACTION_PROMPT` 常量（与 seed 模板一致）；
- 使用 `model_factory.create_llm(resolve_model_config(kb_id, "llm"))`，`OpenAILike` 异步接口（`achat`）并发调用；
- LLM 输出经 Pydantic 模型校验（`models/graph_schemas.py`），解析失败的重试 1 次，仍失败丢弃该批（记日志）；
- `QUANTITY`/`TIME` 类型实体**优先走正则匹配**，LLM 不重复抽取（降低幻觉与成本）；
- **chunk 归属（G-06 证据前提）**：在"实体名与原文一致"约束下，后端按实体名在该批各 chunk 原文中定位，命中哪个 chunk 即记入哪个 `mentioned_chunk_ids`；定位失败（改名/歧义）降级记入该批第一个 chunk；
- **词库归并**：合并阶段将 LLM 实体名与词库标准词/别名做精确+包含匹配，命中则归一为标准词作为节点键（与词库抽取的实体天然合并，避免同实体双节点）。

### 5.4 成本控制

| 策略         | 说明                                        |
| ---------- | ----------------------------------------- |
| 逐 chunk 提取 | **每 chunk 一次 LLM 调用**（`graph_extraction_batch=1` 默认）：模型只看单 chunk 原文，无法跨 chunk 拼接编造，幻觉最低；成本最高（约 4 倍请求量）。可调大 `graph_extraction_batch` 合并请求省成本（幻觉风险上升） |
| 小 chunk 跳过 | 文本 < 20 字符不抽                              |
| 词库命中优先     | 词库/正则已识别的实体不要求 LLM 再报                     |
| 异步并发       | asyncio 并发调用 LLM（受队列单 worker 串行约束，单文件内并发） |

***

## 6. 图谱检索设计（G-06，本阶段 API）

### 6.1 检索能力矩阵

| 场景   | 实现                                      | 示例             |
| ---- | --------------------------------------- | -------------- |
| 实体定位 | 词库匹配 query 中的实体/别名 → 正则（数量/时间/编号）→ Neo4j name 精确/包含匹配 | "甲公司" / "3000万" |
| 邻居展开 | 入口实体 1\~2 hop 展开，返回子图（ECharts 渲染）       | 甲公司 → 合作方/参与项目 |
| 关系查询 | 关键词映射关系类型 + 方向模板 Cypher                 | "甲公司的合作方"      |
| **chunk 证据** | **实体节点 mentioned\_chunk\_ids → 查 document\_chunks 取切片** | 甲公司 → 第 5 页切片文本 |
| 全局统计 | 聚合 Cypher（COUNT/DISTINCT/SUM）           | KB 图谱概要        |

### 6.2 子图查询（返回给 ECharts + chunk 证据）

```
POST /api/knowledge-bases/{kb_id}/graph/search
body: {"query": "甲公司", "max_hops": 2, "limit": 200}

1. 词库匹配 query → 候选实体名列表（含别名→标准词）
2. Neo4j MATCH 入口实体 (kb_id, name IN candidates)
3. 以入口实体为中心，按 max_hops 展开（LIMIT 200 防爆炸）
4. 收集子图内全部实体的 mentioned_chunk_ids → 去重 → 查 document_chunks 取证据明细
   SELECT c.doc_id, c.chunk_index, c.page_number, c.text_content, d.file_name
   FROM document_chunks c LEFT JOIN documents d ON c.doc_id = d.id
   WHERE c.kb_id = $kb_id AND c.id = ANY($chunk_ids)
   ORDER BY c.page_number, c.chunk_index
5. 返回 {nodes, edges, evidence}
```

```json
{
  "nodes": [{"id": "org_甲公司", "name": "甲公司", "type": "Organization", "degree": 28}],
  "edges": [{"source": "org_甲公司", "target": "proj_XX项目", "type": "参与"}],
  "evidence": [
    {
      "entity": "甲公司",
      "chunks": [
        {"doc_id": "doc_a", "file_name": "XX招标文件.pdf", "chunk_id": "c3",
         "chunk_index": 2, "page_number": 5,
         "text": "…中标单位：甲公司…", "highlight": [["中标单位：", "甲公司"]]}
      ]
    }
  ]
}
```

> `evidence` 即 **chunk 级检索结果**：每个实体附其来源切片（文本 + 页码 + 文件），前端可高亮实体出现位置并跳转 chunk 原文（§11.3）。

### 6.3 关系查询模板

```cypher
// 例："X 参与了哪些项目" → 方向 out，关系 参与
MATCH (s:Entity {kb_id: $kb_id, name: $entity})-[r:`参与`]->(t)
RETURN s.name AS source, type(r) AS relation, t.name AS target,
       r.description AS description, size(r.source_doc_ids) AS doc_count
```

MVP 关系类型识别：query 与关系类型关键词表做包含匹配（"参与/合作/包含/负责/位于/发生在/属于/拥有/要求/数量/日期/关联"…），未命中则默认走实体邻居展开。

### 6.4 统计查询

```cypher
// KB 图谱概要（类型分布用分类型二次查询或 collect 构造，避免 APOC 依赖）
MATCH (n:Entity {kb_id: $kb_id})
RETURN count(n) AS total_entities, count(DISTINCT n.type) AS entity_types
```

***

## 7. 数据库变更（PostgreSQL）

### 7.1 documents 表新增列（alembic 迁移 `0014_graph_status`）

| 列名                    | 类型          | 约束                      | 说明                             |
| --------------------- | ----------- | ----------------------- | ------------------------------ |
| `graph_status`        | VARCHAR(20) | NOT NULL DEFAULT 'none' | none/building/completed/failed |
| `graph_error_message` | TEXT        | NULLABLE                | 构建失败原因                         |
| `graph_built_at`      | TIMESTAMPTZ | NULLABLE                | 最近一次成功构建时间                     |
| `graph_duration_ms`   | INTEGER     | NULLABLE                | 最近一次构建耗时                       |

- 状态流转：`none → building → completed | failed`；重构建 = `building → completed | failed`；清除成功 → `none`；
- **联动复位**：`delete_document`/`reparse`/`rechunk` 联动清除图谱后 → `none`（§4.3 调用契约）；
- **词库变更联动**：词条增删改/分类变更后，将该 KB 内已构建（completed）文档的 `graph_status` 置 `none`（stale 标记），前端提示"词库已变更，建议重新构建"；不自动触发重建（成本可控，见 §13 风险 10）；
- 不新增任务表：任务状态直接落在 documents 行上（与解析状态 `status` 模式一致，无状态漂移）；
- 知识库级统计实时计算（COUNT documents WHERE graph\_status='completed'），不加冗余列。

### 7.2 ORM 变更

`models/document.py` 的 `Document` 增加 4 个 mapped\_column；`_doc_to_dict`（document\_service）与 `DocumentResponse`（schemas）补充 graph 字段，前端文档列表可展示。

### 7.3 词库分类数据规范化（配合 §5.2.1）

`lexicon_terms.category` 字段**结构不变**（VARCHAR(50)），无需 alembic 迁移，需两类配套动作：

1. **旧数据批量修正**：迁移 `0015` 将自由文本/旧投标分类映射为通用枚举（`公司/企业/单位→organization`、`项目/工程→project`、`人员/人名→person`、`地点/位置→place`、`资质/证书/标准/技能→concept`、`location→place`、`qualification→concept`，无法识别→`other`），并把现有空字符串分类统一归 `other`；
2. **（可选）新增 CHECK 约束**：枚举化稳定后加 `CHECK (category IN ('organization','project','person','location','qualification','other'))`，从数据库层杜绝自由文本。**约束集合不含空串**（空串已在上一步归一为 `other`）；需在数据修正完成后单独迁移，避免约束失败。

### 7.4 无新增证据表（设计取舍说明）

**不新增任何 PG 表**：chunk 级证据完全复用现有 `document_chunks` 表。

- 图谱实体节点仅存 `mentioned_chunk_ids`（切片 ID 数组，§3.2）；
- 检索时凭 chunk\_id 直查 `document_chunks` 取文本/页码（§6.2）；
- 收益：**零冗余、零一致性负担**——切片被编辑/重切片后，图谱证据自动反映最新文本；文档删除/重切片时证据随之消失，无需联动清理。

***

## 8. 任务队列设计（graph\_queue）

新建 `core/graph_queue.py`，模式复刻 `parse_queue.py`（asyncio.Queue + 单 worker，FastAPI lifespan 启动/停止）：

| 任务类型         | 元组格式                            | 处理函数                             |
| ------------ | ------------------------------- | -------------------------------- |
| `build_file` | `("build_file", kb_id, doc_id)` | `graph_service.build_file_graph` |
| `build_kb`   | `("build_kb", kb_id)`           | `graph_service.build_kb_graph`   |
| `clear_file` | `("clear_file", kb_id, doc_id)` | `graph_service.clear_file_graph` |

- 独立队列理由：图谱构建依赖文档已解析（读 PG chunks）且大量调用 LLM，不应与解析队列互相阻塞；
- 队列容量上限（如 50），入队返回 position，前端轮询文档 graph\_status 展示进度；
- 服务重启队列清空（内存队列）：启动时**不做自动恢复**，用户可重新点击构建（状态为 building 的行会因重启滞留，启动时将其重置为 none/failed 并提示）。

***

## 9. API 设计

### 9.1 图谱管理 API

| 方法     | 路径                                                             | 说明                          |
| ------ | -------------------------------------------------------------- | --------------------------- |
| GET    | `/api/knowledge-bases/{kb_id}/graph/stats`                     | 图谱概要统计（实体/关系数、类型分布、构建进度）    |
| GET    | `/api/knowledge-bases/{kb_id}/graph`                           | 图谱数据（按 degree 降序采样取前 N，默认 500，ECharts 渲染用）     |
| POST   | `/api/knowledge-bases/{kb_id}/graph/build`                     | 全量构建（异步，返回 queue\_position） |
| DELETE | `/api/knowledge-bases/{kb_id}/graph`                           | 清空整个知识库图谱                   |
| POST   | `/api/knowledge-bases/{kb_id}/documents/{doc_id}/graph/build`  | 单文件增量构建（异步）                 |
| DELETE | `/api/knowledge-bases/{kb_id}/documents/{doc_id}/graph`        | 清除单文件图谱                     |
| GET    | `/api/knowledge-bases/{kb_id}/documents/{doc_id}/graph/status` | 单文件图谱状态                     |

### 9.2 图检索 API

| 方法   | 路径                                                   | 说明                     |
| ---- | ---------------------------------------------------- | ---------------------- |
| POST | `/api/knowledge-bases/{kb_id}/graph/search`          | 子图检索（§6.2，返回 chunk 证据） |
| GET  | `/api/knowledge-bases/{kb_id}/graph/entities?name={name}` | 实体详情 + 1-hop 邻居 + **chunk 证据**（name 用 query 参数，实体名含特殊字符不破坏 URL） |

### 9.4 实体管理 API（实体列表 + 编辑）

| 方法 | 路径 | 说明 |
| ---- | ---------------------------------------------------- | ---------------------- |
| GET  | `/api/knowledge-bases/{kb_id}/graph/entities/list?page=&page_size=&q=` | 实体分页列表：名称模糊搜索（CONTAINS，大小写不敏感）+ 关系数（不含 `提及`）/ chunk 数；按关系数降序、名称升序 |
| PUT  | `/api/knowledge-bases/{kb_id}/graph/entities` | 编辑实体（名称/类型/描述）：`old_name`/`old_type` 定位；类型/名称键变化做冲突检测（409）；实体不存在 404；类型非法 400 |

- 更新语义：类型变化时 `REMOVE n:旧label` + `SET n:新label`（label 过白名单）；名称/类型是节点键，冲突即拒绝；关系基于图结构引用自动跟随改名/改类型；description 覆盖先到先得约定（用户手动编辑优先级最高）；
- 列表响应：`{total, page, page_size, items: [{name, type, description, relation_count, chunk_count}]}`；
- 前端（GraphView 右侧卡片「实体列表/详情」双标签页）：列表搜索 + 分页；行内「编辑」（弹窗表单）；「渲染」= 实体 + 1-hop 邻居并入画布（复用 `expand_node`）；chunk 数可点击查看该实体全部证据原文。

### 9.3 关键响应示例

`GET .../graph/stats`：

```json
{
  "kb_id": "kb_a1b2c3d4",
  "graph_exists": true,
  "stats": {
    "total_entities": 1250,
    "total_relations": 3420,
    "entity_types": {"Organization": 85, "Project": 230, "Person": 156},
    "relation_types": {"包含": 536, "关联": 159, "要求": 15}
  },
  "build": {
    "doc_total": 12, "built": 8, "building": 1, "failed": 1, "none": 2
  }
}
```

`GET .../graph`（ECharts 数据，limit 默认 500，超量返回"需要检索缩小范围"提示）：

```json
{
  "nodes": [{"id": "org_甲公司", "name": "甲公司", "type": "Organization", "degree": 28}],
  "edges": [{"source": "org_甲公司", "target": "proj_XX项目", "type": "参与"}]
}
```

`POST .../documents/{doc_id}/graph/build` 响应（与解析队列一致风格）：

```json
{"doc_id": "doc_xxx", "graph_status": "building", "queue_position": 1}
```

`GET .../graph/entities?name={name}` 响应（chunk 证据）——按 §6.2 `evidence` 结构，chunks 按页码/切片序号排序，供前端高亮与跳转。

***

## 10. Neo4j 部署与配置

### 10.1 docker-compose 新增服务（本地开发）

```yaml
  neo4j:
    image: neo4j:5.21.0
    container_name: bid_neo4j
    ports:
      - "7474:7474"   # Neo4j Browser（可选调试）
      - "7687:7687"   # Bolt 协议（应用连接）
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD:-password123}
      NEO4J_PLUGINS: '["apoc"]'                 # 可选：已改用纯 Cypher 去重（reduce），运行时不依赖 APOC
      NEO4J_server_memory_heap_max__size: 1g
    volumes:
      - ./volumes/neo4j/data:/data
      - ./volumes/neo4j/logs:/logs
```

### 10.2 配置项（config.py + .env）

```python
# config.py
# === Neo4j 图谱配置 ===
graph_enabled: bool = False        # 图谱功能总开关（未部署 Neo4j 时优雅降级）
neo4j_uri: str = "bolt://localhost:7687"
neo4j_username: str = "neo4j"
neo4j_password: str = "password123"
neo4j_database: str = "neo4j"
graph_extraction_batch: int = 1    # LLM 抽取：逐 chunk 提取（每 chunk 一次调用，减少跨 chunk 幻觉；可调大合并节省成本）
graph_max_hops: int = 2            # 图检索最大跳数
graph_query_limit: int = 200       # 子图返回上限
```

### 10.3 连接封装（core/neo4j\_store.py）

- 懒加载单例 `neo4j.GraphDatabase.driver(...)`，连接池由 driver 管理；
- 所有 Cypher 执行经 `asyncio.to_thread` 放线程池（与 Milvus 同模式），避免阻塞事件循环；
- 提供 `execute_read/execute_write/health_check`（health 扩展现有 `GET /api/health`，返回 neo4j 状态字段）；
- 启动时执行 §3.1 的复合索引创建（幂等）。

***

## 11. 前端设计

### 11.1 路由与入口

| 项      | 内容                                                             |
| ------ | -------------------------------------------------------------- |
| 路由     | `/knowledge-bases/:kbId/graph` → `GraphView.vue`               |
| 入口     | KnowledgeBaseView 知识库卡片操作"图谱"；DocumentListView 文档行"图谱状态/构建/清除" |
| API 封装 | `src/api/graph.js`（stats/graph/build/clear/search 等）           |
| 依赖     | `npm install echarts`（按需引入 `echarts/core` + ForceGraph 系列）     |

### 11.2 GraphView\.vue 布局

```
┌────────────────────────────────────────────────────────────┐
│ 统计卡片行：实体数 | 关系数 | 类型分布 | 构建进度 (built/total) │
│ 操作栏：    [全量构建] [清空图谱] [实体搜索框]                 │
├──────────────────────────────────────────────┬─────────────┤
│  ECharts 力导向图画布                          │ 详情面板     │
│  · 节点按 type 着色，大小按 degree              │ 选中实体：   │
│  · 关系按 type 显示 label                      │ 属性/邻居列表/│
│  · 点击节点 → 展开 1-hop 邻居（调 search API）  │ chunk 证据： │
│  · 拖拽/缩放（ECharts 内置）                    │ 切片文本(高亮)│
│                                              │ +页码+跳转原文│
├──────────────────────────────────────────────┴─────────────┤
│ 文档图谱状态 Tab：文件列表（graph_status + 构建/清除按钮）      │
└────────────────────────────────────────────────────────────┘
```

### 11.3 交互细节

- 构建/清除按钮点击后**轮询文档 graph\_status**（1s 间隔，直至 completed/failed），避免长轮询；
- 清除操作二次确认（Element Plus `ElMessageBox.confirm`）；
- 图谱数据量大时（> 500 节点）提示"请用检索缩小范围"；
- 全量构建进行中（stats.build 显示 building）图谱页显示"图谱构建中"提示/遮罩，避免用户看到清空后/重建中的空图谱误判；
- 全量构建/清空在知识库卡片或图谱页操作栏提供；
- **点击实体/检索结果 → 详情面板展示 chunk 证据列表**（切片文本 + 实体高亮 + 页码 + 文件名），点击跳转 `ChunkListView`（`/knowledge-bases/:kbId/documents/:docId/chunks`）定位原文。

### 11.4 LexiconView 词库分类枚举化改造（配合 §5.2.1）

| 改造点 | 内容 |
|--------|------|
| 分类输入 | `el-input` 自由文本 → `el-select` 固定下拉（公司/机构、人员、地点、项目、产品、概念/资质、其他/术语） |
| 列表展示 | 分类列按枚举映射显示中文 label（存储值为英文枚举） |
| 分类筛选 | 筛选项同步改为枚举下拉 |
| CSV 导入 | 中文分类自动映射为枚举值，未知值归 `other` 并在导入结果提示 |
| 旧数据整理 | 提供"分类规范化"提示（或一键修正入口），引导用户修正存量词条 |

***

## 12. 分阶段实施计划

| 阶段     | 内容                                                                     | 验证                                   |
| ------ | ---------------------------------------------------------------------- | ------------------------------------ |
| **P0** | Neo4j 部署（compose/.env/config）+ `core/neo4j_store.py`（连接/健康检查/索引）；**测试策略**：pytest 用 docker compose 测试实例（真实 Neo4j），CI 无 Neo4j 时跳过图相关用例 | `GET /api/health` 返回 neo4j ok；索引创建成功；P3/P5 图用例可运行 |
| **P1** | alembic 迁移 `0014_graph_status`（documents 图谱状态列）+ ORM/Schema 字段 + `core/graph_queue.py` | 迁移升级成功；入队/出队单测通过 |
| **P2** | 抽取管线：`lexicon_service.match_entities` 扩展 + 词库分类枚举化（数据规范化脚本） + 正则规则 + LLM 抽取 + 去重 | 单测：给定 chunk 文本，抽取结果符合 Schema；旧词条分类修正完成 |
| **P3** | 图谱写入/清除：MERGE 事务 + 清除三连 Cypher                                         | 单测：构建/清除后 Neo4j 节点关系计数正确             |
| **P4** | 构建管理 API：全量/单文件/清除/状态（graph\_service + graph router）                   | 接口测试：状态流转 none→building→completed    |
| **P5** | 图检索 API：stats/search/entities（含 **chunk 证据返回**）               | 接口测试：子图 + evidence 结构正确；隔离校验（跨 KB 查不到）；证据页码/文本准确 |
| **P6** | 前端：GraphView + ECharts + 文档列表操作 + api/graph.js + **LexiconView 分类枚举化** | 手工验收：构建/查看/清除全流程可视化正确；词库分类下拉生效 |
| **P7** | 测试与验收：pytest 全量 + 端到端                                                  | 全绿；图谱管理三需求演示通过                       |

## 13. 风险与注意事项

| 序号 | 风险              | 影响              | 应对                                               |
| -- | --------------- | --------------- | ------------------------------------------------ |
| 1  | Neo4j 未部署时功能不可用 | 图谱模块整体不可用       | `graph_enabled` 开关 + 接口返回 503 友好提示               |
| 2  | LLM 抽取成本/耗时     | 全量构建慢、费用高       | 词库优先 + 批量 + 小 chunk 跳过 + 单 worker 串行             |
| 3  | 实体同名异义误合并       | 图谱出现错误关联        | 键含 type；description 保留；清除按 source\_doc\_ids 精确剥离 |
| 4  | 子图展开爆炸          | 前端渲染卡顿          | max\_hops ≤ 2 + limit 200 + 前端采样提示               |
| 5  | 重启丢失队列任务        | 构建状态滞留 building | 启动时重置滞留 building → none（提示用户重试）                  |
| 6  | 抽取质量不稳定         | 图谱噪声            | Schema 约束 + 仅抽取明确提及信息（prompt 硬约束）                |
| 7  | 存量词条分类未整理       | 实体类型标签不准/漏建节点 | 枚举化改造时一次性数据修正（§7.3）+ CSV 导入自动映射兜底             |
| 8  | 删除文档/重切片未联动清图 | mentioned\_chunk\_ids 悬空指向已删切片 | `delete_document`/`reparse`/`rechunk` 流程联动调用 `clear_file_graph`（先取 chunk\_ids 再删 PG，失败不阻断主流程，§4.3 调用契约）；证据查询时过滤失效 chunk\_id；重建幂等兜底 |
| 9  | 与 chat 双通道衔接    | 本阶段无此能力         | API 结构（stats/search）已为下一阶段 query router 预留       |
| 10 | 词库变更与存量图谱不一致 | 已建图谱的实体名/类型过期（词条改名、删除、改分类） | 词条变更后该 KB 已建文档 `graph_status` 置 none（stale 标记），前端提示重新构建（§7.1）；重建走清旧重建，成本可控 |

