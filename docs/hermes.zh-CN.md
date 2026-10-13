# 为什么选择 Hermes

[English](hermes.md) | **简体中文**

Hermes 是一个运行在你服务器上的持久化、自主运行的 AI Agent。它拥有跨会话不断累积的分层记忆、能在你离线时触发任务的定时调度器，以及会自动保存可复用流程的自我改进技能系统。你可以从终端、浏览器或消息应用访问它——每次都是同一个 Agent，带着同一份历史。

Leap Aide 是 Hermes 的 Web UI。

本文解释 Hermes 的心智模型、它与其他工具的诚实对比，以及它适合与不适合的场景。

---

## 真正的问题：大多数工具当下表现出色，长期积累乏力

记忆本身已经不再是差异化能力。ChatGPT、Claude、Cursor 和 GitHub Copilot 现在都有某种形式的记忆。Anthropic、OpenAI 和 Microsoft 都在推出调度和 Agent 能力。十二个月前还清晰的品类边界正在快速模糊。

Hermes 并不是唯一具备记忆或自动化的工具。它所做的，是把这些能力变得持久、自托管、跨端可用，并在你自己的服务器上持续累积。真正重要的区别不是"有记忆"对"没记忆"——而是：上下文是否自动跨会话保留、执行是否发生在你掌控的硬件上、你是否能从任何设备触达同一个 Agent 身份，以及系统是否会在无需手动配置的情况下，随着时间推移在你特定的工作流上变得越来越好。

```
会话级工具：      你 -> [工具] -> 回答 -> 结束
                  （部分工具现在带有记忆，但执行本身是无状态的）

持久化 Agent：    你 <-> [Hermes] <-> （记忆、技能、调度、工具、多端入口）
                  （运行在你的服务器上，累积上下文，离线时代你行动）
```

---

## 关于趋同的一点说明

市场正在趋同。聊天助手在加入任务调度和文件连接器；IDE 工具在推出云端 Agent 模式；CLI 工具在加入技能系统和移动端入口。"助手""编辑器"和"Agent"之间的界限正在消融。

这让对比变得更难，但也让问题变得更尖锐：当每个工具都在宣称拥有每个功能的某个版本时，什么才是真正重要的？对 Hermes 来说，答案是整合。任何单一功能——记忆、调度、消息——都能在别处找到。真正的价值在于：把所有这些放进一个自托管系统，持续运行，拥有一个会随着时间推移累积你技术栈真实知识的持久身份。

---

## 三大支柱

### 1. 复利式记忆

Hermes 拥有跨越每次会话、每次重启、每次模型更换而存活的分层记忆：

- 用户画像——你是谁、你的偏好、你的沟通风格、你纠正过 Hermes 的地方
- Agent 记忆——关于你的环境、工具链、项目约定的事实
- 技能——Hermes 自动发现并保存的可复用流程；它永远不需要重新学习如何部署你的应用、运行测试或评审 PR
- 会话历史——每一次过去的对话都可搜索；Hermes 能回忆起你上周二在做什么

当你纠正 Hermes 时，它会记住。当它解决了一个棘手问题时，它会保存这个方法。当它学会你的技术栈后，这些知识会带入之后的每一次会话。你从不需要手动配置这一切——它在后台作为正常使用 的副产品自然发生。

### 2. 自主调度

Hermes 可以在你不在场时运行任务——每小时、每天早上，或任何 cron 计划。它会启动一个全新会话，完整访问你的记忆和技能，执行任务，并把结果送达你指定的任何地方：Telegram、Discord、Slack、Signal、WhatsApp、短信、邮件等等。

Hermes 能在你睡觉时做的事：

- 评审你 GitHub 仓库的新 pull request，并发布完整的判定评论
- 发送新闻、市场或你追踪的任何内容的晨报
- 运行你的测试套件，出问题时告警
- 监视竞争对手的博客新文章并做摘要
- 监控数据源，在越过阈值时通知你

与云端调度方案的不同在于：任务跑在你的服务器上，带着你的记忆和技能，你的数据从不离开你的硬件。

### 3. 从任何地方触达

Hermes 运行在你的服务器上，可从每个入口访问：SSH 终端、Web UI（本项目），以及包括 Telegram、Discord、Slack、WhatsApp、Signal 和 Matrix 在内的消息应用。从手机上发起任务，在笔记本浏览器上查看，在远程服务器的终端里继续。同一个 Agent、记忆和历史随你跨越所有入口。

---

## 当前 AI 工具的分层

旧的四分类模型——聊天、编辑器、CLI、Agent——过于干净了。这些层次正在主动互相融合。下面是一幅更诚实的图景：

聊天助手（Claude.ai、ChatGPT）如今拥有持久记忆、任务调度、50+ 服务连接器，某些情况下还有带计算机操作能力的完整 Agent 模式。它们早已不只是"聊天"。

IDE 工具（Cursor、Windsurf、Copilot）已经推出或正在推出跨会话记忆、云端后台 Agent，以及（在 Cursor 那里的）带 Slack 集成的完整 Automations 平台。Cursor v3.0（2026 年 4 月）明确以 Agent 优先。

CLI 工具（Claude Code、Codex、OpenCode）加入了 hooks、技能、桌面应用自动化和多端触达。Claude Code 现已覆盖终端、IDE、桌面和浏览器。Codex 已演变为产品家族：CLI、IDE 扩展、桌面应用和 Codex Cloud。

持久化自托管 Agent（Hermes、OpenClaw）位于交汇点：它们结合了 CLI Agent 的工具使用能力、聊天助手的记忆、自动化平台的调度，以及消息集成的跨端触达——持续运行在你自有的硬件上。

问题不在于某个工具属于哪个品类。问题在于你实际需要哪种能力组合、执行发生在哪里，以及系统是否会在你的特定上下文上随时间变得更好。

---

## Hermes 与其他工具的对比

### 对比 OpenClaw

OpenClaw 是最直接的对比对象，也是大多数人首先问到的。两者都是开源、自托管、常驻运行的 Agent，具备持久记忆、cron 调度和消息应用集成。如果你在评估 Hermes，也请一并评估 OpenClaw。

OpenClaw（MIT）围绕一个用 Node.js/TypeScript 编写的 Gateway 控制面构建。它拥有这个领域最广的消息覆盖——24+ 渠道，包括 WhatsApp、Telegram、Signal、iMessage、LINE、WeChat、Slack、Discord、Teams、Matrix、Google Chat、飞书、Mattermost、IRC、Nextcloud Talk 等。它通过 CDP 原生控制 Chrome/Chromium，在 macOS 和 iOS 上支持语音唤醒词，并拥有 10,700+ 技能的 ClawHub 市场。社区规模很大（350k+ GitHub stars、16,900+ commits）且还在增长。

Hermes 用 Python 构建，核心是自我改进的 Agent 循环，而非 Gateway 控制面。核心架构差异在技能上：OpenClaw 的技能主要是人工编写的插件，从市场安装。Hermes 则在每次会话中自动编写并保存自己的技能。当 Hermes 用新方式解决了一个问题，它会保存流程并在之后复用，无需用户任何投入。这不是细微差别——这正是 Hermes 能在你不用维护插件库的情况下，在你的工作流上切实变好的原因。

两个值得直接了解的实际差异：

稳定性。OpenClaw 的 GitHub issues 和社区论坛记录了反复出现的升级破坏性回归。Telegram 集成从 2026 年初到至少 2026 年 4 月跨多个版本处于损坏状态。OpenClaw 依赖的非官方 WhatsApp Web 协议会断连，需要定期重新配对——这写在 OpenClaw 自己的 FAQ 里。

安全性。ClawHub 的开放发布模式已被大规模利用。2026 年初的三次独立审计发现了严重问题：Koi Security（2026 年 1 月）将 335 个技能关联到一个名为 "ClawHavoc" 的活动，该活动在 macOS 上传播 Atomic Stealer 恶意软件；Bitdefender 发现约 900 个恶意包，约占当时生态系统的 20%；Snyk 的 "ToxicSkills" 报告（2026 年 2 月）在约 4,000 个扫描包中发现了恶意技能。中国 CNCERT 就 ClawHub 发布了国家级预警。Hermes 没有第三方市场，攻击面相应小得多。

OpenClaw 的真实优势值得明说：更广的消息覆盖（iMessage、LINE、WeChat、Teams、Google Chat——Hermes 不支持的平台）、通过 Chrome CDP 原生控制浏览器和计算机、语音唤醒词、更大的社区，以及比 Hermes 更多的第三方集成。如果这些能力对你最重要，OpenClaw 值得认真考虑。

Hermes 更合适的情形：你想要一个从经验中自我改进、无需维护插件库的 Agent；你在 Python 中工作，需要 ML/数据科学生态；你想要一个不会在升级之间坏掉的稳定部署；或者你想要一个完整的 Web 聊天 UI，而不是一个控制台面板。

| | OpenClaw | Hermes |
|---|---|---|
| 持久记忆 | 是 | 是 |
| 定时任务（cron） | 是 | 是 |
| 消息应用访问 | 是（24+ 平台，含 iMessage/WeChat/LINE） | 是（众多平台） |
| Web UI | 聊天 UI + 控制面板 | 完整三栏聊天 UI |
| 自托管 | 是 | 是 |
| 开源 | 是（MIT） | 是 |
| 自我改进技能 | 部分（AI 可生成；非默认循环） | 是（自动、一等能力） |
| 浏览器 / 计算机控制 | 是（原生 Chrome CDP） | 通过 shell / 工具 |
| 语音唤醒词 | 是（macOS/iOS） | 否 |
| Python / ML 生态 | 否（Node.js） | 是 |
| 编排 Claude Code / Codex | 否 | 是 |
| 多 Profile 支持 | 通过绑定规则路由 | 是（一等命名 Profile） |
| 提供商无关 | 是 | 是 |
| 升级可靠性 | 一般（有记录的回归） | 高 |
| 记忆可检视性 | 有限 | 是（markdown 文件，可编辑） |
| 自托管自主执行 | 是 | 是 |

### 对比 Claude Code（Anthropic）

Claude Code 是 Anthropic 的官方 Agent 工具，也是专注编码会话的最强选择之一。它具备深度代码理解、shell 访问、文件编辑和多步推理。它扩张迅速——现已覆盖终端、IDE 插件、桌面应用和浏览器——在若干方面差距正在缩小。

Claude Code 有哪些值得了解的能力：

- Hooks 系统——26 种事件类型（SessionStart、PreToolUse、PostToolUse、Stop 等）配 4 种处理器类型（shell 命令、HTTP 端点、LLM 提示词、子 Agent）；提供对 Agent 生命周期的确定性非 LLM 控制
- Plugins / Skills——可通过 `/plugin install` 安装，从 `~/.claude/skills` 热加载，带有市场；包括官方 ralph-wiggum 插件（`/ralph-loop`），用于朝完成目标自主迭代（与 `/loop` 不同）
- `/loop`——原生内置技能，每个会话无需任何插件即可使用，在活跃的 CLI 会话内按重复调度运行提示词（轮询/监控场景）；作用域限于会话，终端关闭即终止
- 调度——云端托管 cron（Anthropic 基础设施，最小 1 小时间隔）和桌面应用计划任务（应用打开时本地运行，最小 1 分钟间隔，完整本地文件访问）；没有自托管 cron
- 消息渠道——通过 Channels 功能（研究预览，需要 Bun 运行时）支持 Telegram、Discord 和 iMessage；Slack 是呼声最高的新增项，尚未发布
- 记忆——CLAUDE.md 和 MEMORY.md 提供项目级上下文；v2.1.59+ 起支持 auto-memory
- Claude Cowork——独立的知识工作者产品，通过 MCP 连接 38+ 服务，包括 Gmail、Microsoft Teams、Notion、Jira、Salesforce 等

Claude Code 的源码在 2026 年 3 月曾短暂意外公开，随后被下架。CLI 以 Bun 编译的压缩/打包 TypeScript 发布——它不是开源的。

仍然存在的关键差异：

- 调度依赖云端（Anthropic 基础设施，数据离开你的硬件，最小 1 小时）或桌面应用（本地运行，但应用必须保持打开——不是无头服务器进程）；两者都不能像 Hermes cron 那样作为服务器守护进程运行
- 记忆基于项目文件（CLAUDE.md / MEMORY.md 加滚动 auto-memory）；它不会像 Hermes 那样自动累积跨项目的知识图谱
- 非提供商无关——可经由 Anthropic、Bedrock、Vertex 或 Foundry 路由，但始终是 Claude 模型；你无法切换到 GPT、Gemini 或本地模型
- 消息渠道仍是研究预览，未达生产可用

Hermes 可以把 Claude Code 用作子 Agent。对于大型实现任务，Hermes 可以派生 Claude Code 承担重活，并把结果折回自己的记忆和历史。

| | Claude Code | Hermes |
|---|---|---|
| 持久记忆（自动） | 部分（CLAUDE.md / MEMORY.md + auto-memory v2.1.59+） | 是 |
| 技能 / hooks 系统 | 是（26 事件 Hooks + Plugin/Skills 市场） | 是（从经验自动生成） |
| 定时任务（自托管） | 否（仅云端或桌面应用） | 是 |
| 消息访问 | 部分（Telegram/Discord/iMessage 研究预览；Slack 尚无） | 是（众多平台，生产可用） |
| Cowork 连接器（Slack、Gmail 等） | 是（经由 Claude Cowork，独立产品） | 通过 Agent 工具使用 |
| Web UI | 是（claude.ai/code，Anthropic 托管） | 是（自托管） |
| 提供商无关 | 否（仅 Claude 模型） | 是（任意提供商） |
| 自托管调度 | 否 | 是 |
| 开源 | 否 | 是 |
| 后台/云端 Agent 模式 | 是（云端调度） | 是（自托管 cron） |
| 作为 Hermes 的子 Agent 运行 | 是 | 不适用 |
| 记忆可检视性 | 部分（CLAUDE.md 可读；auto-memory 较难） | 是（markdown 文件） |

### 对比 Codex CLI（OpenAI）

Codex CLI（Apache 2.0，约 60k GitHub stars）起步是一个简单的终端工具，现已扩展为产品家族。它从 TypeScript 重写为 Rust。如今包含 IDE 扩展、带 Automations 功能的桌面应用，以及用于远程执行的 Codex Cloud。技能系统跨入口共享。它支持 12+ 内置提供商：OpenAI、Anthropic、Google/Gemini、Mistral、Groq、Ollama、OpenRouter、LM Studio、Together AI、DeepSeek、xAI、Azure OpenAI 和自定义端点。

CLI 本身没有原生调度（开放的功能请求）。会话延续通过 `codex resume` 提供。记忆基于会话历史加 AGENTS.md 项目上下文——不是一个跨所有项目累积的活知识图谱。没有第一方消息集成。桌面应用中的 Automations 功能覆盖本地计划任务，但达不到 Hermes 那种跨会话、跨入口的连续性。

| | Codex CLI | Hermes |
|---|---|---|
| 持久记忆 | 部分（会话历史 + AGENTS.md） | 是（自动、分层） |
| 定时任务 | 部分（桌面应用 Automations；CLI 无） | 是 |
| 消息应用访问 | 否 | 是 |
| Web UI | 否（CLI + 桌面应用） | 是（自托管） |
| 提供商无关 | 是（12+ 提供商） | 是 |
| 自托管 | 是 | 是 |
| 开源 | 是（Apache 2.0） | 是 |
| 后台/云端 Agent 模式 | 是（Codex Cloud） | 是（自托管 cron） |
| 自我改进技能 | 否 | 是 |

### 对比 OpenCode

OpenCode 是一个开源 TUI Agent 编码助手，支持 75+ 提供商。它的二进制内嵌 WebUI，有官方桌面应用、SQLite 会话历史和 AGENTS.md 项目上下文。它支持把 CLAUDE.md 作为从 Claude Code 迁移用户的回退。有 30+ 社区插件，社区也存在面向 Telegram、Slack、Discord 和 Microsoft Teams 的消息集成——但均非第一方，且都需要手动配置。

OpenCode Go（$10/月）和 OpenCode Zen（精选模型服务）是订阅档位。GitHub Copilot 官方集成于 2026 年 1 月推出。没有原生调度；有一个社区后台插件。没有自动的跨会话语义记忆。

| | OpenCode | Hermes |
|---|---|---|
| 持久记忆 | 部分（会话历史 + AGENTS.md） | 是（自动、分层） |
| 定时任务 | 否（仅社区插件） | 是 |
| 消息应用访问 | 仅社区集成（Telegram/Slack/Discord/Teams） | 是（第一方，众多平台） |
| Web UI | 是（内嵌 + 桌面应用） | 是（自托管） |
| 移动端访问 | 否 | 是 |
| 技能 / 插件 | 是（30+ 社区插件） | 是（自动生成、第一方） |
| 提供商无关 | 是（75+ 提供商） | 是 |
| 开源 | 是 | 是 |
| 自托管自主执行 | 否 | 是 |

### 对比 Cursor

Cursor 已经发生了实质变化。"没记忆、没调度、没消息"这个描述在 2024 年是准确的，现在是错的。

Memories（按项目的跨会话知识库）随 v1.0 于 2025 年 6 月以 beta 形式发布。Automations 于 2026 年 3 月 5 日推出——基于时间、基于事件（GitHub/Linear/PagerDuty）和基于沟通（Slack）的触发器，在云端 VM 上启动后台 Agent。Web 应用、移动端 Agent 和 Slack bot 给了它多端触达。Cursor v3.0（2026 年 4 月 2 日）明确以 Agent 优先，带 Design Mode 和 30+ 市场插件。Cursor 收购了 Supermaven 做自动补全。截至 2026 年初，它估值 $29.3B，ARR $2B。它不再是一个窄众的编辑器工具。

Hermes 的定位仍然不同：它自托管、驻留在服务器上，同一个持久身份随你跨越每个入口、无云端中介，且能与任意模型家族协作，而不是基于云端 VM。对于需要数据主权、自托管调度，或在自有硬件上深度使用 Python/ML 工具的工作流，Cursor 的云端 Agent 架构是根本性的错配。而对于想要编辑器原生、强 IDE 集成 Agent 的团队，Cursor 近来的演进意义重大。

| | Cursor | Windsurf | Copilot | Hermes |
|---|---|---|---|---|
| 编辑器内自动补全 | 优秀（Supermaven） | 优秀（Cascade） | 优秀 | 否 |
| 内联 diff / 重构 | 是 | 是 | 是 | 通过 shell |
| 跨会话记忆 | 是（Memories，按项目） | 是（Cascade Memories，按工作区） | 是（Agentic Memory，按仓库，28 天过期） | 是（自动、持久） |
| 定时后台任务 | 是（Automations，云端 VM） | 否 | 经由 Coding Agent（issue 驱动） | 是（自托管 cron） |
| 消息应用 / 多端 | 是（Slack bot、Web 应用、移动端） | 否 | 经由 Copilot CLI / fleet | 是（众多平台） |
| 后台/云端 Agent 模式 | 是（Automations，云端 VM） | 否 | 是（Coding Agent，2026 年 3 月 GA） | 是（自托管） |
| 终端工具使用 | 有限 | 有限 | 有限 | 完整 |
| 自托管 | 否 | 否 | 否 | 是 |
| 自托管自主执行 | 否 | 否 | 否 | 是 |
| 提供商无关 | 部分 | 部分 | 否（GitHub 模型） | 是 |
| 开源 | 否 | 否 | 否 | 是 |
| 记忆可检视性 | 部分 | 是（本地存储） | 有限 | 是（markdown 文件） |

### 对比 Claude.ai 和 ChatGPT

它们不再是简单的聊天工具。"没记忆、没调度、没消息"的描述对两者都不准确。

Claude Cowork（位于 Claude Desktop）于 2026 年 2 月 25 日推出计划任务——每小时、每天、每周、工作日和按需。它在带文件和 shell 访问的隔离 VM 中运行。截至 2026 年 2 月，Claude 拥有 50+ 服务连接器，包括 Slack（2026 年 1 月 26 日推出）、Gmail、Google Calendar、Google Drive、Microsoft 365、Notion、Asana、Linear 和 Jira。记忆从聊天历史自动生成，而不仅靠用户手动整理。Artifacts 中的代码执行和文件访问是沙箱化的，与你自己服务器上的 shell 访问不是一回事。

ChatGPT 有 Agent Mode（2025 年 7 月 17 日推出）、Scheduled Tasks（2025 年 1 月，循环自动提示）、计算机操作 Agent、Projects、50+ 连接器（含 Gmail、GitHub 和 Google Drive）、双模式记忆（自动 + 手动），以及面向 Pro 用户的 ChatGPT Pulse（每日研究简报）。它不是一个被动的问答界面。

Claude.ai 和 ChatGPT 与 Hermes 的差异：两者都不是自托管，都不提供商无关，都不给你在自有硬件上的执行。连接器和调度是有的，但运行在 Anthropic 或 OpenAI 的基础设施上。你的记忆、会话历史和 Agent 执行活在他们的服务器上，而不是你的。对很多场景这没问题——它们能力强、支持完善。但对注重隐私的用户、受监管环境，或需要在受控硬件上持续服务端执行的工作流来说，这是一票否决的约束。

| | Claude.ai | ChatGPT | Hermes |
|---|---|---|---|
| 跨对话记忆 | 是（从历史自动生成） | 是（双模式：自动 + 手动） | 是（深度、自动） |
| 计划任务 | 是（Cowork：每小时/每天/每周） | 是（2025 年 1 月起） | 是（任意 cron，自托管） |
| 服务连接器 / 消息 | 是（经 Cowork 50+） | 是（50+ 连接器） | 是（众多平台，直连） |
| 运行 shell 命令 | 沙箱（Cowork VM） | 沙箱 | 是（完整 shell） |
| 代码执行 | 沙箱 | 沙箱 | 是（完整 shell） |
| 读写文件 | 沙箱 | 沙箱 | 是（完整文件系统） |
| Web UI | 是（Anthropic 托管） | 是（OpenAI 托管） | 是（自托管） |
| 自托管 | 否 | 否 | 是 |
| 提供商无关 | 否 | 否 | 是 |
| 开源 | 否 | 否 | 是 |
| 自托管自主执行 | 否 | 否 | 是 |
| 记忆可检视性 | 有限 | 有限 | 是（markdown 文件） |

---

## 复利优势

Hermes 与上述大多数工具的区别在于：它会在无需手动配置的情况下，随着时间推移在你特定的工作流上切实变得更好。

每当 Hermes 遇到新环境，它会把事实存入记忆。每当它用新方式解决问题，它会把方法存为技能。每当它被你纠正，它会更新你的画像。每次会话、每个计划任务、每次工具调用，都在为一份专属你的知识体添砖加瓦——这份知识存在你的硬件上，供之后每一次交互使用。

Claude Code 的第一天和第一百天是一样的——每次都从零开始。而 Hermes Agent 的第一天和第一百天，已经了解你的技术栈、你的约定、你的偏好，以及过去奏效过的解法。这才是真正的复利。

---

## Hermes 适合谁

不想每个会话都重新解释一遍技术栈、想要一个真正了解自己环境的 AI 的独立开发者和重度用户。

共用一台服务器的团队——多人想要可用的 AI 能力，而不用每人单独付费订阅或各自跑一套本地工具。

自动化密集型工作流——你希望 AI 按计划执行任务、把结果送到手机上，而不需要你盯着。

注重隐私的用户——希望对话、记忆和文件留在自己的硬件上。

多模型用户——想根据成本、能力或速率限制在 OpenAI、Anthropic、Google、DeepSeek 等之间切换，而不必每次重建工作流。

---

## Hermes 不是什么

Hermes 不是最好的编辑器内自动补全工具。Cursor 和 Windsurf 做得更好。可以搭配使用。

它不是零配置。你在运行一台服务器。这意味着初始配置，也意味着你要负责可用性、升级和备份。交换来的是数据主权和控制权；只有你真的想要这些，这笔交易才划算。

它不会让弱模型变神奇。记忆和技能有帮助，但底层模型仍然决定推理质量。弱模型加 Hermes，只是一个组织得更好的弱模型。

高风险自动化仍然需要护栏、审批和可观测性。带 shell 访问的定时自主执行威力巨大，需要你判断什么该放行。终端命令可以设置运行前确认；任何有重大后果的操作都应启用它。

如果你需要的是获取一次性答案或快速编辑的最低摩擦路径，聊天界面或编辑器内工具才是正确选择。Hermes 为连续性和自主性而生，不是为最低摩擦的一次性操作。

---

## 范围与边界

Hermes 活在终端、浏览器和消息应用里。编辑器内自动补全和内联 diff，请用 Cursor 或 Windsurf——它们做得更好，也能与 Hermes 并肩工作。

Hermes 跑在你自己的服务器上。这意味着初始配置，但数据留在你的硬件上，调度、模型和成本都由你掌控。

Hermes 是一个编排与记忆层。它让你指给它的任何模型随时间变得更有用。模型负责推理；Hermes 负责让这些推理累积成持久之物。

---

## 安全与控制

记忆以可读、可编辑的文件形式存储在你的服务器本地：用户画像、Agent 记忆和技能都是 markdown。会话历史在你机器上的 SQLite 里。你可以直接检视、编辑或删除其中任何内容。

如果你想要外部记忆提供商，支持八种：Mem0、Honcho、Hindsight、RetainDB、ByteRover、Supermemory、Holographic 等。这些是可选的、可配置的。

执行运行在可配置的后端中：本地 shell、Docker、SSH、Daytona、Singularity 或 Modal。你决定 Hermes 在什么执行环境里操作、能触达什么。

终端命令可以设置运行前确认。任何触及生产系统或发出外部调用的自动化，请启用审批控制。

密钥留在你的硬件上。Hermes 不会回传数据；它直接调用你配置的模型 API。

多 Profile 提供用户或项目之间的隔离。一台共享服务器可以有各自独立的 Profile——独立的记忆、独立的技能、独立的历史。

---

## 快速对照

| | OpenClaw | Claude Code | Codex | OpenCode | Cursor | Copilot | Claude.ai | ChatGPT | Hermes |
|---|---|---|---|---|---|---|---|---|---|
| 持久记忆（自动） | 是 | 部分† | 部分 | 部分 | 是（按项目） | 是（按仓库‡） | 是 | 是 | 是 |
| 定时 / 后台任务 | 是 | 部分§ | 部分¶ | 否 | 是（Automations） | 经由 Coding Agent | 是（Cowork） | 是 | 是（自托管） |
| 消息 / 多端 | 是（24+ 平台） | 部分（预览） | 否 | 仅社区 | 是（Slack/网页/移动端） | 经由 CLI/fleet | 是（50+ 连接器） | 是（50+ 连接器） | 是（众多平台） |
| Web UI | 聊天 UI + 控制面板 | Anthropic 托管 | 否 | 是 | 是 + 移动端 | github.com | 是（Claude Desktop） | 是 | 是（自托管） |
| 技能系统 | 是（ClawHub 市场） | 是（Hooks + Plugins） | 部分（Skills） | 社区插件 | 是（市场） | 否 | 否 | 否 | 是（自动生成） |
| 自我改进技能 | 部分 | 否 | 否 | 否 | 否 | 否 | 否 | 否 | 是 |
| 浏览器 / 计算机控制 | 是（Chrome CDP） | 否 | 否 | 否 | 否 | 否 | 否 | 是（CUA） | 通过 shell |
| 编辑器内自动补全 | 否 | 否 | 经由扩展 | 否 | 优秀 | 优秀 | 否 | 否 | 否 |
| 编排其他 Agent | 否 | 否 | 否 | 否 | 否 | 否 | 否 | 否 | 是 |
| 提供商无关 | 是 | 否（仅 Claude） | 是 | 是 | 部分 | 否 | 否 | 否 | 是 |
| 自托管 | 是 | 否 | 是（CLI） | 是 | 否 | 否 | 否 | 否 | 是 |
| 自托管自主执行 | 是 | 否 | 否 | 否 | 否 | 否 | 否 | 否 | 是 |
| 后台/云端 Agent 模式 | 是 | 是（云端） | 是（Codex Cloud） | 否 | 是（云端 VM） | 是（Coding Agent） | 是（Cowork VM） | 是（Agent Mode） | 是（自托管） |
| 记忆可检视性 | 有限 | 部分 | 部分 | 部分 | 部分 | 有限 | 有限 | 有限 | 是（markdown 文件） |
| 开源 | 是（MIT） | 否 | 是（Apache 2.0） | 是 | 否 | 否 | 否 | 否 | 是 |
| 常驻自主执行 | 是 | 否 | 否 | 否 | 否 | 否 | 否 | 否 | 是 |

† Claude Code：CLAUDE.md / MEMORY.md 项目上下文，加 v2.1.59+ 起的 auto-memory；无自动跨项目累积
‡ Copilot Agentic Memory：2026 年 1 月 15 日公开预览；2026 年 3 月 4 日默认启用；按仓库作用域，28 天后自动过期
§ Claude Code 调度：仅云端托管（Anthropic 基础设施）或桌面应用；无自托管 cron
¶ Codex 调度：仅桌面应用 Automations；CLI 无原生调度
