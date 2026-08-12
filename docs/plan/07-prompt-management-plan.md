# 提示词管理模块 — 开发方案

> 版本: v1.0 | 日期: 2026-08-12 | 状态: 待评审

---

## 1. Context

### 1.1 现状问题

当前 RAG 系统的 System Prompt 是硬编码在 [chat_service.py:30-36](../backend/services/chat_service.py) 的 Python 常量：

```python
RAG_SYSTEM_PROMPT = """你是一个投标文档分析助手。请根据以下文档内容回答用户问题。

【参考文档内容】
{context_str}

请基于参考文档内容回答。如果文档中找不到相关信息，请明确说明。
回答时请注明信息来源（文档名、页码）。"""
```

存在以下问题：
- 修改 Prompt 需要改代码、重启服务
- 无法切换不同风格的 Prompt（正式/简洁/详细等）
- 不支持多场景（RAG 问答、实体抽取、文档总结等）
- 变量仅支持 `{context_str}`，不支持 `{query}`、`{history}`、`{kb_name}` 等

### 1.2 目标

构建一个多场景、全局统一的提示词模板管理系统，支持变量的可视化编辑和动态渲染。

### 1.3 用户确认的设计决策

| 决策 | 选项 |
|------|------|
| 场景范围 | **多场景**（RAG 问答 + 实体抽取 + 文档总结 + 自定义） |
| 绑定粒度 | **全局**（所有知识库共用） |
| 版本管理 | **不需要**（简单 CRUD） |

---

## 2. 数据库设计

### 2.1 新表：`prompt_templates`

按[数据库设计文档](../docs/05-database-design.md)的命名规范和字段约定设计。

| 序号 | 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|------|
| 1 | `id` | VARCHAR(36) | PK | UUID（与现有表一致，已切换为 VARCHAR） |
| 2 | `name` | VARCHAR(255) | NOT NULL, UNIQUE | 模板名称 |
| 3 | `description` | TEXT | NULLABLE | 模板描述 |
| 4 | `scenario` | VARCHAR(50) | NOT NULL | 场景类型 |
| 5 | `system_prompt` | TEXT | NOT NULL | 系统提示词（含变量占位符） |
| 6 | `user_prompt` | TEXT | NULLABLE | 用户提示词模板（可选） |
| 7 | `variables` | JSONB | NULLABLE | 变量定义列表 |
| 8 | `is_active` | BOOLEAN | NOT NULL, DEFAULT FALSE | 是否为当前使用模板 |
| 9 | `is_default` | BOOLEAN | NOT NULL, DEFAULT FALSE | 是否为该场景默认模板 |
| 10 | `sort_order` | INTEGER | NOT NULL, DEFAULT 0 | 排序序号 |
| 11 | `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | |
| 12 | `updated_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | |

**`scenario` CHECK 约束：**

| 值 | 说明 |
|-----|------|
| `rag_qa` | RAG 智能问答（对应 F-10~F-13） |
| `entity_extraction` | 实体抽取（Graph RAG / 词库） |
| `document_summary` | 文档总结 |
| `custom` | 自定义场景 |

**`variables` JSONB 结构：**

```json
[
  {"name": "context_str", "label": "检索上下文", "required": true, "description": "从知识库检索到的文档片段"},
  {"name": "query", "label": "用户问题", "required": true, "description": "用户输入的原始问题"},
  {"name": "history", "label": "对话历史", "required": false, "description": "最近几轮对话记录"},
  {"name": "kb_name", "label": "知识库名称", "required": false, "description": "当前使用的知识库名称"}
]
```

### 2.2 DDL

```sql
CREATE TABLE prompt_templates (
    id            VARCHAR(36) PRIMARY KEY,
    name          VARCHAR(255) NOT NULL,
    description   TEXT,
    scenario      VARCHAR(50) NOT NULL,
    system_prompt TEXT NOT NULL,
    user_prompt   TEXT,
    variables     JSONB,
    is_active     BOOLEAN NOT NULL DEFAULT FALSE,
    is_default    BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_prompt_templates_name UNIQUE (name),
    CONSTRAINT chk_prompt_templates_scenario CHECK (
        scenario IN ('rag_qa', 'entity_extraction', 'document_summary', 'custom')
    )
);

CREATE INDEX idx_prompt_templates_scenario ON prompt_templates (scenario);
CREATE INDEX idx_prompt_templates_active ON prompt_templates (scenario, is_active) WHERE is_active = TRUE;
CREATE INDEX idx_prompt_templates_default ON prompt_templates (scenario, is_default) WHERE is_default = TRUE;

CREATE TRIGGER trg_prompt_templates_updated_at
    BEFORE UPDATE ON prompt_templates
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
```

### 2.3 默认数据

迁移时插入默认 RAG 问答模板（等同于当前硬编码的 Prompt）：

```sql
INSERT INTO prompt_templates (id, name, description, scenario, system_prompt, variables, is_active, is_default, sort_order)
VALUES (
    gen_random_uuid()::varchar,
    'RAG 问答（默认）',
    '基于文档内容回答用户问题，标注来源',
    'rag_qa',
    '你是一个投标文档分析助手。请根据以下文档内容回答用户问题。

【参考文档内容】
{context_str}

【对话历史】
{history}

【用户问题】
{query}

请基于参考文档内容回答。如果文档中找不到相关信息，请明确说明。
回答时请注明信息来源（文档名、页码）。',
    '[
      {"name": "context_str", "label": "检索上下文", "required": true, "description": "从知识库检索到的文档片段"},
      {"name": "query", "label": "用户问题", "required": true, "description": "用户输入的原始问题"},
      {"name": "history", "label": "对话历史", "required": true, "description": "最近几轮对话记录"},
      {"name": "kb_name", "label": "知识库名称", "required": false, "description": "当前使用的知识库名称"}
    ]'::jsonb,
    TRUE,
    TRUE,
    0
);
```

---

## 3. 改动范围

### 3.1 新建文件

```
backend/
├── models/prompt_template.py          # ORM 模型
├── routers/prompt.py                  # API 路由（7 个端点）
├── services/prompt_service.py         # 业务逻辑
├── core/prompt_renderer.py            # 变量渲染引擎
└── alembic/versions/0007_prompt_templates.py  # 数据库迁移

frontend/
└── src/
    ├── views/PromptManagementView.vue # 提示词管理页
    └── api/prompt.js                  # API 封装
```

### 3.2 修改文件

| 文件 | 改动内容 |
|------|----------|
| `backend/models/__init__.py` | 导出 `PromptTemplate` |
| `backend/models/schemas.py` | 新增 Prompt 相关 Pydantic Schema |
| `backend/main.py` | 注册 `prompt_router` |
| `backend/services/chat_service.py` | 从 DB 读取 Prompt 替换硬编码常量；扩展变量渲染 |
| `frontend/src/router/index.js` | 新增 `/prompts` 路由 |
| `frontend/src/components/AppLayout.vue` | 侧边栏新增"提示词管理"菜单项 |

---

## 4. 实现步骤

### Step 1: 数据库层 → 验证: 迁移成功 + 模型可用

1. 创建 `backend/alembic/versions/0007_prompt_templates.py` 迁移文件
2. 创建 `backend/models/prompt_template.py` ORM 模型
3. 更新 `backend/models/__init__.py` 导出
4. 执行 `alembic upgrade head` 验证表创建

### Step 2: Schema + 渲染引擎 → 验证: 单元测试

1. 在 `backend/models/schemas.py` 新增：
   - `PromptTemplateCreate` — 创建请求
   - `PromptTemplateUpdate` — 更新请求
   - `PromptTemplateResponse` — 列表/详情响应
   - `PromptPreviewRequest` — 预览请求（模板内容 + 变量值）
2. 创建 `backend/core/prompt_renderer.py`：
   - `render_prompt(template: str, variables: dict) -> str` — 核心渲染函数
   - `extract_variables(template: str) -> list[str]` — 提取模板中的变量名
   - `validate_template(template: str) -> dict` — 校验模板完整性
3. 编写 `test_prompt_renderer.py` 验证渲染逻辑

### Step 3: Service + Router → 验证: Swagger 可调用

1. 创建 `backend/services/prompt_service.py`：
   - `list_prompts(scenario?)` — 列表（支持按场景筛选）
   - `get_prompt(id)` — 详情
   - `create_prompt(data)` — 创建
   - `update_prompt(id, data)` — 更新
   - `delete_prompt(id)` — 删除（禁止删除默认模板）
   - `set_active(id)` — 设为此场景激活模板（同场景互斥）
   - `get_active_prompt(scenario)` — 获取场景当前激活模板
   - `preview(id_or_content, variables)` — 预览渲染结果
2. 创建 `backend/routers/prompt.py`：
   - `GET /api/prompts` — 列表（支持 `?scenario=` 筛选）
   - `POST /api/prompts` — 创建
   - `GET /api/prompts/{id}` — 详情
   - `PUT /api/prompts/{id}` — 更新
   - `DELETE /api/prompts/{id}` — 删除
   - `POST /api/prompts/{id}/activate` — 激活
   - `POST /api/prompts/preview` — 预览
3. 更新 `backend/main.py` 注册路由

### Step 4: Chat Service 集成 → 验证: 问答正常 + 切换 Prompt 生效

1. 修改 `backend/services/chat_service.py`：
   - 删除硬编码的 `RAG_SYSTEM_PROMPT` 常量
   - 在 Stage 4 通过 `PromptService.get_active_prompt("rag_qa")` 获取模板
   - 使用 `render_prompt()` 渲染模板（传入 context_str, query, history, kb_name）
   - 若 DB 中无激活模板，fallback 到代码中的默认 Prompt
2. 更新 `ChatRequest` Schema 可选传 `prompt_id` 参数（允许指定模板）

### Step 5: 前端管理页 → 验证: 页面可用 + CRUD 正常

1. 创建 `frontend/src/api/prompt.js`：
   - `listPrompts(scenario?)`、`getPrompt(id)`、`createPrompt(data)`、`updatePrompt(id, data)`、`deletePrompt(id)`、`activatePrompt(id)`、`previewPrompt(data)`
2. 创建 `frontend/src/views/PromptManagementView.vue`：
   - 左侧：场景筛选标签（rag_qa / entity_extraction / document_summary / custom）
   - 主区域：模板列表表格（名称、场景标签、激活状态、操作按钮）
   - 对话框：创建/编辑（名称、描述、场景下拉、system_prompt 代码编辑器、变量定义面板）
   - 预览功能：输入变量值，实时渲染预览
3. 更新 `frontend/src/router/index.js` 新增路由
4. 更新 `frontend/src/components/AppLayout.vue` 新增菜单项

---

## 5. 关键逻辑伪代码

### 5.1 变量渲染引擎

```
def render_prompt(template: str, variables: dict) -> str:
    # 1. 提取模板中所有 {variable_name}
    # 2. 检查 required 变量是否都已提供
    # 3. 缺失的非必填变量替换为默认值（如 "（无）"）
    # 4. 使用 str.format_map() 执行替换
    # 5. 返回渲染后的完整 Prompt
```

### 5.2 Chat Service 集成

```
# Stage 4: 构建 Prompt（替换原有硬编码逻辑）
async def _build_prompt(kb_id, message, history, retrieved):
    # 1. 尝试从 DB 获取 rag_qa 场景的激活模板
    active_prompt = await prompt_service.get_active_prompt("rag_qa")
    
    # 2. 若不存在，使用内置 fallback
    if not active_prompt:
        system_prompt = FALLBACK_RAG_PROMPT
    
    # 3. 渲染变量
    context_str = _build_context(retrieved)
    history_str = _build_history(history)
    variables = {
        "context_str": context_str,
        "query": message,
        "history": history_str,
        "kb_name": kb_name,
    }
    system_prompt = render_prompt(active_prompt.system_prompt, variables)
    
    # 4. 组装 ChatMessage 列表
    return [ChatMessage(role=SYSTEM, content=system_prompt), ...]
```

### 5.3 激活互斥逻辑

```
async def set_active(prompt_id: str):
    # 1. 获取目标模板，获取其 scenario
    prompt = await get(prompt_id)
    
    # 2. 将该 scenario 下所有模板的 is_active 设为 FALSE
    UPDATE prompt_templates SET is_active = FALSE WHERE scenario = prompt.scenario
    
    # 3. 将目标模板的 is_active 设为 TRUE
    UPDATE prompt_templates SET is_active = TRUE WHERE id = prompt_id
```

---

## 6. 前端页面设计

### 6.1 PromptManagementView.vue 布局

```
┌─────────────────────────────────────────────────────┐
│ 提示词管理                                           │
├──────────┬──────────────────────────────────────────┤
│ 场景筛选 │  模板列表表格                              │
│          │  ┌──────────────────────────────────┐    │
│ ● RAG问答│  │ 名称 │ 场景 │ 状态 │ 操作        │    │
│ ○ 实体抽取│  │ ...  │ ...  │ ...  │ 编辑/删除   │    │
│ ○ 文档总结│  └──────────────────────────────────┘    │
│ ○ 自定义  │  分页器                                   │
│          │                                           │
│ [+ 新建] │                                           │
└──────────┴──────────────────────────────────────────┘
```

### 6.2 编辑对话框

- 名称输入框
- 描述输入框
- 场景下拉选择
- System Prompt 编辑区（`<el-input type="textarea">` 编辑器，含变量高亮）
- User Prompt 编辑区（可选）
- 变量定义面板（`<el-table>` 展示变量名、标签、是否必填、描述）
- 预览区域：输入变量测试值 → 实时渲染结果

---

## 7. 验证方式

### 7.1 端到端验证

```bash
# 1. 执行迁移
cd backend && uv run alembic upgrade head

# 2. 验证表创建
docker exec bid_postgres psql -U postgres -d ai_bid_rag -c "\d prompt_templates"

# 3. 验证默认数据
docker exec bid_postgres psql -U postgres -d ai_bid_rag -c "SELECT name, scenario, is_active FROM prompt_templates;"

# 4. 启动后端
uv run uvicorn main:app --port 8000 --reload

# 5. 验证 API
curl http://localhost:8000/api/prompts          # 应返回默认模板
curl http://localhost:8000/api/docs              # 应看到新 prompt 分组

# 6. 验证问答仍正常（使用默认模板）
curl -X POST http://localhost:8000/api/knowledge-bases/{kb_id}/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "测试问题"}'

# 7. 启动前端
cd frontend && npm run dev
# 浏览器打开 http://localhost:5173/prompts
```

### 7.2 核心验证项

| 序号 | 检查项 | 方式 |
|------|--------|------|
| 1 | 迁移创建 prompt_templates 表 + 默认数据 | psql 验证 |
| 2 | API CRUD 全部端点正常（200/404/422） | curl / Swagger |
| 3 | 同场景激活互斥生效 | 激活 B 后再查 A 为 inactive |
| 4 | 问答功能不受影响（使用 DB 中默认模板） | SSE 问答正常 |
| 5 | 新建 RAG 模板并激活后，问答使用新 Prompt | 修改 Prompt 文案 → 问答验证 |
| 6 | 前端管理页列表/创建/编辑/删除/预览正常 | 手动操作 |
| 7 | 删除默认模板被拒绝 | 前端 toast + API 返回错误 |
| 8 | 变量渲染：缺失必填变量报错 | 单元测试 + API preview |

---

## 8. 不做什么（本次范围外）

- ❌ 不支持按知识库绑定 Prompt（全局统一）
- ❌ 不实现版本管理和历史回滚
- ❌ 不实现 Prompt A/B 测试
- ❌ 不实现 Prompt 导入/导出
- ❌ 不集成代码编辑器（Monaco Editor 等），先用 textarea
- ❌ 不修改 entity_extraction 和 document_summary 的调用方（仅预留场景类型，实际切换由后续迭代完成）
