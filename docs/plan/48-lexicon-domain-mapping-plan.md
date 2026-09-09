# 48 词库 × 领域模板多对多映射（取代全局固定分类）开发方案

> 关联设计：docs/10-generic-kb-v2-design.md §7（本节修订取代 §3.3/§6.3 旧表述）、docs/04-lexicon-design.md（v2 修订注记）
> 日期：2026-09-08｜分支：v2-generic-kb｜状态：待评审

## 1. 需求（已确认）

- v2 实体类型由“KB → 领域模板”决定；词库全局固定分类（category）与之冲突。
- **决策**：词库与领域模板建立多对多映射：`词条 × 领域模板 → 该模板内 1 个实体 code`（同域唯一）；无映射则该词条在该域不进图（仍全局分词/同义词）。
- 删除 `lexicon_terms.category`；存量分类弃置（不迁移）；词库其余（IK 词典、查询同义词扩展、term+aliases 结构）不变。

## 2. 改动范围

| 面 | 文件 | 说明 |
|---|---|---|
| 模型 | `models/lexicon.py`（去 category）、新增 `models/lexicon_mapping.py` | ORM：LexiconTerm / LexiconDomainMapping |
| DB | 新迁移 `0025_lexicon_domain_mappings.py` | drop idx/category + 建映射表（unique(term,profile) + idx(profile)） |
| Repo | `repositories/lexicon_repository.py`；新增 `repositories/lexicon_mapping_repository.py` | 词条去 category；映射按 term/profile CRUD、按 profile 取词条→code |
| Service | `services/lexicon_service.py` | 去 category/映射表/match_entities 改造：缓存词条 aliases 不变；新增“按 profile 的词库实体映射”查询与变更联动 |
| 抽取 | `core/graph_extractor.py` | 词库命中 → 需 KB 模板下映射 code；LLM 归并仅同 code；删固定 label 映射 |
| 图检索 | `services/graph_service.py` search_subgraph | 候选 = 该 KB 模板映射词条 + 正则 + query |
| 删除联动 | `services/domain_service.py`、`services/lexicon_service.py` | 删模板/词条 → 删映射；模板复制不复制映射 |
| API | `routers/lexicon.py`、`models/schemas.py` | 词条级映射 GET/PUT；词条/统计响应去 category |
| 前端 | `api/lexicon.js`、`views/LexiconView.vue` | 去分类列/过滤/表单；词条“领域映射”编辑（领域下拉 → 域内实体类型下拉） |
| 文档 | 本文件、docs/10 §7、docs/04（已完成） | — |
| 测试 | test_lexicon_service/router、test_graph_extractor、新 test_lexicon_mapping_*、domain/graph 受影响用例 | 见 §5 |

## 3. 关键设计

### 3.1 映射语义（供评审）
- 键唯一（term, profile）；写入时校验 code ∈ profile.entity_codes（查 domain_service）；
- 抽取按 KB 的 profile 取 `{标准词: code}` 映射集（mapping 数量小，构建时按 profile 一次取全，进程内随词库缓存联动失效）；
- 词条命中（term/aliases 最长匹配，沿用现逻辑）→ 仅当词条在该 profile 有映射才产出实体；
- LLM 归并（原 `_merge_with_lexicon`）：只对“该词条在该 KB profile 的映射 code == LLM type”的词条生效；
- 图检索候选语义同上（无类型要求，只需“命中词条名”）；
- 联动失效：词条或映射变更 → 沿用全量图谱失效（保守、与现状一致）；模板删除 code 后映射自然失效不报错。

### 3.2 伪代码（服务/抽取）

```
# lexicon_service
cache[term] = {"aliases": [...]}              # 全局不变（分词/同义词）
def match_terms_in_profile(text, term_map) -> [(name, code)]:
    for term, code in term_map:               # term_map = 该 KB 模板的 {标准词: code}
        if term in text: 命中(term, code)
        elif any(a in text for a in aliases[term]): 命中(term, code)
    长词优先排序

# graph_extractor.extract_from_chunks(kb_id, chunks, profile)
term_map = await lexicon_service.profile_term_map(profile.id)   # {词条: code}，码不在 profile.entity_codes 则剔除
for chunk:
    for (name, code) in match_terms_in_profile(chunk.text, term_map):
        _add_entity(name, code, "", chunk_id)
    ...正则（模板含 Quantity/Time）… LLM 照旧
LLM 实体: name = merge 仅当词条映射 code == e.type（同码归并）; 否则原文名

# lexicon_mapping repo
upsert_replace(term_id, [(profile_id, code)])   # 整包：删旧插新；写前校验 code ∈ profile
list_by_term(term_id) / list_by_profile(profile_id)
delete_by_term / delete_by_profile
```

## 4. 实现步骤

1. 模型/迁移/仓库 → 验证：`alembic upgrade head`（注入 PG_*）成功、列/表结构正确、行数不变
2. 服务层（profile_term_map + 变更联动 + 去 category）→ 验证：词库单测适配通过
3. 抽取/图检索/删除联动/API → 验证：graph/domain/lexicon 相关单测适配通过
4. 前端（去分类 + 领域映射编辑 + 列表映射展示）→ 验证：`npm run build` 通过
5. 收尾：全量 pytest、docs/changes.md、提交

## 5. 验证

- 定向：lexicon/mapping/extractor/graph_service 相关 pytest 全绿；
- 行为断言：无映射词条在任意域不建实体；同词条不同域映射不同 code 各自生效；映射 code 不在模板时跳过；归并仅同码；
- 迁移真实执行 + 行数不变；CSV 导入 3 列旧文件兼容；
- 全量 pytest（记录于 changes.md）；`npm run build`。
