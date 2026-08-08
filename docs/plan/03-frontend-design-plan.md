# 前端页面设计 — 开发方案

> 版本: v1.0 | 日期: 2026-08-07 | 状态: 已评审

---

## Context

当前前端是 Element Plus 骨架：4 个页面全是 `el-empty` 占位，侧边栏用硬编码色值，无统一主题变量。Phase 2（RAG 核心链路）开发在即，需要先把前端页面和主题定下来，让后续后端开发有明确的对齐目标。

---

## 改动范围

| 文件 | 操作 | 说明 |
|------|------|------|
| `frontend/src/style.css` | **重写** | 全局主题变量（CSS 变量）+ Element Plus 覆写 |
| `frontend/src/components/AppLayout.vue` | **修改** | 侧边栏样式升级，导航图标优化 |
| `frontend/src/views/KnowledgeBaseView.vue` | **重写** | 卡片网格 + 创建对话框 + 删除确认 |
| `frontend/src/views/DocumentListView.vue` | **重写** | 表格列表 + 上传对话框 + 状态标签 |
| `frontend/src/views/SearchView.vue` | **重写** | 搜索栏 + 知识库选择 + 结果列表 |
| `frontend/src/views/ChatView.vue` | **重写** | 聊天气泡 + 流式区域 + 来源引用 |
| `frontend/src/main.js` | **修改** | 注册中文语言包 + 额外图标 |

不涉及：router、API 封装、后端代码。

---

## 1. 全局主题系统

### 1.1 CSS 变量定义

```css
:root {
  /* 主色系 */
  --color-primary: #1E6FFF;
  --color-primary-light: #4D8FFF;
  --color-primary-dark: #0050D9;

  /* 功能色 */
  --color-success: #52C41A;
  --color-warning: #FAAD14;
  --color-danger: #FF4D4F;
  --color-info: #909399;

  /* 中性色 */
  --color-bg: #F0F2F5;
  --color-bg-white: #FFFFFF;
  --color-text-primary: #1D2129;
  --color-text-regular: #4E5969;
  --color-text-secondary: #86909C;
  --color-border: #E5E6EB;

  /* 侧边栏 */
  --sidebar-bg: #1D2330;
  --sidebar-text: #B0B8C8;
  --sidebar-active: #1E6FFF;
  --sidebar-width: 220px;

  /* 布局 */
  --header-height: 56px;
  --content-padding: 24px;
  --page-max-width: 1400px;

  /* 圆角 */
  --radius-sm: 4px;
  --radius-md: 8px;
  --radius-lg: 12px;

  /* 阴影 */
  --shadow-card: 0 1px 3px rgba(0, 0, 0, 0.06);
  --shadow-popup: 0 4px 16px rgba(0, 0, 0, 0.12);
}
```

### 1.2 Element Plus 主题覆写

通过 CSS 变量覆写 Element Plus 默认变量，保持组件风格一致。

---

## 2. AppLayout 改造

**现状问题**：侧边栏用硬编码 `#304156`，无菜单选中高亮样式。

**改造内容**：
- 侧边栏背景改为 `var(--sidebar-bg)`，Logo 区域添加渐变分割线
- 菜单项 hover/active 状态用主题蓝
- 顶栏添加系统状态指示（后续可用）

---

## 3. 四个页面设计

### 3.1 知识库管理页

**目标效果**：取代 `el-empty`，展示可以交互的卡片网格。

```
┌─────────────────────────────────────────────┐
│  知识库管理                  [+ 创建知识库]   │
│  管理文档知识库...                            │
├─────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐           │
│  │ 📁 2024投标  │  │ 📁 历史招标  │           │
│  │ 12 个文档    │  │ 5 个文档     │           │
│  │ 创建于...    │  │ 创建于...   │           │
│  │ [进入] [删除]│  │ [进入] [删除]│           │
│  └─────────────┘  └─────────────┘           │
└─────────────────────────────────────────────┘
```

**交互**：
- `el-card` 网格布局（3 列自适应）
- 点击「创建知识库」→ `el-dialog` 内含表单（名称 + 描述）
- 点击「删除」→ `el-popconfirm` 确认
- 点击卡片/「进入」→ 跳转到该知识库的文档列表页
- 空状态：`el-empty` 提示「暂无知识库，点击上方按钮创建」

**关键逻辑（伪代码）**：
```
state:
  kbList: []           // 知识库列表
  dialogVisible: false  // 创建对话框
  form: { name, description }

onMounted → GET /api/knowledge-bases → kbList

handleCreate:
  dialogVisible = true

handleSubmit:
  POST /api/knowledge-bases with form
  → 刷新 kbList → 关闭 dialog

handleDelete(id):
  DELETE /api/knowledge-bases/{id}
  → 刷新 kbList
```

### 3.2 文档管理页

**目标效果**：表格 + 上传区域。

```
┌─────────────────────────────────────────────┐
│  ← 返回  |  XX知识库 / 文档管理    [上传文档]  │
├─────────────────────────────────────────────┤
│  文件名          │大小  │状态    │时间  │操作   │
│  ──────────────────────────────────────────  │
│  投标书.pdf      │5MB  │✅ 就绪 │08-07│删除   │
│  招标文件.docx   │3MB  │🔄 解析中│... │删除  │
│  报价单.pdf      │8MB  │❌ 失败 │... │删除   │
├─────────────────────────────────────────────┤
│  共 3 个文档                                  │
│           [< 1 2 3 >]                        │
└─────────────────────────────────────────────┘

上传对话框：
┌─────────────────────────────┐
│        上传文档              │
│  ┌─────────────────────────┐│
│  │    拖拽文件到此处        ││
│  │  支持 PDF / Word 格式    ││
│  └─────────────────────────┘│
│         [选择文件]           │
└─────────────────────────────┘
```

**交互**：
- 从知识库列表点击进入，路由参数 `:kbId`
- 表格列：文件名、大小、状态标签、页数、上传时间、操作
- 状态用 `el-tag`：uploading(蓝) / processing(橙) / completed(绿) / failed(红)
- 上传对话框用 `el-upload` + 拖拽区域
- 空状态：无文档时显示引导上传

**关键逻辑（伪代码）**：
```
state:
  kbId: route.params.kbId
  kbName: ""
  docList: []
  uploadDialogVisible: false
  uploading: false

onMounted:
  GET /api/knowledge-bases/{kbId} → kbName
  GET /api/knowledge-bases/{kbId}/documents → docList

handleUpload(file):
  POST /api/knowledge-bases/{kbId}/documents (FormData)
  → 刷新 docList → 关闭 dialog

handleDelete(docId):
  DELETE /api/knowledge-bases/{kbId}/documents/{docId}
  → 刷新 docList
```

### 3.3 文档检索页

**目标效果**：简洁搜索界面，结果以列表展示。

```
┌─────────────────────────────────────────────┐
│  文档检索                                    │
│  [知识库选择器 ▼]  [搜索关键词...]   [检索]   │
├─────────────────────────────────────────────┤
│  检索结果（共 5 条）                          │
│  ┌─────────────────────────────────────────┐│
│  │ 相关性 ████████░░ 92%                    ││
│  │ ...投标保证金金额为人民币伍拾万元整...      ││
│  │ 📄 XX项目招标文件.pdf  |  📍 第12页        ││
│  └─────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────┐│
│  │ 相关性 ███████░░░ 87%                    ││
│  │ ...另需缴纳履约保证金...                  ││
│  │ 📄 XX项目招标文件.pdf  |  📍 第8页         ││
│  └─────────────────────────────────────────┘│
└─────────────────────────────────────────────┘
```

**交互**：
- 知识库下拉选择器（必选）
- 搜索框支持回车触发
- 结果卡片显示：分数进度条、文本片段（截断 200 字）、文件名 + 页码
- 关键词高亮（用 `v-html` 简单标记）
- 分页

**关键逻辑（伪代码）**：
```
state:
  kbId: null
  kbList: []
  query: ""
  results: []
  total: 0
  searching: false

onMounted → GET /api/knowledge-bases → kbList

handleSearch:
  POST /api/knowledge-bases/{kbId}/search { query, top_k: 10 }
  → results, total
```

### 3.4 智能问答页

**目标效果**：类似 ChatGPT 布局，左对话 + 右信息。

```
┌──────────────────────────────────┬───────────┐
│  智能问答                         │ 当前知识库 │
│                                  │ ○ 2024投标 │
│  ┌─ 用户 ───────────────────────┐│ ○ 历史招标 │
│  │ 2024年投标金额是多少？        ││           │
│  └──────────────────────────────┘│ 文档数量   │
│                                  │ 12        │
│  ┌─ AI ────────────────────────┐│           │
│  │ 根据文档内容，2024年投标...    ││ 引用来源   │
│  │                              ││ XX招标书  │
│  │ 📎 来源: XX招标书 P12        ││ P12       │
│  └──────────────────────────────┘│           │
│  ┌──────────────────────────────┐│           │
│  │  输入新问题...        [发送]  ││           │
│  └──────────────────────────────┘│           │
└──────────────────────────────────┴───────────┘
```

**交互**：
- 左侧：消息列表（用户蓝底白字靠右，AI 白底靠左）+ 输入区
- 右侧：知识库单选列表 + 选中知识库的信息卡片
- AI 回答时显示逐字流式效果（光标闪烁或打字动画）
- 回答结束后显示来源引用链接
- 支持多轮对话（同一个知识库内）

**关键逻辑（伪代码）**：
```
state:
  kbId: null           // 选中知识库
  kbList: []
  messages: []         // { role, content, sources? }
  inputText: ""
  streaming: false     // 是否正在流式输出
  currentReply: ""     // 当前流式回复内容

onMounted → GET /api/knowledge-bases → kbList

handleSelectKb(id):
  kbId = id
  messages = []        // 切换知识库清空对话

handleSend:
  messages.push({ role: 'user', content: inputText })
  streaming = true
  currentReply = ""
  
  fetch POST /api/knowledge-bases/{kbId}/chat (SSE)
    on 'token' event → currentReply += content
    on 'sources' event → 保存引用来源
    on 'done' → messages.push({ role: 'assistant', content: currentReply, sources })
              → streaming = false
```

---

## 4. 实现步骤

| 步骤 | 内容 | 验证 |
|------|------|------|
| 1 | 重写 `style.css` — 全局 CSS 变量 + Element Plus 覆写 | 启动前端，检查侧边栏、按钮颜色是否生效 |
| 2 | 修改 `AppLayout.vue` — 侧边栏样式升级 | 导航切换正常，hover/active 样式正确 |
| 3 | 修改 `main.js` — 中文语言包 + 图标注册 | 组件文案显示中文 |
| 4 | 重写 `KnowledgeBaseView.vue` — 卡片网格 + 对话框 | 创建/删除操作，跳转到文档页 |
| 5 | 重写 `DocumentListView.vue` — 表格 + 上传 | 上传按钮、状态标签渲染 |
| 6 | 重写 `SearchView.vue` — 搜索栏 + 结果列表 | 选择知识库、输入检索词、展示结果 |
| 7 | 重写 `ChatView.vue` — 聊天气泡 + 流式区域 | 发送消息、模拟流式效果（先用 mock） |

---

## 5. 依赖关系

```
Step 1 (主题)  →  Step 2 (布局)  →  Step 3 (语言包)
                                        │
                Step 4 (知识库) ◄────────┘
                     │
                Step 5 (文档管理)  ← 依赖知识库选择
                     │
                Step 6 (检索)
                     │
                Step 7 (问答)
```

---

## 6. 不做什么

- 不接真实 API（页面内仍用 stub 逻辑，但 UI 完整可交互）
- 不做响应式移动端适配（桌面端优先）
- 不做暗黑模式
- 不做国际化（仅中文）
- 不修改 router / API 封装层（现有封装满足需求）
