# 词库管理页：去掉「状态」相关 UI

## 需求

词库管理页（`LexiconView.vue`）去掉页面上的「状态」：表格「状态」列（启用/停用开关）与新增/编辑弹窗的「启用」开关（用户已确认全部去掉）。后端 `enabled` 字段与接口保持不变（新增词条默认启用）。

## 改动范围

| 文件 | 改动 |
| --- | --- |
| `frontend/src/views/LexiconView.vue` | 删表格「状态」列、删「启用」表单项、删 `toggleEnabled` 方法、`form`/`resetForm`/`openEdit` 去掉 `enabled` 字段 |
| `docs/changes.md` | 追加修改记录 |

后端不改（`LexiconTermCreate.enabled` 默认 True，`LexiconTermUpdate.enabled` 可空）。

## 验证方式

前端 `npm run build` 通过；词条新增/编辑/列表正常（后端测试不受影响）。
