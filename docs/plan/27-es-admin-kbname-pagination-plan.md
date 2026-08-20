# ES 对账页：知识库名称显示 + 知识库对账表后端分页

## 需求

1. 「知识库对账」表（`EsAdminView.vue`）每行目前只显示 `kb_id`，需要同时显示知识库名称（`knowledge_bases.name`）。
2. 该表需要**后端接口分页**（用户已确认，非前端切片）。

边界（已与用户确认）：
- 名称只加在「知识库对账」表；「文档级差异」表不改。
- 分页走 `/api/es/status` 接口（page/page_size 参数），不是前端切片。

## 改动范围

| 文件 | 改动 |
| --- | --- |
| `backend/services/es_admin_service.py` | `reconcile_status(page, page_size)`：查询知识库名称映射；`kbs` 每行加 `kb_name`；`kbs` 按页切片；响应新增 `kb_total` |
| `backend/routers/es_admin.py` | `/api/es/status` 增加 `page`、`page_size` 查询参数（默认 page=1, page_size=20） |
| `frontend/src/api/esAdmin.js` | `getEsStatus(page, pageSize)` 透传分页参数 |
| `frontend/src/views/EsAdminView.vue` | 新增「知识库名称」列（名称为空时回退显示 kb_id）；表格下方加 `el-pagination`（total 用 `kb_total`）；翻页/改页大小触发重新请求 |
| `backend/tests/test_es_admin_service.py` | mock 补 `KnowledgeBaseRepository.list_all`；断言 `kb_name`；新增分页用例 |
| `backend/tests/test_es_admin_router.py` | 断言分页参数透传 |
| `docs/changes.md` | 追加修改记录 |

## 关键逻辑（伪代码）

```
# services/es_admin_service.py
async def reconcile_status(page=1, page_size=20):
    ... 现有 ES/PG 统计逻辑不变 ...
    kb_names = {kb.id: kb.name for kb in await KnowledgeBaseRepository(session).list_all()}
    kbs = [...原全量构建...]                      # 每行加 kb_name = kb_names.get(kb, "")
    differences = [非 ok 的 kbs]                  # 兼容字段，仍返回全量（前端未使用）
    kb_total = len(kbs)
    start = (page - 1) * page_size
    kbs = kbs[start : start + page_size]          # 后端切片
    return { ..., "kbs": kbs, "kb_total": kb_total, "diff_count": len(differences), ... }

# routers/es_admin.py
@router.get("/api/es/status")
async def es_status(page: int = 1, page_size: int = 20):
    return await es_admin_service.reconcile_status(page=page, page_size=page_size)

# frontend EsAdminView.vue
const page = ref(1), pageSize = ref(20)
loadStatus() → getEsStatus(page.value, pageSize.value)
:total = status.kb_total ?? 0
@current-change / @size-change → 更新 page/pageSize 并 loadStatus()
```

## 验证方式

1. 后端：`pytest backend/tests/test_es_admin_service.py backend/tests/test_es_admin_router.py` 通过；全量测试无回归。
2. 前端：`npm run build` 通过。
3. 手工（可选）：调用 `/api/es/status?page=1&page_size=2` 确认 `kbs` 只返回 2 条且含 `kb_name`、`kb_total` 为总数。
