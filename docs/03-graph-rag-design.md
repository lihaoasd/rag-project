# Graph RAG 技术方案 — 基于 Neo4j

> 版本: v1.0 | 日期: 2026-08-07 | 状态: 初稿

---

## 1. 方案概述

### 1.1 背景

当前 Vector RAG 方案（文档 → Embedding → Milvus → LLM 回答）擅长精准信息查找，但在以下场景存在局限：

| 序号 | 场景 | Vector RAG 问题 |
|------|------|----------------|
| 1 | 跨文档汇总 | "2024年总共中标了多少个项目？"回答不完整 |
| 2 | 实体关系查询 | "甲公司和哪些供应商有合作关系？"无法遍历图 |
| 3 | 全局趋势分析 | "近三年中标金额变化趋势？"碎片化信息无法总结 |

引入 Graph RAG 作为补充通道，解决上述全局性、关联性问题。

### 1.2 目标

1. 从投标文档中自动抽取实体（公司、项目、金额、资质等）和关系（中标、合作、分包等）
2. 基于 Neo4j 构建投标知识图谱
3. 实现图谱检索与向量检索的双通道 RAG
4. 通过 Neo4j Browser 提供图谱可视化

### 1.3 与现有方案的关系

```
                        ┌─────────────┐
                        │   用户提问    │
                        └──────┬──────┘
                               │
                        ┌──────▼──────┐
                        │  问题路由器   │
                        └──┬───────┬──┘
                           │       │
             精准查询      │       │      全局/关联查询
                           │       │
              ┌────────────▼──┐  ┌──▼────────────┐
              │  Vector RAG   │  │   Graph RAG   │
              │  (现有方案)    │  │  (新增方案)    │
              └───────┬───────┘  └──────┬────────┘
                      │                │
                  Milvus            Neo4j
                  向量检索          图谱检索
                      │                │
                      └───────┬────────┘
                              │
                     ┌────────▼────────┐
                     │     LLM 回答     │
                     └─────────────────┘
```

---

## 2. 整体架构

### 2.1 系统架构图

```
┌─────────────────────────────────────────────────────────────┐
│                      后端 (FastAPI)                          │
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
│  │ 文档管理  │  │ 知识库管理│  │ 文档检索  │  │  问答服务  │  │
│  │  Router  │  │  Router  │  │  Router  │  │  Router   │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └─────┬─────┘  │
│       │              │             │              │          │
│  ┌────┴──────────────┴─────────────┴──────────────┴─────┐  │
│  │                    Service Layer                     │  │
│  │  ┌────────┐ ┌──────┐ ┌────────┐ ┌──────┐ ┌───────┐  │  │
│  │  │Document│ │  KB  │ │ Search │ │ Chat │ │ Graph │  │  │
│  │  │Service │ │Service│ │Service │ │Service│ │Service│  │  │
│  │  └───┬────┘ └──────┘ └───┬────┘ └──┬───┘ └───┬───┘  │  │
│  └──────┼────────────────────┼─────────┼──────────┼─────┘  │
│         │                    │         │          │         │
│  ┌──────┴────────────────────┴─────────┴──────────┴─────┐  │
│  │                     Core Layer                       │  │
│  │  ┌────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │  │
│  │  │ Parser │ │ Indexing │ │Retrieval │ │  Graph   │  │  │
│  │  │ Engine │ │ Engine   │ │ Engine   │ │ Engine   │  │  │
│  │  │        │ │(LlamaIdx)│ │(LlamaIdx)│ │(LlamaIdx)│  │  │
│  │  └────────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘  │  │
│  └──────────────────┼─────────────┼─────────────┼───────┘  │
└─────────────────────┼─────────────┼─────────────┼──────────┘
                      │             │             │
          ┌───────────┴──┐  ┌───────┴──────┐  ┌──┴──────────┐
          │    Milvus    │  │  Xinference  │  │    Neo4j    │
          │   (Docker)   │  │   (本地)     │  │   (Docker)  │
          │   :19530     │  │   :9997     │  │   :7687     │
          └──────────────┘  └──────────────┘  │   :7474     │
                                              └─────────────┘
```

### 2.2 新增依赖

| 序号 | 依赖 | 用途 | 部署方式 |
|------|------|------|----------|
| 1 | Neo4j 5.x | 图存储与查询 | Docker |
| 2 | `llama-index-graph-stores-neo4j` | LlamaIndex Neo4j 集成 | uv |
| 3 | `neo4j` (Python Driver) | 底层 Cypher 查询 | uv |

### 2.3 新增模块

```
backend/
├── core/
│   ├── graph_extractor.py     # 实体/关系抽取 (新增)
│   ├── graph_indexer.py       # 图谱构建引擎 (新增)
│   ├── graph_retriever.py     # 图谱检索引擎 (新增)
│   └── query_router.py        # 问题路由分发 (新增)
├── services/
│   └── graph_service.py       # 图谱业务服务 (新增)
├── routers/
│   └── graph.py               # 图谱可视化/查询 API (新增)
└── models/
    └── graph_schemas.py       # 图数据 Pydantic Schema (新增)
```

---

## 3. Neo4j 部署与配置

### 3.1 Docker Compose

在现有 `docker-compose.yml` 中追加 Neo4j 容器（与 Milvus 同级）：

```yaml
  neo4j:
    image: neo4j:5.21.0
    container_name: bid_neo4j
    ports:
      - "7474:7474"   # HTTP / Neo4j Browser
      - "7687:7687"   # Bolt 协议（应用连接）
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD:-password123}
      NEO4J_PLUGINS: '["apoc"]'                    # APOC 扩展库
      NEO4J_server_memory_heap_initial__size: 512m  # 初始堆内存
      NEO4J_server_memory_heap_max__size: 1g        # 最大堆内存
      NEO4J_server_memory_pagecache_size: 512m       # 页面缓存
    volumes:
      - ./volumes/neo4j/data:/data
      - ./volumes/neo4j/logs:/logs
      - ./volumes/neo4j/import:/var/lib/neo4j/import
    healthcheck:
      test: ["CMD", "cypher-shell", "-u", "neo4j", "-p", "${NEO4J_PASSWORD:-password123}", "RETURN 1"]
      interval: 10s
      timeout: 5s
      retries: 5
```

### 3.2 配置项（环境变量）

```bash
# === Neo4j 配置 ===
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=password123
NEO4J_DATABASE=neo4j
```

### 3.3 LlamaIndex 连接初始化

```python
# backend/core/graph_indexer.py

from llama_index.graph_stores.neo4j import Neo4jPropertyGraphStore
from llama_index.core import PropertyGraphIndex
import os

def create_graph_store(kb_id: str) -> Neo4jPropertyGraphStore:
    """为指定知识库创建 Neo4j 图存储连接"""
    return Neo4jPropertyGraphStore(
        username=os.getenv("NEO4J_USERNAME", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "password123"),
        url=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        database=os.getenv("NEO4J_DATABASE", "neo4j"),
        # 通过节点标签区分知识库（而非创建多个 Database）
        # 例如：所有属于 kb_001 的节点打上 :KB_001 标签
    )
```

---

## 4. 图谱 Schema 设计

### 4.1 实体类型（Entity / Node）

| 序号 | 实体类型 | 标签 | 属性 | 说明 |
|------|----------|------|------|------|
| 1 | 组织 | `Organization` | name, description, kb_id | 公司、部门、机构 |
| 2 | 项目 | `Project` | name, description, project_no, kb_id | 投标项目 |
| 3 | 人员 | `Person` | name, title, kb_id | 项目负责人、法人等 |
| 4 | 金额 | `Money` | amount, currency, description, kb_id | 投标金额、保证金等 |
| 5 | 日期 | `Date` | date_value, description, kb_id | 开标日期、签约日期等 |
| 6 | 地点 | `Location` | name, address, kb_id | 项目地点、公司地址 |
| 7 | 资质 | `Qualification` | name, level, description, kb_id | 资质要求、资质证书 |
| 8 | 文档 | `Document` | file_name, page_number, chunk_text, doc_id, kb_id | 原文证据片段 |
| 9 | 事件 | `Event` | name, description, kb_id | 开标、签约、竣工等 |

### 4.2 关系类型（Relationship / Edge）

| 序号 | 关系 | 方向 | 说明 | 示例 |
|------|------|------|------|------|
| 1 | `中标` | Organization → Project | 公司中标某项目 | 甲公司 -[中标]→ XX 项目 |
| 2 | `参与投标` | Organization → Project | 公司参与投标（未中标） | 乙公司 -[参与投标]→ XX 项目 |
| 3 | `招标` | Organization → Project | 招标方发布项目 | 市交通局 -[招标]→ XX 项目 |
| 4 | `合作` | Organization → Organization | 公司间合作关系 | 甲公司 -[合作]→ 丙公司 |
| 5 | `分包` | Organization → Organization | 分包关系 | 甲公司 -[分包]→ 乙公司 |
| 6 | `合同金额` | Project → Money | 项目中标金额 | XX 项目 -[合同金额]→ 3000万 |
| 7 | `缴纳保证金` | Organization → Money | 保证金缴纳 | 甲公司 -[缴纳保证金]→ 50万 |
| 8 | `要求资质` | Project → Qualification | 项目资质要求 | XX 项目 -[要求资质]→ 建筑一级 |
| 9 | `拥有资质` | Organization → Qualification | 公司拥有资质 | 甲公司 -[拥有资质]→ 建筑一级 |
| 10 | `位于` | Project → Location | 项目地点 | XX 项目 -[位于]→ 北京市 |
| 11 | `发生在` | Event → Date | 事件日期 | 开标 -[发生在]→ 2024-03-01 |
| 12 | `提及` | Document → Entity | 文档片段提到某实体 | chunk_03 -[提及]→ 甲公司 |
| 13 | `包含` | Project → Event | 项目包含的事件 | XX 项目 -[包含]→ 开标 |
| 14 | `负责` | Person → Project | 人员负责项目 | 张三 -[负责]→ XX 项目 |
| 15 | `隶属于` | Person → Organization | 人员隶属组织 | 张三 -[隶属于]→ 甲公司 |

### 4.3 图谱 Schema 可视化

```
                        ┌──────────────┐
                        │  Document    │
                        │  (原文片段)   │
                        └──┬───┬───┬──┘
                     ┌─提及  │   │  提及─┐
                     ▼       │   ▼       ▼
               ┌─────────┐   │  ┌──────────────┐
               │ Person  │   │  │ Qualification│
               └────┬────┘   │  └──────┬───────┘
                    │隶属     │    拥有资质 │要求资质
                    ▼        │         │   │
┌───────┐ 中标  ┌────────┐  │  ┌──────▼───┴──┐
│Project│◄──────│Organization│  │   Project   │
└──┬────┘       └┬───┬────┘  │  └──┬──┬──┬───┘
   │合同金额      │   │合作    │     │  │  │包含
   ▼             │   ▼       │     │  │  ▼
┌───────┐        │ ┌───────┐ │     │  │ ┌───────┐
│ Money │        │ │Organization│  │  │ Event  │
└───────┘        │ └───────┘ │     │  │ └───┬───┘
   ▲             │            │     │  │     │发生在
   │缴纳保证金    │            │     │  │     ▼
   └─────────────┘            │     │  │ ┌───────┐
                              │     │  │ │ Date  │
                              │     │  │ └───────┘
                              │     │  │
                         ┌────┘     │  └──────────┐
                         ▼          ▼             ▼
                    ┌────────┐ ┌────────┐  ┌──────────┐
                    │ Location│ │ Money  │  │ Location │
                    └────────┘ └────────┘  └──────────┘
```

---

## 5. 核心流程

### 5.1 图谱构建流程

```
文档上传
   │
   ▼
┌─────────────┐
│ 1. 文档解析  │  已有流程（parser.py）
│ PDF/Word    │  → LlamaIndex Documents
└──────┬──────┘
       │
       ├──────────────────────────────┐
       ▼                              ▼
┌─────────────┐              ┌─────────────────┐
│ 2. 向量索引  │              │ 3. 图谱构建     │
│ (已有流程)   │              │                 │
│ Chunk →     │              │ 3.1 文本分块    │
│ Embedding → │              │ 每个chunk独立   │
│ Milvus      │              │ 抽取实体/关系   │
└──────┬──────┘              │                 │
       │                     │ 3.2 LLM抽取     │
       │                     │ SchemaLLMPath   │
       │                     │ Extractor      │
       │                     │                 │
       │                     │ 3.3 实体去重    │
       │                     │ 同名/同义合并   │
       │                     │                 │
       │                     │ 3.4 写入 Neo4j  │
       │                     │ PropertyGraphStore│
       │                     └────────┬────────┘
       │                              │
       ▼                              ▼
  ┌──────────────────────────────────────┐
  │         4. 索引状态更新               │
  │  vector_ready + graph_ready          │
  └──────────────────────────────────────┘
```

### 5.2 检索流程

```
用户提问
   │
   ▼
┌─────────────────┐
│ 1. 问题路由      │  LLM 判断问题类型
│ factual /       │
│ aggregate /     │
│ relational      │
└───┬───────┬─────┘
    │       │
    ▼       ▼  (aggregate / relational)
┌─────────┐ ┌─────────────┐
│Vector RAG│ │ Graph RAG   │
└─────────┘ │             │
            │ 2. 实体检索  │  Embedding 匹配相关实体
            │ 3. 邻居展开  │  1-hop 获取关联实体和关系
            │ 4. 路径查询  │  Cypher 执行多跳关系查询
            │ 5. 证据收集  │  关联 TextUnit 原始文本
            └──────┬──────┘
                   │
    ┌──────────────┴──────────────┐
    ▼                             ▼
┌─────────────────────────────────────┐
│ 6. 上下文组装 + Prompt 构建          │
│ 7. LLM 回答                         │
└─────────────────────────────────────┘
```

### 5.3 问题路由决策

| 序号 | 问题类型 | 示例 | 路由 |
|------|----------|------|------|
| 1 | factual | "XX项目的投标保证金是多少？" | → Vector RAG |
| 2 | factual | "YY招标需要什么资质？" | → Vector RAG |
| 3 | aggregate | "2024年共中标了多少个项目？总金额？" | → Graph RAG |
| 4 | aggregate | "哪些类型项目的中标率最高？" | → Graph RAG |
| 5 | relational | "甲公司和哪些公司有合作？" | → Graph RAG |
| 6 | relational | "某项目涉及的所有供应商有哪些？" | → Graph RAG |
| 7 | mixed | "甲公司中标的项目中，金额最大的是哪个？" | → 混合检索 |

---

## 6. 实体抽取设计

### 6.1 抽取策略

采用 **Schema 约束抽取**，预先定义实体和关系类型，LLM 在约束范围内抽取：

```python
from llama_index.core.indices.property_graph import SchemaLLMPathExtractor

extractor = SchemaLLMPathExtractor(
    llm=llm,
    possible_entities=[
        "ORGANIZATION",      # 公司、部门
        "PROJECT",           # 投标项目
        "PERSON",            # 人员
        "MONEY",             # 金额
        "DATE",              # 日期
        "LOCATION",          # 地点
        "QUALIFICATION",     # 资质
        "DOCUMENT",          # 文件
        "EVENT",             # 事件
    ],
    possible_relations=[
        "中标",
        "参与投标",
        "招标",
        "合作",
        "分包",
        "合同金额",
        "缴纳保证金",
        "要求资质",
        "拥有资质",
        "位于",
        "发生在",
        "负责",
        "隶属于",
    ],
    kg_schema_cls=YourCustomSchema,  # Pydantic Model 约束
    max_triplets_per_chunk=15,
    num_workers=4,
    strict=False,  # False = LLM 可补充 Schema 外的类型
)
```

### 6.2 抽取 Prompt 模板

```
你是一个投标文档信息抽取系统。

从以下文本中提取实体和关系。实体必须属于以下类型之一：
- ORGANIZATION: 公司、单位、政府部门
- PROJECT: 招标项目、工程项目
- PERSON: 人名
- MONEY: 金额（需标注币种）
- DATE: 日期
- LOCATION: 地点
- QUALIFICATION: 资质、证书
- EVENT: 事件（开标、签约、竣工等）

关系必须属于以下类型之一：
- (ORGANIZATION)-[中标]->(PROJECT)
- (ORGANIZATION)-[参与投标]->(PROJECT)
- (ORGANIZATION)-[招标]->(PROJECT)
- (ORGANIZATION)-[合作]->(ORGANIZATION)
- (ORGANIZATION)-[分包]->(ORGANIZATION)
- (PROJECT)-[合同金额]->(MONEY)
- (ORGANIZATION)-[缴纳保证金]->(MONEY)
- (PROJECT)-[要求资质]->(QUALIFICATION)
- (ORGANIZATION)-[拥有资质]->(QUALIFICATION)
- (PROJECT)-[位于]->(LOCATION)
- (EVENT)-[发生在]->(DATE)
- (PERSON)-[负责]->(PROJECT)
- (PERSON)-[隶属于]->(ORGANIZATION)

【文本内容】
{chunk_text}

【输出格式】
{
  "entities": [
    {"name": "实体名称", "type": "ENTITY_TYPE", "description": "简短描述"}
  ],
  "relationships": [
    {"source": "源实体名", "target": "目标实体名", "type": "RELATION_TYPE", "description": "关系说明"}
  ]
}

只抽取文本中明确提及的信息，不要编造。
```

### 6.3 批量抽取优化

为降低 LLM 调用成本，采用批量抽取策略：

| 策略 | 说明 | 效果 |
|------|------|------|
| 批量 chunk | 每 3~5 个 chunk 合并为一次 LLM 调用 | 调用量减少 60%~80% |
| 小 chunk 跳过 | text 长度 < 20 字符的 chunk 不抽取 | 减少无效调用 |
| 缓存实体 | 同名实体复用 description，不重复抽取 | 减少 token 消耗 |
| 异步并行 | asyncio 并发调用 LLM | 大幅提升整体速度 |

---

## 7. 图谱检索设计

### 7.1 实体语义检索

通过向量相似度找到与问题最相关的实体，作为图谱遍历的入口点：

```python
async def search_entities(self, query: str, top_k: int = 5):
    """
    1. 问题 → Embedding
    2. 在实体向量索引中检索（实体名称 + 描述的 Embedding）
    3. 返回最相关实体列表
    """
    query_embedding = await self.embed_model.aget_text_embedding(query)

    # 使用 Milvus 存储实体 Embedding（or 直接用 Neo4j 的向量索引）
    results = entity_vector_store.search(query_embedding, top_k=top_k)

    return results  # [{name, type, score, node_id}, ...]
```

### 7.2 邻居展开（1-hop / 2-hop）

```python
async def expand_neighbors(self, entity_ids: list, hop: int = 1):
    """
    从入口实体出发，展开 1-hop 或 2-hop 邻居，
    获取关联的所有实体和关系。
    """
    cypher = f"""
    MATCH (n)-[r*1..{hop}]-(m)
    WHERE elementId(n) IN $entity_ids
    RETURN n, r, m
    LIMIT 200
    """
    return await self.graph_store.execute_query(cypher, {"entity_ids": entity_ids})
```

### 7.3 精确关系查询

```python
async def query_relation(self, entity: str, relation: str, direction: str = "out"):
    """
    精确查询某实体的特定关系，例如：
    "甲公司 中标了哪些项目？"
    "XX项目 要求哪些资质？"
    """
    if direction == "out":
        cypher = f"""
        MATCH (s {{name: $entity}})-[r:{relation}]->(t)
        RETURN s.name AS source, type(r) AS relation,
               r.description AS description, t.name AS target
        """
    else:
        cypher = f"""
        MATCH (s)-[r:{relation}]->(t {{name: $entity}})
        RETURN s.name AS source, type(r) AS relation,
               r.description AS description, t.name AS target
        """
    return await self.graph_store.execute_query(cypher, {"entity": entity})
```

### 7.4 统计聚合查询

```python
async def aggregate_stats(self, kb_id: str):
    """
    全局统计，例如：
    "总共多少个项目？总中标金额？"
    """
    cypher = """
    MATCH (org:Organization)-[r:中标]->(p:Project)
    WHERE p.kb_id = $kb_id
    RETURN count(DISTINCT p) AS total_projects,
           count(DISTINCT org) AS total_companies
    """
    cypher_amount = """
    MATCH (p:Project)-[r:合同金额]->(m:Money)
    WHERE p.kb_id = $kb_id
    RETURN sum(toFloat(m.amount)) AS total_amount
    """
    projects = await self.graph_store.execute_query(cypher, {"kb_id": kb_id})
    amounts = await self.graph_store.execute_query(cypher_amount, {"kb_id": kb_id})
    return {"projects": projects, "amounts": amounts}
```

### 7.5 上下文组装

检索到图谱结果后，组装给 LLM 的上下文：

```python
def build_graph_context(entities, relations, text_units) -> str:
    """将图谱检索结果格式化为 LLM 可读的上下文"""
    context_parts = []

    # 1. 实体信息
    context_parts.append("【相关实体】")
    for e in entities:
        context_parts.append(
            f"- {e.type}: {e.name}（{e.description}）"
        )

    # 2. 关系信息
    context_parts.append("\n【实体间关系】")
    for r in relations:
        context_parts.append(
            f"- {r.source} --[{r.type}]--> {r.target}：{r.description}"
        )

    # 3. 原文证据
    context_parts.append("\n【原文片段】")
    for t in text_units:
        context_parts.append(
            f"- [{t.file_name} 第{t.page_number}页] {t.text}"
        )

    return "\n".join(context_parts)
```

---

## 8. API 设计

### 8.1 图谱可视化 API

| 序号 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 1 | GET | `/api/knowledge-bases/{kb_id}/graph/summary` | 图谱概要统计 |
| 2 | GET | `/api/knowledge-bases/{kb_id}/graph/entity/{name}` | 查询实体详情及邻居 |
| 3 | GET | `/api/knowledge-bases/{kb_id}/graph/search` | 图谱检索（实体+关系） |

#### 8.1.1 图谱概要响应

```json
{
  "kb_id": "kb_a1b2c3d4",
  "stats": {
    "total_entities": 1250,
    "total_relations": 3420,
    "entity_types": {
      "Organization": 85,
      "Project": 230,
      "Person": 156,
      "Money": 320,
      "Qualification": 89,
      "Location": 120,
      "Date": 180,
      "Event": 70
    },
    "relation_types": {
      "中标": 95,
      "参与投标": 180,
      "合同金额": 230,
      "合作": 45,
      "要求资质": 120
    }
  }
}
```

#### 8.1.2 实体查询响应

```json
{
  "entity": {
    "name": "甲公司",
    "type": "Organization",
    "description": "建筑施工企业，主承建商",
    "kb_id": "kb_a1b2c3d4",
    "degree": 28
  },
  "neighbors": [
    {"target": "XX项目", "relation": "中标", "direction": "out"},
    {"target": "YY项目", "relation": "中标", "direction": "out"},
    {"target": "乙公司", "relation": "合作", "direction": "out"},
    {"target": "丙公司", "relation": "分包", "direction": "in"}
  ],
  "documents": [
    {"file_name": "XX招标文件.pdf", "page": 5, "text": "中标单位：甲公司..."}
  ]
}
```

#### 8.1.3 图谱检索请求

```json
{
  "query": "甲公司中标了哪些桥梁项目",
  "search_type": "entity",
  "max_hops": 2,
  "limit": 50
}
```

### 8.2 问答 API — 扩展

在现有问答 API 基础上增加路由字段：

```json
// 请求体 不变
{
  "message": "甲公司中标了哪些项目？",
  "session_id": "uuid-optional"
}

// SSE 响应 增加路由信息
data: {"type": "route", "method": "graph"}
data: {"type": "token", "content": "根据知识图谱查询..."}
data: {"type": "sources", ...}
data: {"type": "done"}
```

---

## 9. 前端页面设计

### 9.1 图谱可视化页

在现有前端 `SearchView.vue` 基础上，新增 `GraphView.vue`：

| 序号 | 功能模块 | 说明 |
|------|----------|------|
| 1 | 图谱统计概览 | 实体数、关系数、类型分布 |
| 2 | 实体关系搜索 | 输入实体名，查看 1-hop / 2-hop 邻居图 |
| 3 | 图谱画布 | 使用 ECharts / vis.js 渲染力导向图 |
| 4 | 点击展开 | 点击图谱节点展开更多邻居 |
| 5 | 详情面板 | 侧边栏展示选中实体的属性和来源文档 |
| 6 | 跳转问答 | "基于该实体提问"按钮，跳转到问答页 |

### 9.2 可视化技术选型

| 序号 | 库 | 说明 |
|------|-----|------|
| 1 | **Neo4j Browser**（推荐） | 直接内嵌 iframe 到系统页面中，零开发成本 |
| 2 | ECharts (echarts-graph) | 轻量，与 Element Plus 风格统一 |
| 3 | vis.js Network | 专业图可视化，交互体验好 |

---

## 10. 分阶段实施计划

| 阶段 | 内容 | 工期 | 产出 |
|------|------|------|------|
| **Phase 1** | Neo4j Docker 部署 + LlamaIndex 集成配置 | 0.5 天 | Neo4j 可用，连接验证通过 |
| **Phase 2** | 实体抽取器开发（Schema 定义 + Prompt 调优） | 2 天 | 从文档中抽取实体关系 |
| **Phase 3** | 图谱索引构建（抽取 → 去重 → 写入 Neo4j） | 1.5 天 | 文档上传后自动构建图谱 |
| **Phase 4** | 图谱检索（实体搜索、邻居展开、Cypher 查询） | 2 天 | GraphRetriever 可用 |
| **Phase 5** | 问题路由器（LLM 路由 + 双通道整合） | 1 天 | 自动选择 Vector / Graph |
| **Phase 6** | API 扩展 + 前端 GraphView.vue | 2 天 | 图谱可视化和搜索页面可用 |
| **Phase 7** | 批量抽取优化 + 增量更新 + 测试 | 2 天 | 性能和稳定性达标 |
| **合计** | | **11 天** | |

---

## 11. 风险与注意事项

| 序号 | 风险 | 影响 | 应对措施 |
|------|------|------|----------|
| 1 | LLM 抽取质量不稳定 | 图谱不完整或错误 | Schema 约束 + 多次抽取交叉验证 |
| 2 | 抽取成本高 | 每个 chunk 调一次 LLM | 批量抽取 + 小模型（DeepSeek-V3）+ 缓存 |
| 3 | 实体去重不准确 | 同一实体的多个别名被当作不同节点 | 名称归一化 + Embedding 相似度合并 |
| 4 | Neo4j 内存不足 | 百万级节点时性能下降 | 合理设置堆内存，及时清理无用数据 |
| 5 | 图谱检索响应慢 | 多跳查询耗时 | 限制最大跳跃数（≤2）+ Cypher 查询优化 + 索引 |
| 6 | 问题路由不准 | 简单问题走了 Graph RAG | 路由回退策略：路由不确定时默认走 Vector RAG |
