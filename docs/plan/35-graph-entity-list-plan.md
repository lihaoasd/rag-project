# 图谱实体分页列表 + 编辑 + 搜索 + 点击渲染画布开发方案

> 关联设计: [09-graph-module-design.md](../09-graph-module-design.md) | 日期: 2026-08-24 | 状态: 已确认

## 1. 需求（用户已确认）

知识图谱页面新增：
1. **实体详情分页列表**：列出知识库图谱实体（分页），**行内可编辑**：描述 + 名称 + 类型；列表列展示：名称 / 类型 / **关系数** / **chunk 数** / 描述；
2. **chunk 数可点击查看原文**（弹窗列出该实体全部 chunk 证据原文，高亮实体名）；
3. **搜索实体**：按实体名模糊搜索（CONTAINS），结果同样进入分页列表；
4. **点击实体渲染到画布**：渲染「该实体 + 1-hop 邻居」子图（复用现有 `expand_node` 原语，合并去重入画布）；
5. 布局：右侧详情卡片改为 **「实体列表 / 详情」双标签页**。

## 2. 现状与关键决策

- 现有接口：`GET /graph/entities?name=`（单实体详情，保留不动）、`POST /graph/expand`（1-hop 邻居，前端渲染复用）、`GET /graph`（degree 分页）。
- Neo4j 节点结构：label = `:Entity:<类型>`（如 `:Entity:Organization`），MERGE 键 = `(kb_id, name, type)` 属性；索引 `entity_kb_name` 为**普通索引**（非唯一约束），同名不同类型可共存。
- **关系数**：排除 `提及` 边（业务关系数，`COUNT { MATCH (n)-[r]-() WHERE type(r) <> '提及' }`）；**chunk 数**：`size(coalesce(n.mentioned_chunk_ids, []))`。
- **列表排序**：`relation_count DESC, name`（核心实体优先，与画布 degree 排序一致风格）。
- **编辑语义**：name/type 是节点键的一部分，改动后：
  - type 变化：`REMOVE n:旧label` + `SET n:新label` + `SET n.type=新`（label 来自白名单 `_ENTITY_LABELS`，拼接安全，与构建一致）；
  - name 变化：`SET n.name=新`（关系基于图结构引用，自动跟随，无需迁移边）；
  - **冲突检测**：目标键 `(kb_id, new_type, new_name)` 已存在且非自身 → 409 拒绝；
  - description：直接 SET（覆盖先到先得约定——用户手动编辑优先级最高）。

## 3. 改动范围

### 后端

| 文件 | 改动 |
|------|------|
| `models/graph_schemas.py` | 新增 `EntityListItem` / `EntityListResponse` / `EntityUpdateRequest` |
| `services/graph_service.py` | 新增 `list_entities(kb_id, page, page_size, q)`、`update_entity(kb_id, old_name, old_type, name, type, description)` |
| `routers/graph.py` | 新增 `GET /graph/entities/list`（分页+搜索）、`PUT /graph/entities`（更新）；保留 `GET /graph/entities?name=` 详情 |
| `tests/test_graph_service.py` | 新增列表/更新服务测试（mock Neo4jStore） |
| `tests/test_graph_router.py` | 新增 503 / 更新校验 / 冲突 409 用例 |

### 前端

| 文件 | 改动 |
|------|------|
| `src/api/graph.js` | 新增 `listGraphEntities` / `updateGraphEntity` |
| `src/views/GraphView.vue` | 右侧卡片改 el-tabs（实体列表 / 详情）；实体列表：搜索框 + 分页表格（名称/类型/关系数/chunk数/描述 + 编辑/渲染按钮）+ chunk 数点击证据弹窗；编辑弹窗（name/type/description）；渲染=expand + merge + 切详情 |

## 4. 关键伪代码

### 4.1 后端 list_entities

```
list_entities(kb_id, page, page_size, q):
    page = max(1, page); page_size = clamp(1..200); q = (q or "").strip()
    total = execute_read("MATCH (n:Entity) WHERE n.kb_id=$kb AND ($q='' OR toLower(n.name) CONTAINS toLower($q))
                         RETURN count(n) AS c")
    items = execute_read("""
        MATCH (n:Entity) WHERE n.kb_id=$kb AND ($q='' OR toLower(n.name) CONTAINS toLower($q))
        RETURN n.name AS name, n.type AS type, n.description AS description,
               COUNT { MATCH (n)-[r]-() WHERE type(r) <> '提及' } AS relation_count,
               size(coalesce(n.mentioned_chunk_ids, [])) AS chunk_count
        ORDER BY relation_count DESC, n.name SKIP $skip LIMIT $limit""")
    return {total, page, page_size, items: [...]}
```

### 4.2 后端 update_entity

```
update_entity(kb_id, old_name, old_type, new_name, new_type, description):
    if new_type not in _ENTITY_LABELS: raise ValueError("实体类型非法")
    # 1. 定位旧实体
    exists = execute_read("MATCH (n:Entity) WHERE n.kb_id=$kb AND n.name=$old_name AND n.type=$old_type RETURN count(n) AS c")
    if not exists or exists[0].c == 0: return None          # → 404
    # 2. 冲突检测（键变化时）
    if (new_name, new_type) != (old_name, old_type):
        dup = execute_read("MATCH (o:Entity) WHERE o.kb_id=$kb AND o.name=$new_name AND o.type=$new_type
                            AND NOT (o.name=$old_name AND o.type=$old_type) RETURN count(o) AS c")
        if dup[0].c > 0: raise ConflictError                   # → 409
    # 3. 更新（label 白名单拼接）
    clauses = []
    if old_type != new_type: clauses.append(f"REMOVE n:`{old_type}` SET n:`{new_type}`")
    clauses.append("SET n.type = $new_type, n.name = $new_name, n.description = $description")
    execute_write(f"MATCH (n:Entity) WHERE n.kb_id=$kb AND n.name=$old_name AND n.type=$old_type {clauses[0]} {clauses[1]}")
    return {name: new_name, type: new_type, description, ...}
```

### 4.3 前端 GraphView

```
右侧卡片 → <el-tabs v-model="rightTab">
  tab "entity-list" 实体列表：
     搜索框(v-model listQuery, @keyup.enter=loadEntityList)
     <el-table :data="entityList.items">
       名称(点击 → renderEntityToCanvas) / 类型(tag) / 关系数 / chunk数(点击 → showEvidence(name))
       / 描述(tooltip) / 操作(编辑 → editDialog / 渲染 → renderEntityToCanvas)
     <el-pagination v-model:current-page ... @current-change=loadEntityList>

  tab "detail" 详情：现有 detail 内容整体移入

renderEntityToCanvas(name):
    data = await expandGraphNode(kbId, name, 100)
    mergeGraphData(data.nodes, data.edges); expandedNames.push(name)
    renderChart(...); rightTab='detail'; handleEntityClick(name)

showEvidence(name):                      # chunk 数点击 → 证据弹窗
    detail = await getEntityDetail(kbId, name)
    evidenceDialog = { visible: true, evidence: detail.evidence, entity: name }

editEntity(row):
    form = { old_name: row.name, old_type: row.type, name: row.name, type: row.type, description: row.description }
    保存 → updateGraphEntity(kbId, form) → 刷新列表；若画布含 row.name → loadGraph() 重拉画布
```

## 5. 验证方式

1. 后端：新增服务/路由测试（列表分页与搜索、更新改名/改类型/冲突 409/实体不存在 404/类型非法 400）+ 全量 pytest exit 0；
2. 真实环境：`GET /graph/entities/list` 返回分页数据；`PUT /graph/entities` 改描述/改名/改类型后 `GET /graph/entities?name=` 与画布反映新值；
3. 前端 `npm run build` 构建成功。

## 6. 风险与边界

- **列表性能**：`COUNT {}` 子查询对全 kb 实体计算关系数用于排序，千级实体可接受；万级可后续加 Neo4j 属性冗余（本期不做）。
- **改名后证据语义**：实体名与原文一致约束被打破，chunk 证据仍按 chunk_id 展示，仅原文高亮可能不命中（可接受，用户手动行为）。
- **画布刷新**：编辑 name/type 后画布中旧节点失效 → 编辑成功即重拉画布第一页（loadGraph），列表/详情自然一致。
- **label 注入**：type 必须过 `_ENTITY_LABELS` 白名单才可拼接进 Cypher（与构建逻辑一致）。
