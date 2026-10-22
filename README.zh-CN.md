<div align="center">

[English](README.md) | **中文**

</div>

# LeapAide — Your agent for the next leap.

LeapAide 是一个开箱可用的智能体，可一键接入业界主流的 LLM 提供商。智能体当前基于 [Hermes Agent](https://hermes-agent.nousresearch.com/) 框架开发，提供自托管的 Web 浏览器界面，因此 LeapAide 具备所有 Hermes Agent 的能力，Hermes CLI 能做到的一切，都可在 LeapAide  里完成。

项目后端基于 Python  标准库（无 Web 框架），前端为 React 18 + TypeScript + Vite，遵循 Material 3前端设计规范。

界面为三栏布局，主界面左侧边栏用于会话与导航，中间是聊天区，右侧是工作区文件浏览。模型、工作区选择等控件位于**输入区页脚**；所有设置与会话工具集中在**配置中心**（入口在侧边栏底部）。

<img alt="LeapAide —— 三栏布局" src="docs/images/ui-overview.png" width="1200" />

---

## 为什么使用 LeapAide

相对于业界其它开源 Agent，LeapAide 在目标上有着显著区别：

- 提供开箱可用的智能体 Web App，提供极致流畅、丝滑的用户体验。
- 解决业界智能体使用过程的痛点，比如 Web 浏览器使用过程中登录认证繁琐等。
- 通过扩展(工具、Skill 等)针对线上基础软件诊断场景(LLVM、JDK、操作系统、K8S等)进行能力增强，为线上 IT 系统提供强大、可靠的故障诊断能力。

LeapAide 整体 Web App 架构方案追求精简，UI 方案也追求符合使用直觉，提供极致丝滑的用户使用体验。

针对业界智能体使用过程的痛点，LeapAide 也提供了针对性的解决方案。比如，很多 AI 任务会使用第三方 Web 系统，一方面，这些 Web 系统往往会有着短信验证、手机 App 验证等复杂的登录机制，导致 AI 无法进入 Web 系统，另一方面，AI 如何使用 Web 系统对用户也不透明，用户无法实时跟踪 AI 在 Web 系统中进行了哪些操作，从而影响用户收集现场任务信息，导致最终的决策错误。LeapAide 为每一个置顶的 LLM Session 提供了独立隔离的浏览器桌面环境，如果 LLM Session 中的任务涉及的网站需要登录认证，用户可以在观察窗口为 LLM Session 提前打开页面，人工使用微信、手机进行认证登录，这些操作产生的 Cookie 信息会被系统保留下来，这样后续 Agent 在自动化访问网站时就无需浪费 Token  进行各种注定会失败的登录尝试。另外，LLM Session 的观察者窗口中也会实时显示 Web 浏览器的跳转变化，从而确保用户能够实时掌握现场的第一手信息。

众所周知，线上 IT 系统依赖大量的基础软件，比如 LLVM 编译器、JDK 运行时、操作系统、K8S 集群管控系统等，很多线上 IT 故障都是来自于这些基础软件的错误。这些问题的诊断排查面临着很多障碍，比如网络环境隔离（线上系统高度敏感，往往权限受控且无法访问外网）、基础软件复杂且不透明（多为大型复杂开源软件，需要相关领域专家维护）等。项目提供专业工具、Skill、MCP 及上下文背景知识库等系统扩展，提供线上IT系统的故障诊断排查能力。

---

## 功能特性

### 聊天 ###
- 基于 WebSocket 的消息流式实时传输展示。
- 消息展示包括工具调用、思考/推理过程等详细轨迹信息。
- 聊天输入框底部支持模型选择、推理力度选择、个性选择、Skills 选择、Workspace 选择。
- 聊天输入框支持文字/语音输入，用户可以取消当前轮次对话。
- 聊天输入框底部可以展示 Token 统计、上下占比，并支持自动/手动触发上下文压缩。
- 聊天输入框底部支持 Manual/Smart/YOLO审批模式切换，并可以管理审批规则。
- 聊天进行过程中，用户可以根据配置选择中断、排队等待、Steer（在对话过程中见缝插针插入消息）当前轮次对话。
- 用户可以通过配置接入 OpenAI、Anthropic、Gemini、DeepSeek、GLM、KIMI 等主流LLM提供商。
- 支持通过 `slash /` 调用 Skill 和内部命令。

### 会话 ###
- 支持会话的创建、删除、重命名、搜索。
- 所有会话在左侧边栏按照 Workspace聚合分组展示。
- 支持会话的置顶，置顶操作会为会话创建独立的隔离浏览器桌面环境，用户可通过观察者窗口操作、监视会话的行为。
- 主 Agent 会话根据对话内容，自主决策是否生成子 Agent 会话，子 Agent 会话创建后，主 Agent 会话会有图标呼吸闪烁提示，并可以点击切换到子 Agent 会话。

### 定时任务 ###
- 支持定时任务的创建。
- 定时任务创建支持用户选择模型、Workspace、技能、上下文来源、工具集。

### 其它 ###
- 支持用户搜索、上传技能。
- 支持技能的浏览、编辑。
- 支持 Workspace 的创建、浏览，如果 Workspace 是 git 目录，可以展示最近的 git 历史。
- 支持 Web Shell。
- 支持 Dark/Light 外观风格切换。

---

## 快速开始

### Docker 方式(推荐) ###

Web App 启动运行：

```bash
# 用宿主机当前执行脚本的UID、GID启动服务，一般用于rootless执行脚本的场景（普通账户非特权模式执行脚本）
bash scripts/ctl.sh start-docker <image>
# 指定UID、GID启动服务，一般用于rootful执行脚本的场景（sudo或者root账户执行脚本）。
WANTED_UID=1000 WANTED_GID=1000 bash scripts/ctl.sh start-docker <image>
# compose down
bash scripts/ctl.sh stop-docker      
```

Docker 容器启动成功后，用户直接访问 [http://localhost:8787](http://localhost:8787) 即可使用系统。默认登录密码为 `lingyue-lab`。

当前配置把 `~/.hermes` 挂载到 `/home/leap-aide/.hermes`、`~/.hermes_workspace` 挂载到 `/workspace`。这两个持久化目录用于保存运行数据，确保服务重启能够正常恢复。本机环境和 LeapAide的数据交换也通过`~/.hermes_workspace`进行。

更多详细配置见 `docker-compose.yml`，用户可根据需要更改发布端口以及更改系统配置。系统的配置全部通过环境变量进行。

构建 Docker 镜像：

```bash
./scripts/build.sh --docker-image leap-aide-runtime:X.Y.Z
```
> [!NOTE]
> 当前 `scripts/build.sh` Docker 镜像构建仅支持 buildah(version >= 1.24)。

如果网络环境不稳定，用户可以通过下面的 MIRROR 配置加速构建过程。如果你配置了 HERMES_AGENT_REPO_URL 为内部 Git 仓库来加速 Hermes Agent SDK 的 Git Clone，则需要将 BUILD_GIT_IDENTITY_KEY_FILE 指向你的Git私钥，该 Git  私钥通过`--mount=type=secret`的方式被使用，具体内容不会泄漏进 Docker 镜像，没有安全风险。HERMES_AGENT_REPO_URL 为空时，会使用 github 的 https 地址来克隆 Hermes Agent SDK，无需配置 BUILD_GIT_IDENTITY_KEY_FILE。

```bash
export BUILD_APT_MIRROR="http://mirrors.tuna.tsinghua.edu.cn/ubuntu"
export BUILD_PIP_INDEX_URL="http://pypi.tuna.tsinghua.edu.cn/simple"
export BUILD_NPM_REGISTRY="https://registry.npmmirror.com"
export BUILD_PLAYWRIGHT_DOWNLOAD_HOST="https://registry.npmmirror.com/-/binary/playwright"
# Must end with slash
export BUILD_ELECTRON_MIRROR="https://registry.npmmirror.com/-/binary/electron/"
export BUILD_ELECTRON_BUILDER_BINARIES_MIRROR="https://registry.npmmirror.com/-/binary/electron-builder-binaries"

export BUILD_GIT_IDENTITY_KEY_FILE="$HOME/.ssh/id_ed25519"
export HERMES_AGENT_REPO_URL="ssh://git@<path-to-your-internal-mirror-repo>/hermes-agent.git"
export HERMES_AGENT_COMMIT="fcbd1076a93841fa88855acce810e342a5b78101"
```

SDK 的 commit pin 默认取自仓库根目录的 [`.build.env`](.build.env)。像上面这样 export 会覆盖该默认值；把 `.build.env` 里那一行注释掉则构建不带 pin，SDK 安装会跟随其克隆来源仓库的默认分支。同一个文件里还存放构建期 npm warmup 使用的 Playwright 回退版本。

除了上述镜像，用户也可以自行添加 HTTP_PROXY HTTPS_PROXY NO_PROXY 环境变量来加速安装过程。

### 主机方式 ###

主机环境的启动方式如下

```bash
# install all deps
bash ./scripts/install.sh --deps-only

# build frontend into static/ + pre-compile backend
bash ./scripts/build.sh

# start server + hermes gateway
bash ./scripts/ctl.sh start

# check health
bash ./scripts/ctl.sh status
```

打开 [http://localhost:8787](http://localhost:8787) 即可访问。

> [!NOTE]
> 引导脚本不支持原生 Windows。请使用 Linux、macOS 或 WSL2。

## 测试

参见 [docs/testing.zh-CN.md](docs/testing.zh-CN.md)。

---

## 架构

参见 [docs/architecture.zh-CN.md](docs/architecture.zh-CN.md)。

---

## 参与贡献

参见 [CONTRIBUTING.zh-CN.md](CONTRIBUTING.zh-CN.md)。

---

## 致谢

LeapAide 项目最早脱胎于 [hermes-webui](https://github.com/nesquena/hermes-webui) 项目，当前也保留了很多 hermes-webui 的代码，但由于项目目标不同，目前2个项目分叉越来越远，LeapAide 未来也没有同步 hermes-webui 的计划。

感谢 hermes-webui 社区。

---

## 许可证

[MIT](LICENSE)
