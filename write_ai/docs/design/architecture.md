# WriteAI 技术设计（MVP）

> 版本：v1.0（2026-08-26）｜状态：待评审

## 1. 技术栈

| 层 | 选型 | 说明 |
| --- | --- | --- |
| 语言 | TypeScript（strict） | 前后端同栈 |
| 后端 | Express 4 + Node 内置 http | 简单稳定，单进程 |
| 前端 | Vite + React 18 + react-router-dom | 桌面浏览器优先 |
| 数据库 | SQLite（better-sqlite3）+ Markdown 文件 | 单文件、零运维 |
| LLM | openai SDK（自定义 baseURL，兼容任意 OpenAI 接口） | 经 LLMProvider 接口隔离 |
| 搜索 | Tavily REST（可选） | 经 SearchProvider 接口隔离 |
| 测试 | Vitest + supertest | 服务端单测与接口测试 |

## 2. 模块划分与目录结构

```
write_ai/
├─ package.json / tsconfig.base.json / .env.example / .gitignore / README.md
├─ docs/                        # 设计文档（见 docs/README.md 索引）
├─ data/                        # 运行时数据（gitignore）: writeai.db, articles/, knowledge/, config
├─ server/
│  ├─ package.json / tsconfig.json
│  └─ src/
│     ├─ index.ts               # Express 启动、静态托管、错误处理
│     ├─ config.ts              # env + settings 表合并读取
│     ├─ api/                   # 路由薄层: projects.ts / settings.ts / knowledge.ts / health.ts
│     ├─ core/
│     │  ├─ pipeline.ts         # 阶段顺序与审核点定义（纯数据）
│     │  └─ orchestrator.ts     # 状态机：start/advance/runStage/approve/retry
│     ├─ agents/
│     │  ├─ types.ts            # Agent 接口与 AgentContext
│     │  ├─ topic.ts / research.ts / outline.ts / writer.ts / reviewer.ts
│     ├─ llm/
│     │  ├─ types.ts            # LLMProvider 接口
│     │  ├─ openai.ts           # OpenAI 兼容实现
│     │  └─ mock.ts             # Mock 实现（无 Key 验证与测试）
│     ├─ rag/
│     │  ├─ indexer.ts          # 知识文档解析、分块、FTS5 索引
│     │  ├─ retriever.ts        # 检索
│     │  ├─ search.ts           # SearchProvider 接口 + Tavily 实现
│     │  └─ types.ts
│     └─ store/
│        ├─ db.ts               # 建表 + user_version 迁移
│        ├─ projects.ts / stages.ts / artifacts.ts / settings.ts / knowledge.ts
│        └─ files.ts            # Markdown 文件读写（镜像 artifacts）
├─ web/
│  ├─ package.json / vite.config.ts / index.html
│  └─ src/
│     ├─ main.tsx / App.tsx / api.ts（fetch 封装）
│     ├─ pages/ ProjectsPage.tsx / ProjectDetailPage.tsx / SettingsPage.tsx
│     └─ components/ ProjectList.tsx / StageTimeline.tsx / ArtifactViewer.tsx / ReviewPanel.tsx / Toast.tsx
└─ tests/                       # 服务端测试（vitest）
```

职责边界：

- `core`：领域核心，定义流水线与状态机，不依赖 HTTP 与具体 LLM 实现。
- `agents`：角色 Agent，实现统一 `Agent` 接口：输入项目上下文 + 历史产物 + 检索结果，输出产物。
- `llm` / `rag`：基础设施，通过接口注入 core 与 agents。
- `store`：持久化，提供仓储 API。
- `api`：REST 薄层，做参数校验后调用 core。
- `web`：展示与人工交互，轮询拉取状态。

## 3. 数据流

```
用户创建项目(API) → orchestrator.start() 建立五阶段记录
→ advance() 依次执行 runStage(stage)
→ runStage: 构造 AgentContext(项目信息、前置产物、rag.retrieve(主题)、search.query(主题))
→ agent.run(ctx) → 产物写入 artifacts 表 + Markdown 文件
→ 阶段状态推进：审核点 → review（暂停等人工）；否则 done
→ 全部 done → 项目 finished
前端每 2s 轮询 GET /api/projects/:id 刷新阶段状态；审核点展示产物与操作按钮
```

## 4. 存储

- SQLite `data/writeai.db`：projects / stages / artifacts / agent_runs / settings / knowledge_chunks（含 FTS5 虚拟表）。详见 [database.md](database.md)。
- 文件 `data/articles/<projectId>/`：各阶段产物 Markdown（outline.md / draft.md / final.md / round-N.md）与 project.json 快照，便于用户直接用编辑器打开。
- 知识库 `data/knowledge/**`（md/txt）：启动时增量索引，上传接口实时索引。

## 5. 关键流程：阶段状态机

阶段状态：`pending → running → review | done | failed`；`review →(approve)→ done`；`failed →(retry)→ running`。
项目状态：`draft | running | review | finished | failed`（任一阶段 review 则项目为 review）。

伪代码：

```
start(projectId):
  ensureStages(projectId, PIPELINE)          // 按 pipeline 定义建 pending 阶段
  advance(projectId)

advance(projectId):
  stage = nextStage(projectId, ['pending','review'])
  if stage: runStage(stage)
  else: project.status = 'finished'

runStage(stage):
  stage.status = 'running'; persist()
  try:
    agent = resolveAgent(stage.kind)
    ctx = buildContext(project, prevArtifacts(stage), rag.retrieve, search.query)
    artifact = await agent.run(ctx)
    persistArtifact(stage, artifact)         // DB + Markdown 文件
    stage.status = stage.kind in REVIEW_POINTS ? 'review' : 'done'
  catch e:
    stage.status = 'failed'; stage.error = e.message
  advance(projectId)                         // 若刚置为 review 则自然停在人工审核点

approve(stageId, edits):                     // 人工审核
  applyEdits(stageId, edits)                 // 有修改则更新产物
  stage.status = 'done'
  advance(projectId)

retry(stageId):
  stage.status = 'running'                   // 仅允许 failed 阶段
  runStage(stage)
```

流水线定义（pipeline.ts）：

```
PIPELINE = [
  { kind: 'topic',    label: '选题分析' },
  { kind: 'research', label: '资料搜集' },
  { kind: 'outline',  label: '大纲生成', review: true },   // 审核点 1
  { kind: 'draft',    label: '分节撰写' },
  { kind: 'review',   label: '审校润色', review: true },   // 审核点 2
]
```

审校多轮（reviewer agent）：

```
reviewRound(project, draft, round):
  report = reviewer.run({ draft, round })
  if report.score >= PASS_SCORE: 结束（产物：final）
  else: draft = report.revisedDraft；记录 artifact(round)
  达到 REVIEW_ROUNDS 上限则结束
人工可在审校阶段调用 review-more 追加一轮
```

## 6. 外部依赖与配置

设置来源：`.env`（基础）+ settings 表（界面可改，优先于 env）。

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| LLM_BASE_URL | https://api.deepseek.com | OpenAI 兼容端点 |
| LLM_API_KEY | - | 必填（除 Mock 模式） |
| LLM_MODEL | deepseek-chat | 模型名 |
| TAVILY_API_KEY | - | 可选；缺失则跳过联网检索 |
| REVIEW_ROUNDS | 2 | 审校最大轮数 |
| KNOWLEDGE_DIR | data/knowledge | 知识库目录 |
| PORT | 3000 | 服务端口（仅 127.0.0.1） |
| LLM_MOCK | 0 | 1 时使用 MockProvider |

## 7. 异常策略

- LLM 调用：超时 120s；失败重试 2 次（退避 1s/3s）；仍失败 → 阶段 failed，界面提供重试。
- 知识库解析失败：跳过该文件并记录日志，不影响流水线。
- 搜索 Key 缺失 / 搜索失败：跳过联网检索，仅本地检索，产物 meta 注明。
- 项目/阶段不存在 → 404；非法状态转移（如重复 start、approve 非 review 阶段）→ 409。
- 单机单用户，不做并发控制与冲突处理。

## 8. 部署与运行

- 开发：`npm run dev`（concurrently 启动 Vite 5173 + API 3000，Vite proxy `/api` → 3000）。
- 生产：`npm run build` 后由 server 同端口静态托管 `web/dist` 并提供 API。
- 服务仅监听 127.0.0.1。

## 9. 扩展点（由当前需求驱动）

- `Agent` 接口：新增角色 = 新增一个 Agent 实现 + pipeline 追加一项。
- `LLMProvider` / `SearchProvider` 接口：可换实现（Mock 实现本身即是接口合理性的证据）。
- 审校轮数可配置、可追加轮次，为「多轮」提供真实扩展需求。
- 后续版本扩展点：流式输出、PDF/DOCX 解析、SSE 推送，均在当前模块边界内可加。
