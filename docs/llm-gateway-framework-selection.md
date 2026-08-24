# LLM 网关框架选型评估

> 状态：Draft
> 日期：2026-08-24
> 关联文档：`LLM-GATEWAY.md`（网关架构设计）

## 1. 选型背景

根据 `LLM-GATEWAY.md` 的架构设计，网关需独立部署，承担以下核心职责：

- 统一代理（chat + embeddings），OpenAI 兼容协议
- 精确缓存（Redis + TTL，hash 键，返回 `X-Cache: HIT/MISS`）
- 按业务/租户限流（通过 `X-Business` 请求头区分业务线）
- 密钥集中管理（虚拟密钥 + 团队/预算）
- 调用审计（命中率、成本、延迟可观测）
- 多模型路由与降级（fallback、重试、负载均衡）
- 多实例共享状态、可横向扩容

各业务方仅改 `base_url` 指向网关，零代码改造。

本文档对主流开源 LLM 网关/代理框架进行评估，输出选型结论。

## 2. 候选框架

| 框架 | 技术栈 | 定位 | 许可证 |
|---|---|---|---|
| **LiteLLM Proxy** | Python | 企业级 LLM 代理/网关 | MIT |
| **OmniRoute** | Node.js/TypeScript | 个人开发者 AI 账号聚合器 | MIT |
| **New API**（One API 活跃 fork） | Go | API 分发/计费中转 | MIT |
| **Portkey**（自托管版） | Node/TS | 企业级可观测网关（商业核心） | 部分开源 |
| **自研 FastAPI** | Python | 完全定制 | — |

## 3. 维度对比

| 维度 | 设计要求 | LiteLLM Proxy | OmniRoute | New API | Portkey 自托管 | 自研 |
|---|---|---|---|---|---|---|
| 协议兼容 | OpenAI 兼容，改 base_url | ✅ | ✅ | ✅ | ✅ | 需自己实现 |
| 模型覆盖 | 主流厂商 + 国内模型 | ✅ 100+，全 | ✅ 248+，最广 | ✅ 国内适配好 | ✅ 主流 | 自己接 |
| chat 代理 | `/chat/completions` | ✅ | ✅ | ✅ | ✅ | ✅ |
| embeddings 代理 | `/embeddings` | ✅ 成熟 | ⚠️ 支持有限 | ⚠️ 部分 | ✅ | ✅ |
| 精确缓存 | Redis + TTL + hash 键 + X-Cache 头 | ✅ 原生支持 | ⚠️ 有 cache，精确匹配/Redis TTL/命中率头不明确 | ❌ 无原生缓存 | ✅ 内置 | 自己写 |
| 限流维度 | 按业务/租户（X-Business） | ✅ team/key/模型/rpm/tpm，team 可映射业务线 | ⚠️ 有 rate limits/policies，按自定义 header 维度不明确 | ⚠️ 渠道/令牌级，较粗 | ✅ 细粒度 | 自己实现 |
| 密钥管理 | 虚拟密钥 + 团队 + 预算 | ✅ | ⚠️ 多 provider 密钥，偏个人账号聚合 | ✅ 令牌 + 渠道 | ✅ | 自己做 |
| 调用审计 | 审计存储 + 命中率观测 | ✅ 日志 + Webhook + Langfuse/Phoenix 集成 | ⚠️ 有日志，企业级审计链路弱 | ✅ 调用日志 + 计费 | ✅ 强（商业核心） | 自己接 |
| 路由/降级/重试 | fallback + 重试 + 负载均衡 | ✅ 够用 | ✅ **强项**，15 种策略、task-aware | ⚠️ 仅渠道轮询/故障切换 | ✅ 强 | 自己实现 |
| 多实例横向扩容 | 共享状态、无状态 | ✅ PG/Redis 共享 | ❌ 默认 better-sqlite3 本地存储，单机倾向 | ⚠️ 需配 MySQL 共享 | ✅ | 自己设计 |
| 技术栈对齐 | 与 docflow（Python）一致 | ✅ Python | ❌ Node.js/TS | ❌ Go | ❌ Node/TS | ✅ Python |
| 部署复杂度 | Docker 一键 | ✅ | ✅ npm/Docker/Electron | ✅ | ⚠️ 相对重 | 最高 |
| 维护模式 | 可持续 | ✅ 公司/社区双驱动，成熟 | ⚠️ 单核心开发者 + 社区 | ✅ 社区活跃 | ⚠️ 商业开源混合 | 自负 |
| 额外功能 | — | 聚焦网关治理 | MCP/A2A/Memory/Skills/Cloud Agents/Guardrails/桌面端（噪音） | 计费分发 | 可观测性 | 无 |

## 4. OmniRoute 专项分析

### 4.1 项目概况

- **仓库**：`diegosouzapw/OmniRoute`
- **Stars**：17K+
- **起源**：fork 自 9router 项目
- **维护**：单核心开发者（diegosouzapw）+ 280+ 社区贡献者
- **口号**："Never stop coding"
- **核心卖点**：将 248+ AI provider（含大量免费 tier）聚合成单一 OpenAI 兼容端点，自动切换、永不断线

### 4.2 优势

1. **Provider 覆盖最广**：248+ 厂商，免费 tier 聚合能力强
2. **路由策略丰富**：15 种路由策略，支持 task-aware smart routing、thinking budget controls、wildcard routing
3. **协议转换强**：OpenAI ↔ Claude ↔ Gemini ↔ Responses API 自动格式转换
4. **个人开发体验好**：npm 一键安装、Electron 桌面端、适合 Claude Code/Codex 等客户端统一入口
5. **附加生态多**：内置 MCP Server（99 tools）、A2A v0.3、Memory/Skills、Cloud Agents 集成

### 4.3 与本项目需求的不匹配点

| 不匹配项 | 说明 |
|---|---|
| **定位错位** | OmniRoute 是个人开发者的"AI 账号聚合器"，核心场景是聚合免费/付费账号自动切换不断线。本项目需要的是企业级治理网关（精确缓存、按业务限流、密钥管理、审计），这些不是其设计重心 |
| **单机存储架构** | 默认使用 `better-sqlite3`（本地 SQLite），与"多实例共享状态、可横向扩容"的架构要求冲突。改造成共享存储需自行改造 |
| **精确缓存不透明** | 文档未明确精确匹配缓存的 hash 键设计、Redis TTL 配置、`X-Cache` 响应头返回，无法直接对齐设计要求 |
| **限流维度不足** | 虽有 rate limits 和 policies，但按自定义请求头（`X-Business`）维度限流的能力不明确 |
| **审计链路弱** | 缺乏企业级审计 webhook、命中率统计回流能力，无法支撑"数据驱动二期是否上语义缓存"的决策 |
| **技术栈不对齐** | Node.js/TypeScript，docflow 全栈 Python，二次开发和排障成本高 |
| **功能噪音多** | MCP、A2A、Memory、Skills、Cloud Agents、Guardrails、Electron 桌面端对本网关场景均为多余功能，增加攻击面和维护复杂度 |
| **维护风险** | 单核心开发者模式，企业级依赖存在可持续性风险 |

### 4.4 OmniRoute 适用场景

- 个人开发者 / 小团队，想用一个端点管理一堆免费/付费 AI 账号
- 需要在 Claude Code / Codex 等客户端里统一切换模型
- 对缓存、限流、审计、多租户没有强治理要求

**结论：OmniRoute 不适用于本项目的企业级治理网关场景。**

## 5. New API 专项分析

### 5.1 定位

New API 是 One API 的活跃 fork，Go 语言实现，定位为"LLM API 分发与计费系统"。

### 5.2 优势

- 国内模型渠道适配好（通义、文心、智谱、DeepSeek 等）
- 部署轻量，Docker 一键
- 令牌管理 + 渠道管理 + 计费功能完善
- 社区活跃，国内用户多

### 5.3 不匹配点

- **无原生精确缓存**：缓存能力缺失，需自行扩展
- **限流粒度粗**：仅渠道/令牌级，不支持按业务请求头维度限流
- **路由降级弱**：仅渠道轮询和故障切换，无智能路由策略
- **定位偏差**：核心是"API 分发+计费"，不是"治理网关"

**结论：New API 适合做渠道中转和计费，不适合作为治理网关。**

## 6. Portkey 专项分析

### 6.1 定位

Portkey 是企业级 LLM 可观测性与网关平台，有自托管版本。

### 6.2 优势

- 路由/降级/缓存/可观测性能力均强
- 企业级功能完善

### 6.3 不匹配点

- 核心能力在商业版，自托管版功能深度受限
- 部署相对重
- 开源透明度不如 LiteLLM

**结论：如需企业级可观测性可考虑 LiteLLM + Langfuse 组合，不选 Portkey 自托管。**

## 7. 推荐结论

### 7.1 选型：LiteLLM Proxy

**推荐理由（逐条对应设计要求）：**

1. **零代码改造接入**：LiteLLM 本身是 OpenAI 兼容代理，docflow 将 `base_url` 指向网关即可。`X-Business` 请求头可通过 headers 透传，或映射为 LiteLLM 的 `team` 概念实现业务隔离。

2. **精确缓存开箱即用**：通过 `litellm_settings.cache: redis` + `cache_params` 配置 TTL，返回头自带缓存状态。缓存键默认包含 `model + messages + temperature/top_p` 等采样参数，与设计的 `hash(model + 规范化 messages + 采样参数)` 思路一致。

3. **限流维度对齐**：支持按虚拟 key、team（映射业务线）、模型、rpm/tpm 限流，覆盖"按业务/租户限流"要求。

4. **路由降级成熟**：`model_list` + `router_settings` 配置 fallback、重试、超时、负载均衡，多模型路由无需自研。

5. **审计链路完整**：内置调用日志，可通过 webhook 推送到 Langfuse（项目已在关注）做命中率/成本/延迟分析，支撑"数据驱动二期是否上语义缓存"的决策。

6. **多实例横向扩容**：基于 PG（密钥/预算/日志）+ Redis（缓存/限流）共享状态，代理本身无状态，可多实例部署。

7. **技术栈对齐**：Python 实现，与 docflow 技术栈一致，团队读源码、二次开发（自定义缓存键、X-Business 维度统计）成本最低。

8. **维护可持续**：公司/社区双驱动，成熟稳定，生产环境广泛使用。

### 7.2 落地架构

```
docflow / generate / review 等业务方
        │  base_url → LiteLLM Proxy
        │  Header: X-Business: docflow | generate | review
        ▼
  ┌─────────────────────────────────┐
  │   LiteLLM Proxy（多实例，无状态）  │
  │   ├── 虚拟密钥 → 映射业务线 team   │
  │   ├── 鉴权 → 限流 → 缓存 → 路由   │
  │   └── 转发 → 回填缓存 + 审计       │
  └─────────────────────────────────┘
        │           │            │
        ▼           ▼            ▼
     Redis        PostgreSQL    Webhook
   (缓存/限流)   (密钥/日志)   → Langfuse
        │                        (审计+命中率观测)
        ▼
  多模型厂商（OpenAI / 通义 / DeepSeek / ...）
```

### 7.3 待确认事项

1. **缓存键自定义**：LiteLLM 默认缓存键包含 `model + messages + 采样参数`，如对"messages 规范化"（去 role 顺序、trim whitespace 等）有特殊要求，需使用 `cache_key_builder` 钩子自定义。
2. **X-Business 映射**：确认使用 LiteLLM 的 `team` 概念映射业务线，还是通过自定义 header + 中间件实现业务维度统计。
3. **embeddings 缓存**：确认 LiteLLM 对 `/embeddings` 端点的精确缓存支持情况，如不支持需评估是否在网关层扩展。

## 8. 总结

| 框架 | 结论 | 一句话理由 |
|---|---|---|
| **LiteLLM Proxy** | ✅ **推荐** | 企业级治理能力全覆盖，Python 技术栈对齐，多实例架构匹配 |
| OmniRoute | ❌ 排除 | 定位是个人账号聚合器，单机存储、治理能力不透明、技术栈不对齐 |
| New API | ❌ 排除 | 定位是 API 分发计费，无原生缓存、限流粒度粗、路由降级弱 |
| Portkey 自托管 | ❌ 排除 | 核心能力在商业版，自托管深度受限 |
| 自研 FastAPI | ❌ 不推荐 | 重复造轮子，LiteLLM 已覆盖全部核心需求 |

**最终选型：LiteLLM Proxy。** 待上述待确认事项验证后，可将本文档状态改为 Accepted，并同步到 `docs/INDEX.md`。
