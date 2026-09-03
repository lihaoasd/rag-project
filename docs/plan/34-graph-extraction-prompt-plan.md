# 实体抽取提示词接入提示词管理开发方案

> 关联设计: [09-graph-module-design.md](../09-graph-module-design.md)（§5.1/§5.3 抽取管线） | 日期: 2026-08-24 | 状态: 已确认

## 1. 需求与现状

**需求**：实体抽取的提示词要放提示词管理（`prompt_templates` 表，场景 `entity_extraction`）。

**现状**：
- 提示词管理体系已存在：`prompt_templates` 表 + `prompt_service`（CRUD/激活互斥/默认模板）+ `core/prompt_renderer`（`{var}` 渲染）+ 前端 `PromptManagementView.vue`；场景 `rag_qa`（默认模板已 seed）、`image_recognition`（迁移 0010 seed）。
- `entity_extraction` 场景在 DB CHECK 约束（0007）、`prompt_service.SCENARIOS`、前端场景筛选/下拉中**均已存在**，但：
  - 没有 seed 默认模板（`get_active_prompt("entity_extraction")` 返回 None）；
  - `core/graph_extractor.py` 使用模块级硬编码常量 `_EXTRACTION_SYSTEM_PROMPT`，与提示词管理完全脱钩。

**假设（与既有约定一致，不再确认）**：
1. 沿用 chat/image_recognition 的既有消费模式：**DB 激活模板优先 → 渲染 → 失败/缺失用内置 fallback 常量**；
2. 抽取提示词无动态变量，`variables` 字段 seed 为 NULL，渲染时传入空变量 dict；
3. 内置 fallback 常量保留（改名为 `FALLBACK_ENTITY_EXTRACTION_PROMPT`），内容与 seed 模板一致，作为无 DB/DB 故障时的兜底；
4. 前端无需改动（场景下拉/筛选已支持 `entity_extraction`）。

## 2. 改动范围

| 文件 | 改动 |
|------|------|
| `backend/alembic/versions/0016_entity_extraction_default_prompt.py`（新增） | seed 默认 `entity_extraction` 模板（is_active=TRUE, is_default=TRUE）；downgrade 删除该默认模板 |
| `backend/core/graph_extractor.py` | `_EXTRACTION_SYSTEM_PROMPT` → `FALLBACK_ENTITY_EXTRACTION_PROMPT`；新增 `_get_extraction_prompt()`（DB 激活模板优先 + fallback）；`_llm_extract_batch` 改用动态 prompt |
| `backend/tests/test_graph_extractor.py` | 新增 `TestExtractionPromptLoading`：激活模板生效 / 无模板 fallback / 异常 fallback |
| `docs/09-graph-module-design.md` | §5.3 补"提示词由提示词管理维护"说明；§5.1 管线图示标注 |
| `docs/changes.md` | 追加修改记录 |

## 3. 实现步骤（伪代码）

### 3.1 迁移 0016（仿 0010 模式）

```
upgrade:
    INSERT INTO prompt_templates (id, name, description, scenario, system_prompt, variables,
                                  is_active, is_default, sort_order)
    VALUES (uuid_generate_v4()::varchar, '实体抽取（默认）',
            '用于知识图谱实体/关系抽取的默认提示词（词库优先 + LLM 补充）',
            'entity_extraction',
            '<当前 _EXTRACTION_SYSTEM_PROMPT 全文>', NULL, TRUE, TRUE, 0)
downgrade:
    DELETE FROM prompt_templates WHERE scenario='entity_extraction' AND is_default=TRUE
```

校验：`alembic upgrade head` 后 DB 中该模板存在且激活。

### 3.2 graph_extractor.py

```
# 常量改名（内容不变）
FALLBACK_ENTITY_EXTRACTION_PROMPT = <原 _EXTRACTION_SYSTEM_PROMPT 内容>

async def _get_extraction_prompt() -> str:
    """优先 DB 激活模板（提示词管理 entity_extraction），失败/缺失用内置 fallback。"""
    try:
        active = await prompt_service.get_active_prompt("entity_extraction")
        if active:
            return render_prompt(active["system_prompt"], {}, active.get("variables") or [])
    except Exception as e:
        logger.warning("获取实体抽取提示词失败，使用内置默认值: %s", e)
    return FALLBACK_ENTITY_EXTRACTION_PROMPT

async def _llm_extract_batch(kb_id, batch_chunks):
    system_prompt = await _get_extraction_prompt()   # ← 新增一行
    messages = [ChatMessage(role="system", content=system_prompt), ...]
```

校验：单测断言 `_llm_extract_batch` 传给 `llm.achat` 的 system 消息分别是 DB 模板内容 / fallback 常量。

### 3.3 测试（test_graph_extractor.py 新增）

```
class TestExtractionPromptLoading:
    # patch: graph_extractor.resolve_model_config / graph_extractor.create_llm（achat 返回合法抽取 JSON）
    # patch: graph_extractor.prompt_service.get_active_prompt
    test_uses_active_template:      get_active_prompt → {system_prompt: "自定义抽取提示词", variables: []}
                                   → achat messages[0].content == "自定义抽取提示词"
    test_fallback_when_missing:    get_active_prompt → None
                                   → messages[0].content == FALLBACK_ENTITY_EXTRACTION_PROMPT
    test_fallback_on_error:        get_active_prompt → raise RuntimeError
                                   → messages[0].content == FALLBACK_ENTITY_EXTRACTION_PROMPT
```

## 4. 验证方式

1. 后端全量 pytest（`-p no:cacheprovider`，stderr 重定向）退出码 0；
2. `alembic upgrade head`（注入 PG_* 环境变量）成功，0016 落到 head；
3. 真实环境（GRAPH_ENABLED=true）：`get_active_prompt("entity_extraction")` 返回 seed 模板；重建图谱走 DB 模板（可在提示词管理改模板后重跑单文件构建验证生效）。

## 5. 风险与边界

- **渲染安全性**：`render_prompt` 会将模板中 `{word}` 占位符替换（未声明变量 → 空串）。seed 模板无占位符（所有花括号后紧跟引号，非 `{word}` 形态），渲染后与原文一致；用户若在管理界面新增占位符，需同步在模板 variables 中声明（与 rag_qa 行为一致）。
- **每次批量抽取一次 DB 查询**：`get_active_prompt` 每批 chunk 调一次（与 chat_service 每次问答调一次同量级），构建为后台单 worker 串行，开销可忽略；如需优化可改为每次文档构建加载一次（本期不做）。
