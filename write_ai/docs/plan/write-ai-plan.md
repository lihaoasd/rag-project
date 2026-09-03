# WriteAI 开发方案（MVP）

> 版本：v1.0（2026-08-26）｜状态：**待评审**
> 关联：需求 [docs/requirements/overview.md](../requirements/overview.md) · 技术 [docs/design/architecture.md](../design/architecture.md) · 数据 [docs/design/database.md](../design/database.md) · API [docs/api/api.md](../api/api.md)

## 1. 改动范围

全新项目（工作区当前为空）。产出：

- `server/`：Express + TypeScript 后端（core / agents / llm / rag / store / api）。
- `web/`：Vite + React 前端。
- `data/`：运行时目录（gitignore）。
- 根：package.json（npm workspaces）、tsconfig.base.json、.env.example、.gitignore、README.md。
- `docs/`：需求/设计/API/前端/方案/修改记录。

## 2. 实现步骤

每步完成即验证，验证不通过不进入下一步。

| # | 内容 | 验证方式 |
| --- | --- | --- |
| 0 | 骨架：workspaces、tsconfig、Express 最小服务 + /health、Vite 空壳 | `npm run build` 通过；`npm run dev` 后 curl /health 返回 ok |
| 1 | 存储层：db.ts（建表 + user_version）+ 各仓储 + files.ts | vitest：CRUD、级联删除、FTS 建表 |
| 2 | LLM：LLMProvider 接口 + OpenAI 兼容实现 + MockProvider | vitest：Mock 注入断言调用参数与返回 |
| 3 | 流水线核心：pipeline 定义 + orchestrator 状态机 + 五个 Agent | vitest：全流程状态迁移、审核点暂停/approve 继续、失败重试、审校多轮 |
| 4 | RAG：indexer（md/txt 解析分块 + FTS 索引）+ retriever + TavilyProvider | vitest：分块边界、检索命中种子文档；无 Key 时降级 |
| 5 | API 层：projects / stages / settings / knowledge 路由 + 校验与错误码 | supertest：主要接口 + 404/409 用例 |
| 6 | 前端：列表 / 详情（时间线+产物+审核面板）/ 设置 / 知识库 | vite build 通过；手工验收清单逐项勾选 |
| 7 | 收尾：Mock 端到端演示、README、修改记录 | `npm test` 全绿；演示脚本走通全流程导出 Markdown |

## 3. 关键逻辑伪代码

### Agent 接口（agents/types.ts）

```
interface Agent {
  kind: StageKind
  run(ctx: AgentContext): Promise<Artifact>
  // ctx = { project, prevArtifacts, retrievedChunks[], searchResults[], round? }
}
```

```
run(ctx):
  prompt = buildPrompt(role=this.kind, project, prevArtifacts, chunks, search)
  text = llm.chat(system=this.systemPrompt, user=prompt, temperature)
  return { kind: this.kind, title, content: text, meta: { sources } }
```

### orchestrator 状态机

见 architecture.md §5（start / advance / runStage / approve / retry），此处补充约束：

```
ensureStages: 按 PIPELINE 顺序建 pending 阶段（已存在则跳过）
nextStage: 取 seq 最小且 status in ('pending','review') 的阶段
runStage 结束：审核点 → 'review'（项目 status='review'）；否则 'done'
advance 递归出口：无 pending/review 阶段 → 项目 'finished'
```

### 审校多轮（reviewer）

```
reviewRound(project):
  draft = 最新 draft 产物
  for round = 1..REVIEW_ROUNDS:
    report = reviewer.run({ draft, round })            // { score, issues[], revisedDraft }
    记录 artifact(kind='review', round, content=报告)
    if score >= 80: draft = revisedDraft; break
    draft = revisedDraft; 记录 artifact(kind='revision', round)
  写 final 产物（kind='final'）
人工 review-more：round 续号 +1 再跑一轮
```

### RAG 分块与检索

```
indexDoc(path):
  text = read(md|txt)                                  // 解析失败 → skip 并记录
  blocks = split(text)                                 // 按空行/标题分块，块≤800字，重叠 100 字
  upsert chunks (doc_name, chunk_index, content, mtime)；重建对应 FTS 行

retrieve(query, k=6):
  terms = query.split(/\s+/).map(escapeFts).join(' AND ')
  rows = FTS MATCH terms ORDER BY rank LIMIT k
  return rows
```

### API 校验要点

- POST /projects：title/topic 必填非空。
- approve：阶段必须 status='review'，否则 409。
- retry：阶段必须 status='failed'，否则 409。
- PUT /settings：reviewRounds ∈ [1,10]；apiKey 字段留空视为不修改。

## 4. 验收方式汇总

1. `npm test`（vitest + supertest）全绿。
2. `LLM_MOCK=1 npm run dev`：浏览器创建项目 → 跑通五阶段 → 两个审核点暂停/批准 → 导出 Markdown 成功。
3. 配置真实 DeepSeek Key 冒烟一条示例文章（成功标准 2）。
4. 种子文档放入知识库后，资料阶段产物出现引用内容（成功标准 4）。
5. 修改记录与本次 diff 一一对应。

## 5. 风险与对策

| 风险 | 对策 |
| --- | --- |
| better-sqlite3 原生模块在 Windows 编译失败 | 优先用官方预编译二进制；失败则回退 Node 24 内置 `node:sqlite`（接口相近，仓储层隔离） |
| openai SDK 与兼容接口差异 | 官方 SDK + baseURL；Step 2 用真实 Key 冒烟确认 |
| Tavily 无 Key / 不可用 | SearchProvider 降级：跳过联网检索并在产物 meta 注明，不影响主流程 |
| 长文生成 token 超限 | 撰写按大纲分节调用、逐节拼接；单节超长时提示截断并记录 |

## 6. 依赖清单（MVP）

- server：express、better-sqlite3、openai、uuid、multer（上传）、vitest、supertest、tsx/tsc。
- web：react、react-dom、react-router-dom、marked、dompurify、vite、@vitejs/plugin-react。
- 根：concurrently、typescript、@types/*。
