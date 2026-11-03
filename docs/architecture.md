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
| Panels | `/api/cron*`, `/api/skills*`, `/api/memory*`, `/api/terminal*`, `/api/vnc*`, `/api/observer*`, `/api/cli*` | Tasks/cron, skills, memory, terminal, VNC, observer, CLI session bridge |

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
- **Picker provider hints**: `/api/models` prefixes every model ID from a
  non-active provider group with `@provider:`, including slash-form IDs
  (OpenRouter's `deepseek/deepseek-v4-flash` becomes
  `@openrouter:deepseek/deepseek-v4-flash`). A bare slash ID is ambiguous:
  `resolve_model_provider()` strips a prefix that matches `model.provider`,
  so without the hint an OpenRouter selection would silently route to the
  DeepSeek direct API whenever `deepseek` is the active provider. Custom
  providers (`custom:<name>`) are exempt because their hint itself contains
  a colon. The active provider's IDs are left untouched (it is the default
  route), and `POST /api/default-model` resolves the hint back to
  `model.default` + `model.provider` before writing config.yaml.
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
  response, in-flight build, or a legacy disk snapshot). When no provider
  survives the eligibility filter, availability is `false` if the
  default's provider is a catalog API-key provider without a key — that is
  a definitive failure, not the unknown case. A companion field,
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
