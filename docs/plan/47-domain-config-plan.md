# 方案 47 — 通用知识库 v2 领域配置（图谱本体动态化）开发方案

> 版本: v0.1（草案） | 日期: 2026-09-08 | 分支: `v2-generic-kb` | 关联设计: [10-generic-kb-v2-design.md](../10-generic-kb-v2-design.md)（评审中）
> 前置：设计文档 §14 开放问题确认后本方案定稿；默认值见 §1.1，未确认项不阻断本方案结构，只影响种子与范围微调。

## 0. 目标

按设计 10 实现 v2 领域可配置：`domain_profiles` 表 + 页面 CRUD + KB 绑定/换绑 + 图谱抽取动态化（提示词生成/动态校验/白名单/词库过滤）+ 图谱 meta 接口 + 前端 Graph 动态渲染。落地环境：`v2-generic-kb` / `ai_bid_rag_v2`（已 0023）。

## 1. 改动范围

### 1.1 待确认默认值（对应设计 §14）

| 开放问题 | 本方案默认 | 影响 |
|---|---|---|
| D4 内置投标预设 | 内置（通用 + 投标 两枚种子） | 0024 种子条数 |
| D8 v1 存量搬移 | 不搬移（v2 空库，操作手册式） | 无迁移逻辑 |
| 预设可编辑性 | 预设只读，可复制后编辑 | API 权限/前端按钮 |
| 每实体类型独立开关（正则/归并） | 不做 | 无 |

### 1.2 后端改动文件

| 文件 | 改动 |
|---|---|
| `alembic/versions/0024_domain_profiles.py`（新增） | 建 `domain_profiles` 表 + `knowledge_bases.domain_profile_id` 列 + 种子（import `core.domain_presets`） |
| `core/domain_presets.py`（新增） | 通用/投标预设常量（entity/relation 类型结构体），迁移与代码共用单一事实源 |
| `models/domain.py`（新增） | `DomainProfile` ORM（UUIDMixin/TimestampMixin，JSONB 两列） |
| `models/document.py` / `models/knowledge_base.py` | KB 加 `domain_profile_id` 列 |
| `repositories/domain_repository.py`（新增） | 模板 CRUD、按 kb 查引用、KB 换绑、引用计数 |
| `services/domain_service.py`（新增） | 模板缓存（load/refresh/invalidate）、KB→profile 解析（缺省回退通用）、`build_extraction_prompt`、code/关系校验、类型数量约束（实体 3..20 / 关系 ≤30）、模板变更→引用 KB 图谱失效 |
| `models/graph_schemas.py` | `ExtractedEntity.type` 去 Literal → `str`；新增 domain/graph meta 相关 schema |
| `core/graph_extractor.py` | 删除 `ENTITY_TYPE_LABEL_MAP`；`_get_extraction_prompt` 改由 profile 生成（DB entity_extraction 模板退役）；LLM 输出按 profile 过滤；词库/正则命中按 profile 过滤 |
| `services/graph_service.py` | 删 `RELATION_TYPES` 常量；写图按 profile 过滤；`rel.get("a_type","Event")` 兜底改"缺失即丢"；`update_entity` 类型校验按 profile |
| `services/prompt_service.py` | `entity_extraction` 场景仅标注退役（不删数据） |
| `routers/domain.py`（新增） | `/api/domains` CRUD + `/copy` + `/preview`（生成提示词，不调 LLM） |
| `routers/knowledge_base.py` | 创建带 `domain_profile_id`；新增 `PUT /{kb_id}/domain` 换绑；`GET /{kb_id}/graph/meta`（或放 graph router） |
| `routers/graph.py` | 新增 `GET /graph/meta` |
| `main.py` | 注册 domain router + 启动时模板缓存预热 |

### 1.3 前端改动文件

| 文件 | 改动 |
|---|---|
| `src/api/domain.js`（新增） | domain CRUD/copy/preview 封装 |
| `src/views/DomainListView.vue`（新增） | 领域列表（预设标记/类型数/复制/删除） |
| `src/views/DomainEditView.vue`（新增） | 实体/关系类型表格编辑器（code 正则校验/去重/取色器/描述/示例/预览提示词弹窗） |
| `src/views/KnowledgeBaseView.vue` | 新建/编辑加"领域"下拉 + 领域摘要；卡片显示领域标签 + 换绑入口 |
| `src/views/GraphView.vue` | `ENTITY_TYPE_OPTIONS`/`TYPE_COLORS` 常量删除 → 拉 `graph/meta` 驱动图例/分布/编辑下拉 |
| `src/router/index.js` | `/domains` 路由 + 菜单 |

### 1.4 文档

- `docs/changes.md` 追加记录；README 产品定位句改"通用知识库 RAG（领域可配置）"；10 设计定稿（去掉"草稿"）。

## 2. 实现步骤与验证（每步验证后才进下一步）

```
Step 1  后端数据层
  1. core/domain_presets.py：通用 + 投标 预设常量（含 code 正则自检）
  2. models/domain.py + models/knowledge_base.py 加列
  3. 迁移 0024（建表/加列/种子）
  4. repositories/domain_repository.py CRUD
  验证: 迁移在 ai_bid_rag_v2 可执行；pytest 新增 test_domain_repository（CRUD + JSONB 读写）

Step 2  模板服务与提示词生成
  1. services/domain_service.py：缓存 + KB 解析 + build_extraction_prompt + 校验
  2. 单测 test_domain_service（解析/回退通用/越界拒绝/提示词含全部类型）
  验证: pytest test_domain_service 全绿

Step 3  抽取动态化（核心）
  1. graph_schemas: ExtractedEntity.type → str
  2. graph_extractor：profile 传入、过滤词库/正则/LLM 输出、删 LEXICON/ENTITY_TYPE_LABEL_MAP、DB 模板退役
  3. graph_service：白名单按 profile、a_type 兜底改丢弃
  4. 更新 test_graph_extractor（原 6 处常量用例按 profile 重构）+ test_graph_service
  验证: pytest 相关用例绿；真实建图（kb 绑通用预设）类型/关系全部落在模板内、模板外类型零残留

Step 4  API + 联动失效
  1. routers/domain.py CRUD/copy/preview
  2. KB 创建带 domain_profile_id + PUT domain 换绑
  3. GET graph/meta
  4. 模板编辑/换绑 → reset_graph_status_by_kb 逐库失效
  验证: 路由测试（CRUD/409 引用删除/404/换绑 200）+ 手工：改模板 → 文档图置 none

Step 5  前端
  1. api/domain.js + DomainListView/DomainEditView + 路由菜单
  2. KB 视图选领域/换绑
  3. GraphView 动态 meta
  验证: npm run build 通过；页面走查（建模板→建 KB 绑模板→传文档建图→图例/颜色/编辑下拉=模板）

Step 6  收尾
  1. changes.md / README / 10 定稿；提示词管理页 entity_extraction 只读说明
  2. 后端全量 pytest + 前端 build + 检索页 graph 通道回归
  验证: pytest exit 0；npm run build 0 错；graph 检索通道结果正常
```

## 3. 关键伪代码（约束实现，非最终码）

```python
# services/domain_service.py
def build_extraction_prompt(profile) -> str:
    lines = ["你是信息抽取系统。从文本中提取实体和关系，只提取明确提及的信息，不要编造。",
             "实体 name 必须与原文完全一致（禁止改写、合并或规范化），同一实体重复出现只需输出一次。",
             "", "实体类型（仅限以下）:"]
    for e in profile.entity_types:
        lines.append(f"- {e.code}({e.name})：{e.description}" + (f" 示例：{'、'.join(e.examples)}" if e.examples else ""))
    lines += ["", "关系类型（仅限以下，方向见括号）:"]
    for r in profile.relation_types:
        hint = f"（{r.direction_hint}）" if r.direction_hint else ""
        lines.append(f"- {r.name}{hint}：{r.description}")
    lines += ["", "输出严格 JSON（不要其他文字，不要 markdown 代码块）:", '{"entities":[...],"relationships":[...]}']
    return "\n".join(lines)

def validate_profile(payload) -> list[str]:          # 返回错误列表
    codes = [e.code for e in payload.entity_types]
    errs = [e for e in codes if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", e)]  # + 长度 3..20、去重、颜色 hex、关系 ≤30

# core/graph_extractor.py 变更点
# 词库命中: for ent in match_entities(text):
#     if ent["type"] not in profile.entity_codes: continue      # 领域外词条跳过
# LLM 实体: if e.type not in profile.entity_codes: drop_count++; continue
# 正则:     仅当 profile 含 Quantity/Time 时调用对应正则以 code 出实体
# 关系:     if r.type not in profile.relation_names: drop_count++; continue
# 提示词:   system_prompt = build_extraction_prompt(profile)   # 不再读 DB entity_extraction

# 模板变更联动（routers/domain.py PUT 后）
async def on_profile_updated(profile_id):
    kb_ids = await domain_repo.list_kb_ids_by_profile(profile_id)
    for kb_id in kb_ids:
        await document_repo.reset_graph_status_by_kb(kb_id)      # completed → none
    domain_service.invalidate(profile_id)
```

## 4. 风险与注意

1. `ExtractedEntity.type` 去 Literal 后 Pydantic 不再拦截非法值 → 过滤责任在抽取层（Step 3），务必有丢弃计数日志。
2. 迁移 0024 import `core.domain_presets` 常量，避免种子双写；预设 code 变更即"换本体"，走失效重建。
3. Neo4j 关系类型仍为中文（反引号已支持）；实体 label 用 code（正则已约束，无注入面）。
4. 删除被引用模板必须 409（repository 引用计数）。
5. 环境：迁移跑前注入 PG_* 环境变量（alembic 不读 .env，见设计 §10）；联调数据都在 `ai_bid_rag_v2`，勿连 v1 库。
6. v1 分支/库不受影响；.env 回滚路径见设计 §10。

## 5. 验证方式汇总

- 每步 pytest（新增 test_domain_repository/service/router + 重构 test_graph_extractor/service/router）；
- 真实环境（ai_bid_rag_v2 + 远程 PG/Neo4j）：通用预设库建图类型/关系全在模板内；医疗自定义模板库（含 3-5 类新实体/关系）建图正常且投标词库命中不越界；
- 模板变更 → 引用 KB 图谱置 none；换绑生效；graph/meta 驱动前端图例/编辑下拉；
- 后端全量 pytest exit 0 + 前端 `npm run build` 成功 + 检索页 graph 通道回归正常。
