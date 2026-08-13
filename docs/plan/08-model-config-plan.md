# 模型配置模块 — 开发方案

> 版本: v1.0 | 日期: 2026-08-13 | 状态: 待评审

---

## 1. Context

### 1.1 现状问题

当前模型配置分散且不可配置：

1. **LLM / Embedding 只读环境变量**：工厂函数 [llm_factory.py](../backend/core/llm_factory.py) 的 `create_llm(settings)` / `create_embedding(settings)` 直接读 `config.Settings`（.env），运行时无法切换、无法多模型并存。
2. **已有表但未接线**：`llm_configs` / `embedding_configs` 两张 ORM 表在 [0001_init.py](../backend/alembic/versions/0001_init.py) 已建表并播种默认数据，但没有任何 repository/service/router 使用它们。
3. **模型类型单一**：实际只用到 LLM 与 Embedding 两类；OCR 硬编码为本地 PaddleOCR（[parser.py](../backend/core/parser.py) `_get_ocr()`）；**没有 rerank、没有 vision**。
4. **知识库无法绑定模型**：[knowledge_base.py](../backend/models/knowledge_base.py) 的 `KnowledgeBase` 表无任何模型字段，做不到"不同知识库不同模型"。
5. **维度耦合在环境变量**：Milvus Collection 的 `dim` 从 `settings.embedding_dim` 读，换 Embedding 模型即维度变化，旧向量不匹配。

### 1.2 目标

1. 支持配置**多个**模型、**多个平台**（多 Provider）。
2. 支持 **5 类模型**：语言模型（llm）、向量模型（embedding）、排序模型（rerank）、视觉模型（vision）、OCR 模型（ocr）。
3. 支持**全局默认模型** + **按知识库覆盖**（不同知识库可各自绑定不同模型）。

### 1.3 用户确认的设计决策

| 决策 | 选项 |
|------|------|
| 表结构 | **统一 `model_configs` 表**（`model_type` 区分类型 + 参数 JSONB），不分表 |
| 知识库绑定 | **独立绑定表 `kb_model_bindings`**（KB 与模型按类型一对一，未绑定回退全局默认） |
| 换 Embedding 模型 | **有文档则锁定，禁止切换**（KB 有文档后锁定其 embedding 模型，只能新建 KB 换模型） |

---

## 2. 数据库设计

### 2.1 新表：`model_configs`

| 序号 | 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|------|
| 1 | `id` | VARCHAR(36) | PK | UUID |
| 2 | `name` | VARCHAR(100) | NOT NULL, UNIQUE | 配置名（如 "DeepSeek V3"） |
| 3 | `model_type` | VARCHAR(20) | NOT NULL | 模型类型 |
| 4 | `platform` | VARCHAR(50) | NOT NULL | 平台/提供商标识 |
| 5 | `model` | VARCHAR(100) | NOT NULL | 模型名（如 "deepseek-chat"） |
| 6 | `api_base` | VARCHAR(512) | NULLABLE | API 地址 |
| 7 | `api_key` | VARCHAR(512) | NULLABLE | API Key（字面量或 `${ENV}` 占位符） |
| 8 | `params` | JSONB | NULLABLE | 类型特定参数（见 2.3） |
| 9 | `is_default` | BOOLEAN | NOT NULL, DEFAULT FALSE | 该 `model_type` 下的全局默认模型 |
| 10 | `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | |
| 11 | `updated_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | |

**`model_type` CHECK 约束：**

| 值 | 说明 | 是否必配 |
|-----|------|----------|
| `llm` | 语言模型（问答生成） | 必配 |
| `embedding` | 向量模型（索引 + 检索） | 必配 |
| `rerank` | 排序模型（检索重排） | 可选（本期仅建模 + 工厂，运行时接入下期） |
| `vision` | 视觉模型（多模态理解） | 可选（本期仅建模 + 工厂） |
| `ocr` | OCR 模型（图片文字识别） | 可选（未配置回退本地 PaddleOCR） |

**`platform` 不设 CHECK**（多平台开放），推荐取值：`deepseek` / `openai` / `zhipu` / `moonshot` / `siliconflow` / `qwen` / `ollama` / `xinference` / `paddleocr` / `aliyun-ocr`。`platform` 仅作标识与 OCR 后端分派，LLM/Embedding/Rerank/Vision 均走 OpenAI 兼容协议，由 `api_base` + `api_key` 决定实际目标。

**DDL：**

```sql
CREATE TABLE model_configs (
    id          VARCHAR(36) PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    model_type  VARCHAR(20) NOT NULL,
    platform    VARCHAR(50) NOT NULL,
    model       VARCHAR(100) NOT NULL,
    api_base    VARCHAR(512),
    api_key     VARCHAR(512),
    params      JSONB,
    is_default  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_model_configs_name UNIQUE (name),
    CONSTRAINT chk_model_configs_type CHECK (
        model_type IN ('llm', 'embedding', 'rerank', 'vision', 'ocr')
    )
);

-- 每类型唯一默认
CREATE UNIQUE INDEX uq_model_configs_default ON model_configs (model_type)
    WHERE is_default = TRUE;

CREATE INDEX idx_model_configs_type ON model_configs (model_type);

CREATE TRIGGER trg_model_configs_updated_at
    BEFORE UPDATE ON model_configs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
```

> `uq_model_configs_default`（部分唯一索引）保证每种类型最多一个 `is_default = TRUE`，替代应用层互斥的竞态。

### 2.2 新表：`kb_model_bindings`

| 序号 | 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|------|
| 1 | `id` | VARCHAR(36) | PK | UUID |
| 2 | `kb_id` | VARCHAR(36) | NOT NULL | 知识库 ID（无 FK，手动级联，与现有表一致） |
| 3 | `model_type` | VARCHAR(20) | NOT NULL | 模型类型 |
| 4 | `model_config_id` | VARCHAR(36) | NOT NULL | 绑定的模型配置 ID |
| 5 | `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | |
| 6 | `updated_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | |

**DDL：**

```sql
CREATE TABLE kb_model_bindings (
    id              VARCHAR(36) PRIMARY KEY,
    kb_id           VARCHAR(36) NOT NULL,
    model_type      VARCHAR(20) NOT NULL,
    model_config_id VARCHAR(36) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_kb_bindings_kb_type UNIQUE (kb_id, model_type)
);

CREATE INDEX idx_kb_bindings_config ON kb_model_bindings (model_config_id);

CREATE TRIGGER trg_kb_model_bindings_updated_at
    BEFORE UPDATE ON kb_model_bindings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
```

> 遵循项目"无外键约束"约定（见 [0005_drop_all_fks.py](../backend/alembic/versions/0005_drop_all_fks.py)），删除知识库时在 `kb_service` 手动级联删除绑定行；删除模型配置时校验是否仍被绑定/设为默认。

### 2.3 `params` JSONB 定义（按 `model_type`）

| 类型 | params 示例 | 说明 |
|------|-------------|------|
| `llm` | `{"temperature": 0.1, "max_tokens": null, "context_window": 4096, "top_p": 1.0}` | 生成参数 |
| `embedding` | `{"dimension": 1024, "chunk_size": 512, "chunk_overlap": 64, "normalize": true}` | `dimension` 必填，决定 Milvus 维度 |
| `rerank` | `{"top_n": 3}` | 重排后保留条数 |
| `vision` | `{"temperature": 0.1, "max_tokens": 1024, "detail": "auto"}` | 多模态生成参数 |
| `ocr` | `{"lang": "ch", "backend": "paddle"}` | `backend`: `paddle`（本地）/ `aliyun`（云）等 |

> **清理说明**：旧 `llm_configs` 上的 `top_k` / `similarity_threshold` 属于检索参数，与模型无关，**不迁入**新表，继续由全局 `settings.retrieval_top_k` / `retrieval_similarity_threshold` 及 `SearchRequest` 控制（按知识库调检索参数列为范围外）。

### 2.4 旧表处理

迁移 0008 中：
1. 将 `llm_configs` 各行的 `provider/model/api_key/api_base/temperature/max_tokens/context_window` 映射为 `model_type='llm'` 的 `model_configs` 行（`params` 收 temperature/max_tokens/context_window），`is_active=TRUE` → `is_default=TRUE`。
2. 将 `embedding_configs` 各行映射为 `model_type='embedding'` 行（`params` 收 dimension/chunk_size/chunk_overlap）。
3. `DROP TABLE` 旧表 `llm_configs`、`embedding_configs` 及其索引/触发器/约束。

---

## 3. 解析与工厂设计

### 3.1 模型解析（核心）

```
resolve_model_config(session, kb_id, model_type) -> ModelConfig | None:
    1. 查 kb_model_bindings WHERE kb_id=? AND model_type=?
       命中 → 返回绑定的 model_config
    2. 未命中 → 查 model_configs WHERE model_type=? AND is_default=TRUE
       命中 → 返回全局默认
    3. 都没有：
       - llm / embedding → 抛错（必需，无默认则无法工作）
       - rerank / vision / ocr → 返回 None（调用方降级/跳过）
```

### 3.2 工厂分派

将 `llm_factory.py` 扩展为 `core/model_factory.py`，保留原函数名兼容：

| 函数 | 输入 | 返回 | 用途 |
|------|------|------|------|
| `create_llm(config)` | llm config | `OpenAILike` | 问答生成（沿用现有实现） |
| `create_embedding(config)` | embedding config | `OpenAIEmbedding` | 索引 + 检索（`dimension` 取自 params） |
| `create_reranker(config)` | rerank config | rerank client | 检索后重排（预留，本期不接管线） |
| `create_vision_llm(config)` | vision config | 多模态 `OpenAILike` | 视觉理解（本期仅建模 + 工厂） |
| `create_ocr(config)` | ocr config | PaddleOCR / 云 OCR client | 文档图片识别 |

所有 client 构造是**同步**的；配置读取（`resolve_model_config`）是**异步**的。调用方在 async 上下文中先 resolve 出 config，再同步构造 client。

### 3.3 `api_key` 解析

`api_key` 支持两种写法，构造 client 前统一解析：
- 字面量：直接使用（如 `sk-xxxx`）。
- `${ENV_VAR}` 占位符：从环境变量解析（如 `${DEEPSEEK_API_KEY}`）。

> 真正的加密存储不在本期范围（沿用占位符约定）。

---

## 4. 改动范围

### 4.1 新建文件

```
backend/
├── models/model_config.py             # ModelConfig + KBModelBinding ORM
├── repositories/model_repository.py   # model_configs / kb_model_bindings CRUD + resolve
├── services/model_service.py          # 校验、互斥默认、绑定、锁定逻辑
├── routers/model.py                   # 模型配置 API
├── core/model_factory.py              # 5 类 client 工厂（取代 llm_factory）
└── alembic/versions/0008_model_config.py

frontend/
└── src/
    ├── views/ModelConfigView.vue      # 模型管理页
    └── api/model.js                   # API 封装
```

### 4.2 修改文件

| 文件 | 改动内容 |
|------|----------|
| `backend/models/__init__.py` | 移除 `LLMConfig`/`EmbeddingConfig`，导出 `ModelConfig`/`KBModelBinding` |
| `backend/models/config_model.py` | 删除（被 model_config.py 取代） |
| `backend/models/schemas.py` | 新增 ModelConfig / KBModelBinding 相关 Pydantic Schema |
| `backend/core/llm_factory.py` | 删除或改为 re-export `model_factory` |
| `backend/core/retrieval.py` | `_get_vector_store` 接收 `dim`；`retrieve()` 按 KB resolve embedding |
| `backend/core/indexing.py` | `build_index`/`insert_single_vector`/`_get_vector_store` 使用 resolve 出的 embedding + dim |
| `backend/core/parser.py` | `_get_ocr()` 改为按 OCR config 分派（本地/云） |
| `backend/services/chat_service.py` | `create_llm(settings)` → 按 kb_id resolve llm config |
| `backend/services/kb_service.py` | 删除 KB 时级联删除绑定；新增绑定读写 |
| `backend/routers/knowledge_base.py` | 新增 KB 模型绑定端点 |
| `backend/main.py` | 注册 `model` 路由 |
| `frontend/src/router/index.js` | 新增 `/models` 路由 |
| `frontend/src/components/AppLayout.vue` | 侧边栏新增"模型管理"菜单 |
| `frontend/src/views/KnowledgeBaseView.vue` | KB 行/详情加"模型"配置入口 |

---

## 5. 实现步骤

### Step 1: 数据库迁移 → 验证: 迁移成功 + 新表有默认数据
1. 写 `0008_model_config.py`：建 `model_configs` + `kb_model_bindings`，迁移旧表数据，drop 旧表。
2. 建 `models/model_config.py`，更新 `models/__init__.py`，删 `models/config_model.py`。
3. `uv run alembic upgrade head` + psql 验证两张新表与默认数据。

### Step 2: Schema + Repository + Factory → 验证: 单元测试
1. `schemas.py` 新增：`ModelConfigCreate/Update/Response`、`KBModelBindingSet`、`KBModelsResponse`。
2. `model_repository.py`：CRUD + `resolve(kb_id, model_type)` + `get_default(model_type)` + 默认互斥设置。
3. `core/model_factory.py`：5 类 `create_*` 工厂 + `resolve_api_key`。
4. 单测：resolve 优先级、默认互斥、api_key 占位符解析。

### Step 3: Service + Router → 验证: Swagger 可调用
1. `model_service.py`：`set_default`（互斥）、`delete`（引用校验）、`bind`（embedding 锁定校验）。
2. `routers/model.py` + KB 绑定端点（见 §6）。
3. 更新 `main.py` 注册路由。

### Step 4: 接入检索/索引/问答 → 验证: 端到端问答 + 检索正常
1. `retrieval.py`：`_get_vector_store(kb_id, dim)`，`retrieve()` 内 resolve embedding。
2. `indexing.py`：`build_index`/`insert_single_vector` 用 resolve 的 embedding + dim。
3. `chat_service.py`：按 kb_id resolve llm。
4. `parser.py`：OCR 分派。

### Step 5: 前端管理页 + KB 绑定 → 验证: 页面 CRUD + 绑定生效
1. `api/model.js` + `ModelConfigView.vue`（模型管理：类型筛选、列表、创建/编辑对话框、设默认、连通性测试）。
2. `KnowledgeBaseView.vue` 加模型绑定入口（每类模型下拉：全局默认 / 指定模型 / 未绑定）。
3. 路由 + 侧边栏。

---

## 6. API 设计

### 6.1 模型配置 `/api/models`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/models?model_type=&platform=` | 列表（支持类型/平台筛选） |
| POST | `/api/models` | 创建 |
| GET | `/api/models/{id}` | 详情 |
| PUT | `/api/models/{id}` | 更新 |
| DELETE | `/api/models/{id}` | 删除（被绑定或为默认时拒绝） |
| POST | `/api/models/{id}/set-default` | 设为该类型全局默认（互斥） |
| POST | `/api/models/{id}/test` | 连通性测试（可选，加分项） |

### 6.2 知识库模型绑定

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/knowledge-bases/{kb_id}/models` | 返回各类型最终生效模型（含是否继承默认）+ 已绑定列表 |
| PUT | `/api/knowledge-bases/{kb_id}/models/{model_type}` | 绑定/解绑（body `{model_config_id: str\|null}`，null 回退默认） |

**绑定请求体：**

```json
{ "model_config_id": "uuid" }
```

**锁定校验：** 当 `model_type = 'embedding'` 且 KB `document_count > 0`，绑定/解绑/换绑返回 `409`，提示"该知识库已有文档，Embedding 模型已锁定"。

---

## 7. 关键逻辑伪代码

### 7.1 resolve 模型

```
async def resolve_model_config(session, kb_id, model_type):
    # 1. 绑定优先
    binding = await repo.get_binding(kb_id, model_type)
    if binding:
        return await repo.get(binding.model_config_id)

    # 2. 全局默认兜底
    default = await repo.get_default(model_type)
    if default:
        return default

    # 3. 必需类型无默认 → 报错
    if model_type in ("llm", "embedding"):
        raise ConfigError(f"未配置 {model_type} 默认模型")
    return None  # rerank/vision/ocr 可降级
```

### 7.2 Embedding 锁定校验

```
async def bind_model(kb_id, model_type, model_config_id):
    kb = await kb_repo.get_by_id(kb_id)
    # 只有 embedding 需要锁定
    if model_type == "embedding" and kb.document_count > 0:
        old = await repo.get_binding(kb_id, "embedding")
        if old is None or old.model_config_id != model_config_id:
            raise Conflict("知识库已有文档，Embedding 模型已锁定，禁止切换")
    # 空库或无变化 → 直接 upsert 绑定
    await repo.upsert_binding(kb_id, model_type, model_config_id)
```

---

## 8. 前端页面设计

### 8.1 ModelConfigView.vue 布局

```
┌─────────────────────────────────────────────────────┐
│ 模型管理                                             │
├──────────┬──────────────────────────────────────────┤
│ 类型筛选 │  模型列表表格                              │
│          │  ┌───────────────────────────────────┐   │
│ ● 全部   │  │ 名称 │ 类型 │ 平台 │ 模型 │ 默认 │ 操作│   │
│ ○ 语言   │  │ ...  │ ...  │ ...  │ ...  │ ★  │ 编辑│   │
│ ○ 向量   │  └───────────────────────────────────┘   │
│ ○ 排序   │                                           │
│ ○ 视觉   │                                           │
│ ○ OCR   │  [+ 新建模型]                              │
└──────────┴──────────────────────────────────────────┘
```

### 8.2 创建/编辑对话框

- 名称、类型（下拉 5 类）、平台（下拉 + 可自定义）、模型名、API Base、API Key（密码框 + `${ENV}` 提示）
- 按类型动态渲染参数表单（llm→temperature 等；embedding→dimension/chunk_size/chunk_overlap；rerank→top_n；ocr→lang/backend）
- 连通性测试按钮

### 8.3 知识库模型绑定

- 在 `KnowledgeBaseView` 每行或详情加"模型"按钮 → 弹窗
- 弹窗内 5 类模型各一行：下拉展示"继承全局默认（DeepSeek V3）"或已绑定模型；选择某模型 = 绑定，选"默认" = 解绑
- Embedding 行在有文档时置灰并提示"已锁定"

---

## 9. 验证方式

### 9.1 端到端验证

```bash
cd backend && uv run alembic upgrade head
docker exec bid_postgres psql -U postgres -d ai_bid_rag -c "\d model_configs"
docker exec bid_postgres psql -U postgres -d ai_bid_rag -c "SELECT name, model_type, is_default FROM model_configs;"

uv run uvicorn main:app --port 8000 --reload
curl http://localhost:8000/api/models          # 应返回迁移来的默认 LLM + Embedding
curl http://localhost:8000/api/docs            # 看到 model 分组

# 验证绑定与锁定
curl -X PUT http://localhost:8000/api/knowledge-bases/{kb_id}/models/embedding \
  -H "Content-Type: application/json" -d '{"model_config_id": "另一个embedding"}'
# → 已有文档的 KB 应返回 409
```

### 9.2 核心验证项

| 序号 | 检查项 | 方式 |
|------|--------|------|
| 1 | 迁移建表 + 默认数据，旧表已删 | psql |
| 2 | 模型 CRUD 全端点正常 | curl / Swagger |
| 3 | 设默认互斥（设 B 为默认后 A 非默认） | API + psql |
| 4 | 绑定优先于默认（绑定后 resolve 返回绑定模型） | 单测 |
| 5 | 删除被绑定/默认模型被拒绝 | API 报错 |
| 6 | 有文档 KB 切换 embedding 返回 409 | API |
| 7 | 问答/检索在 DB 默认模型下仍正常 | SSE 问答 + 检索 |
| 8 | 换 LLM 默认模型后问答用新模型 | 改默认 → 问答验证 |
| 9 | 前端模型管理页 + KB 绑定弹窗正常 | 手动操作 |

---

## 10. 不做什么（本次范围外）

- ❌ 真正的 api_key 加密存储（沿用 `${ENV}` 占位符约定）
- ❌ vision 模型的运行时调用点（本期仅建模 + 工厂，待有具体视觉用例再接入）
- ❌ rerank 模型的运行时接入检索管线（本期仅建模 + 工厂，同 vision）
- ❌ 云 OCR 的具体接入（仅预留 `backend` 分派，本地 PaddleOCR 仍为默认）
- ❌ 按知识库配置检索参数（top_k / similarity_threshold 仍走全局）
- ❌ 模型配置的版本管理 / 导入导出
- ❌ 按知识库绑定 Prompt（已在上期范围外，见 [07-prompt-management-plan.md](07-prompt-management-plan.md)）
