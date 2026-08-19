# 模型启用/停用与启动自检方案

> 版本: v1.0 | 日期: 2026-08-19 | 状态: 开发中
> 关联: [08-model-config-plan.md](08-model-config-plan.md)

## 1. Context

`model_configs` 目前只有 `is_default`（该类型全局默认），模型一经创建即参与解析与绑定，
没有"停用"手段。需求（用户确认）：

- 模型配置新增**启用/停用**开关：停用的模型不参与解析（不选作默认、不可绑定、运行时跳过）。
- 应用**启动时对启用状态的模型做连接自检**，结果记录日志（失败不阻断启动）。

## 2. 数据模型

`model_configs` 新增：

```sql
ALTER TABLE model_configs
    ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE;
```

- `is_active = TRUE`：启用（默认），参与解析与启动自检。
- `is_active = FALSE`：停用，运行时不可用。

## 3. 解析逻辑（core/model_factory.py）

`resolve_model_config(kb_id, model_type)` 调整：

```
1. 绑定优先：命中 kb_model_bindings → 取配置
   若绑定配置已停用（is_active=False）→ 视为未命中，继续第 2 步
2. 全局默认兜底：只查 is_active=True 且 is_default=True
3. 都没有：llm/embedding 抛错；rerank/vision/ocr 返回 None
```

## 4. 模型服务（services/model_service.py）

| 函数 | 改动 |
| --- | --- |
| `_to_dict` | 响应增加 `is_active` |
| `create_model` / `update_model` | 支持 `is_active`；`update_model` 将默认模型停用时同步取消其默认 |
| `set_default` | 仅允许激活模型设为默认（停用模型返回错误） |
| `bind_model` | 仅允许绑定激活模型 |
| `startup_model_check`（新增） | 查询所有 `is_active=True` 的模型，并发调用 `test_model` 自检，汇总成功/失败并记录日志（失败仅 warning，不抛出） |

`delete_model`：现有"默认不可删除"逻辑不变。

## 5. 启动自检（main.py lifespan）

- `setup_logging` / `setup_langfuse` 之后、`parse_queue.start_worker` 之前调用 `startup_model_check()`。
- 并发执行所有启用模型的自检，总超时 15 秒（`asyncio.wait_for(gather, 15)`），
  超时未返回的按失败记录。
- 失败只记 warning，不阻断应用启动。

## 6. 接口与前端

- `ModelConfigCreate/Update/Response` 增加 `is_active`。
- `ModelConfigView.vue`：
  - 列表新增"状态"列：`el-switch` 直接切换启用/停用（调 PUT 更新）。
  - 创建/编辑对话框新增"启用"开关（默认开）。

## 7. 测试方案

- `test_model_service.py`：
  - create/update 带 `is_active` 落库与返回。
  - `set_default` 拒绝停用模型。
  - `bind_model` 拒绝绑定停用模型。
  - `update_model` 停用默认模型时同步取消默认。
  - `startup_model_check`：mock `test_model`，验证并发调用、成功/失败汇总、失败不抛出。
- resolve 逻辑测试：停用绑定模型回退默认、停用默认不被解析。
- 前端 `npm run build`。

## 8. 本期不做

- 启动自检的告警/通知（邮件、Webhook）。
- 停用模型的历史数据清理。
- 按环境（开发/生产）差异化自检配置。
