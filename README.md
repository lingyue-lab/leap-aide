<div align="center">

**English** | [中文](README.zh-CN.md)

</div>

# LeapAide — Your agent for the next leap.

LeapAide is an out-of-the-box agent that connects to mainstream LLM providers in one step. The agent is currently built on the [Hermes Agent](https://hermes-agent.nousresearch.com/) framework and provides a self-hosted browser interface, so LeapAide has every Hermes Agent capability: anything the Hermes CLI can do can also be done in LeapAide.

The backend is Python standard library (no web framework); the frontend is React 18 + TypeScript + Vite, following the Material 3 frontend design specification.

The UI is a three-panel layout: the left sidebar for sessions and navigation, the center chat area, and the right panel for workspace file browsing. Controls such as model and workspace selection live in the **composer footer**; all settings and session tools are centralized in the **Configuration Center** (entry at the bottom of the sidebar).

<img alt="LeapAide — three-panel layout" src="docs/images/ui-overview.png" width="1200" />

---

## Why LeapAide

Compared with other open-source agents, LeapAide has distinctly different goals:

- Ship an out-of-the-box agent web app with an exceptionally smooth, fluid user experience.
- Solve the pain points of using agents today, such as the friction of logging into web browsers.
- Enhance production infrastructure-diagnosis scenarios (LLVM, JDK, OS, K8S, and so on) through extensions (tools, Skills, and more), providing powerful and reliable fault diagnosis for production IT systems.

LeapAide's overall web app architecture aims to stay lean, and its UI aims to match user intuition — delivering an exceptionally smooth experience.

LeapAide also offers targeted solutions to the pain points of using agents today. Many AI tasks need to use third-party web systems. On one hand, those systems often have complex login mechanisms — SMS codes, mobile-app verification, and so on — that lock the AI out. On the other hand, how the AI uses those systems is opaque to the user: there is no way to follow in real time what the AI did on the site, which hurts on-site information gathering and can lead to wrong final decisions. LeapAide provides every pinned LLM session with its own isolated browser desktop environment. If a task touches a website that requires login, the user can open the page ahead of time in the observer window and log in manually via WeChat or a mobile phone; the cookies produced by those operations are retained by the system, so later automated agent visits do not waste tokens on login attempts doomed to fail. The observer window also shows browser navigation changes in real time, keeping the user on top of first-hand on-site information.

As everyone knows, production IT systems depend on a great deal of infrastructure software — the LLVM compiler, the JDK runtime, operating systems, K8S cluster management, and more — and many production IT failures originate in that software. Diagnosing these problems faces many obstacles: isolated network environments (production systems are highly sensitive, often access-controlled and unable to reach the public internet), and infrastructure software that is complex and opaque (mostly large, complex open-source projects that require domain experts to maintain). The project provides professional tools, Skills, MCP, and context/background knowledge bases as system extensions, giving the ability to diagnose and troubleshoot production IT systems.

---

## Features

### Chat ###
- Real-time message streaming over WebSocket.
- Messages render detailed trajectory information including tool calls and thinking/reasoning traces.
- The composer footer supports model selection, reasoning effort, personality, Skills, and Workspace selection.
- The composer supports text and voice input; users can cancel the current turn.
- The composer footer shows token statistics and context usage share, and supports automatic or manual context compaction.
- The composer footer supports Manual/Smart/YOLO approval-mode switching, with approval-rule management.
- While a chat turn is running, users can — depending on configuration — interrupt, queue, or steer (squeeze a message into the running turn) the current turn.
- Users can configure mainstream LLM providers such as OpenAI, Anthropic, Gemini, DeepSeek, GLM, and KIMI.
- Skills and internal commands can be invoked with slash `/`.

### Sessions ###
- Create, delete, rename, and search sessions.
- All sessions are grouped by Workspace in the left sidebar.
- Sessions can be pinned; pinning creates an isolated browser desktop environment for the session, and users can operate on and watch the session through the observer window.
- Based on the conversation, the main agent session decides on its own whether to spawn sub-agent sessions. Once a sub-agent session is created, the main agent session shows a breathing icon hint, and clicking it switches to the sub-agent session.

### Scheduled Tasks ###
- Create scheduled tasks.
- When creating a scheduled task, users can choose the model, Workspace, skills, context source, and toolsets.

### Other ###
- Search and upload skills.
- Browse and edit skills.
- Create and browse Workspaces; if a Workspace is a git directory, recent git history can be shown.
- Web Shell support.
- Dark/Light appearance switching.

---

## Quick Start

### Docker (recommended) ###

To start and run the web app:

```bash
docker compose up -d
```

After the Docker container starts successfully, open [http://localhost:8787](http://localhost:8787) to use the system. The default login password is `lingyue-lab`.

The current configuration mounts `~/.hermes` to `/home/leap-aide/.hermes` and `~/.hermes_workspace` to `/workspace`. These two persistent directories hold runtime data so the service can recover properly across restarts. Data exchange between the host machine and LeapAide also goes through `~/.hermes_workspace`.

See `docker-compose.yml` for more detailed configuration; change the published port and the system configuration as needed. All system configuration is done through environment variables.

Building the Docker image:

```bash
./scripts/build.sh --docker-image leap-aide-runtime:X.Y.Z
```
> [!NOTE]
> Docker image builds via `scripts/build.sh` currently support buildah only (version >= 1.24).

If your network is unreliable, the MIRROR settings below can speed up the build. If you set HERMES_AGENT_REPO_URL to an internal Git repository to accelerate cloning the Hermes Agent SDK, point BUILD_GIT_IDENTITY_KEY_FILE at your Git private key. That key is used via `--mount=type=secret`, so its contents never leak into the Docker image — no security risk. When HERMES_AGENT_REPO_URL is empty, the Hermes Agent SDK is cloned from the GitHub HTTPS address and BUILD_GIT_IDENTITY_KEY_FILE is not needed.

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

Besides the mirrors above, you can also add HTTP_PROXY, HTTPS_PROXY, and NO_PROXY environment variables to speed up installation.

### Host ###

To run in a host environment:

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

Open [http://localhost:8787](http://localhost:8787) to access it.

> [!NOTE]
> The bootstrap scripts do not support native Windows. Use Linux, macOS, or WSL2.

## Testing

See [docs/testing.md](docs/testing.md).

---

## Architecture

See [docs/architecture.md](docs/architecture.md).

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Acknowledgements

The LeapAide project grew out of the [hermes-webui](https://github.com/nesquena/hermes-webui) project and still carries much of its code. Because the project goals differ, the two projects have been diverging further and further, and LeapAide has no plans to sync with hermes-webui going forward.

Thanks to the hermes-webui community.

---

## License

[MIT](LICENSE)
