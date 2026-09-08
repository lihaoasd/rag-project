# 46 知识库文档数量改为“行数口径” — 方案

> 状态：已实现（2026-09-08）　|　类型：需求变更（计数口径修正）　|　关联：43/44/45　|　日期：2026-09-08
> 评审：用户通过提问选择「改成"文档行数"口径（Recommended）」，2026-09-08。

## 背景 / 问题

KB 列表 `document_count` 与「知识库内文档列表行数」不一致（实测：投标库 count=1，行数=3：
completed=2 + failed=1）。根因是旧口径「成功解析才算」：
- 上传/失败/partial/processing 行不入计数 → 列表可见但计数缺失；
- `parse_document` completed **无条件 +1**、`resume_document` 仅 chunk_count 为空才 +1、`delete_document` **无条件 -1** →
  历史规则变更/删除未计数文档/重跑后计数漂移，字段非实时对账，纯事件累加会失真。

## 目标口径（用户确认）

`knowledge_bases.document_count` = 该库 `documents` 表实际行数（所有状态）。

- 上传创建文档行 → +1；
- 删除文档行 → -1（原逻辑保留，无条件扣减与新口径一致）；
- 解析 / 续跑 / 重切片 / 重解析 → 不再加减（同一行永远只计一次）。

## 改动范围与步骤

| 文件 | 改动 |
|---|---|
| `backend/services/document_service.py` | `upload_document` 创建行后同会话 `kb_repo.increment_doc_count`（失败仅记日志不阻断上传）；`parse_document` 删除 completed 后无条件 +1；`resume_document` 删除 was_never_completed / +1；docstring 同步 |
| `backend/tests/test_document_service.py` | TestResumeDocument 成功用例：+1 改为「不调用 increment_doc_count」 |
| 数据库（存量一次性对账，需执行） | `UPDATE knowledge_bases k SET document_count = (SELECT count(*) FROM documents d WHERE d.kb_id = k.id)` |

不新增迁移（knowledge_bases 无结构变化），对账直接 UPDATE。

## 关键伪代码

```
upload_document:
    doc = repo.create(...)                    # 提交
    kb_repo.increment_doc_count(kb_id)        # 同会话；异常仅 logger.error
    return {...}

parse_document / resume_document:
    update_status(completed, ...)             # 不再触碰 document_count
    # 删除 kb_repo.increment_doc_count 分支

delete_document:
    ... 清理 ...
    kb_repo.decrement_doc_count(kb_id)        # 原逻辑保留
```

## 验证

1. 单测：TestResumeDocument 成功用例断言 increment 不再被调用；上传路径断言 +1（补/改用例）。
2. `pytest tests/test_document_service.py tests/test_document_repository.py …` → 相关模块通过；全量回归 ≥ 732。
3. 对账 SQL 在目标库执行后：`kb.document_count == count(documents)` 逐库成立。
4. 此后上传/删除各 +1/-1，解析重跑不影响 → 计数恒等于行数。
