# 41 问答意图识别（检索 vs 普通聊天）方案
> 版本: v1.0 | 日期: 2026-09-04 | 状态: 已确认（推荐默认方案，用户确认实施）

## 1. 需求（已确认）
问答页每轮问答前先做一次**意图识别**，判断本轮输入：
- **需要检索** → 走既有 RAG 链路（Query 改写 + 三路混合检索 + rerank + 引用来源），行为不变；
- **普通聊天**（寒暄/问候/身份/能力/开放闲聊，与文档内容无关）→ **跳过检索/改写/词库/rerank**，直接 LLM 流式回复，不返回引用来源。

| 澄清项 | 结论 |
|--------|------|
| 范围 | 仅问答页 `chat`（检索页为用户主动检索，不参与） |
| 判定方式 | LLM 单次分类；新增提示词管理场景 `intent_detect`（DB 激活模板优先 + 内置兜底，模式同 `query_rewrite`） |
| 判定依据 | 本轮 `{query}` + 最近 4 轮历史 `{history}`（防"那这个呢/继续"等指代误判为闲聊） |
| 判定模型 | 复用问答 LLM 解析 `resolve_model_config(kb_id, "llm")`（与答案生成同一解析）；解析失败跳过识别 |
| 失败/异常策略 | 一律回退"需要检索"（保持现状安全，不阻断、不误答） |
| 开关与默认 | 后端配置 `chat_intent_detect_enabled = True`（默认开启；无前端控件，同 `chat_query_rewrite_enabled` 模式） |
| 普通聊天提示词 | 新增提示词管理场景 `chat_normal`（可编辑口径，代码内置兜底），变量 `{query}` 必填 / `{history}` 可选 |
| 会话/来源 | 普通聊天同样保存用户消息与助手回复（保持多轮连续性），`sources` 不返回；`done` 事件不变 |
| 观测 | 一轮问答一条 trace：根 chain `chat_qa`（下发 session_id/kb_id）→ 意图识别 generation `intent_detect`（含完整 LLM 调用信息：model/实际 prompt input/output/token usage/异常 error，同 query_rewrite 口径）+ 分支 chain `rag_qa`/`chat_normal`（其 LLM generation 内嵌） |

## 2. 现状
- `services/chat_service.py`：`chat_stream`（会话/历史/存消息/组装 SSE）→ `_run_rag`（检索→Prompt→LLM 流式）→ 保存回复/sources/done；`_run_rag` 内 Query 改写开关已接（`chat_query_rewrite_enabled`，当前默认 True）。
- `core/query_rewrite.py` 提供"提示词管理激活模板 + 内置兜底 + traced_span generation + 失败静默回退"的完整模板可复用。
- 提示词场景由 DB CHECK 约束 `chk_prompt_templates_scenario` 限定（迁移 0017 已含 `query_rewrite`）；前端管理页场景为写死的 radio/select/标签表。
- `core/model_factory.resolve_model_config(kb_id, "llm")` 返回问答所用 LLM 配置（绑定优先→全局默认，无则抛 RuntimeError）。

## 3. 改动范围
| 文件 | 改动 |
|------|------|
| `backend/config.py` | Query 改写区新增 `chat_intent_detect_enabled: bool = True` |
| `backend/.env.example` | 追加 `CHAT_INTENT_DETECT_ENABLED=true` 注释说明 |
| `backend/core/intent_detect.py`（新增） | `FALLBACK_INTENT_DETECT_PROMPT`、`_build_history_text()`、`_get_detect_prompt()`、`_parse_intent_output()`、`_is_information_request()`（检索请求前缀守卫）、`detect_intent() -> bool`（True=需要检索；失败/无法解析一律 True） |
| `backend/services/chat_service.py` | `FALLBACK_CHAT_NORMAL_PROMPT` + `_get_chat_normal_prompt()`；抽公共生成尾 `_generate_llm_answer()`；新增 `_run_chat_normal()`（chain span `chat_normal`，无检索）；`_run_rag` 尾部改用公共生成函数（行为不变）；新增 `_decide_retrieval()` 与 `_run_qa_round()`（trace 根 `chat_qa`：识别 + 分流），`chat_stream` Stage 4 委托 `_run_qa_round` |
| `backend/alembic/versions/0018_intent_detect_scenarios.py`（新增） | 仿 0017：CHECK 约束追加 `'intent_detect','chat_normal'` + seed 两个默认模板；downgrade 删除并还原约束 |
| `backend/models/schemas.py` | `PromptCreate.scenario` description 场景列表补 `intent_detect / chat_normal` |
| `frontend/src/views/PromptManagementView.vue` | 场景筛选 radio、新建弹窗 select、`SCENARIO_LABELS`/tag 色表补 `intent_detect` 与 `chat_normal` |
| `docs/changes.md` | 追加本次修改记录 |

不涉及：检索页、数据库表结构（仅约束/种子数据）、docker-compose。

## 4. 关键设计
### 4.1 `core/intent_detect.py`（新增，模板仿 query_rewrite）
- `FALLBACK_INTENT_DETECT_PROMPT`：判定口径**放宽检索**——任何想获取信息/资料/专业知识（含工程/医疗/法律等任意专业领域、纯主题名词查询，如"查询冠状动脉粥样硬化性心脏病""X 是什么"）→ 需要检索；仅纯日常闲聊（问候/寒暄/感谢/身份闲聊/讲笑话/天气等生活闲聊/情绪附和）→ 普通聊天；拿不准一律需要检索；只输出"需要检索/普通聊天"；变量 `{history}`（可选，前置提示勿写入结果）+ `{query}`。与迁移 0021 seed 逐字一致（防漂移测试）。
- `_parse_intent_output(text) -> bool | None`：小写化清洗后按关键词判定（"普通聊天/闲聊/寒暄/chat/小talk"→False；"需要检索/检索/文档/retrieval"→True）；输出为 JSON 时读 `intent` 字段；无法判定返回 None。
- `_get_detect_prompt()`：优先 `prompt_service.get_active_prompt("intent_detect")` 并渲染变量；缺失/DB 异常回退内置常量（仿 query_rewrite）。
- `detect_intent()`：`@traced_span("intent_detect","generation")`；`create_llm(config)` → `acomplete(prompt)`；None/异常 → warning + 返回 True（安全兜底）；正常则 update_current_span(input/output/model)。

### 4.2 `services/chat_service.py` 分流与闲聊
- `_run_qa_round()`：`@traced_span("chat_qa","chain")`——先 `propagate_attributes(session_id/kb_id)` 再 `_decide_retrieval`，让 `intent_detect` generation 与 `_run_rag`/`_run_chat_normal` 子链挂在同一 trace 下（避免识别单独成条）；result 记录 `intent`（retrieval/chat）。
- 抽公共生成尾 `_generate_llm_answer(kb_id, system_prompt, message, history, out)`：`resolve_model_config(kb_id,"llm")` → `create_llm` → `[System] + 历史[-10:] + [User]` → `_call_llm_stream` 逐 token yield；结束 `out["content"]` 存全文（span output 由调用方更新；`_run_rag` 改用它，行为不变）。
- `_run_chat_normal()`：`@traced_span("chat_normal","chain")`；propagate_attributes + update_current_span(input)；system_prompt = `_get_chat_normal_prompt(message, _build_history(history))`（场景 `chat_normal`，回退 `FALLBACK_CHAT_NORMAL_PROMPT`）；yield 生成；输出写入 span；`result["retrieved"]=[]`。
- `_decide_retrieval(kb_id, message, history) -> bool`：开关关 → True；开 → `resolve_model_config(kb_id,"llm")` 失败/无配置 warning 返回 True；成功 → `detect_intent(message, config, 最近4轮历史)`。
- `chat_stream`：Stage 4 前 `needs_retrieval = await _decide_retrieval(...)`；按分支取 `_run_rag` 或 `_run_chat_normal` 生成器；后续 token 组装/存回复/（无 retrieved 则不返回 sources）/done 不变。

### 4.3 迁移 0018（仿 0017）
- upgrade：drop + create `chk_prompt_templates_scenario`（原清单 + `'intent_detect','chat_normal'`）；INSERT 两个默认模板（场景/名称/描述/变量 query 必填 history 可选/is_active/is_default/sort_order 0）。
- downgrade：DELETE 两默认模板；还原约束为 0017 清单。
- 默认模板文案与代码内置 fallback 保持一致（部署后亦可再编辑）。

## 5. 验证方式
- 新增 `backend/tests/test_intent_detect.py`：输出解析（需要检索/普通聊天/JSON/标点大小写/空/非法→None）、`detect_intent` 失败回退 True、提示词选择回退、历史文本组装；
- 扩展 `backend/tests/test_chat_service.py`：`_decide_retrieval` 三态（关→True / 识别普通聊天→False / 解析异常→True）；`_run_chat_normal` 接线（不调 retrieve_and_rank、走 chat_normal 提示词、流式收集正常）；`_run_rag` 原有用例回归；
- 前端 `npm run build`；后端定向 + 全量 pytest；
- `docs/changes.md` 修改记录与本次 diff 一一对应。

## 6. 部署提醒
服务器需 `alembic upgrade head`（应用 0018：扩展约束 + seed 两默认模板）后重启 backend；前端需重新构建部署。代码兜底与迁移 seed 文案保持一致。
