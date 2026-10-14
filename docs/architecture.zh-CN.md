<div align="center">

[English](architecture.md) | **中文**

</div>

# Leap Aide 架构指南

Leap Aide后端为Python 标准库实现，直接以Python import 方式调用Hermes Agent SDK（与 Hermes Agent 之间没有 HTTP 跳转）；前端为 React/TypeScript，用 Vite 构建。实时更新依赖 WebSocket。 

---

## 系统总览

```
Browser (React 18 + TS + Vite + Zustand + Tailwind, M3)
   │  fetch /api/*          REST 表面（routes.py 中约 130 条路由）
   │  /api/ws/events        单条 WebSocket：token / tool / approval / done
   │  /api/ws/terminal      终端 WebSocket 通道
   │  /api/ws/vnc[/<port>]  VNC WebSocket 通道（observer 面板）
   │  GET …/stream/replay   SSE 回放，用于会话过程中刷新重连
   ▼
backend/app/server.py       薄 HTTP 壳：TLS、QuietHTTPServer、WS upgrade 分发
backend/app/api/            路由 + 领域模块（纯 Python 标准库）
   ▼ （Python import，而非 HTTP）
hermes-agent SDK            AIAgent.run_conversation()、tools、gateway
   ▼
~/.hermes/                  agent 状态（config、memory、skills、state.db）
~/.hermes/webui/            webui 状态（会话 JSON、settings、工作区）
```

关键原则：

- **单进程**：每请求一线程，Agent 跑在守护线程上，向各会话的队列投递事件。无 asyncio，无外部消息中间件。
- **核心功能依赖 Herems Agent SDK**：后端把 Hermes Agent SDK 当Python 模块调用，state.db 由 SDK 更新，后端不直接用SQL写/更新`state.db`。
- **基于 Vite 构建前端**：Vite 把 `frontend/src` 构建到 `static/`，不要手工编辑 `static/` 下的文件。

---

## 后端

| 领域 | 模块 | 职责 |
|---|---|---|
| HTTP 壳 | `server.py`、`bootstrap.py`、`api/helpers.py`、`api/upload.py`、`api/startup.py` | ThreadingHTTPServer + TLS + WS upgrade 分发；启动/引导；`j()`/`bad()`/`require()`/`safe_resolve()`；multipart 解析；启动期依赖安装 |
| 路由 | `api/routes.py` | 全部 GET/POST 处理器，扁平 if/elif 链 |
| 流式 | `api/streaming.py`、`api/stream_manager.py`、`api/ws_events.py`、`api/notify_stream.py` | agent 调用 + 各会话事件队列；队列/流生命周期；`/api/ws/events` 事件总线；notify 转发 |
| 配置与模型 | `api/config.py`、`api/models.py`、`api/webui_schema.py`、`api/state_sync.py` | 发现/全局量/模型检测；会话模型 + CRUD + LRU 缓存；`__webui` schema 列；`/insights` 同步进 state.db |
| 会话 | `api/agent_sessions.py`、`api/session_ops.py`、`api/session_display.py`、`api/session_watcher.py` | 基于 SDK 的会话生命周期；ops/展示辅助；后台会话监视 |
| 工作区 | `api/workspace.py` | 文件操作、工作区注册、git 检测、信任级别 |
| 认证与身份 | `api/auth.py`、`api/profiles.py`、`api/onboarding.py` | 可选口令门 + 签名 cookie；profile 状态；首次运行向导 |
| 面板 | `api/terminal.py`、`api/vnc.py`、`api/observer.py`、`api/cli_viewer.py` | 终端 PTY 通道；VNC 通道；observer 界面；CLI 会话列表 |
| 业务 | `api/slash_commands.py`、`api/clarify.py`、`api/metering.py`、`api/providers.py`、`api/commands.py`、`api/background.py`、`api/updates.py` | 斜杠命令分发；clarify 对话；用量计量；provider/模型门控；命令注册表暴露；后台 goal；更新检查 |

外部依赖：仅 `pyyaml`（以及 hermes-agent SDK 及其自身依赖，由 hermes-agent venv提供）。

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
| 面板 | `/api/cron*`、`/api/skills*`、`/api/memory*`、`/api/terminal*`、`/api/vnc*`、`/api/observer*`、`/api/cli*` | 任务/cron、技能、记忆、终端、VNC、observer、CLI 会话桥 |

---

## 前端 (`frontend/src/`)

- **React 18 + TypeScript + Vite**，状态由 `stores/` 下的 **Zustand** store 管理。
- i18next 负责国际化。
- 严格遵守Google M3 Design前端规范,进行 M3 合规性评审时，应参考下列文档中的组件规范、间距、字号等标准。
    - https://www.yuque.com/advancedux/xr6e1n
    - https://www.cyub.vip/blog/2026/02/14/material-design-3-material-you-%E5%AE%8C%E6%95%B4%E5%BC%80%E5%8F%91%E6%8C%87%E5%8D%97/
- 核心 store：`useSessionStore`、`useChatStore`、`useSettingsStore`、`useWorkspaceStore`、`useApprovalStore`。
- API 调用通过 `services/api.ts`。Vite 开发服务器将 `/api` 代理到 `localhost:8787`。
- 构建输出到 `../static/`，由 Python 后端直接 serve。


## 数据流（聊天完整链路）

1. 前端 `send()` → `POST /api/chat/start`，携带 session_id + message
2. 服务端创建 `queue.Queue`，启动守护线程运行 `_run_agent_streaming()`，返回 `stream_id`
3. 前端通过 `/api/ws/events` 上的 WebSocket 连接接收事件；会话过程中刷新时通过 `GET /api/chat/session/stream/replay` 做 SSE 回放恢复
4. Agent 线程运行 `AIAgent.run_conversation()`，将 token/tool/approval/done 事件放入队列
5. `/api/ws/events` 从队列读取事件，转发给浏览器
6. 收到 `done`：更新 session messages，保存到磁盘 JSON

---

## 数据库

- Leap Aide 和 Herems Agent SDK都会使用 state.db，默认路径为 `${HERMES_HOME}/state.db`
- Leap Aide 会在 Hermes Agent SDK之外扩展 state.db的表结构，会在表中增加一些额外的列，这些列名必须以 __webui 结尾，只有 Leap Aide 会使用这些列。
- Leap Aide 对 state.db 的更新/写操作都必须依赖 Hermes Agent SDK的 High Level API，不允许直连 state.db 进行数据库底层的更新/写操作。由于 Hermes Agent SDK 不感知任何 __webui 列，因此所有 Hermes Agent SDK提供的 High Level API 不会读写 __webui 列。为了实现业务功能，Leap Aide 针对 __webui 列的更新/写操作可以允许直连state.db。
- Leap Aide 不用考虑数据库的向前兼容，不用考虑数据的迁移，比如X__webui的列数据类型从TEXT换成INT，可以假设 Leap
 Aide 第一次运行，没有历史数据。
- Leap Aide 不用考虑数据库中的历史数据修复，如果 db 中旧的数据的值在新的代码中是非法的，也不需要代码中进行自动数据修复，直接报错。

---

## 配置

| 变量 | 默认值 | 说明 |
|---|---|---|
| `HERMES_WEBUI_HOST` | `127.0.0.1` | 绑定地址 |
| `HERMES_WEBUI_PORT` | `8787` | 端口 |
| `HERMES_WEBUI_STATE_DIR` | `~/.hermes/webui` | 状态目录（会话 JSON、设置、工作区） |
| `HERMES_WEBUI_DEFAULT_WORKSPACE` | *（自动发现）* | 默认工作区路径 |
| `HERMES_WEBUI_DEFAULT_MODEL` | *（空）* | 默认模型；留空表示"从已配置的提供商中自动发现" |
| `HERMES_WEBUI_PASSWORD` | *（未设置）* | 设置后启用口令门 |
| `HERMES_WEBUI_TLS_CERT` / `HERMES_WEBUI_TLS_KEY` | *（未设置）* | TLS 证书 / 密钥 |
| `HERMES_WEBUI_AGENT_DIR` | *（自动发现）* | hermes-agent **安装**目录 |
| `HERMES_HOME` | `~/.hermes` | hermes-agent **状态**目录（配置、记忆、技能、`state.db`） |
| `HERMES_WEBUI_PYTHON` | *（自动发现）* | Python 可执行文件覆盖 |
| `HERMES_WEBUI_ALLOWED_ORIGINS` | *（未设置）* | 额外允许的来源（origin） |
| `HERMES_WEBUI_AUTO_INSTALL` | *（未设置）* | `=1` 表示启动时自动安装依赖 |
| `HERMES_WEBUI_ONBOARDING_OPEN` | *（已设置）* | 打开首次运行向导 |
| `LOG_LEVEL` | `WARNING` | Python 日志级别 |

---

## 架构决策记录（ADR）

每条记录一个历史决策及其当前状态。

### ADR-001：示例
**决策：** 待补充
**理由：** 待补充
**状态：** 待补充