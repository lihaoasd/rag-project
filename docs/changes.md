# 修改记录

## 2026-08-18

- 需求：检索页面新增检索选项配置项，支持向量检索与混合检索，后续再扩展其他检索方式。
- 改动：后端 `SearchRequest` / `search_service` / `search router` / `retrieve` 新增并透传 `retrieval_method`、`hybrid_ranker`、`hybrid_weights`；前端检索页新增“检索方式”配置，hybrid 下支持 `RRFRanker` / `WeightedRanker` 与权重调整。
- 验证：新增 schema / service / router 测试，运行后端测试与前端构建。
