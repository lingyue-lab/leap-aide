---
title: LeapAide Architecture Guide
---

<div align="center">

**English** | [中文](architecture.zh-CN.md)

</div>

# LeapAide Architecture Guide

LeapAide's backend is pure Python standard library, calling the Hermes Agent SDK directly via Python import (no HTTP hop to Hermes Agent); the frontend is React/TypeScript built with Vite. Realtime updates rely on WebSocket.

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
| Config & models | `api/config.py`, `api/model_catalog.py`, `api/models.py`, `api/webui_schema.py`, `api/state_sync.py` | Discovery/globals/model detection; JSON model catalog (load/alias/live query/merge); session model + CRUD + LRU cache; `__webui` schema columns; `/insights` sync into state.db |
| Sessions | `api/agent_sessions.py`, `api/session_ops.py`, `api/session_display.py`, `api/session_watcher.py` | Session lifecycle around the SDK; ops/display helpers; background session watching |
| Workspace | `api/workspace.py` | File ops, workspace registry, git detection, trust levels |
| Auth & identity | `api/auth.py`, `api/profiles.py` | Optional password gate + signed cookies; profile state |
| Panels | `api/terminal.py`, `api/vnc.py`, `api/observer.py`, `api/cli_viewer.py` | Terminal PTY channel; VNC channel; observer surface; CLI session listing |
| Browser desktop | `api/global_desktop.py`, `api/browser_receptionist.py`, `api/browser_desktop.py`, `api/cdp_pipe.py`, `compat/browser_cdp.py`, `compat/browser_session.py`, `compat/desktop_guard.py` | Shared X/Chrome desktop lifecycle; the CDP receptionist (the only CDP surface); SDK-side CDP/session patches; terminal-level desktop guard |
| Business | `api/slash_commands.py`, `api/clarify.py`, `api/metering.py`, `api/providers.py`, `api/commands.py`, `api/background.py`, `api/updates.py` | Slash-command dispatch; clarify dialogs; usage metering; provider/model gate; command registry exposure; background goals; update check |

External dependencies: `pyyaml` only (plus the Hermes Agent SDK and its own deps, supplied by the Hermes Agent SDK venv).

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
| Panels | `/api/cron*`, `/api/skills*`, `/api/memory*`, `/api/terminal*`, `/api/vnc*`, `/api/observer*`, `/api/cli*`, `/api/browser-desktop` | Tasks/cron, skills, memory, terminal, VNC, observer, CLI session bridge, shared browser-desktop status |

---

## Browser desktop

A global shared browser desktop is provided, operated through the
`x-browser-desktop skill`. The main purpose is to make browser operations
visible; on top of that, the user can operate the desktop themselves — to
store login information, so that the Agent's web tasks are not interrupted.

The desktop instance runs **one** Chrome (issue #37): every chat session
shares it — the desktop panel shows the same "living room", not a private
browser per conversation. Chrome's CDP control endpoint is unreachable on
the network: it is started with `--remote-debugging-pipe`, so CDP frames
travel over an inherited file descriptor (Chrome reads fd 3, writes fd 4)
instead of a TCP port. Therefore `ps` and `ss` show no connectable
direct-to-Chrome CDP entry.

The **receptionist** (`api/browser_receptionist.py`) is the process that
holds that pipe, and it acts as the proxy providing the only CDP entry in
the whole instance: an HTTP + WebSocket server on `127.0.0.1:6900`. Clients
never talk to Chrome directly — they talk to the receptionist, and the
receptionist decides what reaches Chrome and what reaches the client. That
is what makes sharing one Chrome safe.

### The pieces

| Module | Role |
|---|---|
| `api/global_desktop.py` | Owns the X stack (Xvfb + openbox + x11vnc + fcitx5), starts the receptionist and Chrome, supervises them, publishes `browser_desktop.json` |
| `api/browser_receptionist.py` | The CDP proxy: discovery endpoints, the WebSocket endpoint, the ownership tables, command forwarding |
| `api/cdp_pipe.py` | Spawns Chrome over `--remote-debugging-pipe`; reads and writes NUL-delimited JSON frames |
| `api/browser_desktop.py` | `GET /api/browser-desktop` — the status the sidebar panel polls |
| `compat/browser_cdp.py` | Runtime patch: points the Hermes SDK at `<receptionist>/json/version?sid=<session_id>` |
| `compat/browser_session.py` | Gives the SDK's browser session a tab of its own inside the shared Chrome |
| `compat/desktop_guard.py` | Terminal-level guard: raw CDP and VNC endpoints stay out of reach of shell commands |

The state file `$HERMES_WEBUI_STATE_DIR/browser_desktop.json` is the single
source of truth for the wrappers and the SDK patch: it carries the
`receptionist_url` and a `status` (`active` / `degraded`). Check the status
before using that address — the SDK hijack patch treats anything but
`active` as "no browser desktop configured", and browser tools then fall
back to local headless.

```text
   client
     │  GET /json/version?sid=<session_id>
     ▼
   receptionist -- fd 3 / fd 4 --> Chrome
     │  webSocketDebuggerUrl: ws://127.0.0.1:6900/devtools/browser/global?sid=<session_id>
     ▼
   client -- CDP frames (filtered by tab owner) --> receptionist -- fd 3 / fd 4 --> Chrome
```

### Concurrent sessions, isolated by tab

A session does not get its own Chrome instance — it gets its own **tab**,
and the receptionist owns the mapping. Every client has to declare which
session it belongs to with `?sid=<session_id>` (or the `X-LeapAide-Session`
header). The receptionist keeps three tables:

| Table | Maps |
|---|---|
| `_tabs` | `targetId` → owning connection |
| `_cdp_sessions` | CDP `sessionId` → owning connection |
| `_conns` / `_sessions` | live connections, by connection id and by sid |

Isolation takes effect in four places:

- **Discovery is filtered.** `Target.getTargets` is answered by the
  receptionist itself — it asks Chrome internally and returns only the tabs
  this connection owns. `/json/list` returns only the tabs of the
  requesting sid. Another session's tab simply does not appear in the
  answer.
- **Acting is gated.** `Target.closeTarget`, `Target.activateTarget` and
  `Target.attachToTarget` are refused with `target not owned by this
  connection` unless the `targetId` belongs to the caller. A `sessionId`
  owned by another connection is refused the same way.
- **Events are dropped.** `Target.attachedToTarget`, `Target.targetDestroyed`
  and any message carrying a foreign `sessionId` never reach the CDP
  socket.
- **Tabs are capped and reaped.** A connection may own at most 10 tabs
  (`MAX_TARGETS_PER_CONN`), and its tabs die with the connection.

Two sessions can therefore work in the same Chrome at the same time: they
share the window and the instance, not the tab.

### Where the CDP WebSocket comes from

Chrome does not expose any CDP debugging address directly — the
receptionist provides the CDP WebSocket:

1. The client calls `GET /json/version?sid=<session_id>` on the
   receptionist. A request without a sid is answered with `400`, and the
   response body spells out the required URL shape.
2. The receptionist answers with
   `webSocketDebuggerUrl: ws://127.0.0.1:6900/devtools/browser/global?sid=<session_id>`.
   The sid is embedded in the URL, so a downstream tool keeps its identity
   when it reconnects — it does not have to re-declare who it is on every
   communication.
3. The client opens that WebSocket. The receptionist accepts the
   connection, registers a connection record (id, sid, peer address) and
   starts reading frames.

What is special about the accept step:

- **No tab is created for you.** The tab set is empty when the connection
  is established; the client creates its own (`Target.createTarget`, or
  `GET /json/new` when the sid already has a connection) — a new connection
  touches nothing it did not ask for.
- **There is no page-scoped socket.** `GET /devtools/page/<targetId>`
  answers `403` unless the requesting sid owns that tab, and `501`
  ("use the browser WS endpoint") otherwise. All traffic goes through the
  one browser-level socket.
- **The socket has a heartbeat** (30 s), so a client that vanished is
  noticed in time instead of lingering.

### How CDP commands are forwarded

The receptionist's main job is proxying and forwarding: the `method` and
`params` of a command reach Chrome unchanged. But around the forwarding
there is a series of further actions.

**Before forwarding, the command is checked.**

| Command | Refused with |
|---|---|
| any command carrying a `sessionId` | `sessionId not owned by this connection` |
| `Target.createTarget` beyond the 10-tab cap | `target cap reached` |
| `Target.closeTarget` / `Target.activateTarget` / `Target.attachToTarget` on a foreign tab | `target not owned by this connection` |
| anything, while Chrome is down | `browser unavailable` |

**The id is rewritten.** Each client request id is swapped for the
receptionist's global id (a counter starting at `-1`) before it goes into
Chrome's pipe, and on the way back it is swapped for the client's original
id. Two clients can then use the same id numbers without colliding, and
every response can be matched to the connection that asked for it. The
receptionist's own commands use a disjoint id range starting at
`INT32_MIN` — Chrome's pipe parser only accepts ids in the int32 range.

**The pending entry is tagged, so the answer can be interpreted.** For
every in-flight request the receptionist records the owning connection, the
original id, and a kind:

| Kind | What the response tells it |
|---|---|
| `client-create` | a `Target.createTarget` succeeded → the new tab now belongs to this connection |
| `client-attach` | a `Target.attachToTarget` succeeded → the new CDP `sessionId` now belongs to this connection |
| `client-close` | a `Target.closeTarget` succeeded → forget the tab (Chrome sends no `targetDestroyed` for a close in pipe mode) |
| `client` / `internal` | plain pass-through |

Both the request and the result of a client `Target.getTargets` are
filtered: the receptionist issues its own internal `Target.getTargets`,
filters the result down to the caller's own tabs, and returns that.

A response whose `sessionId` belongs to another connection is dropped;
browser-level events that are unrelated to a tab are broadcast to everyone,
tab-level events go only to the owner.

### How the WebSocket is closed

A close is not just collecting a socket — it is a cleanup path. When the
client's socket ends (close frame, error, dropped connection), the
receptionist runs the same teardown routine for that connection:

1. mark the connection closed, so nothing else is routed to it;
2. close **every tab it owns**, one by one, with an internal
   `Target.closeTarget`, and forget those tab ids — a dropped connection
   leaves no tab behind on the shared desktop;
3. drop the CDP session ids bound to the connection;
4. drop the connection's still-pending requests, so a late answer is not
   delivered to a dead socket;
5. remove the connection from the tables and close the WebSocket.

Two extra reapers cover what a clean close does not reach:

- **Turn-end sweep.** When a chat turn ends, the backend calls
  `close_session_tabs(session_id)`, which closes the connections of that
  sid that are still attached and already idle (a 30 s grace window) —
  this is exactly what catches a browser tool whose process crashed before
  it could close its socket. The behaviour is controlled by the
  `observer_tab_cleanup` setting (default `session`).
- **Chrome death and shutdown.** When Chrome exits, the receptionist
  clears its tables and respawns Chrome with backoff. When the receptionist
  itself shuts down, it drops all connections first, then terminates Chrome
  over the pipe.

### Ports, files and environment

| Name | Value |
|---|---|
| Receptionist HTTP/WS | `127.0.0.1:6900` (`HERMES_RECEPTIONIST_PORT`) |
| Desktop VNC / display | `5900` (`HERMES_GLOBAL_VNC_PORT`), display `:99` (`HERMES_GLOBAL_DISPLAY`) |
| Chrome CDP | none — parent-child pipe only |
| State file | `$HERMES_WEBUI_STATE_DIR/browser_desktop.json` |
| Kill switch | `HERMES_GLOBAL_DESKTOP=0` — no desktop; browser tools fall back to local headless |

---

## Frontend (`frontend/src/`)

- **React 18 + TypeScript + Vite**; state is managed by **Zustand** stores under `stores/`.
- i18next handles internationalization.
- Strictly follow the Google Material 3 frontend specification. When reviewing M3 compliance, refer to the component specifications, spacing, font sizes, and other standards in the documents below.
    - [Online reference 1](https://www.yuque.com/advancedux/xr6e1n)
    - [Online reference 2](https://www.cyub.vip/blog/2026/02/14/material-design-3-material-you-%E5%AE%8C%E6%95%B4%E5%BC%80%E5%8F%91%E6%8C%87%E5%8D%97/)
- Core stores: `useSessionStore`, `useChatStore`, `useSettingsStore`, `useWorkspaceStore`, `useApprovalStore`.
- API calls go through `services/api.ts`. The Vite dev server proxies `/api` to `localhost:8787`.
- Build output goes to `../static/`, served directly by the Python backend.


## Data Flow (full chat path)

- Frontend `send()` → `POST /api/chat/start`, carrying session_id + message
- The server creates a `queue.Queue`, starts a daemon thread running `_run_agent_streaming()`, and returns `stream_id`
- The frontend receives events over the WebSocket at `/api/ws/events`; on a mid-session refresh it recovers via SSE replay `GET /api/chat/session/stream/replay`
- The agent thread runs `AIAgent.run_conversation()`, putting token/tool/approval/done events into the queue
- `/api/ws/events` reads events from the queue and forwards them to the browser
- On `done`: update session messages, save to on-disk JSON

---

## Database

- Both LeapAide and the Hermes Agent SDK use state.db; the default path is `${HERMES_HOME}/state.db`.
- LeapAide extends the state.db schema beyond the Hermes Agent SDK, adding some extra columns to tables. Column names must end with `__webui`; only LeapAide uses these columns.
- All updates/writes to state.db must go through the Hermes Agent SDK's high-level API; connecting directly to state.db via SQL for low-level database updates is not allowed. Since the Hermes Agent SDK is unaware of any `__webui` columns, none of the high-level APIs it provides read or update `__webui` columns. To implement features, LeapAide is allowed to connect directly to state.db to update `__webui` columns.
- LeapAide does not need to consider database forward compatibility or data migration — for example, changing the data type of an `X__webui` column from TEXT to INT. You may assume LeapAide is running for the first time with no historical data.
- LeapAide does not need to repair historical data in the database. If old values in the db are invalid under new code, no automatic data repair is needed in the backend code — raise an error directly.

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `HERMES_WEBUI_HOST` | `127.0.0.1` | Bind address |
| `HERMES_WEBUI_PORT` | `8787` | Port |
| `HERMES_WEBUI_STATE_DIR` | `~/.hermes/webui` | State directory (sessions JSON, settings, workspaces) |
| `HERMES_WEBUI_DEFAULT_WORKSPACE` | `~/.hermes/webui/workspaces/default` | Default workspace path (single source of truth for the built-in "Default" entry) |
| `HERMES_WEBUI_DEFAULT_MODEL` | *(empty)* | Default model; empty means "discover from configured providers" |
| `HERMES_WEBUI_PASSWORD` | *(unset)* | Setting it enables the password gate |
| `HERMES_WEBUI_TLS_CERT` / `HERMES_WEBUI_TLS_KEY` | *(unset)* | TLS certificate / key |
| `HERMES_WEBUI_AGENT_DIR` | *(auto-discovered)* | hermes-agent **install** directory |
| `HERMES_HOME` | `~/.hermes` | hermes-agent **state** directory (config, memory, skills, `state.db`) |
| `HERMES_WEBUI_PYTHON` | *(auto-discovered)* | Python executable override |
| `HERMES_WEBUI_ALLOWED_ORIGINS` | *(unset)* | Extra allowed origins |
| `HERMES_WEBUI_AUTO_INSTALL` | *(unset)* | `=1` enables dependency auto-install at startup |
| `HERMES_WEBUI_PREWARM_TOOL_DEFS` | *(enabled)* | Boot-time tool-definition snapshot; `=0` disables (first chat then pays the tool-check cost) |
| `HERMES_WEBUI_PREWARM_WAIT_S` | `10` | Max seconds a chat waits for the prewarmed tool definitions; `=0` never waits |
| `HERMES_WEBUI_FORCE_IPV4` | *(enabled)* | Prefer IPv4 for outbound connections (skips blackholed-IPv6 stalls); `=0` disables |
| `HERMES_WEBUI_CONNECTIVITY_PROBE` | *(enabled)* | Background provider reachability probe at boot and after saving a key; `=0` disables |
| `HERMES_WEBUI_CATALOG_QUERY_TIMEOUT_S` | `3.0` | Per-provider timeout (seconds) for live model-list queries during a catalog build |
| `HERMES_WEBUI_COLD_START_WAIT_S` | `5.0` | Max seconds a chat start waits for the boot model-catalog build before it refuses the turn; `=0` never waits |
| `LOG_LEVEL` | `WARNING` | Python logging level |

---

## Model catalog & refresh

Provider display names, aliases, baseline model lists, endpoints, auth
headers and refresh policy live in a single JSON file:
`backend/app/api/data/model_catalog.json` (schema v1). It is loaded and
validated by `api/model_catalog.py`; the Python tables that used to mirror
this data (`_PROVIDER_MODELS`, `_FALLBACK_MODELS`, `_PROVIDER_DISPLAY`,
`_PROVIDER_ALIASES`, `_OPENAI_COMPAT_ENDPOINTS`) were removed — the JSON
file is the only source of truth for *what models a provider offers*.

Key properties:

- **Per-provider record**: `display_name`, `is_custom`, `aliases[]`,
  `auth{type,env_var(s),header}` (`x-api-key` for Anthropic, Bearer
  otherwise), `endpoint{base_url,models_path,trust_local}`,
  `refresh{mode: api|none, membership: replace|curated, timeout_s}`,
  `default_model`, and a human-curated `models[]` initial list. The model
  ids are stored in the `@provider:model` form the picker uses; the few
  providers that keep bare ids (gemini, minimax, openrouter — e.g.
  `MiniMax-M2.7`, `openai/gpt-5.4-mini`) get the `@provider:` prefix
  applied when the picker is assembled, so every id the user sees is
  `@provider:model`. For `refresh.mode=none` providers it is the only source;
  for `refresh.mode=api` providers it is only the first-boot seed — once
  a live query succeeds, the API result fully replaces it.
- **Custom endpoints**: `is_custom` (default `false`) marks a provider
  whose endpoint is the user's own OpenAI-compatible server rather than a
  vendor API — `ollama` and `lmstudio` in the shipped catalog. Such a
  provider needs no API key, and the `base_url` the user configured for it
  survives a change of the default model (switching to a vendor provider
  still clears it). The flag is data, not a hardcoded slug list:
  `model_catalog.is_custom_provider()`.
- **Single deterministic merge** (`resolve_provider_models`): explicit
  `providers.<id>.models` in config.yaml always wins and is never
  queried. For `membership=replace` a successful live query is the
  single source of truth — membership and order come from the API, and
  the JSON baseline only donates display labels for ids the API still
  lists (models the API omits vanish; API-only models appear
  immediately). For `membership=curated` the result is the intersection
  of baseline and live ids. A failed (or empty) query keeps the last
  successful list (memory or disk envelope) or the baseline; a group is
  never cleared by a failure. There is no multi-source fallback chain —
  this replaced the old "try the agent, then a hardcoded endpoint
  table, then the static list" behaviour.
- **Refresh triggers** reuse the existing `/api/models` build machinery
  (no extra background threads): the 24 h TTL, config.yaml or
  `model_catalog.json` mtime changes, key saves, and the boot prewarm.
  Providers with `refresh.mode=api` are queried concurrently
  (`ThreadPoolExecutor`, ≤6 workers) with a per-provider timeout.
  Eligibility remains key/OAuth based: a catalog provider that declares
  API-key auth is dropped when no key is configured — including the active
  provider, so deleting a key removes that provider's models instead of
  falling back to its JSON baseline. Providers without an API-key
  requirement (local endpoints, unknown/custom providers) stay visible when
  active, and custom `model.base_url` fetches still re-add their slug.
- **Picker provider hints**: `/api/models` prefixes every model ID with its
  owning provider (`@provider:model`), including slash-form IDs
  (OpenRouter's `deepseek/deepseek-v4-flash` becomes
  `@openrouter:deepseek/deepseek-v4-flash`) and the active provider's own
  models. A bare or slash-only ID does not name a route:
  `resolve_model_provider()` strips a prefix that matches `model.provider`,
  so without the hint an OpenRouter selection would silently route to the
  DeepSeek direct API whenever `deepseek` is the active provider. The hint is
  split at the first colon, so the model half keeps any further slashes and
  colons (e.g. `@ollama1:qwen3-vl:235b`), and `POST /api/default-model`
  resolves the hint back to `model.default` + `model.provider` before writing
  config.yaml.
- **Stored session model vs. picker entries**: the picker compares ids
  literally, so a session model has to use the same form as the catalog entry
  it refers to — otherwise the popover opens with nothing selected. Every
  catalog id is `@provider:model`, and `normalize_model_prefix()` maps a bare
  or namespaced config value (`deepseek-flash`, `anthropic/claude-opus-4.6`)
  onto the identity of the provider that serves it, so that is what a session
  stores. Sessions created earlier may carry a form the catalog no longer
  lists (a bare name, or a model its provider stopped offering): the frontend
  resolves the stored id to the catalog entry (`findCatalogModelId()`) for
  selection, and the WebUI-owned identity (`model__webui`) is authoritative
  on the display path. The hint is internal routing plumbing and never
  appears in the UI text.
- **Disk envelope**: the models cache snapshot persists
  `{schema_version, active_provider, default_model,
  default_model_available, default_model_provider_has_key, groups,
  provider_models, fetched_at}` so a restart serves a complete first
  response and keeps per-provider last-good lists; legacy `{groups-only}`
  files still load.
- **Default model availability (issue #44)**: `/api/models` carries
  `default_model_available` (`true` / `false` / `null`). It is computed
  from the real provider groups *before* the configured default is
  injected into the picker, and is `null` when unknown (config-only
  response, in-flight build, or a legacy disk snapshot). Matching is
  scoped to the default's own provider (`model.provider` is authoritative;
  the name-only comparison is a fallback for provider-less configs), so a
  same-named model under another provider cannot mask a removed default.
  An empty group list (live results were empty or every provider was
  rejected) also stays `null` — except when the default's provider is a
  catalog API-key provider without a key, which is a definitive failure. A companion field,
  `default_model_provider_has_key` (`true` / `false` / `null`; `null` for
  local/OAuth/unknown providers), lets the UI say the provider's API key
  was removed rather than claiming the provider retired the model. An
  unavailable configured default is still injected so the UI can display
  it, and is marked `unavailable: true`; the backend never rewrites it to
  another model — the UI only warns. When the provider group vanished with
  the key, the composer keeps showing the configured value plus an
  explanation instead of silently selecting a different model; an explicit
  pick in the composer dismisses that warning, because the new selection
  replaces the default for the conversation. The raw
  SDK "provider set in config.yaml but no API key" error is translated in
  both stream failure paths into the same Settings-oriented message.
  New sessions created without an explicit model resolve through
  `get_default_model_for_new_session()`: the effective default first (env
  override `HERMES_MODEL` / `OPENAI_MODEL` / `LLM_MODEL` → config.yaml
  `model.default` → built-in constant), then the first available catalog
  model only when that is empty. Settings saves the control via
  `POST /api/default-model`, which resolves the WebUI-only
  `@provider:model` form back to `model.default` + `model.provider` in
  config.yaml.
- **Model availability of a conversation (issue #44 follow-up)**: the
  catalog only reports the *configured default's* availability, so an
  existing session that runs another model asks the backend through
  `GET /api/models/availability?model=…`. The answer
  (`{model, provider, available, provider_has_key}`) is computed from the
  live provider groups, provider-scoped like the default-model check:
  `available` is `true` / `false` / `null` (unknown — nothing to compare
  against) and `provider_has_key` is `true` / `false` / `null` (the provider
  does not authenticate with an API key). A missing key settles the verdict
  even when the catalog has no groups. The composer keeps the session's own
  model visible (marked unavailable) and explains why instead of silently
  highlighting the next available model of another provider — an existing
  session behaves like the welcome state, which warns about the configured
  default. The query is repeated whenever the catalog content changes, so
  deleting a provider's API key is reflected right away.
- **Wording follows the conversation, not the model value**: a brand-new
  conversation runs the configured default, so it talks about "the default
  model"; an existing session runs the model the user picked for it and
  talks about "the current model" — even when the two happen to be the same
  model. The composer applies the rule from the session context (it knows
  whether the conversation has a model of its own), and the backend applies
  it from the session id (`_handle_chat_start` mints a new session for the
  welcome-state first send, so it knows whether the conversation is new).
  `streaming._missing_key_wording()` picks the matching label, message and
  hint for the two stream error paths.
- **SSRF guard** (`model_catalog._check_url_safe`): scheme allowlist
  (http/https) plus rejection of hostnames resolving to private IPs,
  with explicit escapes for known-local hosts (ollama/localhost/
  lmstudio), catalog `endpoint.trust_local`, and user-declared
  `custom_providers` hostnames.
- **Z.ai endpoint fix**: the catalog carries
  `https://api.z.ai/api/paas/v4` (the old hardcoded `api.z.ai/v1` was
  404). Volcano Ark (`ark`, `ARK_API_KEY`) was added as a verified
  provider.
- **Maintenance script**: `scripts/sync_models.py` drafts an OpenRouter
  baseline update from the public `/api/v1/models` endpoint (labels of
  existing ids preserved with `--map-labels`). It prints a JSON fragment
  for manual review and never writes the catalog itself; it is not part
  of CI.

---

## Model identity (`@provider:model`)

Every model the WebUI shows is identified as `@provider:model`. The
provider half is split at the **first** colon, so gateway namespaces
(`@openrouter:anthropic/claude-opus-4.6`) and Ollama tags
(`@ollama1:qwen3-vl:235b`) survive intact.

- **Storage**: the identity lives in the WebUI-owned `model__webui`
  column of `state.db`. The SDK-owned `model` column keeps only the bare
  model id — the SDK never sees the `@provider:` prefix.
- **Only routable form**: a bare id, a namespaced `provider/model` value
  or `unknown` has no provider hint and cannot be routed, so it is
  rejected (`Session._is_valid_model_identity`) instead of falling through
  to config.yaml's default endpoint. The retired `@custom:<name>:model`
  form is rejected too — a named custom provider is identified by its
  name slug (`@ollama1:model`).
- **Named custom providers**: the WebUI slug (lower-cased name, spaces
  collapsed to dashes, `@ : /` replaced) is the storage and display form.
  The id handed to the SDK is the SDK's own `custom:<slug>`
  (`config.sdk_custom_provider_id`) — the SDK branches on that prefix for
  `extra_body` and to bypass built-in shadowing, and accepts only its own
  alias set.
- **Custom endpoints** come in two flavours. A *named* provider declared in
  config.yaml (`custom_providers:`) is identified by its name slug and the
  SDK receives `custom:<slug>`. A *catalog* custom endpoint (`is_custom`:
  `ollama`, `lmstudio`, plus the synthetic `local` slug) keeps its catalog
  slug — the SDK knows those names — needs no API key, and keeps the
  `base_url` the user configured for it when the default model changes.
- **Cold start**: the chat-start path waits (bounded by
  `HERMES_WEBUI_COLD_START_WAIT_S`) for the boot catalog build before
  refusing a turn, so the first message after a cold start is not rejected
  with "Session model is invalid".

---

## Architecture Decision Records (ADR)

Each entry records one historical decision and its current status.

### ADR-001: Example
- **Decision:** TBD
- **Rationale:** TBD
- **Status:** TBD
