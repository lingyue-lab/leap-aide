# Leap Aide 架构指南

[English](architecture.md) | **简体中文**

Leap Aide（Hermes Web UI）是 Hermes agent 的浏览器界面：后端为 Python
标准库实现，直接以 Python import 方式调用 hermes-agent SDK（与 agent 之间
没有 HTTP 跳转）；前端为 React/TypeScript，用 Vite 构建。实时更新以
WebSocket 优先。本文描述的是系统**现状**；历史叙事见
[CHANGELOG.md](../CHANGELOG.md)。

---

## 1. 系统总览

```
Browser (React 18 + TS + Vite + Zustand + Tailwind, M3)
   │  fetch /api/*          REST 表面（routes.py 中约 130 条路由）
   │  /api/ws/events        单条 WebSocket：token / tool / approval / done
   │  /api/ws/terminal      终端 WebSocket 通道
   │  /api/ws/vnc[/<port>]  VNC WebSocket 通道（observer 面板）
   │  GET …/stream/replay    SSE 回放，用于会话断线重连
   ▼
backend/app/server.py       薄 HTTP 壳：TLS、QuietHTTPServer、WS upgrade 分发
backend/app/api/            路由 + 领域模块（纯 Python 标准库）
   ▼ （Python import，而非 HTTP）
hermes-agent SDK            AIAgent.run_conversation()、tools、gateway
   ▼
~/.hermes/                  agent 状态（config、memory、skills、state.db）
~/.hermes/webui/            webui 状态（会话 JSON、settings、工作区）
```

关键特性：

- **单进程** —— `ThreadingHTTPServer`，每请求一线程；agent 跑在守护线程上，
  向各会话的队列投递事件。无 asyncio，无外部消息中间件。
- **直接 SDK import** —— 后端把 hermes-agent 当 Python 模块调用。除 SDK
  不感知的 `__webui` 列外，WebUI 从不直接用 SQL 写 `state.db`。
- **static/ 是构建产物** —— Vite 把 `frontend/src` 构建到 `static/`
  （`emptyOutDir: true`）。绝不要手工编辑 `static/` 下的文件。

---

## 2. 后端布局

按职责分组；端点表以 `routes.py` 为唯一事实源，所以下面是地图，不是清单。

| 领域 | 模块 | 职责 |
|---|---|---|
| HTTP 壳 | `server.py`、`bootstrap.py`、`api/helpers.py`、`api/upload.py`、`api/startup.py` | ThreadingHTTPServer + TLS + WS upgrade 分发；启动/引导；`j()`/`bad()`/`require()`/`safe_resolve()`；multipart 解析；启动期依赖安装 |
| 路由 | `api/routes.py` | 全部 GET/POST 处理器，扁平 if/elif 链 |
| 流式 | `api/streaming.py`、`api/stream_manager.py`、`api/ws_events.py`、`api/notify_stream.py` | agent 调用 + 各会话事件队列；队列/流生命周期；`/api/ws/events` 事件总线；notify 转发 |
| 配置与模型 | `api/config.py`、`api/models.py`、`api/webui_schema.py`、`api/state_sync.py` | 发现/全局量/模型检测；会话模型 + CRUD + LRU 缓存；`__webui` schema 列；`/insights` 同步进 state.db |
| 会话 | `api/agent_sessions.py`、`api/session_ops.py`、`api/session_display.py`、`api/session_watcher.py` | 基于 SDK 的会话生命周期；ops/展示辅助；后台会话监视 |
| 工作区 | `api/workspace.py` | 文件操作、工作区注册、git 检测、信任级别（见 §6） |
| 认证与身份 | `api/auth.py`、`api/profiles.py`、`api/onboarding.py` | 可选口令门 + 签名 cookie；profile 状态；首次运行向导 |
| 面板 | `api/terminal.py`、`api/vnc.py`、`api/observer.py`、`api/cli_viewer.py` | 终端 PTY 通道；VNC 通道；observer 界面；CLI 会话列表 |
| 业务 | `api/slash_commands.py`、`api/clarify.py`、`api/metering.py`、`api/providers.py`、`api/commands.py`、`api/background.py`、`api/updates.py` | 斜杠命令分发；clarify 对话；用量计量；provider/模型门控；命令注册表暴露；后台 goal；更新检查 |

外部依赖：仅 `pyyaml`（以及 hermes-agent SDK 及其自身依赖，由 agent venv
提供）。

---

## 3. 前端布局

```
frontend/src/
  stores/          Zustand store —— useSessionStore、useChatStore、
                   useSettingsStore、useWorkspaceStore、useApprovalStore、
                   useCronStore、useMemoryStore、useSkillStore、
                   useTerminalStore、useProvidersGateStore、…
  stores/wsManager.ts / streamManager.ts   /api/ws/events 客户端 + 回放
  services/api.ts  /api/* 的 fetch 封装
  components/      chat、sidebar、workspace、terminal、observer、settings、
                   memory、skills、tasks、dialogs、layout、…
  i18n/            i18next；locale 仅 en 与 zh-CN
scripts/generate-m3-theme.mjs   Material 3 主题生成
  （material-color-utilities）
```

- 状态放在 Zustand store 中，一个领域一个 store。`useSessionStore` 把
  `currentSession` 作为直接 state 字段 —— 任何修改 `sessions[]` 上某字段的
  action，都必须在 id 匹配时把该字段 mirror 到 `currentSession`，否则 UI
  会滞后到下次切换会话才更新。
- 主题取值为 `system | dark | light`；旧版主题值会被钳到 dark。
- Vite 开发服务器把 `/api`（及 WS）代理到 8787 端口的后端。

---

## 4. 流式模型

**WebSocket 优先。** 前端与 `/api/ws/events` 保持一条连接；后端 agent 线程
把 `token`、`tool`、`approval`、`done`、`error` 事件推进各会话的队列，由 WS
端点转发。这取代了早期每条流一条 SSE 的设计。

- `/api/ws/events` —— 主事件总线（取代 SSE 端点
  `/api/chat/session/stream` 与 `…/notify`）。
- `/api/ws/terminal` —— 终端 PTY 通道。
- `/api/ws/vnc[/<port>]` —— observer 面板的 VNC 通道。
- `GET /api/chat/session/stream/replay` —— SSE，仅保留用于会话回放与断线
  重连恢复。
- `GET /api/chat/stream/status` —— 流状态查询。**不存在**
  `GET /api/chat/stream?stream_id=` 端点。

聊天完整链路：`POST /api/chat/start`（session_id + message）→ 服务端创建
队列、拉起 agent 线程（`AIAgent.run_conversation(task_id=…)`）、返回
stream id → agent 产生事件时由 WS 总线投递 → 收到 `done` 后持久化会话
消息。

---

## 5. 会话与状态模型

- **WebUI 状态目录** —— `HERMES_WEBUI_STATE_DIR`，默认 `~/.hermes/webui`：
  `sessions/{id}.json`、settings、工作区注册表。
- **Agent 状态目录** —— `HERMES_HOME`，默认 `~/.hermes`：config.yaml、
  memory、skills、`state.db`。注意 `HERMES_HOME` 是 agent 的*状态*目录，
  不是安装目录。
- **会话** —— 每个会话一个 JSON 文件；`state.db`（SQLite）是列表/SDK 的
  事实源。webui 往 state.db 表里加的 `__webui` 列是唯一直接 SQL 写入的
  部分；其余一切经由 SDK 的 high level API。
- **内存缓存** —— 模块级 `SESSIONS` OrderedDict LRU（上限 100），由模块级
  `LOCK` 保护。对 `SESSIONS` 的每次访问都必须持锁。

---

## 6. 认证与安全

- **可选口令门** —— `HERMES_WEBUI_PASSWORD`；HMAC 签名的 HTTP-only
  cookie，24 小时 TTL（`api/auth.py`）。面向 localhost/SSH 隧道场景默认
  关闭。TLS 由 `HERMES_WEBUI_TLS_CERT` / `HERMES_WEBUI_TLS_KEY` 提供。
- **HTTP 表面** —— 所有响应带安全头，POST 体上限 20MB，每次文件操作都经
  `safe_resolve(root, requested)` 做路径穿越防护。

### 工作区路径信任级别

`api/workspace.py` 有两个职责不同的信任函数 —— 不要把它们合并：

- **`validate_workspace_to_add(path)`** —— 用于 `/api/workspaces/add`
  （用户显式注册）。宽松：只拦截不存在、非目录、系统根路径。用户是有意识
  地注册一个外部路径（如 WSL 下的 `/mnt/d/Projects`），因此信任其意图。
- **`resolve_trusted_workspace(path)`** —— 用于已有工作区内的实际文件
  读写。严格：路径必须在 home 之下、在已保存的工作区列表内、或在
  `BOOT_DEFAULT_WORKSPACE` 之下。防止路径穿越与未授权文件访问。

之所以必须区分：add 用宽松校验是为避免循环依赖 —— 如果加入列表本身要求
路径已在列表中，路径就永远进不了列表。

---

## 7. API 表面总览

端点按域分组；权威清单见 `backend/app/api/routes.py`（它是唯一事实源 ——
下表只是地图）。

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

## 8. 配置

环境变量（host/port/状态目录/口令/TLS/agent 目录/模型默认值）只在
[README.zh-CN.md](../README.zh-CN.md) 中维护一份 —— 表放在那里、此处链接
过去，避免两处漂移。

发现逻辑与默认值在 `backend/app/api/config.py`；启动期环境处理在
`backend/app/bootstrap.py` 与 `scripts/ctl.sh`（后者还会在存在时 source
仓库根的 `.env`）。

---

## 9. 架构决策记录（ADR）

精简版；每条记录一个历史决策及其当前状态。

### ADR-001：单文件服务器
**决策：** 服务端代码全部放进 `server.py`。
**理由：** 无构建步骤、便于 agent 修改、零部署复杂度。**代价：** 维护负担
随文件体积增长。
**状态：已取代（Superseded）** —— 拆分为 `server.py` + `api/` 模块。

### ADR-002：HTML 作为 Python 原始字符串
**决策：** 前端以 `r"""..."""` 嵌在 `server.py` 里。
**理由：** 不引入静态文件服务器时最简单的前端交付方式。**代价：** 无语法
高亮、补丁困难。
**状态：已取代（Superseded）** —— 前端源码在 `frontend/src`，构建产物为
`static/`。

### ADR-003：ThreadingHTTPServer
**决策：** Python 标准库同步线程，而非 asyncio。
**理由：** 零依赖；同步的 agent 调用天然适配线程。**代价：** 内存随并发
用户线性增长；线程池无上限。
**状态：仍生效（In effect）** —— 对单用户部署可接受。

### ADR-004：SSE 而非 WebSocket
**决策：** 用 Server-Sent Events 做流式。
**理由：** 比 WebSocket 简单，`EventSource` 是标准浏览器 API。**代价：**
仅服务端到客户端单向。
**状态：已取代（Superseded）** —— 现为 WebSocket 优先（`/api/ws/events`）；
SSE 仅存于会话回放。

### ADR-005：模块级审批状态
**决策：** 审批状态以模块级 dict 形式共享于 SDK 的 `tools/approval.py`，
单进程内跨线程共享。
**理由：** 审批系统是既有的；同进程共享可用。**代价：** 多进程
（gunicorn workers）或子进程模型下失效。
**状态：仍生效（In effect）** —— webui 在 SDK 的审批注册表上挂 notify
回调，并以轮询兜底。单进程约束仍然成立。

### ADR-006：不做认证
**决策：** 初期不做认证（经 SSH 隧道的 localhost 访问）。
**理由：** 传输层已完成认证。**代价：** 任何有 localhost 访问权的人都能
使用服务。
**状态：已取代（Superseded）** —— 现有可选口令门 + TLS（`api/auth.py`）。

### ADR-007：通过环境变量传递审批状态
**决策：** `HERMES_EXEC_ASK`、`HERMES_SESSION_KEY` 等经 `os.environ` 传递。
**理由：** `tools/approval.py` 与终端工具读取这些环境变量。**代价：**
进程全局；并发聊天请求会互相踩踏。
**状态：仍生效（已知债务）** —— `_ENV_LOCK` 串行化了写入，但无法完全隔离
执行期；跟踪于 [bugs.zh-CN.md](bugs.zh-CN.md) #195。

---

## 10. 开发约定

工作约定与关键规则（删除 ≠ 创建、`task_id=` 而非 `session_id=`、`SESSIONS`
访问必须持锁、`currentSession` mirror 等）维护在
[CLAUDE.md](../CLAUDE.md) —— 权威版本在那里，此处刻意不复制。

---

## 11. 调试

```bash
./scripts/ctl.sh status            # 进程在吗？pid/端口是多少
./scripts/ctl.sh log-path          # 服务端日志位置
./scripts/ctl.sh restart           # 重建是独立的：./scripts/build.sh

DRY_RUN=1 ./scripts/run-test.sh smoke     # 打印测试将执行的命令
cd backend && python -m pytest tests/ -k <name>

curl -s http://127.0.0.1:8787/health
```

浏览器手动检查见 [testing.zh-CN.md](testing.zh-CN.md)。
