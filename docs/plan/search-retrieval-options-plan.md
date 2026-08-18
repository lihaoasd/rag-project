# 检索页检索选项配置项

> 版本: v1.0 | 日期: 2026-08-18 | 状态: 待评审

## 需求

检索页面新增“检索方式”配置项。当前产品边界只开放**向量检索**，后续会按需补充关键词检索、知识图谱检索、query 改写、RRF、权重评分等选项。

本次实现可扩展的检索方式参数和页面入口，开放 `vector` 与后端已有的 `hybrid`（向量 + BM25 混合检索）。

## 决策

| 项 | 选择 | 理由 |
|----|------|------|
| API 字段 | `retrieval_method`（`vector` / `hybrid`）、`hybrid_ranker`（`rrf` / `weighted`）、`hybrid_weights` | 为后续方法保留稳定入口 |
| 缺省行为 | 不传时沿用现有服务端检索配置 | 避免破坏现有调用方和混合检索配置 |
| 页面行为 | hybrid 下提供 `RRFRanker` / `WeightedRanker` 选择；权重模式调整“稠密稀疏项目权重”（0~1），稀疏权重自动为 `1 - 稠密权重` | 与 Milvus ranker 能力对齐 |
| 引擎接入 | `core/retrieval.retrieve()` 解析该参数并选择向量查询模式 | 参数不是只做展示，后续新方法在同一切口扩展 |
| 数据库 | 无迁移 | 检索参数是请求级配置，不落库 |

## 改动范围

| 文件 | 改动 |
|------|------|
| `backend/models/schemas.py` | `SearchRequest` 新增 `retrieval_method` |
| `backend/core/retrieval.py` | `retrieve()` 接收并解析 `retrieval_method` |
| `backend/services/search_service.py` | `search()` 透传 `retrieval_method` |
| `backend/routers/search.py` | 两个检索端点透传 `retrieval_method` |
| `frontend/src/api/search.js` | 请求体携带 `retrieval_method` |
| `frontend/src/views/SearchView.vue` | 新增“检索方式”下拉配置 |
| `backend/tests/*` | 补充 schema、service、router 测试 |
| `docs/changes.md` | 追加修改记录 |

## 实现步骤

1. 在 `SearchRequest` 增加可选字段：
   ```python
   retrieval_method: Literal["vector", "hybrid"] | None = None
   hybrid_ranker: Literal["rrf", "weighted"] | None = None
   hybrid_weights: list[float] | None = None
   ```
2. 在 `retrieve()` 增加 `retrieval_method` 参数：
   - 显式 `vector`：使用 `VectorStoreQueryMode.DEFAULT`；
   - 显式 `hybrid`：使用 `VectorStoreQueryMode.HYBRID`；
   - `hybrid_ranker=rrf`：`RRFRanker`，默认 k=60；
   - `hybrid_ranker=weighted`：`WeightedRanker`，权重为 `hybrid_weights`，缺省 `[1.0, 1.0]`；
   - 未传：沿用 `settings.retrieval_hybrid_enabled`；
   - 未知值：抛出明确错误。
3. `search_service.search()` 与 `routers/search.py` 原样透传。
4. 前端新增“检索方式”下拉，提供 `vector` / `hybrid`；hybrid 下新增融合策略与权重配置。
5. 补充测试并验证全量回归。

## 验证

1. `SearchRequest` 默认值、合法值、非法值测试通过。
2. Router 测试确认 `retrieval_method` 透传到 service。
3. Service 测试确认 `retrieval_method` 透传到 `retrieve`。
4. 前端 `npm run build` 通过。
