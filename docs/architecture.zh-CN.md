---
title: LeapAide 架构指南
---

<div align="center">

[English](architecture.md) | **中文**

</div>

# LeapAide 架构指南

LeapAide 后端为 Python 标准库实现，直接以 Python import 方式调用Hermes Agent SDK（与 Hermes Agent 之间没有 HTTP 跳转）；前端为 React/TypeScript，用 Vite 构建。实时更新依赖 WebSocket。 

---

## 系统总览

```
Browser (React 18 + TS + Vite + Zustand + Tailwind, M3)
   │  fetch /api/*          REST surface (~130 routes in routes.py)
   │  /api/ws/events        single WebSocket: token / tool / approval / done
   │  /api/ws/terminal      terminal WebSocket channel
   │  /api/ws/vnc[/<port>]  VNC WebSocket channel (observer panel)
   │  GET …/stream/replay   SSE replay, used to reconnect mid-session on refresh
   ▼
backend/app/server.py       thin HTTP shell: TLS, QuietHTTPServer, WS upgrade dispatch
backend/app/api/            route + domain modules (Python stdlib only)
   ▼ (Python import, not HTTP)
hermes-agent SDK            AIAgent.run_conversation(), tools, gateway
   ▼
~/.hermes/                  agent state (config, memory, skills, state.db)
~/.hermes/webui/            webui state (sessions JSON, settings, workspaces)
```

关键原则：

- **单进程**：每请求一线程，Agent 跑在守护线程上，向各会话的队列投递事件。无 asyncio，无外部消息中间件。
- **核心功能依赖 Herems Agent SDK**：后端把 Hermes Agent SDK 当Python 模块调用，state.db 由 SDK 更新，后端不直接用 SQL 写/更新`state.db`。
- **基于 Vite 构建前端**：Vite 把 `frontend/src` 构建到 `static/`，不要手工编辑 `static/` 下的文件。

---

## 后端

| 领域 | 模块 | 职责 |
|---|---|---|
| HTTP 壳 | `server.py`、`bootstrap.py`、`api/helpers.py`、`api/upload.py`、`api/startup.py` | ThreadingHTTPServer + TLS + WS upgrade 分发；启动/引导；`j()`/`bad()`/`require()`/`safe_resolve()`；multipart 解析；启动期依赖安装 |
| 路由 | `api/routes.py` | 全部 GET/POST 处理器，扁平 if/elif 链 |
| 流式 | `api/streaming.py`、`api/stream_manager.py`、`api/ws_events.py`、`api/notify_stream.py` | agent 调用 + 各会话事件队列；队列/流生命周期；`/api/ws/events` 事件总线；notify 转发 |
| 配置与模型 | `api/config.py`、`api/model_catalog.py`、`api/models.py`、`api/webui_schema.py`、`api/state_sync.py` | 发现/全局量/模型检测；JSON 模型目录（加载/别名/实时查询/合并）；会话模型 + CRUD + LRU 缓存；`__webui` schema 列；`/insights` 同步进 state.db |
| 会话 | `api/agent_sessions.py`、`api/session_ops.py`、`api/session_display.py`、`api/session_watcher.py` | 基于 SDK 的会话生命周期；ops/展示辅助；后台会话监视 |
| 工作区 | `api/workspace.py` | 文件操作、工作区注册、git 检测、信任级别 |
| 认证与身份 | `api/auth.py`、`api/profiles.py` | 可选口令门 + 签名 cookie；profile 状态 |
| 面板 | `api/terminal.py`、`api/vnc.py`、`api/observer.py`、`api/cli_viewer.py` | 终端 PTY 通道；VNC 通道；observer 界面；CLI 会话列表 |
| 浏览器桌面 | `api/global_desktop.py`、`api/browser_receptionist.py`、`api/browser_desktop.py`、`api/cdp_pipe.py`、`compat/browser_cdp.py`、`compat/browser_session.py`、`compat/desktop_guard.py` | 共享 X/Chrome 桌面生命周期；CDP 接待员（唯一的 CDP 入口）；SDK 侧 CDP/会话补丁；终端级桌面边界守卫 |
| 业务 | `api/slash_commands.py`、`api/clarify.py`、`api/metering.py`、`api/providers.py`、`api/commands.py`、`api/background.py`、`api/updates.py` | 斜杠命令分发；clarify 对话；用量计量；provider/模型门控；命令注册表暴露；后台 goal；更新检查 |

外部依赖：仅 pyyaml（以及 Hermes Agent SDK 及其自身依赖，由 Hermes Agent SDK venv 提供）。

---

## API

| 域 | 前缀 / 端点 | 语义 |
|---|---|---|
| 健康 | `GET /health` | 存活 + 版本探测 |
| 会话 | `/api/session`、`/api/sessions/*` | 列出/创建/更新/删除会话；各会话操作 |
| 聊天 | `POST /api/chat/start`、`/api/chat/stream/status`、`/api/chat/session/stream/replay` | 启动一个回合；流状态；SSE 回放 |
| 实时 | `/api/ws/events`、`/api/ws/terminal`、`/api/ws/vnc` | WebSocket 通道 |
| 工作区 | `/api/workspace/*`、`/api/workspaces/*` | 文件操作 + 工作区注册（两级信任） |
| 上传 | `POST /api/upload` | multipart 上传（必须在 `read_body()` **之前**检查大小） |
| Clarify | `/api/clarify/*` | clarify 对话提交/清除 |
| 命令 | `/api/commands` | 斜杠命令注册表（hermes-agent COMMAND_REGISTRY + 本地 handler） |
| Profile | `/api/profiles*` | Profile CRUD 与切换 |
| 认证 | `/api/auth*`、`/login` | 口令门、会话检查 |
| 运维 | `/api/settings*`、`/api/git-info`、`/api/updates*`、`/api/insights*` | 设置、git 徽章、更新检查、用量 insights |
| 面板 | `/api/cron*`、`/api/skills*`、`/api/memory*`、`/api/terminal*`、`/api/vnc*`、`/api/observer*`、`/api/cli*`、`/api/browser-desktop` | 任务/cron、技能、记忆、终端、VNC、observer、CLI 会话桥、共享浏览器桌面状态 |

---

## 浏览器桌面

当前提供了一个全局共享的浏览器桌面，通过 `x-browser-desktop skill` 来操作。主要目的是可视化浏览器操作，另外，用户也可以操作这个桌面，用来保存登录信息，避免 Agent 网页任务被打断。

整个桌面实例只跑 **一个** Chrome（issue #37）：所有聊天会话共用它——桌面面板显示的是同一个"公共客厅"，而不是每个会话各开一个浏览器。Chrome 的 CDP 控制端点在网络上不可达：它以 `--remote-debugging-pipe` 启动，CDP 帧通过继承的文件描述符传递（Chrome 读 fd 3、写 fd 4），不占任何 TCP 端口。因此 `ps`、`ss` 里看不到任何可连接的 Chrome 直连 CDP 入口。

**接待员**（`api/browser_receptionist.py`）就是持有这根管道的进程，它作为代理提供整个实例唯一的 CDP 入口：它是一个跑在 `127.0.0.1:6900` 上的 HTTP + WebSocket 服务。客户端从不直接和 Chrome 对话，而是和接待员对话，由接待员决定哪些东西能到 Chrome、哪些东西能回到客户端。共享一个 Chrome 之所以安全，靠的就是这一层。

### 相关模块

| 模块 | 职责 |
|---|---|
| `api/global_desktop.py` | 管理 X 栈（Xvfb + openbox + x11vnc + fcitx5），拉起接待员与 Chrome，监督它们的健康，发布 `browser_desktop.json` |
| `api/browser_receptionist.py` | CDP 代理：发现类端点、WebSocket 端点、归属表、命令转发 |
| `api/cdp_pipe.py` | 以 `--remote-debugging-pipe` 拉起 Chrome，读写以 NUL 分隔的 JSON 帧 |
| `api/browser_desktop.py` | `GET /api/browser-desktop`——侧边栏面板轮询的状态 |
| `compat/browser_cdp.py` | 运行时补丁：把 Hermes SDK 指向 `<receptionist>/json/version?sid=<session_id>` |
| `compat/browser_session.py` | 让 SDK 的浏览器会话在共享 Chrome 里拿到属于自己的标签页 |
| `compat/desktop_guard.py` | 终端级守卫：裸 CDP、VNC 端点不允许被 shell 命令触及 |

状态文件 `$HERMES_WEBUI_STATE_DIR/browser_desktop.json` 是各 wrapper 与 SDK 补丁的唯一事实来源：里面写着 `receptionist_url` 和一个 `status`（`active` / `degraded`）。使用该地址前必须先看 status——SDK 劫持 Patch 把非 `active` 一律当作"没有配置浏览器桌面"，浏览器工具于是退回到本地 headless。

```text
   client
     │  GET /json/version?sid=<session_id>
     ▼
   receptionist -- fd 3 / fd 4 --> Chrome
     │  webSocketDebuggerUrl: ws://127.0.0.1:6900/devtools/browser/global?sid=<session_id>
     ▼
   client -- CDP frames (filtered by tab owner) --> receptionist -- fd 3 / fd 4 --> Chrome
```

### 多会话并发，靠标签页隔离

会话拿到的不是自己的 Chrome 实例，而是自己的 **标签页**，归属关系由接待员维护。每个客户端都要用 `?sid=<session_id>`（或 `X-LeapAide-Session` 头）声明自己属于哪个会话。接待员维护三张表：

| 表 | 映射 |
|---|---|
| `_tabs` | `targetId` → 归属的连接 |
| `_cdp_sessions` | CDP `sessionId` → 归属的连接 |
| `_conns` / `_sessions` | 在线连接，按连接 id 与按 sid 分别索引 |

隔离在四个地方生效：

- **发现被过滤。** `Target.getTargets` 由接待员自己作答——它在内部问 Chrome，然后只返回本连接拥有的标签页。`/json/list` 只返回请求方 sid 的标签页。别的会话的标签页根本不会出现在回答里。
- **操作被拦截。** `Target.closeTarget`、`Target.activateTarget`、`Target.attachToTarget` 若 `targetId` 不属于调用方，一律拒绝并返回 `target not owned by this connection`；`sessionId` 属于别的连接时同样拒绝。
- **事件被丢弃。** `Target.attachedToTarget`、`Target.targetDestroyed` 以及任何带外来 `sessionId` 的消息都不会送达 CDP socket。
- **标签页有上限且随连接回收。** 一个连接最多拥有 10 个标签页（`MAX_TARGETS_PER_CONN`），并且连接断开时标签页随之关闭。

因此两个会话可以在同一个 Chrome 里同时干活：共享的是窗口和实例，不是标签页。

### CDP WebSocket 从哪来

Chrome 不会直接暴露任何 CDP 调试地址，而是由接待员提供 CDP WebSocket：

1. 客户端向接待员发 `GET /json/version?sid=<session_id>`。没有 sid 的请求会得到 `400`，并在响应体里写明要求的 URL 形状。
2. 接待员返回
   `webSocketDebuggerUrl: ws://127.0.0.1:6900/devtools/browser/global?sid=<session_id>`。
   sid 被嵌进 URL，于是下游工具重连时身份不丢，不必在每一次通信时都重新声明自己的身份。
3. 客户端打开这个 WebSocket。接待员接受连接、登记一条连接记录（id、sid、对端地址），开始读帧。

接受这一步有几点特别：

- **不会替你建标签页。** 连接建立时标签页集合为空，由客户端自己创建（`Target.createTarget`，或当该 sid 已有连接时用 `GET /json/new`）——新建的连接不会碰到任何自己没要求过的东西。
- **没有页面级的 socket。** `GET /devtools/page/<targetId>` 对非归属方返回 `403`，即使归属也返回 `501`（"use the browser WS endpoint"）。所有流量都走那一条浏览器级 socket。
- **socket 带心跳**（30 秒），客户端消失了能被及时发现，而不是一直挂着。

### CDP 命令如何转发

接待员的主要职责是代理转发：命令的 `method` 与 `params` 原样到达 Chrome。但在转发之外的会有一系列动作。

**转发之前先做检查。**

| 命令 | 拒绝时的报错 |
|---|---|
| 任何带 `sessionId` 的命令 | `sessionId not owned by this connection` |
| 超过 10 个标签页上限后的 `Target.createTarget` | `target cap reached` |
| 对外来标签页执行 `Target.closeTarget` / `Target.activateTarget` / `Target.attachToTarget` | `target not owned by this connection` |
| Chrome 不在时的任何命令 | `browser unavailable` |

**id 会被改写。** 每个客户端请求 id 在写入 Chrome 管道前被换成接待员的全局 id（一个从 `-1` 开始的计数器），回程时再换回客户端原本的 id。这样两个客户端可以用相同的 id 编号而不冲突，每条响应也都能对上发起它的连接。接待员自己发出的命令使用另一段互不重叠的 id 区间，从 `INT32_MIN` 开始——Chrome 的管道解析器只接受 int32 范围的 id。

**待处理项带标签，因此能读懂回包。** 每个在途请求，接待员都记下归属连接、原始 id，以及一个种类：

| 种类 | 回包告诉接待员的事 |
|---|---|
| `client-create` | `Target.createTarget` 成功 → 新标签页归这条连接所有 |
| `client-attach` | `Target.attachToTarget` 成功 → 新的 CDP `sessionId` 归这条连接所有 |
| `client-close` | `Target.closeTarget` 成功 → 忘掉这个标签页（管道模式下 Chrome 关闭标签页不会发 `targetDestroyed`） |
| `client` / `internal` | 原样透传 |

客户端 `Target.getTargets` 的发送请求和结果返回都会被过滤：接待员自己发一条内部的 `Target.getTargets`，把结果过滤成调用方自己的标签页，再返回。

`sessionId` 属于别的连接的响应会被丢弃；与标签页无关的浏览器级事件广播给所有人，标签页级事件只发给归属方。

### WebSocket 如何关闭

关闭不只是收个 socket，而是一条清理路径。客户端 socket 结束（close 帧、错误、连接掉线）时，接待员对该连接执行同一套收尾流程：

1. 标记连接已关闭，之后不再有消息路由给它；
2. 把它拥有的**每一个标签页**逐个用内部的 `Target.closeTarget` 关掉，并忘掉这些 tab id——掉线的连接不会在共享桌面上留下残余标签页；
3. 清掉绑定在该连接上的 CDP session id；
4. 清掉该连接仍在等待中的请求，晚到的回包不会投递给已经死掉的 socket；
5. 从各张表里移除该连接，并关闭 WebSocket。

另有两个回收机制补上"正常关闭"覆盖不到的情况：

- **回合结束清扫。** 一个聊天回合结束时，后端调用 `close_session_tabs(session_id)`，关掉该 sid 下仍然挂着、且已经空闲（宽限 30 秒）的连接——这正是为了收拾进程崩溃、来不及关 socket 的浏览器工具。该行为由 `observer_tab_cleanup` 设置控制（默认 `session`）。
- **Chrome 死亡与整体关停。** Chrome 退出时，接待员清空各张表并带退避地重新拉起 Chrome。接待员自身关停时，先丢掉所有连接，再通过管道终止 Chrome。

### 端口、文件与环境变量

| 名称 | 取值 |
|---|---|
| 接待员 HTTP/WS | `127.0.0.1:6900`（`HERMES_RECEPTIONIST_PORT`） |
| 桌面 VNC / 显示号 | `5900`（`HERMES_GLOBAL_VNC_PORT`）、显示号 `:99`（`HERMES_GLOBAL_DISPLAY`） |
| Chrome CDP | 无——只有父子管道 |
| 状态文件 | `$HERMES_WEBUI_STATE_DIR/browser_desktop.json` |
| 总开关 | `HERMES_GLOBAL_DESKTOP=0`——不起桌面，浏览器工具退回本地 headless |

---

## 前端 (`frontend/src/`)

- **React 18 + TypeScript + Vite**，状态由 `stores/` 下的 **Zustand** store 管理。
- i18next 负责国际化。
- 严格遵守Google M3 Design前端规范,进行 M3 合规性评审时，应参考下列文档中的组件规范、间距、字号等标准。
    - [网络参考资源1](https://www.yuque.com/advancedux/xr6e1n)
    - [网络参考资源2](https://www.cyub.vip/blog/2026/02/14/material-design-3-material-you-%E5%AE%8C%E6%95%B4%E5%BC%80%E5%8F%91%E6%8C%87%E5%8D%97/)
- 核心 store：`useSessionStore`、`useChatStore`、`useSettingsStore`、`useWorkspaceStore`、`useApprovalStore`。
- API 调用通过 `services/api.ts`。Vite 开发服务器将 `/api` 代理到 `localhost:8787`。
- 构建输出到 `../static/`，由 Python 后端直接 serve。


## 数据流（聊天完整链路）

- 前端 `send()` → `POST /api/chat/start`，携带 session_id + message
- 服务端创建 `queue.Queue`，启动守护线程运行 `_run_agent_streaming()`，返回 `stream_id`
- 前端通过 `/api/ws/events` 上的 WebSocket 连接接收事件；会话过程中刷新时通过 `GET /api/chat/session/stream/replay` 做 SSE 回放恢复
- Agent 线程运行 `AIAgent.run_conversation()`，将 token/tool/approval/done 事件放入队列
- `/api/ws/events` 从队列读取事件，转发给浏览器
- 收到 `done`：更新 session messages，保存到磁盘 JSON

---

## 数据库

- LeapAide 和 Herems Agent SDK 都会使用 state.db，默认路径为 `${HERMES_HOME}/state.db`
- LeapAide 会在 Hermes Agent SDK 之外扩展 state.db的表结构，会在表中增加一些额外的列，这些列名必须以 __webui 结尾，只有 LeapAide 会使用这些列。
- LeapAide 对 state.db 的更新/写操作都必须依赖 Hermes Agent SDK 的 High Level API，不允许直连 state.db 通过 SQL 进行数据库底层的更新操作。由于 Hermes Agent SDK 不感知任何 __webui 列，因此所有 Hermes Agent SDK提供的 High Level API 不会读/更新 __webui 列。为了实现业务功能，LeapAide 允许直连 state.db 更新 __webui 列。
- LeapAide 不用考虑数据库的向前兼容，不用考虑数据的迁移，比如X__webui的列数据类型从TEXT换成INT，可以假设 LeapAide 第一次运行，没有历史数据。
- LeapAide 不用考虑数据库中的历史数据修复，如果 db 中旧的数据的值在新的代码中是非法的，也不需要在后端代码中进行自动数据修复，直接报错即可。

---

## 配置

| 变量 | 默认值 | 说明 |
|---|---|---|
| `HERMES_WEBUI_HOST` | `127.0.0.1` | 绑定地址 |
| `HERMES_WEBUI_PORT` | `8787` | 端口 |
| `HERMES_WEBUI_STATE_DIR` | `~/.hermes/webui` | 状态目录（会话 JSON、设置、工作区） |
| `HERMES_WEBUI_DEFAULT_WORKSPACE` | `~/.hermes/webui/workspaces/default` | 默认工作区路径（内置 "Default" 条目的唯一事实来源） |
| `HERMES_WEBUI_DEFAULT_MODEL` | *（空）* | 默认模型；留空表示"从已配置的提供商中自动发现" |
| `HERMES_WEBUI_PASSWORD` | *（未设置）* | 设置后启用口令门 |
| `HERMES_WEBUI_TLS_CERT` / `HERMES_WEBUI_TLS_KEY` | *（未设置）* | TLS 证书 / 密钥 |
| `HERMES_WEBUI_AGENT_DIR` | *（自动发现）* | hermes-agent **安装**目录 |
| `HERMES_HOME` | `~/.hermes` | hermes-agent **状态**目录（配置、记忆、技能、`state.db`） |
| `HERMES_WEBUI_PYTHON` | *（自动发现）* | Python 可执行文件覆盖 |
| `HERMES_WEBUI_ALLOWED_ORIGINS` | *（未设置）* | 额外允许的来源（origin） |
| `HERMES_WEBUI_AUTO_INSTALL` | *（未设置）* | `=1` 表示启动时自动安装依赖 |
| `HERMES_WEBUI_PREWARM_TOOL_DEFS` | *（默认启用）* | 启动期工具定义快照；`=0` 关闭（首条消息改付工具检查开销） |
| `HERMES_WEBUI_PREWARM_WAIT_S` | `10` | 聊天线程等待预热工具定义的最长秒数；`=0` 不等待 |
| `HERMES_WEBUI_FORCE_IPV4` | *（默认启用）* | 出网连接优先 IPv4（规避 IPv6 黑洞挂起）；`=0` 关闭 |
| `HERMES_WEBUI_CONNECTIVITY_PROBE` | *（默认启用）* | 后台提供商连通性探测（启动时 + 保存 key 后）；`=0` 关闭 |
| `HERMES_WEBUI_CATALOG_QUERY_TIMEOUT_S` | `3.0` | 目录构建期间每个 provider 实时模型查询的超时（秒） |
| `HERMES_WEBUI_COLD_START_WAIT_S` | `5.0` | 聊天开始时等待启动期模型目录构建的最长秒数，超时则拒绝本轮；`=0` 不等待 |
| `LOG_LEVEL` | `WARNING` | Python 日志级别 |

---

## 模型目录与刷新

Provider 的展示名、别名、基线模型列表、端点、认证头与刷新策略统一存放在
单个 JSON 文件 `backend/app/api/data/model_catalog.json`（schema v1），
由 `api/model_catalog.py` 加载并校验。曾镜像这些数据的 Python 表
（`_PROVIDER_MODELS`、`_FALLBACK_MODELS`、`_PROVIDER_DISPLAY`、
`_PROVIDER_ALIASES`、`_OPENAI_COMPAT_ENDPOINTS`）已删除——JSON 文件是
*某个 provider 提供哪些模型* 的唯一真相源。

要点：

- **每 provider 记录**：`display_name`、`is_custom`、`aliases[]`、
  `auth{type,env_var(s),header}`（Anthropic 用 `x-api-key`，其余 Bearer）、
  `endpoint{base_url,models_path,trust_local}`、
  `refresh{mode: api|none, membership: replace|curated, timeout_s}`、
  `default_model`，以及人工维护的 `models[]` 初始列表（模型 id 以选择器使用的
  `@provider:model` 形态存放；少数 provider 保留裸 id —— gemini、minimax、
  openrouter，如 `MiniMax-M2.7`、`openai/gpt-5.4-mini` —— 在组装选择器时补上
  `@provider:` 前缀，因此用户看到的 id 一律是 `@provider:model`）。对
  `refresh.mode=none` 的 provider 它是唯一来源；对 `refresh.mode=api` 的
  provider 它只是首启种子——一旦实时查询成功即被 API 结果整体取代）。
- **自定义端点**：`is_custom`（默认 `false`）标记端点是用户自己的
  OpenAI 兼容服务器、而非厂商 API 的 provider——随包目录里是 `ollama` 与
  `lmstudio`。这类 provider 不需要 API key，且用户为它配置的 `base_url`
  在切换默认模型时会保留（切到厂商 provider 仍然会清掉）。这个标记是数据，
  不是硬编码的 slug 列表：`model_catalog.is_custom_provider()`。
- **单一确定合并规则**（`resolve_provider_models`）：config.yaml 中显式的
  `providers.<id>.models` 永远最优先且不参与查询。`membership=replace`
  时实时查询成功的结果是唯一事实来源——成员与顺序以 API 为准，JSON 基线
  仅为 API 仍在列出的 id 贡献展示标签（API 不再列出的模型消失，API 新增
  的模型立即出现）；`membership=curated` 时取基线与实时 id 的交集。查询
  失败或返回空列表时保留上次成功列表（内存或磁盘 envelope）或基线，绝不
  清空分组。旧的“先试 agent、再试硬编码端点表、最后静态表”多级兜底已删除。
- **刷新触发**复用现有 `/api/models` 构建机制（不新增后台线程）：24h TTL、
  config.yaml 或 `model_catalog.json` mtime 变化、保存 key、启动预热。
  `refresh.mode=api` 的 provider 并发查询（`ThreadPoolExecutor`，≤6 worker）
  并按 provider 超时。资格判定仍以 key/OAuth 为准：目录中声明 API-key 认证的
  provider 在没有 key 时会被移除——active provider 也不例外，因此删除 key 会
  连同该 provider 的模型一起消失，而不是回退到 JSON 基线。没有 API-key 要求
  的 provider（本地端点、未知/自定义 provider）在 active 时保留；自定义
  `model.base_url` 拉取仍会重新加入其 slug。
- **Picker 的 provider 提示**：`/api/models` 会给每个模型 ID 加上其所属
  provider 的前缀（`@provider:model`），含 `/` 的 ID 也不例外（OpenRouter 的
  `deepseek/deepseek-v4-flash` 变成
  `@openrouter:deepseek/deepseek-v4-flash`），active provider 自己的模型同样
  带前缀。裸名或只有 slash 的 ID 并不指明路由：
  `resolve_model_provider()` 会剥掉与 `model.provider` 相同的前缀，因此没有
  该提示时，只要 active provider 恰好是 `deepseek`，OpenRouter 的选择就会被
  静默路由到 DeepSeek 直连 API。提示只在第一个冒号处切分，模型部分保留其余
  slash 与冒号（如 `@ollama1:qwen3-vl:235b`）；
  `POST /api/default-model` 在写入 config.yaml 前会把提示解析回
  `model.default` + `model.provider`。
- **会话存的模型与 picker 条目的形态**：picker 按 id 字面比较，因此会话存的
  模型必须与它对应的目录条目同形，否则弹出的 model selection 里不会有任何
  勾选。目录中的每个 id 都是 `@provider:model`，而
  `normalize_model_prefix()` 会把 config.yaml 里的裸名或带命名空间的值
  （`deepseek-flash`、`anthropic/claude-opus-4.6`）映射到实际提供该模型的
  provider 身份上，会话存的正是这个身份。更早创建的会话可能仍是目录已不再
  列出的形态（裸名，或其 provider 已下架的模型）：前端用
  `findCatalogModelId()` 把存的 id 解析回目录条目以便勾选，而显示路径以
  WebUI 自己维护的身份（`model__webui`）为准。该提示只是内部路由信息，不会
  出现在界面文案里。
- **磁盘 envelope**：模型缓存快照持久化
  `{schema_version, active_provider, default_model,
  default_model_available, default_model_provider_has_key, groups,
  provider_models, fetched_at}`，重启后首个响应字段完整，并保留每
  provider 的上次成功列表；旧的 `{仅 groups}` 文件仍可加载。
- **默认模型可用性（issue #44）**：`/api/models` 返回
  `default_model_available`（`true` / `false` / `null`）。该值在把配置的
  默认模型注入 picker *之前*，由真实 provider 分组计算；未知时返回
  `null`（config-only 响应、构建中或旧磁盘快照）。匹配限定在默认模型
  自己的 provider 内（以 `model.provider` 为准；仅无 provider 的配置才
  回退到按模型名比较），因此其他 provider 下的同名模型不会掩盖默认模型
  已被移除的事实。分组列表为空（实时结果为空或所有 provider 都被拒绝）
  时同样保持 `null`——除非默认模型所属 provider 是目录中需要 API key 的
  provider 且当前没有 key，这是确定的失败。
  配套字段 `default_model_provider_has_key`（`true` / `false` / `null`；
  本地/OAuth/未知 provider 为 `null`）让 UI 能提示“provider 的 API key
  已被删除”，而不是误报“provider 不再提供该模型”。配置的默认模型不可用时
  仍会注入以便 UI 展示，并标记 `unavailable: true`；后端绝不会把它改写成
  其他模型——只由 UI 提示。当 provider 分组随 key 一起消失时，composer
  仍显示配置值并给出说明，而不是静默选中另一个模型；用户在 composer
  中显式选择其他模型后，该提示消失，因为该选择会替代默认值用于当前会话。
  SDK 原始的
  “provider set in config.yaml but no API key” 错误会在两条流式失败路径中
  被翻译为同一段面向 Settings 的提示。未显式指定模型的新会话通过
  `get_default_model_for_new_session()` 解析：优先有效默认值（环境变量
  `HERMES_MODEL` / `OPENAI_MODEL` / `LLM_MODEL` → config.yaml
  `model.default` → 内置常量），仅当其为空时才使用目录中第一个可用模型。
  Settings 通过 `POST /api/default-model` 保存该控件，由后端把 WebUI 内部
  的 `@provider:model` 形式还原为 config.yaml 的 `model.default` +
  `model.provider`。
- **会话所用模型的可用性（issue #44 后续）**：目录只报告*配置默认模型*的
  可用性，因此运行其他模型的既有会话会通过
  `GET /api/models/availability?model=…` 向后端询问。返回值
  `{model, provider, available, provider_has_key}` 基于实时 provider 分组
  计算，匹配规则与默认模型一致，限定在模型自己的 provider 内：
  `available` 为 `true` / `false` / `null`（未知——没有可比较的分组），
  `provider_has_key` 为 `true` / `false` / `null`（该 provider 不使用
  API key 鉴权）。即使目录没有分组，缺失的 key 也足以确定结论。composer
  会保留会话自身的模型（标记为已失效）并说明原因，而不是静默高亮另一个
  provider 的下一个可用模型——既有会话与 welcome 状态行为一致，后者会提示
  配置默认模型不可用。目录内容变化时会重新查询，因此删除 provider 的
  API key 后能立即反映出来。
- **文案按会话语境而非模型取值决定**：全新对话用的是配置的默认模型，因此
  说的是“默认模型”；既有会话用的是用户为它选定的模型，因此说的是“当前
  模型”——即使两者恰好是同一个模型。composer 根据会话上下文判断（它知道
  该对话是否有自己的模型），后端根据 session id 判断（`_handle_chat_start`
  为 welcome 状态的首条消息新建会话，因此知道这是不是新对话）。
  `streaming._missing_key_wording()` 为两条流式失败路径挑出对应的
  label、message 与 hint。
- **SSRF 防护**（`model_catalog._check_url_safe`）：scheme 白名单
  （http/https）+ 拒绝解析到私网 IP 的主机名；已知本机主机
  （ollama/localhost/lmstudio）、目录 `endpoint.trust_local` 与用户声明的
  `custom_providers` 主机名可豁免。
- **Z.ai 端点修复**：目录使用 `https://api.z.ai/api/paas/v4`
  （旧硬编码 `api.z.ai/v1` 已 404）。同时新增已验证的火山方舟 provider
  （`ark`，`ARK_API_KEY`）。
- **维护脚本**：`scripts/sync_models.py` 从 OpenRouter 公开
  `/api/v1/models` 端点拉取并生成基线草稿（`--map-labels` 保留已有 id 的
  策展标签）。脚本只输出 JSON 片段供人工审核，绝不直接写目录文件，也不在
  CI 中运行。

---

## 模型身份（`@provider:model`）

WebUI 展示的每个模型都以 `@provider:model` 标识。provider 段按**第一个**
冒号切分，因此网关命名空间（`@openrouter:anthropic/claude-opus-4.6`）与
Ollama tag（`@ollama1:qwen3-vl:235b`）都能原样保留。

- **存放位置**：身份存在 WebUI 自有的 `model__webui` 列里。SDK 自有的
  `model` 列只保存裸模型 id——SDK 永远看不到 `@provider:` 前缀。
- **唯一可路由形态**：裸 id、带命名空间的 `provider/model` 值或 `unknown`
  都没有 provider 提示，无法路由，因此一律拒绝
  （`Session._is_valid_model_identity`），而不是落到 config.yaml 的默认
  端点。已废弃的 `@custom:<name>:model` 形式同样被拒绝——具名自定义
  provider 用名称 slug 标识（`@ollama1:model`）。
- **具名自定义 provider**：WebUI slug（名称小写、空格折叠为短横、`@ : /`
  替换为短横）是存放与展示形态；交给 SDK 的则是 SDK 自己的
  `custom:<slug>`（`config.sdk_custom_provider_id`）——SDK 依据该前缀
  决定是否应用 `extra_body`、绕过内置 provider 遮蔽，且只接受自己的别名
  集合。
- **自定义端点**分两类。config.yaml 里声明的*具名* provider（`custom_providers:`）
  用名称 slug 标识，交给 SDK 的是 `custom:<slug>`。*目录里的*自定义端点
  （`is_custom`：`ollama`、`lmstudio`，以及合成的 `local` slug）保留目录
  slug——SDK 认识这些名字——不需要 API key，且切换默认模型时保留用户为它
  配置的 `base_url`。
- **冷启动**：聊天开始路径会等待启动期目录构建（有界等待，由
  `HERMES_WEBUI_COLD_START_WAIT_S` 控制）再决定是否拒绝本轮，因此冷启动
  后的第一条消息不会以 "Session model is invalid" 收场。

---

## 架构决策记录（ADR）

每条记录一个历史决策及其当前状态。

### ADR-001：示例
- **决策：** 待补充
- **理由：** 待补充
- **状态：** 待补充