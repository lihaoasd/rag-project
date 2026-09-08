# 通用知识库平台 v2 — 领域可配置 详细设计

> 版本: v0.1（草稿，待评审） | 日期: 2026-09-08 | 分支: `v2-generic-kb` | 数据库: `ai_bid_rag_v2`（PG 192.168.60.120:5444，已迁移至 0023）
> v1 封板：`langfuse-v4` 分支 HEAD 已打 tag `v1.1.0`，本地 `.env` 备份为 `backend/.env.v1.local`（回滚：切回 `langfuse-v4` 后将 `.env.v1.local` 覆盖回 `.env`）。
> 关联文档：[03-graph-rag-design.md](03-graph-rag-design.md)、[04-lexicon-design.md](04-lexicon-design.md)、[09-graph-module-design.md](09-graph-module-design.md)、[changes.md](changes.md)

---

## 0. 一句话目标

把"投标文档 RAG"升级为**通用知识库平台 v2**：知识库（KB）可承载任意领域语料；图谱的**实体类型 / 关系类型**不再写死，而是由**领域模板（domain profile）**承载 —— 用户在**页面**上即可创建/编辑领域模板并挂到知识库，图谱抽取、白名单校验、可视化全部跟随模板运行。

> 本设计仅覆盖"图谱本体领域化"（对应此前方案分析中的 **A1 + B1**）；词库内容/分类的领域化、提示词全套领域化列为 2.1 二期（§8）。

---

## 1. 背景、范围与决策总表

### 1.1 现状痛点（v1，2026-08-24 起为通用本体 9 实体 / 12 关系）

- 图谱类型/关系写死在代码与 DB 模板：`models/graph_schemas.py`（`ENTITY_TYPES` Literal）、`core/graph_extractor.py`（`ENTITY_TYPE_LABEL_MAP` + 内置 fallback 提示词）、`services/graph_service.py`（`RELATION_TYPES` 白名单）、DB `entity_extraction` 激活模板（迁移 0016 种子）、前端 `GraphView.vue`（`ENTITY_TYPE_OPTIONS`/`TYPE_COLORS`）。
- 真实抽取分布失衡（kb 98b88fd5 重建）：Concept 467 / Product 206 / Quantity 56 / Organization 4 —— 兜底类型把 资质/标准/条款/要求 全吸收，动作语义（中标/招标/资质要求等）在通用 12 关系里被压成 关联/参与/要求。
- 系统绑定投标场景（产品名、词库、示例语料），无法承载其他领域；反过来，写死投标本体又让通用化无从谈起。
- **结论（用户已确认方向）**：平台通用化，分类领域化且**由用户页面配置**；不再在代码里选"通用还是投标"。

### 1.2 版本边界（v2 2.0 做什么 / 不做什么）

**做（2.0）**
1. 领域模板数据模型 + 页面 CRUD（实体/关系类型自定义，含名称、颜色、描述、示例）；
2. 知识库绑定领域（新建选、可换绑）；
3. 图谱抽取管线动态化：提示词由模板生成、LLM 输出按模板校验、Neo4j 标签/关系白名单按模板、词库命中按模板过滤；
4. 图谱可视化/实体编辑/词库分类提示全部由"KB 所属模板元数据"驱动；
5. v1 封板与 v2 环境隔离（分支/数据库）落地，迁移 0024+ 与种子模板。

**不做（2.0 边界，2.1+ 再议）**
- 词库内容/分类按领域隔离（词库仍全局，见 §7）；
- 金额/日期字面值改为实体属性（改由模板省略对应类型即可消除噪声，§3.5）；
- chat 问答双通道（沿用 09 文档边界）；
- v1 存量数据自动搬移（v2 空库起步，§9）。

### 1.3 决策总表（带确认状态）

| 编号 | 决策点 | 结论（推荐） | 理由 | 状态 |
|---|---|---|---|---|
| D1 | 领域建模 | **独立 `domain_profiles` 表，KB 引用 profile_id**（A1） | 多库复用、集中升级、预设=种子数据；仅多一张表 | ✅ 采用 |
| D2 | KB↔模板语义 | **引用（live）**：模板改动即时生效，已绑定 KB 的文档图谱置 `none` 提示重建（复用词库变更联动机制 ③） | 编辑模板不静默影响线上图；重建机制现成 | ✅ 采用 |
| D3 | 配置范围 | **图谱本体（实体/关系/颜色/描述）**；词库命中按模板过滤 | 直击痛点、影响面可枚举、与向量检索解耦 | ✅ 采用 |
| D4 | 内置模板 | **通用预设（必选种子）**；投标预设（把现 9/12 固化）默认一并内置供选 | 平台通用 + 存量投标语料仍可用；成本≈0 | ⚠️ 投标预设请确认 |
| D5 | 抽取提示词来源 | 由模板**自动生成** schema 约束提示词；`entity_extraction` 模板管理场景**退役**（不再优先于生成式） | 消除"DB 模板 vs 本体配置"双事实源 | ✅ 采用 |
| D6 | 实体类型字段 | `code`（Neo4j 标签安全：`^[A-Za-z][A-Za-z0-9_]*$`，存 label/type 属性）+ `name`（中文显示）+ `color` + `description`；关系类型 = 中文名 + description（沿用反引号写库） | 图标签安全 + 前端展示友好 | ✅ 采用 |
| D7 | 类型数量约束 | 实体 3..20、关系 0..30；LLM 只输出模板内类型，越界丢弃并记日志 | 防自由度过高导致抽取混乱 | ✅ 采用 |
| D8 | 存量（v1→v2） | v1 库保留不动；v2 空库；提供操作手册式搬移（词库 CSV 导出导入 / 文档重传），不做自动迁移 | v2 全新产品线，避免脏数据 | ⚠️ 请确认 |
| D9 | 字面值（金额/时间） | 2.0 仍建模为节点（模板含 Quantity/Time 才抽取；用户可移除该类型） | 属性化重构推迟；类型可由用户配置即已解决"噪声" | ✅ 采用（随模板默认） |

---

## 2. 数据模型设计

### 2.1 新表 `domain_profiles`（领域模板）

| 列 | 类型 | 说明 |
|---|---|---|
| id | VARCHAR(36) PK（沿用 UUID 字符串风格） | |
| name | VARCHAR(100) UNIQUE NOT NULL | 领域名，如"投标""医疗""法律" |
| description | TEXT NULL | |
| entity_types | JSONB NOT NULL | 实体类型数组，见 §2.2 |
| relation_types | JSONB NOT NULL | 关系类型数组，见 §2.2 |
| is_preset | BOOLEAN NOT NULL DEFAULT false | 是否系统内置预设（预设禁止删除，可复制） |
| created_at / updated_at | TIMESTAMPTZ | TimestampMixin |

### 2.2 类型 JSON 结构

```jsonc
// entity_types 元素（实体）
{
  "code": "Organization",          // Neo4j 节点标签 + type 属性；^[A-Za-z][A-Za-z0-9_]*$
  "name": "组织/机构",              // 前端展示名
  "color": "#409EFF",              // ECharts/标签颜色（hex）
  "description": "公司、部门、机构、招标人/代理/监管单位",  // 进入抽取提示词的引导
  "examples": ["甲公司", "市交通局"]  // 可选：示例，进入提示词
}

// relation_types 元素（关系，业务关系；锚点边"提及"为系统保留，不在配置内）
{
  "name": "中标",                  // 关系名（中文，Neo4j 反引号写库）
  "description": "公司中标某项目（Organization→Project）",
  "direction_hint": "Organization→Project"  // 可选提示，进提示词辅助方向约束
}
```

> 约束校验（后端写接口强制）：实体 code 唯一且满足标签正则；type 数组长度 3..20；relation 数组 ≤30、name 非空且去重；颜色为合法 hex；**不允许**实体 code 与系统保留标签冲突（`Entity`/`Document`）。

### 2.3 KB 关联（改动 `knowledge_bases`）

- 新增列 `domain_profile_id VARCHAR(36) NULL`（v2 空库无存量；约束可选 FK，沿用本库"无外键"风格 —— 与 0005 全部去外键一致，只做普通列 + 应用层校验）。
- 删除知识库时：仅删 KB，模板保留（模板不因被引用而禁删；删除模板前校验无 KB 引用 → 有则 409）。
- 新建 KB 必选模板（前端默认选中"通用预设"）；允许换绑（§6.3）。

---

## 3. 图谱抽取动态化（后端核心改造）

> 现状写入链路：`graph_extractor.extract_from_chunks`（词库/正则 + LLM schema 抽取）→ `graph_service._build_write_statements`（RELATION_TYPES 白名单）→ Neo4j。改造目标：**链路上所有"类型常量"改为"按 KB → 模板解析"**。

### 3.1 模板解析服务（新增 `services/domain_service.py`）

```python
# 缓存: {profile_id: DomainProfileData}，复用 lexicon_service 缓存风格（load/refresh/invalidate）
DomainProfileData = {
  "id": ..., "name": ...,
  "entity_codes": {"Organization", ...},          # 集合，供过滤/白名单
  "entity_meta": {"Organization": {name, color, description, examples}},
  "relation_names": {"中标", ...},                # 集合
  "relation_meta": {"中标": {description, direction_hint}},
}

async def get_profile_for_kb(kb_id) -> DomainProfileData   # 缺省/未绑定 → 通用预设
async def get_profile(profile_id) -> DomainProfileData
def build_extraction_prompt(profile) -> str                  # §3.2
def validate_entity_type(profile, code) -> bool
def validate_relation(profile, name) -> bool
```

- KB→profile 解析顺序：`kb.domain_profile_id` → 有则取；无（异常兜底）→ 内置通用预设，记日志。

### 3.2 抽取提示词生成（替代固定 fallback + DB `entity_extraction` 模板）

```text
[system]
你是信息抽取系统。从文本中提取实体和关系，只提取明确提及的信息，不要编造。
实体 name 必须与原文完全一致（禁止改写、合并或规范化），同一实体重复出现只需输出一次。

实体类型（仅限以下）:
- Organization(组织/机构)：公司、部门、机构、招标人/代理/监管单位。示例：甲公司、市交通局
- Project(项目/工程/任务)：……
- …（按模板 entity_types 逐条生成，含 name/description/examples）

关系类型（仅限以下，方向见括号）:
- 中标(Organization→Project)：公司中标某项目
- …（按模板 relation_types 生成，含 direction_hint）

输出严格 JSON（不要其他文字，不要 markdown 代码块）:
{"entities":[{"name":"...","type":"Organization","description":"..."}],
 "relationships":[{"source":"...","target":"...","type":"中标","description":"..."}]}
```

- **退役 DB 模板**：`graph_extractor._get_extraction_prompt` 不再调 `prompt_service.get_active_prompt("entity_extraction")`；改为 `build_extraction_prompt(profile)`。迁移 0016 种子保留但不再被读（或 0024 里将其 is_default 置 false 并注释）；提示词管理页该场景做只读提示文案（"图谱实体抽取提示词由领域模板自动生成"）。
- `_llm_extract_batch` 签名加 `profile` 参数（`extract_from_chunks` 开头解析一次传入）。

### 3.3 LLM 输出校验动态化（`models/graph_schemas.py`）

- `ExtractedEntity.type` 去掉固定 `ENTITY_TYPES` Literal → 改 `str`；**校验移到抽取层**：`type not in profile.entity_codes` 的实体丢弃并计数（复用现有 drop 日志）；关系 `name not in profile.relation_names` 丢弃。
- `ENTITY_TYPE_LABEL_MAP` 删除，标签即 `code`；正则 `_regex_match_entities`（金额/日期）仅在 profile 含 `Quantity`/`Time` 类型时启用，类型标签用模板 code（通用预设含则保留）。
- 词库匹配过滤：`lexicon_service.match_entities` 输出仍按全局映射给 label（Organization/Person/…）；在 `_add_entity` 前检查 `label in profile.entity_codes`，不在则跳过（§7 规则落地）。

### 3.4 写图与查询动态化（`services/graph_service.py`、`core/neo4j_store.py`）

- `RELATION_TYPES` 常量删除 → 写图事务前用 `profile.relation_names` 过滤（`_build_write_statements` 传入 `valid_relations`）。
- 节点 label 拼接用模板 code（白名单 = `profile.entity_codes`，杜绝 Cypher 注入：code 已由 D6 正则约束，拼接安全，沿用现有"标签白名单校验"注释逻辑）。
- 实体编辑 `update_entity` 的类型下拉校验同模板（§6.3）。
- `get_graph_stats`/`get_graph_data`/`search_subgraph` 等**不需要**改类型逻辑（按既有属性/标签读），但响应需附带模板元数据供前端渲染（见 3.5）。
- **读取侧零改动确认（已核对 `graph_service` 源码）**：图谱读取全部按属性值取类型 —— stats 用 `RETURN n.type AS type`、子图/展开/分页用 `n.type`/`type(r)`、节点与边 id 为 `{type}_{name}`/`{source_type}_{source}` 拼接、检索过滤走通用 `(n:Entity)` 标签。因此实体 code 只需满足 D6 正则且库内唯一、稳定，读取路径无需改造。
- **实现注意（清理 v1 遗留默认值）**：`_build_write_statements` 中 `rel.get("a_type", "Event")` 的兜底默认 "Event" 是 v1 写法；动态化后应改为"端点类型缺失即丢弃该关系"（上游 §3.3 端点过滤已保证两端存在，此兜底不再需要默认类型）。

### 3.5 图谱元数据接口（前端动态渲染的单一来源）

```
GET /api/knowledge-bases/{kb_id}/graph/meta
→ {
    "profile": {"id","name"},
    "entity_types": [{"code","name","color","description"}],   // 本 KB 模板
    "relation_types": [{"name","description"}],
    "system_relation": "提及"
  }
```

GraphView 初始化/进入页面时拉取一次：图例、实体类型分布标签、详情/编辑下拉、颜色全部由该 meta 驱动。

---

## 4. API 设计（增量）

| 方法 | 路径 | 说明 | 备注 |
|---|---|---|---|
| GET | `/api/domains` | 模板列表（含 is_preset） | 分页可后加 |
| POST | `/api/domains` | 新建模板（非预设） | 校验 §2.2 |
| GET | `/api/domains/{id}` | 模板详情 | |
| PUT | `/api/domains/{id}` | 编辑模板 → 更新缓存 + 触发受影响 KB 图谱失效（§5） | 预设可编辑？→ 见 §6.1（推荐：预设可复制不可改） |
| POST | `/api/domains/{id}/copy` | 复制为自建模板 | |
| DELETE | `/api/domains/{id}` | 删除（is_preset 或存在引用 → 409） | |
| POST | `/api/domains/{id}/preview` | 输入示例文本 → 返回将生成的抽取提示词（不含 LLM 调用） | 配置页"预览效果" |
| PUT | `/api/knowledge-bases/{kb_id}/domain` | 换绑模板 | body `{domain_profile_id}` |
| GET | `/api/knowledge-bases/{kb_id}/graph/meta` | §3.5 | |

KB 创建请求 `KnowledgeBaseCreate` 增加 `domain_profile_id`（必填，默认通用预设由前端带入）。

---

## 5. 模板变更联动（复用"词库变更 → graph 失效"机制）

- 模板被编辑/换绑后：查所有 `knowledge_bases.domain_profile_id = X` → 将这些 KB 下 `documents.graph_status IN ('completed')` 置 `none`（提示用户重建）。复用现有仓库图状态方法：单库用 `DocumentRepository.reset_graph_status_by_kb(kb_id)`，模板变更按 kb 列表循环调用（或新增 `reset_graph_status_by_kbs(ids)` 批量方法）；词库全局变更仍走现有 `invalidate_all_graphs`。
- 换绑 KB 时同理（旧模板图全部失效）；
- 抽取时实时读模板（build 时解析），无版本快照 —— 重建后即新模板结果；
- 说明：**变更不自动重建**，只失效 + 前端提示（与 v1 一致的产品语义：数据保全、成本可控）。

---

## 6. 前端设计（Vue3 + Element Plus）

### 6.1 新增"领域配置"页（`/domains`，菜单：知识库 → 领域配置）

- 列表：名称、预设标记、实体/关系类型数、创建时间；行操作：编辑 / 复制 / 删除（预设只显示"复制"）。
- 编辑页（新建/编辑共用）：
  - 基本信息：名称、描述；
  - **实体类型编辑器**：表格行 {code, 中文名, 颜色(el-color-picker), 描述, 示例(逗号分隔)}；行内增删；行内校验（code 正则、非空、去重、3..20 条）；上限提示；
  - **关系类型编辑器**：表格行 {名称, 方向提示, 描述}；同上；
  - 底部"预览抽取提示词"按钮 → 调 `/preview` 弹窗展示生成文本；
  - 保存 → 校验 → POST/PUT；保存后若有 KB 引用弹提示"将提示 N 个知识库重新构建图谱"。
- 内置预设（种子，is_preset=true）：**通用**；可选加**投标**（D4 待确认）。

### 6.2 知识库创建/编辑（`KnowledgeBaseView.vue`）

- 新建弹窗：名称、描述 + **领域下拉**（默认"通用"），选中后展示该领域实体/关系类型摘要（tooltip/收起面板）；
- KB 卡片/详情显示领域标签；提供"更换领域"操作（确认框：将失效当前图谱，需重建）。

### 6.3 图谱页 / 词库页适配（`GraphView.vue`、`LexiconView.vue`）

- `GraphView`：`ENTITY_TYPE_OPTIONS`/`TYPE_COLORS` 常量删除，初始化拉 `graph/meta`；图例 = meta.entity_types（name+color）；实体分布 chip 用 name；编辑弹窗类型下拉 = meta entity name（值 code）；兜底色 `#909399`。
- 实体列表/详情/编辑（后端 `update_entity` 白名单改模板）联动同上。
- `LexiconView`：MVP 分类下拉**不变**（全局 7 枚举）；提示文案补充："词条分类映射到图谱实体类型，仅当该 KB 领域含对应类型才生效"。

---

## 7. 词库策略（2.0 规则 + 2.1 展望）

- **2.0**：词库保持全局（表、页面、IK/CSV 导出均不变）。图谱构建时词库命中实体按"全局分类→label"，命中 label 不在 KB 模板则**跳过不进图**（§3.3）——投标词不会污染医疗 KB 的图。
- **2.1（二期，不在本设计实施）**：词库按领域隔离（表加 `domain_profile_id NULL=全局`、分类枚举随模板、页面过滤、导出分域）；届时再评估查询扩展的域化。

---

## 8. 提示词管理与 LLM 配置的影响

- `entity_extraction` 场景：停止作为抽取事实源（§3.2）；PromptManagementView 中该场景提示文案改为只读说明，可保留历史模板供查看，避免删数据。
- `rag_qa`/`query_rewrite`/`intent_detect` 等模板管理**不变**（通用提示语，不涉领域）。
- LLM 模型配置（kb_model_bindings）不变：抽取 LLM 仍按 KB 解析。

---

## 9. 数据库迁移清单（alembic `0024_domain_profiles.py`）

1. `CREATE TABLE domain_profiles`（列见 §2.1，含 JSONB 两列）；
2. `ALTER TABLE knowledge_bases ADD COLUMN domain_profile_id VARCHAR(36)`（无外键，应用层校验）；
3. 种子：内置**通用预设**（entity_types = 现 9 类中性化：Organization/Person/Place/Event/Time/Project/Product/Quantity/Concept 带 name/color/描述/examples；relation_types = 现 12 种带方向提示）；可选第二枚**投标预设**（D4）；
4. （v2 空库无存量回填；若对 v1 库执行需另行评估 —— 本设计不迁移 v1 库，见 §10。）

> 若 D4 确认"仅通用一套"，则 3 只插通用。种子内容以迁移内 Python 结构体为准，与 §3.2 生成器共用同一常量源（`core/domain_presets.py` 导出，迁移 import 之，避免双写）。

---

## 10. 版本隔离与环境（已落地 + 使用说明）

| 项 | 值 | 说明 |
|---|---|---|
| v1 封板 | git tag `v1.1.0` @ `langfuse-v4` HEAD | v1 不再开发；分支保留可切回 |
| v2 分支 | `v2-generic-kb`（当前） | 从封板点切出，本设计全部落地于此 |
| v2 数据库 | `ai_bid_rag_v2` @ 192.168.60.120:5444 | 已 `alembic upgrade head` → 0023，10 表 |
| 环境文件 | `backend/.env`（现指向 v2 库）；v1 备份 `backend/.env.v1.local` | `.env` 被 gitignore，不随分支走 |
| 回滚 v1 | `git switch langfuse-v4` + `Copy-Item backend/.env.v1.local backend/.env` | |
| 共享中间件 | Milvus/ES/Neo4j 与 v1 同实例 | v2 数据天然按 kb_id/集合隔离；Neo4j 图谱按 kb_id 隔离（09 §3.1） |

> alembic 备注：`alembic/env.py` 用 `os.getenv` 读 PG 参数，**不读 .env** —— 跑迁移需先注入环境变量（`PG_HOST/PG_PORT/PG_DATABASE/PG_USER/PG_PASSWORD`），本地已按此流程验证通过。

---

## 11. 测试计划

- **单测（mock Neo4j）**：
  - `domain_service`：模板解析缓存、KB 解析、缺省回退通用、越界/重复/非法 code 校验拒绝；
  - `build_extraction_prompt`：按模板生成含全部类型/关系、非法 code 不出现；
  - `graph_extractor`：LLM 输出含模板外类型/关系 → 丢弃；词库命中 label 不在模板 → 跳过；正则在无 Quantity/Time 模板下不产出；替换原 `test_graph_extractor` 中 9 类 Literal 相关用例；
  - `graph_service`：写图前关系白名单按模板过滤；`update_entity` 类型下拉模板校验；
  - 路由：domain CRUD/复制/删除引用 409、KB 换绑失效联动、graph/meta 返回。
- **真实环境验收**：建"通用"模板库 + 临时"医疗样例"模板库各一份，上传样例文档建图 → 类型分布/关系全部落在各自模板内；改模板 → 文档图谱置 none 提示重建；重建后新类型生效；前端 Graph 图例/编辑下拉按模板显示。
- **回归**：后端全量 pytest + 前端 `npm run build`；检索页 graph 通道不回归（其走 search_subgraph 不动）。

---

## 12. 文档更新清单（随实现）

- 本设计定稿后进入 `docs/plan/` 开发方案（按项目流程 47-xxx-plan.md）；
- `docs/09-graph-module-design.md`：§3.1/§3.3/§5.2.1/§5.3/§7.3 改为"模板驱动"表述（v2 后该文档为 v1 图谱 + v2 动态层共存视角，或拆分 v2 专属章节）；
- `docs/04-lexicon-design.md`：补"词库命中按模板过滤"与 2.1 展望；
- `docs/changes.md`：按例追加每次改动；
- `README.md`：产品定位改"通用知识库 RAG 平台（领域可配置）"。

---

## 13. 里程碑（建议）

| 阶段 | 内容 | 验证 |
|---|---|---|
| M1 | 迁移 0024 + 种子 + domain_service + 抽取动态化（后端） | pytest + 真实建图（通用/自定义模板各一） |
| M2 | API 层 + 模板联动失效 | 路由测试 + 手工换绑 |
| M3 | 前端领域配置页 + KB 选域 + Graph 动态 meta | npm build + 页面走查 |
| M4 | 收尾：文档、README、回归、v2 部署说明 | 全量测试 + 评审 |

---

## 14. 开放问题（评审时确认）

1. D4：是否内置"投标"预设？（推荐：内置，供现有语料直接用）
2. D8：v1 语料是否有搬移需求（词库 CSV 已具备导入手动路径）？
3. 预设是否允许直接编辑（推荐：只可复制后编辑，保证种子纯净）？
4. 实体类型是否需要"是否参与词库归并/是否走正则"等每类开关（本期默认全开，超范围可不做）？
