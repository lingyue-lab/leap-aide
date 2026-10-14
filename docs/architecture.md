<div align="center">

**English** | [中文](architecture.zh-CN.md)

</div>

# Leap Aide Architecture Guide

Leap Aide's backend is pure Python standard library, calling the Hermes Agent SDK directly via Python import (no HTTP hop to Hermes Agent); the frontend is React/TypeScript built with Vite. Realtime updates rely on WebSocket.

---

## System Overview

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

Key principles:

- **Single process**: one thread per request; the agent runs on daemon threads, pushing events into per-session queues. No asyncio, no external message broker.
- **Core features depend on the Hermes Agent SDK**: the backend calls the Hermes Agent SDK as Python modules; state.db is updated by the SDK, and the backend never writes or updates `state.db` through raw SQL.
- **Frontend built with Vite**: Vite builds `frontend/src` into `static/`; never hand-edit files under `static/`.

---

## Backend

| Area | Modules | Role |
|---|---|---|
| HTTP shell | `server.py`, `bootstrap.py`, `api/helpers.py`, `api/upload.py`, `api/startup.py` | ThreadingHTTPServer + TLS + WS upgrade dispatch; launch/bootstrap; `j()`/`bad()`/`require()`/`safe_resolve()`; multipart parsing; startup dependency install |
| Routing | `api/routes.py` | All GET/POST handlers, flat if/elif chain |
| Streaming | `api/streaming.py`, `api/stream_manager.py`, `api/ws_events.py`, `api/notify_stream.py` | Agent invocation + per-session event queues; queue/stream lifecycle; the `/api/ws/events` bus; notify forwarding |
| Config & models | `api/config.py`, `api/models.py`, `api/webui_schema.py`, `api/state_sync.py` | Discovery/globals/model detection; session model + CRUD + LRU cache; `__webui` schema columns; `/insights` sync into state.db |
| Sessions | `api/agent_sessions.py`, `api/session_ops.py`, `api/session_display.py`, `api/session_watcher.py` | Session lifecycle around the SDK; ops/display helpers; background session watching |
| Workspace | `api/workspace.py` | File ops, workspace registry, git detection, trust levels |
| Auth & identity | `api/auth.py`, `api/profiles.py`, `api/onboarding.py` | Optional password gate + signed cookies; profile state; first-run wizard |
| Panels | `api/terminal.py`, `api/vnc.py`, `api/observer.py`, `api/cli_viewer.py` | Terminal PTY channel; VNC channel; observer surface; CLI session listing |
| Business | `api/slash_commands.py`, `api/clarify.py`, `api/metering.py`, `api/providers.py`, `api/commands.py`, `api/background.py`, `api/updates.py` | Slash-command dispatch; clarify dialogs; usage metering; provider/model gate; command registry exposure; background goals; update check |

External dependencies: `pyyaml` only (plus the hermes-agent SDK and its own deps, supplied by the hermes-agent venv).

---

## API

| Domain | Prefix / endpoint | Semantics |
|---|---|---|
| Health | `GET /health` | Liveness + version probe |
| Sessions | `/api/session`, `/api/sessions/*` | List/create/update/delete sessions; per-session ops |
| Chat | `POST /api/chat/start`, `/api/chat/stream/status`, `/api/chat/session/stream/replay` | Start a turn; stream status; SSE replay |
| Realtime | `/api/ws/events`, `/api/ws/terminal`, `/api/ws/vnc` | WebSocket channels |
| Workspace | `/api/workspace/*`, `/api/workspaces/*` | File ops + workspace registry (two trust levels) |
| Upload | `POST /api/upload` | Multipart upload (must check size *before* `read_body()`) |
| Clarify | `/api/clarify/*` | Clarify dialog submit/clear |
| Commands | `/api/commands` | Slash-command registry (hermes-agent COMMAND_REGISTRY + local handlers) |
| Profile | `/api/profiles*` | Profile CRUD and switching |
| Auth | `/api/auth*`, `/login` | Password gate, session check |
| Ops | `/api/settings*`, `/api/git-info`, `/api/updates*`, `/api/insights*` | Settings, git badge, update check, usage insights |
| Panels | `/api/cron*`, `/api/skills*`, `/api/memory*`, `/api/terminal*`, `/api/vnc*`, `/api/observer*`, `/api/cli*` | Tasks/cron, skills, memory, terminal, VNC, observer, CLI session bridge |

---

## Frontend (`frontend/src/`)

- **React 18 + TypeScript + Vite**; state is managed by **Zustand** stores under `stores/`.
- i18next handles internationalization.
- Strictly follow the Google Material 3 frontend specification. When reviewing M3 compliance, refer to the component specifications, spacing, font sizes, and other standards in the documents below.
    - https://www.yuque.com/advancedux/xr6e1n
    - https://www.cyub.vip/blog/2026/02/14/material-design-3-material-you-%E5%AE%8C%E6%95%B4%E5%BC%80%E5%8F%91%E6%8C%87%E5%8D%97/
- Core stores: `useSessionStore`, `useChatStore`, `useSettingsStore`, `useWorkspaceStore`, `useApprovalStore`.
- API calls go through `services/api.ts`. The Vite dev server proxies `/api` to `localhost:8787`.
- Build output goes to `../static/`, served directly by the Python backend.


## Data Flow (full chat path)

1. Frontend `send()` → `POST /api/chat/start`, carrying session_id + message
2. The server creates a `queue.Queue`, starts a daemon thread running `_run_agent_streaming()`, and returns `stream_id`
3. The frontend receives events over the WebSocket at `/api/ws/events`; on a mid-session refresh it recovers via SSE replay `GET /api/chat/session/stream/replay`
4. The agent thread runs `AIAgent.run_conversation()`, putting token/tool/approval/done events into the queue
5. `/api/ws/events` reads events from the queue and forwards them to the browser
6. On `done`: update session messages, save to on-disk JSON

---

## Database

- Both Leap Aide and the Hermes Agent SDK use state.db; the default path is `${HERMES_HOME}/state.db`.
- Leap Aide extends the state.db schema beyond the Hermes Agent SDK, adding some extra columns to tables. Column names must end with `__webui`; only Leap Aide uses these columns.
- All updates/writes to state.db must go through the Hermes Agent SDK's high-level API; connecting directly to state.db for low-level database updates/writes is not allowed. Since the Hermes Agent SDK is unaware of any `__webui` columns, none of the high-level APIs it provides read or write `__webui` columns. To implement features, Leap Aide is allowed to connect directly to state.db for updates/writes to `__webui` columns.
- Leap Aide does not need to consider database forward compatibility or data migration — for example, changing the data type of an `X__webui` column from TEXT to INT. You may assume Leap Aide is running for the first time with no historical data.
- Leap Aide does not need to repair historical data in the database. If old values in the db are invalid under new code, no automatic data repair is needed in the code — raise an error directly.

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `HERMES_WEBUI_HOST` | `127.0.0.1` | Bind address |
| `HERMES_WEBUI_PORT` | `8787` | Port |
| `HERMES_WEBUI_STATE_DIR` | `~/.hermes/webui` | State directory (sessions JSON, settings, workspaces) |
| `HERMES_WEBUI_DEFAULT_WORKSPACE` | *(auto-discovered)* | Default workspace path |
| `HERMES_WEBUI_DEFAULT_MODEL` | *(empty)* | Default model; empty means "discover from configured providers" |
| `HERMES_WEBUI_PASSWORD` | *(unset)* | Setting it enables the password gate |
| `HERMES_WEBUI_TLS_CERT` / `HERMES_WEBUI_TLS_KEY` | *(unset)* | TLS certificate / key |
| `HERMES_WEBUI_AGENT_DIR` | *(auto-discovered)* | hermes-agent **install** directory |
| `HERMES_HOME` | `~/.hermes` | hermes-agent **state** directory (config, memory, skills, `state.db`) |
| `HERMES_WEBUI_PYTHON` | *(auto-discovered)* | Python executable override |
| `HERMES_WEBUI_ALLOWED_ORIGINS` | *(unset)* | Extra allowed origins |
| `HERMES_WEBUI_AUTO_INSTALL` | *(unset)* | `=1` enables dependency auto-install at startup |
| `HERMES_WEBUI_ONBOARDING_OPEN` | *(set)* | Open the first-run wizard |
| `LOG_LEVEL` | `WARNING` | Python logging level |

---

## Architecture Decision Records (ADR)

Each entry records one historical decision and its current status.

### ADR-001: Example
**Decision:** TBD
**Rationale:** TBD
**Status:** TBD
