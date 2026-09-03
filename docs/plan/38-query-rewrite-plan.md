# 38 文档检索加入 Query 改写（LLM 单查询改写 + 可开关）计划

> 版本: v1.1 | 日期: 2026-09-01 | 状态: 待评审
> 关联: `docs/08-es-fulltext-search-design.md`、`docs/plan/24-retrieval-methods-multiselect-plan.md`、`docs/plan/32-chat-multi-retrieval-plan.md`
> v1.1 变更：按评审意见，**改写提示词放入提示词管理**（新场景 `query_rewrite`，DB 激活模板优先 + 内置兜底，模式同计划 #34 / 迁移 0016），新增迁移 0017 扩展 `chk_prompt_templates_scenario` CHECK 约束并 seed 默认模板。

## 1. 需求（已与用户确认）

在文档检索链路加入 **LLM Query 改写**，并以**可开关选项**形式暴露：

| 澄清项 | 结论 |
|--------|------|
| 范围 | **检索页 + 问答（chat）** 两个入口 |
| 改写方式 | **LLM 单查询改写**（一次调用，输出一条优化查询） |
| 改写模型 | 检索页取**全局默认 LLM**（`model_type=llm` 且 `is_default` 且 `is_active`，无则跳过改写）；问答复用**知识库 LLM 解析**（绑定优先、全局默认兜底，与答案生成同一解析） |
| 改写提示词 | **提示词管理**新场景 `query_rewrite`（激活模板优先 + 代码内 `FALLBACK_QUERY_REWRITE_PROMPT` 兜底），变量 `{query}`（必填）+ `{history}`（可选） |
| 开关与默认 | 检索页：`SearchRequest.query_rewrite`（每请求可选，前端 UI 开关，默认关）；问答：后端配置 `chat_query_rewrite_enabled`（聊天页无参数面板，沿用 `chat_retrieval_methods` 的配置项模式）。**两处默认均关闭**，关闭时行为与现状完全一致 |

**行为规则：**

1. **改写发生在召回之前**：改写后的查询用于 dense / sparse / fulltext / graph 各路召回；fulltext 路的词库 OR 扩展叠加在改写词之上；
2. **rerank 与最终回答使用原问题**：改写只服务召回（召回质量），相关性判断与 Prompt 中的用户问题仍是原文；
3. **改写失败 / 输出为空 / 输出与原词相同 → 静默回退原词**，检索照常，不阻断；
4. `SearchResponse.query` 仍返回原查询（前端高亮、展示逻辑不变）；改写前后查询写入 Langfuse span（input=原词, output=改写词）与日志；
5. 问答改写时把最近 4 轮对话历史渲染进 `{history}` 变量（解决"它/那项"等指代与承接问题）；检索页无历史传空串。

## 2. 现状

| 链路 | 现状 |
|------|------|
| 检索页 | `routers/search.py` 两端点 → `search_service.search()` → 多路召回（dense/sparse/fulltext/graph）单选直返/多选 RRF → 可选 rerank。请求参数见 `SearchRequest`；前端参数面板在 `SearchView.vue` |
| 问答 | `chat_service._run_rag` → `retrieve_and_rank`（`core/retrieval.py`）三路召回 + RRF + 可选 rerank；`retrieve_and_rank` 内部 rerank 用 `query` 参数原样传入 |
| Query 扩展现状 | `lexicon_service.expand_query`（词库 OR 扩展）只作用于 fulltext 路，已有；**无任何 Query 改写代码** |
| 模型体系 | `model_factory.resolve_model_config(kb_id, type)`（绑定优先→全局默认兜底）+ `create_llm(config)` 可复用；**无"仅取全局默认 LLM"的独立函数**（检索页多知识库无 kb 绑定概念，需新增） |
| 提示词管理 | `prompt_service.get_active_prompt(scenario)` + `render_prompt`；场景枚举含 `rag_qa / entity_extraction / document_summary / custom / image_recognition`；DB CHECK 约束 `chk_prompt_templates_scenario` 限定场景值（迁移 0007 建、0010 扩 image_recognition）；内置兜底先例：`FALLBACK_RAG_PROMPT`、`FALLBACK_ENTITY_EXTRACTION_PROMPT` + `_get_extraction_prompt()`（计划 #34） |

## 3. 改动范围

| 文件 | 改动 |
|------|------|
| `backend/alembic/versions/0017_query_rewrite_default_prompt.py`（新增） | 参照 0010：upgrade 重建 `chk_prompt_templates_scenario`（加入 `'query_rewrite'`，保留 image_recognition）+ seed 默认模板（场景 `query_rewrite`，变量 `query` 必填 / `history` 可选，is_active+is_default）；downgrade 删除默认模板并还原约束 |
| `backend/core/query_rewrite.py`（新增） | `FALLBACK_QUERY_REWRITE_PROMPT` 常量 + `_get_rewrite_prompt(query, history_text)`（优先提示词管理 `query_rewrite` 激活模板并渲染变量，缺失/异常回退常量，仿 `_get_extraction_prompt`）+ `rewrite_query(query, config, history=None) -> str`（`traced_span("query_rewrite", "GENERATION")`；内部 try/except，失败/空输出回退原词） |
| `backend/config.py` | 检索段新增 `query_rewrite_enabled: bool = False`（检索页服务端默认）、`chat_query_rewrite_enabled: bool = False`（问答开关） |
| `backend/core/model_factory.py` | 新增 `resolve_default_model_config(model_type) -> dict \| None`（仅全局默认启用模型，无则 None 不抛错）；`resolve_model_config` 的"默认兜底"分支改为调用它（行为不变，消除重复） |
| `backend/core/retrieval.py` | `retrieve_and_rank` 新增 `query_rewrite_config: dict \| None = None`、`query_rewrite_history: list \| None = None`：有配置才改写；**rerank 改用改写前的原查询**（无改写时与现状一致） |
| `backend/services/search_service.py` | `search()` 新增 `query_rewrite: bool \| None = None`；开启时 `resolve_default_model_config("llm")` 解析改写配置并调用 `rewrite_query`；召回路用改写词、rerank 用原词；search span metadata 记录是否改写 |
| `backend/services/chat_service.py` | `_run_rag` 按 `settings.chat_query_rewrite_enabled` 解析 LLM 配置（失败仅 warning 跳过）并连同最近 4 轮历史传给 `retrieve_and_rank` |
| `backend/models/schemas.py` | `SearchRequest` 新增 `query_rewrite: bool \| None = Field(None, ...)`；`PromptTemplateCreate/Update` 的 scenario 注释补充 `query_rewrite`（无 Literal 校验，仅文档） |
| `backend/routers/search.py` | 两个端点透传 `body.query_rewrite` |
| `backend/.env.example` | 文档处理/检索段追加两行开关注释（追加时保持文件既有编码） |
| `frontend/src/views/SearchView.vue` | 参数面板新增 `el-switch`「Query 改写（LLM 优化检索词）」+ 说明文案；`handleSearch` 传 `query_rewrite` |
| `frontend/src/api/search.js` | `search()` 新增 `query_rewrite` 透传 |
| `frontend/src/views/PromptManagementView.vue` | 场景新增 `query_rewrite`（radio 按钮、表单下拉、`SCENARIO_LABELS`/`SCENARIO_TAG_TYPES`，label「Query 改写」） |
| 测试 | `test_model_factory.py`（resolve_default_model_config）、新增 `test_query_rewrite.py`（提示词加载 + 改写行为）、`test_retrieval.py`（retrieve_and_rank 改写参数）、`test_search_service.py`、`test_search_router.py`、`test_schemas.py`（query_rewrite 字段）、`test_chat_service.py`（_run_rag 开关接线） |
| `docs/08-es-fulltext-search-design.md` | §7.1/§7.5/§7.6/§8.1/§10 同步（改写阶段、检索参数、span 树、配置项） |
| `docs/changes.md` | 追加修改记录 |

**不改动：** `ChatRequest`/`routers/chat.py`（问答请求结构不变）、聊天页前端、`lexicon_service`（词库扩展逻辑不变，仅与改写叠加顺序变化）、ES/Milvus/PG 数据与索引结构（`prompt_templates` 仅加一条 seed + 约束重放，见迁移 0017）。

## 4. 关键逻辑（伪代码）

### 4.1 `core/query_rewrite.py`（新增）

```python
# 内置兜底（与迁移 0017 seed 的默认模板内容一致，仿 graph_extractor.FALLBACK_ENTITY_EXTRACTION_PROMPT）
FALLBACK_QUERY_REWRITE_PROMPT = """你是一名投标文档检索助手。请把下面的检索输入改写成更适合在投标文档库中检索的查询语句：
- 去除口语化表达、寒暄与无关内容；
- 补全行业缩写与关键业务要素（如资质、标准、评分办法等）；
- 保持原意，不要编造原文没有的新要求，不要回答问题；
- 只输出改写后的查询文本本身，不要引号、解释或换行。
{history}检索输入：{query}"""

async def _get_rewrite_prompt(query: str, history_text: str = "") -> str:
    """优先提示词管理 query_rewrite 激活模板并渲染 {query}/{history}；缺失/异常回退内置常量。"""
    try:
        active = await prompt_service.get_active_prompt("query_rewrite")
        if active:
            variable_defs = active.get("variables") or []
            return render_prompt(active["system_prompt"],
                                 {"query": query, "history": history_text}, variable_defs)
    except Exception as e:
        logger.warning("获取 Query 改写提示词失败，使用内置默认值: %s", e)
    return render_prompt(FALLBACK_QUERY_REWRITE_PROMPT,
                         {"query": query, "history": history_text}, [])

@traced_span("query_rewrite", "GENERATION")
async def rewrite_query(query: str, config: dict, history: list[dict] | None = None) -> str:
    """LLM 单查询改写；任何失败/空输出/未变化均回退原词。"""
    try:
        llm = create_llm(config)
        history_text = ""
        if history:
            lines = [f"{'用户' if m.get('role') == 'user' else '助手'}: {m.get('content')}" for m in history]
            history_text = "对话历史（用于理解指代，勿写入结果）：\n" + "\n".join(lines) + "\n\n"
        prompt = await _get_rewrite_prompt(query, history_text)
        resp = await llm.acomplete(prompt)
        rewritten = (resp.text or "").strip().splitlines()[0].strip().strip('"\'“”')
        if not rewritten or rewritten == query:
            return query
        get_client().update_current_span(input=query, output=rewritten,
                                         metadata={"model": config.get("model")})
        return rewritten
    except Exception as e:
        logger.warning("Query 改写失败，回退原词: %s", e)
        get_client().update_current_span(input=query, metadata={"error": str(e)})
        return query
```

### 4.2 `alembic/versions/0017_query_rewrite_default_prompt.py`（新增，参照 0010）

```python
# revision "0017", down_revision "0016"
def upgrade() -> None:
    op.drop_constraint("chk_prompt_templates_scenario", "prompt_templates", type_="check")
    op.create_check_constraint(
        "chk_prompt_templates_scenario", "prompt_templates",
        "scenario IN ('rag_qa', 'entity_extraction', 'document_summary', 'custom', 'image_recognition', 'query_rewrite')",
    )
    op.execute("""INSERT INTO prompt_templates (...) VALUES (
        uuid_generate_v4()::varchar, 'Query 改写（默认）', '用于检索前把用户输入改写为更适合文档库检索的查询语句',
        'query_rewrite', '<FALLBACK_QUERY_REWRITE_PROMPT 同款内容>',
        '[
          {"name": "query", "label": "检索输入", "required": true, "description": "用户输入的检索词/问题"},
          {"name": "history", "label": "对话历史", "required": false, "description": "最近几轮对话（仅问答传入，检索页为空）"}
        ]'::jsonb,
        TRUE, TRUE, 0);""")

def downgrade() -> None:
    op.execute("DELETE FROM prompt_templates WHERE scenario = 'query_rewrite' AND is_default = TRUE;")
    op.drop_constraint("chk_prompt_templates_scenario", "prompt_templates", type_="check")
    op.create_check_constraint("chk_prompt_templates_scenario", "prompt_templates",
        "scenario IN ('rag_qa', 'entity_extraction', 'document_summary', 'custom', 'image_recognition')")
```

### 4.3 `core/model_factory.py` — 全局默认解析（供检索页多 KB 场景）

```python
async def resolve_default_model_config(model_type: str) -> dict | None:
    """仅取全局默认启用配置（is_default 且 is_active）；无则返回 None，不抛错。"""
    async with async_session() as session:
        result = await session.execute(select(ModelConfig).where(
            ModelConfig.model_type == model_type,
            ModelConfig.is_default == True,   # noqa: E712
            ModelConfig.is_active == True,    # noqa: E712
        ))
        mc = result.scalar_one_or_none()
        return _config_to_dict(mc) if mc is not None else None

# resolve_model_config 的"2. 全局默认兜底"分支改为：
#     cfg = await resolve_default_model_config(model_type)
#     if cfg: return cfg
#     if model_type in REQUIRED_MODEL_TYPES: raise RuntimeError(...)
#     return None
```

### 4.4 `core/retrieval.py` — `retrieve_and_rank` 扩展

```python
async def retrieve_and_rank(query, kb_id, top_k=5, similarity_threshold=0.2,
                            rerank_config=None, rerank_threshold=0.8,
                            retrieval_methods=None, expand_query=None,
                            query_rewrite_config=None, query_rewrite_history=None):
    original_query = query
    if query_rewrite_config:
        query = await rewrite_query(query, query_rewrite_config, history=query_rewrite_history)
        # 内部失败/空输出已回退原词；改写词用于下方各路召回与 fulltext 词库扩展
    # …1. 各路召回（dense/sparse 用 query，fulltext 用 expand_query(query)）不变…
    # …2. 单选直返/多选 RRF 不变…
    # 3. rerank 用原查询（改写只服务召回，不扭曲相关性判断）
    if rerank_config and all_hits:
        all_hits = await rerank(original_query, all_hits, rerank_config)
        all_hits = [h for h in all_hits if h["score"] >= rerank_threshold]
    return all_hits
```

### 4.5 `services/search_service.py` — `search()` 头部

```python
async def search(query, kb_ids, top_k=10, similarity_threshold=0.6,
                 retrieval_methods=None, rerank_model_id=None, rerank_top_n=5,
                 highlight=True, query_rewrite: bool | None = None):
    original_query = query
    rewrite_on = settings.query_rewrite_enabled if query_rewrite is None else query_rewrite
    if rewrite_on:
        cfg = await resolve_default_model_config("llm")   # 无默认 LLM → None → 跳过
        if cfg:
            query = await rewrite_query(original_query, cfg)
    # …校验 kb_ids / methods 不变；_recall_milvus/_recall_fulltext/_recall_graph 用 query（改写词）…
    # rerank：_apply_rerank(original_query, candidates, rerank_model_id)
    # search span metadata 增加 {"original_query": original_query, "query_rewritten": query != original_query}
    # 响应 {"query": original_query, ...}（对外仍是原查询）
```

### 4.6 `services/chat_service.py` — `_run_rag`

```python
rewrite_config = None
if settings.chat_query_rewrite_enabled:
    try:
        rewrite_config = await resolve_model_config(kb_id, "llm")   # 绑定优先→全局默认兜底
    except Exception as e:
        logger.warning("Query 改写配置解析失败，跳过改写: %s", e)
rewrite_history = [{"role": m.role, "content": m.content} for m in history[-4:]]
retrieved = await retrieve_and_rank(
    query=message, kb_id=kb_id, ..., retrieval_methods=settings.chat_retrieval_methods,
    expand_query=lexicon_service.expand_query,
    query_rewrite_config=rewrite_config, query_rewrite_history=rewrite_history,
)
# Prompt 构建与 LLM 生成仍使用原始 message（不变）
```

### 4.7 前端

```js
// SearchView.vue: const queryRewrite = ref(false)
// 参数面板新增：el-switch「Query 改写（LLM 优化检索词）」+ 提示“用全局默认 LLM 改写检索词，失败自动回退原词”
// handleSearch 传参 query_rewrite: queryRewrite.value
// api/search.js: 请求体新增 query_rewrite: queryRewrite ?? null

// PromptManagementView.vue：场景列表新增 query_rewrite → label「Query 改写」（radio/下拉/SCENARIO_LABELS/SCENARIO_TAG_TYPES）
```

## 5. 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 改写提示词存放 | **提示词管理**新场景 `query_rewrite`（激活模板优先）+ 内置 fallback | 用户评审要求；与实体抽取提示词（计划 #34）同模式，便于运营调优 |
| 改写作用于哪些环节 | 仅**召回与词库扩展**；rerank/回答用原问题 | 改写提升召回调优，相关性判断与回答语义不应被改写词扭曲 |
| 改写模型来源 | 检索页全局默认 LLM；问答 kb 绑定优先→默认兜底 | 检索页多知识库无单一绑定；问答与答案生成同一解析、行为一致 |
| 无 LLM 可用时 | 跳过改写 = 等同关闭（日志 warning） | 默认关 + 优雅降级；问答无 LLM 时原有"模型配置错误"行为不变（改写解析失败只 warning） |
| 失败处理 | 函数内 try/except 静默回退原词 | 调用方零感知，链路永不因改写中断 |
| 问答历史 | 最近 4 轮渲染进 `{history}` 变量 | 多轮问答指代/承接是问答检索的主要失效场景，成本极低；变量可选，模板可不用 |
| 默认开关 | 两处默认 False，行为与现状一致 | 无额外延迟/成本；用户按需开启 |
| UI 与默认关系 | 前端显式传 bool（默认 false）；服务端默认仅对未传参的调用生效 | 与 `retrieval_methods`（null→服务端默认）同一模式；.env 可设默认但前端面板显式覆盖 |
| 响应字段 | 不改 `SearchResponse`，`query` 仍为原词 | 前端零适配；改写过程经 Langfuse span 与日志可观测 |
| DB 变更 | 仅约束重放 + 一条 seed（0017） | `prompt_templates` 无结构变化；downgrade 可完整回退 |

## 6. 实现步骤

1. `config.py` 新增两开关 → 验证：`Settings()` 默认 False、env 可覆盖。
2. `model_factory.py`：新增 `resolve_default_model_config` 并重构默认兜底分支 → 验证：现有 `test_model_factory.py` 全绿 + 新增用例。
3. 迁移 `0017_query_rewrite_default_prompt.py`（约束 + seed）→ 验证：`alembic upgrade head` 幂等可执行、`scenario='query_rewrite'` 默认模板可查、`alembic downgrade -1` 回退正常（如连库环境可达；否则仅代码审查）。
4. 新增 `core/query_rewrite.py` → 验证：`test_query_rewrite.py`（激活模板生效/无激活回退/DB 异常回退；成功/空输出/异常/带历史）。
5. `core/retrieval.py` `retrieve_and_rank` 扩展 → 验证：`test_retrieval.py` 新增（改写后召回路用改写词、rerank 用原词、默认 None 行为不变）。
6. `services/search_service.py` → 验证：`test_search_service.py` 新增（开关关不调用改写、无默认 LLM 跳过、开启后召回用改写词、rerank 用原词、响应 query 为原词）。
7. `services/chat_service.py` `_run_rag` 接线 → 验证：`test_chat_service.py` 新增（开关 on/off 时 `retrieve_and_rank` 收到/收不到 `query_rewrite_config`）。
8. `schemas.py` + `routers/search.py` + `.env.example` → 验证：`test_schemas.py`/`test_search_router.py`。
9. 前端 `api/search.js` + `SearchView.vue` + `PromptManagementView.vue` → 验证：`npm run build`。
10. 全量 `pytest -p no:cacheprovider` 通过。
11. 更新 `docs/08` 与 `docs/changes.md`。

## 7. 验证方式

1. **单元测试**
   - `test_query_rewrite.py`（新增）：
     - 提示词加载：mock `prompt_service.get_active_prompt("query_rewrite")` 返回激活模板 → 使用模板并渲染 `{query}`/`{history}`；无激活 → 内置 fallback；DB 异常 → 回退（仿 `test_graph_extractor` 提示词用例）；
     - 改写行为：改写成功返回清洗文本；空输出/异常/结果等于原词 → 回退原词；`history` 存在时渲染含历史、不存在时 `{history}` 为空；
   - `test_model_factory.py`：`resolve_default_model_config` 返回启用的默认配置；默认被停用/无默认 → None；`resolve_model_config` 兜底行为与原先一致；
   - `test_retrieval.py`：patch `core.retrieval.rewrite_query` 返回改写词 —— `retrieve_and_rank(query_rewrite_config={...})` 时 dense `retrieve()` 与 fulltext `es_search()` 收到改写词；rerank mock 收到的 query 是**原词**；不传 `query_rewrite_config` 时完全不调用 `rewrite_query`、rerank 收到原 query（回归）；
   - `test_search_service.py`：`query_rewrite` 缺省/False 不调用 `resolve_default_model_config` 与改写；True 且无默认 LLM → 原词召回；True 且有默认 LLM → 召回用改写词、响应 `query` 为原词；`query_rewrite=None` 时跟随 `settings.query_rewrite_enabled`；
   - `test_schemas.py`/`test_search_router.py`：字段默认 None、可传 False/True、router 透传；
   - `test_chat_service.py`：`_run_rag` 在 `settings.chat_query_rewrite_enabled=True` 时解析 LLM 配置并传入 `query_rewrite_config`；False 时传 None；解析抛异常时跳过改写不报错。
2. **前端**：`npm run build` 通过；提示词管理页可见「Query 改写」场景并可编辑激活。
3. **回归**：默认开关关闭下，检索页与问答行为、分数展示、Langfuse span 树与改动前一致。

## 8. 风险与应对

| 风险 | 说明 | 应对 |
|------|------|------|
| 短关键词被改写"画蛇添足" | 如"招标文件"被扩写成带无关修饰的查询 | 提示词约束保留原意；空/相等输出回退；默认关闭按需开启 |
| 每次检索多一次 LLM 调用 | 延迟与成本 | 仅开关开启时产生；Langfuse `query_rewrite` GENERATION span 可观测耗时 |
| 未配置全局默认 LLM | 检索页改写无模型可用 | `resolve_default_model_config` 返回 None → 跳过改写（等价关闭），不报错 |
| 问答历史拼入改写 | 可能引入歧义 | 仅最近 4 轮、以"理解指代"角色注入且要求不写入结果 |
| 迁移 0017 破坏现有约束 | 重建 `chk_prompt_templates_scenario` 时遗漏既有场景值 | 新值列表 = 当前全量（含 image_recognition）+ query_rewrite；downgrade 还原 |
| fallback 与 seed 内容漂移 | 内置常量与 DB 默认模板不一致 | 迁移 seed 引用同一文案，代码注释互相标注（同 0016 做法） |
| `.env.example` 编码 | 文件存在历史乱码显示 | 追加前确认实际字节编码，按原编码追加，不整体重写 |
