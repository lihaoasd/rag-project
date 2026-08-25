# 知识图谱模块开发方案

> 关联设计: [09-graph-module-design.md](../09-graph-module-design.md)（实现按 **v1.1 评审决策**执行：chunk 归属/词库缓存扩展/联动清除/去 APOC/分批事务等） | 日期: 2026-02-14 | 状态: 已确认

## 1. 改动范围

### 新增文件（后端）

| 文件 | 内容 |
|------|------|
| `backend/core/neo4j_store.py` | Neo4j driver 单例、连接/健康检查/索引、Cypher 执行封装（asyncio.to_thread） |
| `backend/core/graph_queue.py` | 图谱任务队列（build_file/build_kb/clear_file，独立单 worker） |
| `backend/core/graph_extractor.py` | 抽取管线：词库/正则匹配 + LLM 批量补充 + 合并去重 |
| `backend/services/graph_service.py` | 构建/清除/查询编排、graph_status 落库、证据查询 |
| `backend/routers/graph.py` | 图谱管理 + 图检索 API |
| `backend/models/graph_schemas.py` | LLM 抽取输出校验 + 图检索响应 Pydantic |
| `backend/tests/test_graph_*.py` | 队列/抽取/服务/接口测试（Neo4j 用 mock） |

### 修改文件（后端）

| 文件 | 改动 |
|------|------|
| `backend/pyproject.toml` | `neo4j>=5.18.0` 移入主依赖 |
| `backend/config.py` | 新增 Neo4j 图谱配置段（graph_enabled/neo4j_* /graph_extraction_batch 等） |
| `backend/.env.example` + `.env` | 新增 NEO4J_* 配置项 |
| `backend/models/document.py` | Document 增加 graph_status/graph_error_message/graph_built_at/graph_duration_ms |
| `backend/models/schemas.py` | DocumentResponse 补 graph 字段；新增图检索请求/响应 Schema |
| `backend/repositories/document_repository.py` | update_graph_status、list_chunks_by_doc_id 无分页版（构建用）、按 id 列表查 chunk 证据 |
| `backend/services/lexicon_service.py` | 新增 `match_entities(text)` 公共函数 + 分类枚举常量 |
| `backend/main.py` | 注册 graph router、启动/停止 graph_queue、Neo4j 索引初始化、graph_enabled 日志 |
| `backend/alembic/versions/0014_graph_status.py` | documents 加 4 列 |
| `docker-compose.yml` | 新增 neo4j 服务 |

### 修改文件（前端）

| 文件 | 改动 |
|------|------|
| `frontend/src/api/graph.js`（新增） | stats/graph/build/clear/search/entities 封装 |
| `frontend/src/views/GraphView.vue`（新增） | ECharts 力导向图 + 统计 + 构建/清除 + 证据面板 |
| `frontend/src/router/index.js` | 新增 `/knowledge-bases/:kbId/graph` 路由 |
| `frontend/src/components/AppLayout.vue` | 菜单（如已有入口按需） |
| `frontend/src/views/DocumentListView.vue` | 文档行加图谱状态列 + 构建/清除按钮 |
| `frontend/src/views/LexiconView.vue` | 分类改为固定枚举下拉 |
| `frontend/src/api/lexicon.js` | 分类枚举常量导出（可选） |

## 2. 实现步骤（伪代码）

### 2.1 core/neo4j_store.py

```
class Neo4jStore:
    _driver = None
    def _get_driver():  # 懒加载单例，neo4j.GraphDatabase.driver(uri, auth)
    async def execute_read(cypher, params) -> list[dict]   # asyncio.to_thread(driver.execute_query...)
    async def execute_write(cypher, params) -> list[dict]
    async def health_check() -> bool
    async def ensure_indexes()   # CREATE INDEX IF NOT EXISTS FOR (n:Entity) ON (n.kb_id, n.name)
    async def close()
```

### 2.2 core/graph_queue.py（复刻 parse_queue 模式）

```
_queue: asyncio.Queue  # ("build_file", kb_id, doc_id) | ("build_kb", kb_id) | ("clear_file", kb_id, doc_id)
enqueue / enqueue_build_kb / enqueue_clear_file / queue_size / start_worker / stop_worker
worker: 出队 → graph_service.build_file_graph / build_kb_graph / clear_file_graph
```

### 2.3 models/document.py + 迁移 0014

```
Document += graph_status(str, default 'none') / graph_error_message(str|None)
            graph_built_at(datetime|None) / graph_duration_ms(int|None)
0014: op.add_column × 4（带 server_default 'none'）
```

### 2.4 services/lexicon_service.py 新增

```
LEXICON_CATEGORY_ENTITY_TYPES = {organization→Organization, project→Project, person→Person,
                                 location→Location, qualification→Qualification}
def normalize_category(cat) -> str  # 旧中文值映射到枚举（公司→organization 等），未知→other

def match_entities(text) -> list[dict]:
    # 用 _cache（标准词→别名），最长匹配；命中别名归一到标准词
    # 返回 [{name: 标准词, type: 实体类型}]；category=other/空 跳过
    # 正则第二层：金额/日期（简单模式，供 MONEY/DATE 类型）
```

### 2.5 core/graph_extractor.py

```
async def extract_from_chunks(kb_id, chunks: list[{id, text, page}]) -> ExtractionResult:
    # entities: {name: {type, chunk_ids: []}}   relations: [{a, type, b, chunk_ids}]
    # 1) 逐 chunk 词库匹配 → 候选实体 + chunk_id
    # 2) 批量 chunk（graph_extraction_batch 个一批）→ LLM prompt（Schema 约束 JSON）
    #    → Pydantic 校验（graph_schemas.ExtractionOutput）→ 实体/关系 + 归属 batch 的 chunk_ids
    # 3) 合并去重：归一化（strip/全半角），同 (name,type) 合并 chunk_ids
    # 4) 返回 entities 列表 + relations 列表（relation 附 source_doc_ids 用 doc_id）
```

### 2.6 services/graph_service.py

```
async def build_file_graph(kb_id, doc_id):
    # 校验文档 completed → graph_status='building'
    # PG 取该文件 chunks(id, text, page) → 过滤短 chunk
    # clear_file_contributions(kb_id, doc_id)   # 先清旧（复用 §4.3 Cypher，含 PG chunk_ids 查询）
    # extract_from_chunks → MERGE 写入（单事务 execute_write）：
    #   实体节点 mentioned_chunk_ids 合并；关系 source_doc_ids；Document 锚点 + 提及
    # graph_status='completed', graph_built_at, graph_duration_ms

async def build_kb_graph(kb_id):
    # 取 completed 文档列表 → DETACH DELETE 整个 kb 图谱 → 逐文件 build_file_graph（不中断）
    # 每个文件异常 → 该文件 graph_status='failed' 继续下一个

async def clear_file_graph(kb_id, doc_id):
    # PG 查该文件 chunk_ids → Neo4j 三步 Cypher（§4.3）→ graph_status='none'

async def clear_kb_graph(kb_id):  # DETACH DELETE + 全部文档 graph_status='none'

async def get_graph_stats(kb_id):      # Neo4j 统计 + PG 构建进度
async def get_graph_data(kb_id, limit):# 子图数据（ECharts）
async def search_subgraph(kb_id, query, max_hops, limit):
    # 词库匹配 query → Neo4j 子图 → 收集 mentioned_chunk_ids → PG 查 chunk 证据（§6.2）
async def get_entity_detail(kb_id, name):  # 实体 + 邻居 + 证据
```

### 2.7 routers/graph.py

```
GET    /api/knowledge-bases/{kb_id}/graph/stats
GET    /api/knowledge-bases/{kb_id}/graph
POST   /api/knowledge-bases/{kb_id}/graph/build        → enqueue build_kb
DELETE /api/knowledge-bases/{kb_id}/graph              → clear_kb_graph
POST   /api/knowledge-bases/{kb_id}/documents/{doc_id}/graph/build  → enqueue build_file
DELETE /api/knowledge-bases/{kb_id}/documents/{doc_id}/graph        → clear_file_graph
GET    /api/knowledge-bases/{kb_id}/documents/{doc_id}/graph/status → graph_status
POST   /api/knowledge-bases/{kb_id}/graph/search       → search_subgraph
GET    /api/knowledge-bases/{kb_id}/graph/entities/{name} → get_entity_detail
# graph_enabled=False 时统一 503（HTTPException, detail 提示）
```

### 2.8 前端

```
api/graph.js: 封装上述接口（axios 同 request.js 模式）
GraphView.vue:
  - 统计卡片（stats）+ 操作栏（全量构建/清空/搜索框）
  - ECharts 力导向图（graph 数据 → categories 按 type 着色；点击节点展开 → search）
  - 详情面板：选中实体 → entities/{name} → 属性/邻居/证据（chunk 文本高亮 + 跳转 chunk 页）
  - 文档 Tab：文档列表（graph_status + 构建/清除按钮）→ 轮询刷新
DocumentListView.vue: 行内加图谱状态 tag + 构建/清除按钮（调 graph.js）
LexiconView.vue: category 改为 el-select 枚举（organization/project/person/location/qualification/other）
```

## 3. 验证方式

| 步骤 | 验证 |
|------|------|
| alembic upgrade head | 迁移成功，documents 出现 4 个 graph 列 |
| pytest backend/tests/test_graph_* | 队列/抽取/服务/接口单测全绿（Neo4j mock、LLM mock） |
| 启动后端（graph_enabled=False 时） | 图谱接口返回 503 友好提示，不影响其他功能 |
| 部署机（docker compose 含 neo4j） | graph_enabled=True；health 含 neo4j ok；手工验收构建/查看/清除 |
| 前端 npm run build | GraphView/文档列表/词库下拉编译通过 |

## 4. 顺序与依赖

P0（部署配置+neo4j_store）→ P1（迁移+ORM+队列）→ P2（抽取）→ P3（写入/清除）→ P4（构建管理 API）→ P5（图检索）→ P6（前端）→ P7（测试+修改记录）。每步可在前序完成后独立验证。
