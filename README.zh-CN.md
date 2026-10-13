# Leap Aide

[English](README.md) | **简体中文**

Leap Aide（Hermes Web UI）是 [Hermes agent](https://hermes-agent.nousresearch.com/) 的自托管浏览器界面：一个常驻在你的服务器上、持续记住所学内容的自主 agent。Hermes CLI 能做到的一切，都可以在这个 UI 里完成 —— 它直接复用你现有的 agent 配置和模型，无需任何额外设置。

后端基于 Python 标准库（无 Web 框架），前端为 React 18 + TypeScript + Vite，采用 Material 3 设计，实时流式传输以 WebSocket 优先。

界面为三栏布局：左侧边栏用于会话与导航，中间是聊天区，右侧是工作区文件浏览。模型、profile 与工作区控件位于**输入区页脚**；所有设置与会话工具集中在**控制中心**（入口在侧边栏底部）。

<img alt="Leap Aide —— 三栏布局" src="docs/images/ui-overview.png" width="1200" />

---

## 为什么选择 Hermes

大多数 AI 工具每次会话都从零开始。Hermes 跨会话保留上下文，在你离线时照常运行定时任务，并且运行越久就越了解你的环境 —— 持久记忆、自托管 cron、消息应用接入、自我改进的技能，且不绑定任何模型提供商。

| | OpenClaw | Claude Code | Codex CLI | OpenCode | Hermes |
|---|---|---|---|---|---|
| 持久记忆（自动） | 是 | 部分 | 部分 | 部分 | 是 |
| 定时任务（自托管） | 是 | 否 | 否 | 否 | 是 |
| 消息应用接入 | 是 | 部分 | 否 | 否 | 是（10+） |
| Web UI（自托管） | 仅仪表盘 | 否 | 否 | 是 | 是 |
| 自我改进的技能 | 部分 | 否 | 否 | 否 | 是 |
| 提供商无关 | 是 | 否 | 是 | 是 | 是 |

完整的定位说明与如实对比见 [docs/hermes.zh-CN.md](docs/hermes.zh-CN.md)。

---

## 功能特性

- **聊天与 agent** —— WebSocket token 流式传输、工具调用卡片、子 agent 卡片、思考/推理过程展示、Mermaid 图表、就地编辑与重试、运行中排队消息、取消进行中的轮次、文件附件、语音输入
- **会话** —— 创建/重命名/复制/删除、搜索、置顶、归档、项目与标签、按日期分组、Markdown/JSON 导出与导入、CLI 会话桥接（金色 `cli` 徽章）、按会话显示 token/费用
- **工作区浏览器** —— 树形视图 + 面包屑、就地预览（文本、代码、Markdown、图片）、文件创建/编辑/重命名/删除、git 分支/未提交变更徽章、可拖拽调宽的面板
- **面板** —— 任务（cron）、技能、记忆、Profiles、待办、Spaces、终端面板、VNC/观察器
- **审批** —— 危险的 shell 命令会弹出审批卡片（允许一次 / 本会话 / 始终允许 / 拒绝）
- **Profiles** —— 每个 profile 独立的网关状态、模型、技能；创建时可自定义 endpoint（Ollama、LMStudio 等）；无需重启即可无缝切换
- **安全** —— 可选口令门（HMAC 签名 cookie）、TLS 支持、安全响应头、20MB 上传限制、路径穿越防护
- **个性化** —— 主题（跟随系统 / 深色 / 浅色）、i18n（English / 简体中文）、发送键偏好、移动端响应式布局

---

## 快速开始

前置条件：Python 3.11+、Node 20+，以及一份 hermes-agent 安装。

```bash
./scripts/build.sh          # build frontend into static/ + pre-compile backend
./scripts/ctl.sh start      # start server + hermes gateway
./scripts/ctl.sh status     # health check of the process
```

`./scripts/ctl.sh` 还支持 `stop`、`restart` 和 `log-path`。服务器默认绑定到 `127.0.0.1:8787`，打开 [http://localhost:8787](http://localhost:8787) 即可访问。

其他入口：

```bash
./scripts/build.sh --watch          # frontend watch mode
./scripts/build.sh --release        # minified production build
./scripts/install.sh                # one-line installer (see its usage header)
```

> 引导脚本不支持原生 Windows。请使用 Linux、macOS 或 WSL2。

---

## 安装

一行安装脚本会准备好仓库、Python 环境和前端工具链，并接入 `scripts/ctl.sh`：

```bash
curl -fsSL <install-url>/scripts/install.sh | bash
# pin a ref or target dir:
curl -fsSL <install-url>/scripts/install.sh | bash -s -- --commit <sha> --dir /opt/leap-aide
```

完整的参数说明与环境变量覆盖见 `scripts/install.sh` 的用法头部注释。

---

## Docker

在本地构建运行时镜像：

```bash
./scripts/build.sh --docker-image leap-aide-runtime:0.1.2
# or directly:
docker buildx build --target runtime -t leap-aide-runtime:0.1.2 -f Dockerfile .
```

用 Docker Compose 运行（推荐）：

```bash
# Set WANTED_UID/WANTED_GID to your host user: id -u / id -g
docker compose up -d
```

`docker-compose.yml`（服务 `leap-aide`）把 `~/.hermes` 挂载到 `/home/leap-aide/.hermes`、`~/.hermes_workspace` 挂载到 `/workspace`，发布端口 `8787:8787`，并设置 `HERMES_WEBUI_STATE_DIR=/home/leap-aide/.hermes/webui`。可通过 `LEAP_AIDE_IMAGE=<registry>/<image>:<tag>` 覆盖镜像。

注意事项：

- hermes-agent venv **在构建时已烘焙进镜像**；entrypoint（`backend/scripts/docker-entrypoint.sh`）在检测到缺失时会立即失败 —— 按设计不存在运行时安装的回退路径。
- compose 文件在所有网卡上绑定 `8787:8787`，并预设了 `HERMES_WEBUI_PASSWORD` —— 在把服务暴露到任何不可信环境之前，请务必修改这两项。

### 构建器要求

`cicd` target 使用 BuildKit 的 `--mount=type=secret` 把 SSH 密钥注入 hermes-agent 安装过程（面向内部 git 服务器构建时）。这要求使用较新的、支持 BuildKit 的构建器：

- **docker buildx** ≥ 23.0 —— 不设 `DOCKER_BUILDKIT=1` 的普通 `docker build` 不支持该特性
- **buildah** ≥ 1.24（podman 继承其支持）

面向默认上游 hermes-agent 构建时不需要 SSH 凭据 —— `--mount=type=secret` 是空操作（`required=false`）。面向内部 git 服务器时：

```bash
docker buildx build --target cicd \
    --secret id=git_host_key,src=/path/to/host_key \
    --secret id=git_identity_key,src=/path/to/id_ed25519 \
    --build-arg HERMES_AGENT_REPO_URL=ssh://git@host:port/path \
    -t leap-aide:cicd -f Dockerfile .
```

**切勿把带 `--secret` 构建的镜像推送到公共镜像仓库** —— 构建时的 secret 转发路径可能会记录文件路径（内网服务器的指纹本身就是一次小小的情报泄露）。

---

## 远程访问

服务器默认绑定到 `127.0.0.1`。在本地机器上可以走 SSH 隧道：

```bash
ssh -N -L 8787:127.0.0.1:8787 user@your.server.com
```

在手机上或不可信网络中，建议使用 Tailscale（或其他 WireGuard 组网）并配合口令门：

```bash
HERMES_WEBUI_HOST=0.0.0.0 HERMES_WEBUI_PASSWORD=your-secret ./scripts/ctl.sh restart
```

然后打开 `http://<server-tailscale-ip>:8787`。UI 完全响应式，并针对移动端做了布局优化。

---

## 配置

| 变量 | 默认值 | 说明 |
|---|---|---|
| `HERMES_WEBUI_HOST` | `127.0.0.1` | 绑定地址 |
| `HERMES_WEBUI_PORT` | `8787` | 端口 |
| `HERMES_WEBUI_STATE_DIR` | `~/.hermes/webui` | WebUI 状态目录（会话 JSON、设置、工作区） |
| `HERMES_WEBUI_DEFAULT_WORKSPACE` | *（自动发现）* | 默认工作区路径 |
| `HERMES_WEBUI_DEFAULT_MODEL` | *（空）* | 默认模型；留空表示"从已配置的提供商中自动发现" |
| `HERMES_WEBUI_PASSWORD` | *（未设置）* | 设置后启用口令门 |
| `HERMES_WEBUI_TLS_CERT` / `HERMES_WEBUI_TLS_KEY` | *（未设置）* | TLS 证书 / 密钥 |
| `HERMES_WEBUI_AGENT_DIR` | *（自动发现）* | hermes-agent 安装位置 |
| `HERMES_WEBUI_PYTHON` | *（自动发现）* | Python 可执行文件覆盖 |
| `HERMES_WEBUI_ALLOWED_ORIGINS` | *（未设置）* | 额外允许的来源（origin） |
| `HERMES_WEBUI_AUTO_INSTALL` | *（未设置）* | `=1` 表示启动时自动安装依赖 |
| `HERMES_WEBUI_ONBOARDING_OPEN` | *（已设置）* | 打开首次运行向导 |
| `HERMES_HOME` | `~/.hermes` | hermes-agent **状态**目录（配置、记忆、技能、`state.db`）—— 不是安装目录 |
| `LOG_LEVEL` | `WARNING` | Python 日志级别 |

健康检查：`curl http://127.0.0.1:8787/health`

---

## 测试

```bash
./scripts/run-test.sh smoke          # L1 core regression (< 5 min)
./scripts/run-test.sh function all   # L2 one domain (session|model|workspace|…)
./scripts/run-test.sh frontend       # vitest suite
./scripts/run-test.sh help           # full usage
```

测试在 8788 端口的隔离服务器上运行，使用临时状态目录 —— 绝不会触碰生产数据。手动浏览器检查与各层级详情见 [docs/testing.zh-CN.md](docs/testing.zh-CN.md)。

---

## 架构

`backend/app/server.py` 是覆盖 `backend/app/api/` 各模块的薄 HTTP 壳；React 前端构建产物输出到 `static/`。流式传输以 WebSocket 优先，并提供一个 SSE 回放面。详细设计、API 地图与 ADR 见 [docs/architecture.zh-CN.md](docs/architecture.zh-CN.md)。

---

## 文档

| 文档 | English | 中文 |
|---|---|---|
| 为什么选择 Hermes（定位） | [docs/hermes.md](docs/hermes.md) | [docs/hermes.zh-CN.md](docs/hermes.zh-CN.md) |
| 架构 + ADR | [docs/architecture.md](docs/architecture.md) | [docs/architecture.zh-CN.md](docs/architecture.zh-CN.md) |
| 测试指南 | [docs/testing.md](docs/testing.md) | [docs/testing.zh-CN.md](docs/testing.zh-CN.md) |
| 路线图 | [docs/roadmap.md](docs/roadmap.md) | [docs/roadmap.zh-CN.md](docs/roadmap.zh-CN.md) |
| 已知问题与限制 | [docs/bugs.md](docs/bugs.md) | [docs/bugs.zh-CN.md](docs/bugs.zh-CN.md) |
| 参与贡献 | [CONTRIBUTING.md](CONTRIBUTING.md) | [CONTRIBUTING.zh-CN.md](CONTRIBUTING.zh-CN.md) |
| 变更日志（仅英文） | [CHANGELOG.md](CHANGELOG.md) | — |

---

## 参与贡献

参见 [CONTRIBUTING.zh-CN.md](CONTRIBUTING.zh-CN.md)。最好的贡献聚焦、经过充分测试，并且真正解决问题。如果你的变更会改变行为、架构或安装方式，请在同一 PR 中更新文档 —— 中英文两个版本都要更新。

---

## 致谢

Leap Aide 构建于开源的 Hermes Web UI 社区项目之上；每一个被合并和采纳的 PR 都塑造了你今天所看到的样子。感谢所有贡献者。

---

## 许可证

[MIT](LICENSE)
