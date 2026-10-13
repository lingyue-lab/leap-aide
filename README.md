# Leap Aide

**English** | [简体中文](README.zh-CN.md)

Leap Aide (Hermes Web UI) is a self-hosted browser interface for the
[Hermes agent](https://hermes-agent.nousresearch.com/): a persistent,
autonomous agent that lives on your server and remembers what it learns.
Everything you can do from the Hermes CLI you can do from this UI — it uses
your existing agent setup and models, with no additional configuration.

Python-stdlib backend (no web framework), React 18 + TypeScript + Vite
frontend, Material 3 design, WebSocket-first realtime streaming.

Layout: three-panel. Left sidebar for sessions and navigation, center for
chat, right for workspace file browsing. Model, profile, and workspace
controls live in the **composer footer**; all settings and session tools are
in the **Control Center** (launcher at the sidebar bottom).

<img alt="Leap Aide — three-panel layout" src="docs/images/ui-overview.png" width="1200" />

---

## Why Hermes

Most AI tools reset every session. Hermes retains context across sessions,
runs scheduled jobs while you're offline, and gets smarter about your
environment the longer it runs — persistent memory, self-hosted cron,
messaging-app access, self-improving skills, provider-agnostic.

| | OpenClaw | Claude Code | Codex CLI | OpenCode | Hermes |
|---|---|---|---|---|---|
| Persistent memory (auto) | Yes | Partial | Partial | Partial | Yes |
| Scheduled jobs (self-hosted) | Yes | No | No | No | Yes |
| Messaging app access | Yes | Partial | No | No | Yes (10+) |
| Web UI (self-hosted) | Dashboard only | No | No | Yes | Yes |
| Self-improving skills | Partial | No | No | No | Yes |
| Provider-agnostic | Yes | No | Yes | Yes | Yes |

Full positioning and honest comparisons: [docs/hermes.md](docs/hermes.md) ·
[中文](docs/hermes.zh-CN.md).

---

## Features

- **Chat & agent** — WebSocket token streaming, tool-call cards, subagent
  cards, thinking/reasoning display, Mermaid diagrams, inline edit & retry,
  queue messages mid-run, cancel running turns, file attachments, voice input
- **Sessions** — create/rename/duplicate/delete, search, pin, archive,
  projects & tags, date grouping, Markdown/JSON export & import, CLI session
  bridge (gold `cli` badge), per-conversation token/cost display
- **Workspace browser** — tree + breadcrumbs, inline preview (text, code,
  Markdown, images), file create/edit/rename/delete, git branch/dirty badge,
  drag-resizable panel
- **Panels** — Tasks (cron), Skills, Memory, Profiles, Todos, Spaces,
  terminal panel, VNC/observer
- **Approvals** — dangerous shell commands get an approval card
  (allow once / session / always / deny)
- **Profiles** — per-profile gateway status, model, skills; custom endpoint
  (Ollama, LMStudio, …) on create; seamless switching without restart
- **Security** — optional password gate (HMAC-signed cookie), TLS support,
  security headers, 20MB upload limit, path-traversal guards
- **Customization** — theme (system / dark / light), i18n (English / 简体中文),
  send-key preference, mobile-responsive layout

---

## Quick start

Prerequisites: Python 3.11+, Node 20+, and a hermes-agent installation.

```bash
./scripts/build.sh          # build frontend into static/ + pre-compile backend
./scripts/ctl.sh start      # start server + hermes gateway
./scripts/ctl.sh status     # health check of the process
```

`./scripts/ctl.sh` also supports `stop`, `restart`, and `log-path`. The
server binds to `127.0.0.1:8787` by default; open
[http://localhost:8787](http://localhost:8787).

Other entry points:

```bash
./scripts/build.sh --watch          # frontend watch mode
./scripts/build.sh --release        # minified production build
./scripts/install.sh                # one-line installer (see its usage header)
```

> Native Windows is not supported for the bootstrap. Use Linux, macOS, or
> WSL2.

---

## Install

The one-line installer provisions the repo, a Python environment, and the
frontend toolchain, then wires up `scripts/ctl.sh`:

```bash
curl -fsSL <install-url>/scripts/install.sh | bash
# pin a ref or target dir:
curl -fsSL <install-url>/scripts/install.sh | bash -s -- --commit <sha> --dir /opt/leap-aide
```

See `scripts/install.sh`'s usage header for the authoritative flags and
environment overrides.

---

## Docker

Build the runtime image locally:

```bash
./scripts/build.sh --docker-image leap-aide-runtime:0.1.2
# or directly:
docker buildx build --target runtime -t leap-aide-runtime:0.1.2 -f Dockerfile .
```

Run with Docker Compose (recommended):

```bash
# Set WANTED_UID/WANTED_GID to your host user: id -u / id -g
docker compose up -d
```

`docker-compose.yml` (service `leap-aide`) mounts `~/.hermes` →
`/home/leap-aide/.hermes` and `~/.hermes_workspace` → `/workspace`, publishes
`8787:8787`, and sets `HERMES_WEBUI_STATE_DIR=/home/leap-aide/.hermes/webui`.
Override the image with `LEAP_AIDE_IMAGE=<registry>/<image>:<tag>`.

Notes:

- The hermes-agent venv is **baked into the image at build time**; the
  entrypoint (`backend/scripts/docker-entrypoint.sh`) fails fast when it is
  missing — there is no runtime install fallback by design.
- The compose file binds `8787:8787` on all interfaces and sets
  `HERMES_WEBUI_PASSWORD` — change both before exposing the service anywhere
  untrusted.

### Builder requirements

The `cicd` target uses BuildKit's `--mount=type=secret` for SSH key injection
into the hermes-agent installer (when building against an internal git
server). This requires a recent BuildKit-enabled builder:

- **docker buildx** ≥ 23.0 — plain `docker build` without `DOCKER_BUILDKIT=1`
  does not support it
- **buildah** ≥ 1.24 (podman inherits its support)

Building against the default upstream hermes-agent needs no SSH credential —
`--mount=type=secret` is a no-op (`required=false`). For an internal git
server:

```bash
docker buildx build --target cicd \
    --secret id=git_host_key,src=/path/to/host_key \
    --secret id=git_identity_key,src=/path/to/id_ed25519 \
    --build-arg HERMES_AGENT_REPO_URL=ssh://git@host:port/path \
    -t leap-aide:cicd -f Dockerfile .
```

**Never push images built with `--secret` to a public registry** — the
build-time secret forwarding path may log file paths (an internal-server
fingerprint is itself a small intelligence leak).

---

## Accessing remotely

The server binds to `127.0.0.1` by default. From your local machine use an
SSH tunnel:

```bash
ssh -N -L 8787:127.0.0.1:8787 user@your.server.com
```

On a phone or untrusted network, prefer Tailscale (or similar WireGuard mesh)
plus the password gate:

```bash
HERMES_WEBUI_HOST=0.0.0.0 HERMES_WEBUI_PASSWORD=your-secret ./scripts/ctl.sh restart
```

then open `http://<server-tailscale-ip>:8787`. The UI is fully responsive
with a mobile-optimized layout.

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `HERMES_WEBUI_HOST` | `127.0.0.1` | Bind address |
| `HERMES_WEBUI_PORT` | `8787` | Port |
| `HERMES_WEBUI_STATE_DIR` | `~/.hermes/webui` | WebUI state (sessions JSON, settings, workspaces) |
| `HERMES_WEBUI_DEFAULT_WORKSPACE` | *(discovered)* | Default workspace path |
| `HERMES_WEBUI_DEFAULT_MODEL` | *(empty)* | Default model; empty means "discover from configured providers" |
| `HERMES_WEBUI_PASSWORD` | *(unset)* | Set to enable the password gate |
| `HERMES_WEBUI_TLS_CERT` / `HERMES_WEBUI_TLS_KEY` | *(unset)* | TLS certificate / key |
| `HERMES_WEBUI_AGENT_DIR` | *(discovered)* | hermes-agent install location |
| `HERMES_WEBUI_PYTHON` | *(discovered)* | Python executable override |
| `HERMES_WEBUI_ALLOWED_ORIGINS` | *(unset)* | Extra allowed origins |
| `HERMES_WEBUI_AUTO_INSTALL` | *(unset)* | `=1` enables dependency auto-install at startup |
| `HERMES_WEBUI_ONBOARDING_OPEN` | *(set)* | Open the first-run wizard |
| `HERMES_HOME` | `~/.hermes` | hermes-agent **state** directory (config, memory, skills, `state.db`) — not the install directory |
| `LOG_LEVEL` | `WARNING` | Python logging level |

Health check: `curl http://127.0.0.1:8787/health`

---

## Testing

```bash
./scripts/run-test.sh smoke          # L1 core regression (< 5 min)
./scripts/run-test.sh function all   # L2 one domain (session|model|workspace|…)
./scripts/run-test.sh frontend       # vitest suite
./scripts/run-test.sh help           # full usage
```

Tests run against an isolated server on port 8788 with temporary state
directories — production data is never touched. Manual browser checks and
tier details: [docs/testing.md](docs/testing.md) ·
[中文](docs/testing.zh-CN.md).

---

## Architecture

`backend/app/server.py` is a thin HTTP shell over `backend/app/api/`
modules; the React frontend builds into `static/`. Streaming is
WebSocket-first with an SSE replay surface. Details, API map, and ADRs:
[docs/architecture.md](docs/architecture.md) ·
[中文](docs/architecture.zh-CN.md).

---

## Docs

| Document | English | 中文 |
|---|---|---|
| Why Hermes (positioning) | [docs/hermes.md](docs/hermes.md) | [docs/hermes.zh-CN.md](docs/hermes.zh-CN.md) |
| Architecture + ADRs | [docs/architecture.md](docs/architecture.md) | [docs/architecture.zh-CN.md](docs/architecture.zh-CN.md) |
| Testing guide | [docs/testing.md](docs/testing.md) | [docs/testing.zh-CN.md](docs/testing.zh-CN.md) |
| Roadmap | [docs/roadmap.md](docs/roadmap.md) | [docs/roadmap.zh-CN.md](docs/roadmap.zh-CN.md) |
| Bugs & known limitations | [docs/bugs.md](docs/bugs.md) | [docs/bugs.zh-CN.md](docs/bugs.zh-CN.md) |
| Contributing | [CONTRIBUTING.md](CONTRIBUTING.md) | [CONTRIBUTING.zh-CN.md](CONTRIBUTING.zh-CN.md) |
| Changelog (English only) | [CHANGELOG.md](CHANGELOG.md) | — |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) ·
[中文](CONTRIBUTING.zh-CN.md). The best contributions are focused,
well-tested, and solve a real problem. If your change alters behavior,
architecture, or setup, update the docs — both the English and Chinese
version — in the same PR.

---

## Acknowledgements

Leap Aide builds on the open-source Hermes Web UI community project; every
merged and adapted PR shaped what you see here. Thank you to everyone who
contributed.

---

## License

[MIT](LICENSE)
