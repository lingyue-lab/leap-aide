# Leap Aide Roadmap

**English** | [简体中文](roadmap.zh-CN.md)

Current state and near-term direction. For shipped history see
[CHANGELOG.md](../CHANGELOG.md); for known gaps see [bugs.md](bugs.md).

---

## Current state

**Backend** — Python stdlib HTTP server (`backend/app/server.py` + `api/`).
Sessions persisted as JSON files plus a `state.db` SQLite store (shared with
the hermes-agent SDK). Optional password gate, TLS support, upload handling,
workspace file operations with two trust levels, profile management.

**Frontend** — React 18 + TypeScript + Vite + Zustand + Tailwind, Material 3
design language, i18n (English, 简体中文). Built into `static/` and served
directly by the Python backend.

**Realtime** — WebSocket-first: a single `/api/ws/events` connection carries
token/tool/approval/done events, with dedicated `/api/ws/terminal` and
`/api/ws/vnc` channels for the terminal and VNC/observer panels. Session
replay keeps a small SSE surface for reconnect recovery.

**Deployment** — multi-stage Docker image (runtime + cicd targets) with the
hermes-agent venv baked at build time; `docker-compose.yml` for single-service
deployment; `scripts/install.sh` one-line installer; `scripts/ctl.sh` for
local process control.

**Testing** — backend pytest suite, frontend vitest suite, layered runner
(`./scripts/run-test.sh`), CI matrix across Python 3.11–3.13. See
[testing.md](testing.md).

---

## Near-term directions

Directional, not scheduled — no sprint numbering, no promised dates.

- **API surface cleanup** — consolidate the route table in `routes.py` toward
  consistent resource naming and error shapes, keeping routes.py the single
  source of truth for endpoints.
- **Observability** — finish the structured logging / insights path so session
  health and background-goal failures are visible without reading raw logs.
- **Streaming resilience** — harden reconnect and multi-tab behavior on
  `/api/ws/events`, and close the remaining os.environ race in concurrent
  sessions (see [bugs.md](bugs.md) #195).
- **Test depth** — grow the L2 functional domains where regressions have
  historically clustered (streaming recovery, workspace trust boundaries,
  approval flow).

Items may be re-prioritized at any time; this list is a statement of intent,
not a commitment.

---

## Non-goals

- Replacing the hermes-agent SDK or re-implementing CLI logic in the web UI.
- Multi-user / multi-tenant auth. The password gate protects a single
  trusted deployment; it is not an account system.
- A public plugin/theme marketplace — the theme system is deliberately limited
  to system / dark / light.
