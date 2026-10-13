# Leap Aide Architecture Guide

**English** | [简体中文](architecture.zh-CN.md)

Leap Aide (Hermes Web UI) is a browser interface for the Hermes agent: a
Python-stdlib backend that imports the hermes-agent SDK directly (no HTTP hop
to the agent), and a React/TypeScript frontend built with Vite. Realtime
updates are WebSocket-first. This document describes the system as it exists
today; historical narrative lives in [CHANGELOG.md](../CHANGELOG.md).

---

## 1. System overview

```
Browser (React 18 + TS + Vite + Zustand + Tailwind, M3)
   │  fetch /api/*          REST surface (~130 routes in routes.py)
   │  /api/ws/events        single WebSocket: token / tool / approval / done
   │  /api/ws/terminal      WebSocket terminal channel
   │  /api/ws/vnc[/<port>]  WebSocket VNC channel (observer panel)
   │  GET …/stream/replay    SSE replay for session reconnect
   ▼
backend/app/server.py       thin HTTP shell: TLS, QuietHTTPServer, WS upgrade dispatch
backend/app/api/            route + domain modules (Python stdlib only)
   ▼ (Python import, not HTTP)
hermes-agent SDK            AIAgent.run_conversation(), tools, gateway
   ▼
~/.hermes/                  agent state (config, memory, skills, state.db)
~/.hermes/webui/            webui state (sessions JSON, settings, workspaces)
```

Key properties:

- **One process** — `ThreadingHTTPServer` with a thread per request; the agent
  runs on daemon threads feeding per-session queues. No asyncio, no external
  message broker.
- **Direct SDK import** — the backend talks to hermes-agent as Python modules.
  WebUI never writes to `state.db` through SQL except the `__webui` columns the
  SDK does not know about.
- **static/ is build output** — Vite builds `frontend/src` into `static/`
  (`emptyOutDir: true`). Never edit files under `static/` by hand.

---

## 2. Backend layout

Grouped by responsibility; `routes.py` is the single source of truth for the
endpoint table, so this is a map, not a manifest.

| Area | Modules | Role |
|---|---|---|
| HTTP shell | `server.py`, `bootstrap.py`, `api/helpers.py`, `api/upload.py`, `api/startup.py` | ThreadingHTTPServer + TLS + WS upgrade dispatch; launch/bootstrap; `j()`/`bad()`/`require()`/`safe_resolve()`; multipart parsing; startup dependency install |
| Routing | `api/routes.py` | All GET/POST handlers, flat if/elif chain |
| Streaming | `api/streaming.py`, `api/stream_manager.py`, `api/ws_events.py`, `api/notify_stream.py` | Agent invocation + per-session event queues; queue/stream lifecycle; the `/api/ws/events` bus; notify forwarding |
| Config & models | `api/config.py`, `api/models.py`, `api/webui_schema.py`, `api/state_sync.py` | Discovery/globals/model detection; session model + CRUD + LRU cache; `__webui` schema columns; `/insights` sync into state.db |
| Sessions | `api/agent_sessions.py`, `api/session_ops.py`, `api/session_display.py`, `api/session_watcher.py` | Session lifecycle around the SDK; ops/display helpers; background session watching |
| Workspace | `api/workspace.py` | File ops, workspace registry, git detection, trust levels (see §5) |
| Auth & identity | `api/auth.py`, `api/profiles.py`, `api/onboarding.py` | Optional password gate + signed cookies; profile state; first-run wizard |
| Panels | `api/terminal.py`, `api/vnc.py`, `api/observer.py`, `api/cli_viewer.py` | Terminal PTY channel; VNC channel; observer surface; CLI session listing |
| Business | `api/slash_commands.py`, `api/clarify.py`, `api/metering.py`, `api/providers.py`, `api/commands.py`, `api/background.py`, `api/updates.py` | Slash-command dispatch; clarify dialogs; usage metering; provider/model gate; command registry exposure; background goals; update check |

External dependency: `pyyaml` only (plus the hermes-agent SDK and its own
deps, supplied via the agent venv).

---

## 3. Frontend layout

```
frontend/src/
  stores/          Zustand stores — useSessionStore, useChatStore,
                   useSettingsStore, useWorkspaceStore, useApprovalStore,
                   useCronStore, useMemoryStore, useSkillStore,
                   useTerminalStore, useProvidersGateStore, …
  stores/wsManager.ts / streamManager.ts   /api/ws/events client + replay
  services/api.ts  fetch wrapper for /api/*
  components/      chat, sidebar, workspace, terminal, observer, settings,
                   memory, skills, tasks, dialogs, layout, …
  i18n/            i18next; locales en and zh-CN only
scripts/generate-m3-theme.mjs   Material 3 theme generation
  (material-color-utilities)
```

- State lives in Zustand stores, one domain per store. `useSessionStore`
  keeps `currentSession` as a direct state field — any action mutating a
  field on `sessions[]` must mirror that field onto `currentSession` when the
  ids match, or the UI lags until the next session switch.
- Theme is `system | dark | light`; legacy theme values are clamped to dark.
- Vite dev server proxies `/api` (and WS) to the backend on port 8787.

---

## 4. Streaming model

**WebSocket-first.** The frontend holds one connection to `/api/ws/events`;
the backend agent thread pushes `token`, `tool`, `approval`, `done`, `error`
events into a per-session queue which the WS endpoint forwards. This replaced
the earlier SSE-per-stream design.

- `/api/ws/events` — main event bus (replaces SSE endpoints
  `/api/chat/session/stream` and `…/notify`).
- `/api/ws/terminal` — terminal PTY channel.
- `/api/ws/vnc[/<port>]` — VNC channel for the observer panel.
- `GET /api/chat/session/stream/replay` — SSE, kept only for session replay
  and reconnect recovery.
- `GET /api/chat/stream/status` — stream status query. There is no
  `GET /api/chat/stream?stream_id=` endpoint.

Chat round trip: `POST /api/chat/start` (session_id + message) → server
creates the queue, spawns the agent thread (`AIAgent.run_conversation(task_id=…)`),
returns a stream id → the WS bus delivers events as the agent produces them →
on `done`, session messages are persisted.

---

## 5. Session & state model

- **WebUI state dir** — `HERMES_WEBUI_STATE_DIR`, default `~/.hermes/webui`:
  `sessions/{id}.json`, settings, workspace registry.
- **Agent state** — `HERMES_HOME`, default `~/.hermes`: config.yaml, memory,
  skills, `state.db`. Note `HERMES_HOME` is the agent *state* directory, not
  the install directory.
- **Sessions** — JSON file per session; `state.db` (SQLite) is the
  listing/SDK source of truth. The `__webui` columns webui adds to state.db
  tables are the only SQL writes webui makes directly; everything else goes
  through the SDK's high-level API.
- **In-memory cache** — a module-level `SESSIONS` OrderedDict LRU (max 100)
  guarded by a module-level `LOCK`. Every access to `SESSIONS` must hold the
  lock.

---

## 6. Auth & security

- **Optional password gate** — `HERMES_WEBUI_PASSWORD`; HMAC-signed HTTP-only
  cookie with 24h TTL (`api/auth.py`). Off by default for localhost/SSH-tunnel
  use. TLS via `HERMES_WEBUI_TLS_CERT` / `HERMES_WEBUI_TLS_KEY`.
- **HTTP surface** — security headers on all responses, 20MB POST body limit,
  path-traversal defense via `safe_resolve(root, requested)` on every file
  operation.

### Workspace path trust levels

`api/workspace.py` has two distinct trust functions — do not collapse them:

- **`validate_workspace_to_add(path)`** — used by `/api/workspaces/add`
  (explicit user registration). Permissive: blocks only non-existent,
  non-directory, and system-root paths. The user is consciously registering
  an external path (e.g. `/mnt/d/Projects` in WSL), so we trust intent.
- **`resolve_trusted_workspace(path)`** — used for actual file read/write
  inside an existing workspace. Strict: path must be under home, in the saved
  workspace list, or under `BOOT_DEFAULT_WORKSPACE`. Prevents path traversal
  and unauthorized file access.

The distinction matters because add uses permissive validation to avoid the
circular dependency: you cannot get a path into the saved list if you need
the saved list to add it.

---

## 7. API surface overview

Endpoints are grouped by domain; consult `backend/app/api/routes.py` for the
authoritative list (it is the single source of truth — this table is a map).

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
| Profiles | `/api/profiles*` | Profile CRUD and switching |
| Auth | `/api/auth*`, `/login` | Password gate, session check |
| Ops | `/api/settings*`, `/api/git-info`, `/api/updates*`, `/api/insights*` | Settings, git badge, update check, usage insights |
| Panels | `/api/cron*`, `/api/skills*`, `/api/memory*`, `/api/terminal*`, `/api/vnc*`, `/api/observer*`, `/api/cli*` | Tasks/cron, skills, memory, terminal, VNC, observer, CLI session bridge |

---

## 8. Configuration

Environment variables (host/port/state dir/password/TLS/agent dir/model
defaults) are documented once in [README.md](../README.md#configuration) —
keep the table there and link here, so it cannot drift in two places.

Discovery and defaults live in `backend/app/api/config.py`; launch-time
environment handling is in `backend/app/bootstrap.py` and `scripts/ctl.sh`
(the latter also sources a repo-root `.env` if present).

---

## 9. Architecture Decision Records

Condensed; each entry records a historical decision and its current status.

### ADR-001: Single-File Server
**Decision:** all server code in `server.py`.
**Rationale:** no build step, easy agent modification, zero deployment
complexity. **Trade-off:** maintenance burden grows with file size.
**Status: Superseded** — split into `server.py` + `api/` modules.

### ADR-002: HTML as Python Raw String
**Decision:** frontend embedded in `server.py` as `r"""..."""`.
**Rationale:** simplest way to serve a frontend without a static file server.
**Trade-off:** no syntax highlighting, painful patching.
**Status: Superseded** — frontend source lives in `frontend/src`, built into
`static/`.

### ADR-003: ThreadingHTTPServer
**Decision:** Python stdlib synchronous threads, not asyncio.
**Rationale:** no dependencies; synchronous agent calls fit threads naturally.
**Trade-off:** memory scales with concurrent users; thread pool unbounded.
**Status: In effect** — acceptable for single-user deployments.

### ADR-004: SSE over WebSockets
**Decision:** Server-Sent Events for streaming.
**Rationale:** simpler than WebSockets, standard `EventSource` API.
**Trade-off:** server-to-client only.
**Status: Superseded** — WebSocket-first (`/api/ws/events`); SSE remains only
for session replay.

### ADR-005: Module-Level Approval State
**Decision:** approval state shared as module-level dict in the SDK's
`tools/approval.py`, cross-thread in one process.
**Rationale:** pre-existing approval system; same-process sharing works.
**Trade-off:** breaks under multi-process (gunicorn workers) or subprocess.
**Status: In effect** — the webui registers a notify callback on the SDK's
approval registry and polls as a fallback. Single-process constraint remains.

### ADR-006: No Authentication
**Decision:** no auth initially (localhost via SSH tunnel).
**Rationale:** transport layer already authenticated.
**Trade-off:** anyone with localhost access can use the server.
**Status: Superseded** — optional password gate + TLS (`api/auth.py`).

### ADR-007: Approval State via Environment Variables
**Decision:** `HERMES_EXEC_ASK`, `HERMES_SESSION_KEY` etc. passed via
`os.environ`.
**Rationale:** `tools/approval.py` and the terminal tool read these env vars.
**Trade-off:** process-global; concurrent chat requests can clobber each
other.
**Status: In effect (known debt)** — `_ENV_LOCK` serializes mutations but
does not fully isolate execution; tracked in [bugs.md](bugs.md) #195.

---

## 10. Developer conventions

Working conventions and critical rules (delete ≠ create, `task_id=` not
`session_id=`, lock around `SESSIONS`, `currentSession` mirroring, …) are
maintained in [CLAUDE.md](../CLAUDE.md) — the authoritative copy. They are
deliberately not duplicated here.

---

## 11. Debugging

```bash
./scripts/ctl.sh status            # process up? which pid/port
./scripts/ctl.sh log-path          # where the server log lives
./scripts/ctl.sh restart           # rebuild is separate: ./scripts/build.sh

DRY_RUN=1 ./scripts/run-test.sh smoke     # print what a test run would do
cd backend && python -m pytest tests/ -k <name>

curl -s http://127.0.0.1:8787/health
```

For manual browser checks see [testing.md](testing.md).
