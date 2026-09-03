# WriteAI 数据库设计（MVP）

> 版本：v1.0（2026-08-26）

SQLite 单文件 `data/writeai.db`，better-sqlite3 同步 API。外键开启；时间统一存 ISO 字符串（UTC）；ID 使用 uuid。

## 表结构

### projects

| 列 | 类型 | 说明 |
| --- | --- | --- |
| id | TEXT PK | uuid |
| title | TEXT NOT NULL | 文章标题 |
| topic | TEXT NOT NULL | 写作主题 |
| requirements | TEXT | 写作要求（受众/篇幅/风格/约束） |
| status | TEXT NOT NULL DEFAULT 'draft' | draft\|running\|review\|finished\|failed |
| created_at / updated_at | TEXT | |

### stages

| 列 | 类型 | 说明 |
| --- | --- | --- |
| id | TEXT PK | |
| project_id | TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE | |
| kind | TEXT NOT NULL | topic\|research\|outline\|draft\|review |
| seq | INTEGER NOT NULL | 流水线顺序 |
| status | TEXT NOT NULL DEFAULT 'pending' | pending\|running\|review\|done\|failed |
| error | TEXT | 失败信息 |
| started_at / finished_at | TEXT | |

约束：`UNIQUE(project_id, kind)`；索引 `(project_id, seq)`。

### artifacts（阶段产物，审校轮次会产生多条）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| id | TEXT PK | |
| stage_id | TEXT NOT NULL REFERENCES stages(id) ON DELETE CASCADE | |
| kind | TEXT NOT NULL | analysis\|research\|outline\|draft\|review\|revision\|final |
| round | INTEGER DEFAULT 1 | 审校轮次 |
| title | TEXT | |
| content | TEXT NOT NULL | Markdown 正文 |
| meta | TEXT | JSON：引用来源、搜索说明、质量评分等 |
| created_at | TEXT | |

### agent_runs（LLM 调用记录，用于排查与计费统计）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| id | TEXT PK | |
| stage_id | TEXT REFERENCES stages(id) | |
| agent | TEXT | 角色 |
| model | TEXT | |
| input_tokens / output_tokens | INTEGER | |
| error | TEXT | |
| created_at | TEXT | |

### settings

| 列 | 类型 |
| --- | --- |
| key | TEXT PK |
| value | TEXT |

### knowledge_chunks（本地知识库分块）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| id | TEXT PK | |
| doc_name | TEXT NOT NULL | 相对知识库目录的路径 |
| chunk_index | INTEGER | 块序号 |
| content | TEXT NOT NULL | 分块内容 |
| doc_mtime | TEXT | 文件修改时间（增量索引依据） |

约束：`UNIQUE(doc_name, chunk_index)`。

### knowledge_fts（FTS5 虚拟表）

- schema：`CREATE VIRTUAL TABLE knowledge_fts USING fts5(content, tokenize='unicode61 remove_diacritics 2')`
- 行映射：`INSERT INTO knowledge_fts(rowid, content) VALUES (knowledge_chunks.id, content)`；删除块时同步删除 FTS 行。
- 检索：`SELECT kc.*, bm25(knowledge_fts) rank FROM knowledge_fts JOIN knowledge_chunks kc ON kc.id = knowledge_fts.rowid WHERE knowledge_fts MATCH ? ORDER BY rank LIMIT ?`；查询词按空白分词拼接 `AND` 词组（用户输入做转义）。

## 迁移策略

- `store/db.ts` 启动时执行 `CREATE TABLE IF NOT EXISTS`，并以 `PRAGMA user_version` 记录 schema 版本。
- 后续结构变更按版本号递增追加迁移函数，`user_version < n` 时依次执行。

## 文件存储（与 DB 互补）

- `data/articles/<projectId>/`：outline.md、draft.md、final.md、round-<n>.md、project.json（镜像 artifacts，便于直接打开阅读）。
- `data/knowledge/**`：用户放置知识文档（md/txt），与上传接口等价。
- Markdown 文件以 DB 为准的镜像副本：写 DB 时同步写文件；读时以 DB 为准。
