# WriteAI 前端风格（MVP）

> 版本：v1.0（2026-08-26）

## 整体

- 界面语言：中文。
- 风格：轻量写作工具风（Notion / 语雀式），浅色主题。
- 视觉基调：白底、浅灰分隔线；主色靛蓝 `#4F46E5`；状态色：灰（pending）、蓝（running）、琥珀（review）、绿（done）、红（failed）。
- 字体：系统字体栈；正文 Markdown 渲染用 16px，行高 1.7。

## 布局

- 左侧固定 260px 项目列表：顶部「新建文章」按钮 + 项目卡片（标题、状态徽章、更新时间）。
- 主区项目详情：
  - 顶部信息条：标题 / 主题 / 要求（可编辑）。
  - 垂直阶段时间线（五步），每步为 StageCard。
  - 当前产物区：Markdown 渲染 + 编辑（textarea 编辑后保存）。
- 设置与知识库管理为独立页面，左侧列表提供入口。

## 组件与交互

- **StageTimeline / StageCard**：状态徽章 + 阶段名；running 显示 spinner 与阶段名；failed 显示错误信息与「重试」按钮。
- **ReviewPanel**（审核点）：醒目琥珀提示「待人工审核」；操作：批准并继续 / 修改后批准（textarea）/ 审校阶段额外有「追加一轮审校」。
- **ArtifactViewer**：marked + DOMPurify 渲染 Markdown；切换查看审校各轮产物（round-N、审校报告、修订稿）。
- **项目状态轮询**：详情页每 2s 轮询 `GET /projects/:id`；项目 finished 或失败停止轮询。
- **Toast**：操作成功/错误提示。
- 设置保存后即时生效（下次 LLM 调用使用新配置）。

## 响应式与可访问性

- 桌面优先：主布局 ≥1024px；<1024px 左侧列表折叠为顶部下拉选择。
- MVP 不做移动端适配。
- 状态除颜色外配文字徽章；交互元素有可读文本/aria-label。

## 组件清单

ProjectList、ProjectCard、StageTimeline、StageCard、ArtifactViewer、ReviewPanel、SettingsForm、KnowledgePage、Toast、ConfirmDialog。
