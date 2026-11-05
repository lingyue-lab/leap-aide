<div align="center">

[English](architecture.md) | **中文**

</div>

# LeapAide 架构指南

LeapAide 后端为 Python 标准库实现，直接以 Python import 方式调用Hermes Agent SDK（与 Hermes Agent 之间没有 HTTP 跳转）；前端为 React/TypeScript，用 Vite 构建。实时更新依赖 WebSocket。 

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
| 面板 | `/api/cron*`、`/api/skills*`、`/api/memory*`、`/api/terminal*`、`/api/vnc*`、`/api/observer*`、`/api/cli*` | 任务/cron、技能、记忆、终端、VNC、observer、CLI 会话桥 |

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
  并按 provider 超时。资格判定（有 key / OAuth）不变——目录只决定*展示什么
  模型*，不决定*该 provider 是否有资格展示*。
- **磁盘 envelope**：模型缓存快照持久化
  `{schema_version, active_provider, default_model, groups,
  provider_models, fetched_at}`，重启后首个响应字段完整，并保留每 provider
  的上次成功列表；旧的 `{仅 groups}` 文件仍可加载。
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