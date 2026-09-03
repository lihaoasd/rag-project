# 37 main.py Neo4j 模块级调用修复方案

> 日期：2026-09-01
> 类型：bug 修复
> 关联：`docs/09-graph-module-design.md`、`docs/plan/33-graph-module-plan.md`、`docs/changes.md`（2026-08-24 段）

## 1. 问题

启动报错：`module 'core.neo4j_store' has no attribute 'ensure_indexes'`（`GRAPH_ENABLED=true` 时触发）。

## 2. 复现与根因

复现（`backend/` 下）：

```python
from core import neo4j_store
neo4j_store.ensure_indexes   # AttributeError: module 'core.neo4j_store' has no attribute 'ensure_indexes'
neo4j_store.close            # 同
```

根因：`core/neo4j_store.py` 将 `ensure_indexes`/`close` 定义为 **`Neo4jStore` 类的 classmethod**（模块级仅暴露 `GraphDisabledError`、`Neo4jStore` 类）；而 `main.py` 以 **模块级** `neo4j_store.ensure_indexes()`（lifespan 启动，line 81）与 `neo4j_store.close()`（lifespan 关闭，line 121）调用，属性解析失败。

同类问题 2026-08-24 已修复过一处（`main.py` health 误用模块级 `health_check` → 改为 `Neo4jStore.health_check()`，见 `docs/changes.md`），但 `ensure_indexes`（仅 `graph_enabled=True` 时执行且被 try/except 吞为 warning）与 `close`（需优雅关闭才触发）两处漏改。

## 3. 改动范围

仅 `backend/main.py`：

1. line 12：`from core import es_store, graph_queue, neo4j_store, parse_queue` → 去掉 `neo4j_store`；
   新增 `from core.neo4j_store import Neo4jStore`（放同组导入）。
2. line 81：`await neo4j_store.ensure_indexes()` → `await Neo4jStore.ensure_indexes()`。
3. line 121：`await neo4j_store.close()` → `await Neo4jStore.close()`。
4. health_check（line 176-179）：删除函数内局部 `from core.neo4j_store import Neo4jStore`，直接用顶部导入。

不改 `core/neo4j_store.py`（类 API 是本模块及 graph_service/routers 的统一用法，`Neo4jStore` 类内部已自带 `graph_enabled` 守卫）。

## 4. 回归测试

`tests/test_health.py` 新增（main 模块级属性在修复前不存在，修复后存在，直接守卫修复点）：

```python
def test_main_neo4j_uses_class_api(self):
    """回归：main.py 生命周期曾以模块级 neo4j_store.ensure_indexes/close 调用
    （实为 Neo4jStore 类 classmethod），GRAPH_ENABLED=true 启动/关闭报 AttributeError。"""
    import asyncio
    import main
    from core.neo4j_store import Neo4jStore

    assert main.Neo4jStore is Neo4jStore
    assert asyncio.iscoroutinefunction(Neo4jStore.ensure_indexes)
    assert asyncio.iscoroutinefunction(Neo4jStore.close)
```

## 5. 验证方式

1. 修复前先跑回归用例 → 失败（`main` 无 `Neo4jStore` 属性）；
2. 修复后跑回归用例 → 通过；
3. 后端相关测试全量通过：`pytest tests/test_health.py tests/test_graph_service.py tests/test_graph_router.py`；
4. 复现脚本确认：`Neo4jStore.ensure_indexes/close` 可解析为协程函数。

## 6. 修改记录

修复后追加至 `docs/changes.md`（2026-09-01 新段）。
