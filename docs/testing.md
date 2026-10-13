# Leap Aide Testing Guide

**English** | [简体中文](testing.zh-CN.md)

This guide covers the automated test tiers, how to run them, and a lean manual
smoke checklist for browser-visible behavior. For contribution workflow see
[CONTRIBUTING.md](../CONTRIBUTING.md).

---

## Test tiers

All automated tests are driven by `./scripts/run-test.sh` from the repo root:

| Command | Tier | Scope | Typical duration |
|---|---|---|---|
| `./scripts/run-test.sh smoke` | L1 | Core regression suite | < 5 min |
| `./scripts/run-test.sh function [DOMAIN]` | L2 | One functional area | 10–30 min |
| `./scripts/run-test.sh full` | L3 | Full regression | hours |
| `./scripts/run-test.sh frontend [SUBCMD]` | — | Frontend vitest (`smoke` / `full` / `clean` / `setup`) | minutes |
| `./scripts/run-test.sh manual` | L4 | Prints the browser manual checklist | — |
| `./scripts/run-test.sh setup` | — | Install all test dependencies (Python + frontend) | — |
| `./scripts/run-test.sh clean` | — | Wipe stale test state and ports | — |

L2 domains: `session` | `model` | `workspace` | `cron` | `i18n` | `ui` |
`streaming` | `auth` | `all`.

Useful environment overrides:

- `DRY_RUN=1` — print the commands without executing them
- `PYTEST_EXTRA="..."` / `VITEST_EXTRA="..."` — append extra flags to the runner

Run `./scripts/run-test.sh help` for the authoritative, up-to-date usage text.

---

## Running pytest directly

The backend suite lives in `backend/tests/` (with `backend/pytest.ini` at
`backend/`):

```bash
cd backend
python -m pytest tests/ -v
python -m pytest tests/ -k <name>      # single test or module
```

Do not run `pytest tests/` from the repo root — there is no root-level `tests/`
directory.

Results are written to `backend/tests/.results/` and `frontend/tests/.results/`.

### CI

`.github/workflows/tests.yml` runs the backend suite on Python 3.11 / 3.12 /
3.13, the frontend vitest suite, and a containerized job (buildah `cicd` image
+ pytest inside the container). Agent-dependent tests auto-skip when the
hermes-agent SDK is unavailable. Keep the local suite green before pushing;
CI is a backstop, not the first line of defense.

---

## Test isolation

- Tests spin up an isolated server on port **8788** with its own temporary
  state directory (per-session temp dirs created by `backend/tests/conftest.py`).
- Production state (`~/.hermes/webui`) and real cron jobs are never touched.
- If a test run is interrupted and leaves a stale process behind,
  `./scripts/run-test.sh clean` restores a clean slate.

---

## Manual smoke checklist (L4)

Run this after UI-facing changes. Each item is one or two sentences; the goal
is a fast pass over the surfaces that automated tests cover least.

### Session lifecycle

1. **Empty state** — first load with no sessions shows an empty state, and the
   Boot flow does not auto-create a session.
2. **Create / rename / delete** — create a session via the `+` button, rename
   it, delete it. Deleting the *last* (only) session returns to the empty
   state — never to a half-created session.
3. **Pin / archive** — pin moves a session to the top; archive hides it and the
   toggle brings it back. After pinning, the observe/pin control in the header
   reflects the new state immediately (no stale UI after switching sessions).

### Chat and streaming

4. **Send and stream** — send a message; tokens stream in live over WebSocket
   (`/api/ws/events`). Tool call cards render inline and expand/collapse.
5. **Reload mid-run** — refresh the page while a response is streaming; the
   in-flight turn recovers without losing the conversation.
6. **Approval card** — trigger a gated shell command; the approval card offers
   allow once / session / always / deny and the agent resumes accordingly.

### Workspace and panels

7. **Workspace browser** — expand folders, preview a text file and an image,
   create + rename + delete a file. Git badge shows branch/dirty count when
   the workspace is a git repo.
8. **Terminal panel** — open the terminal panel, run a simple command
   (`echo hi`), resize the panel.
9. **Theme and language** — switch theme (system / dark / light) and language
   (English / 简体中文) in settings; both persist across reload.

### Settings and device

10. **Settings persistence** — change a setting, reload, confirm it stuck.
11. **Mobile width** — at a narrow viewport (< 640px) the sidebar collapses
    behind a drawer; chat and composer remain usable.

---

## When to extend which layer

- Backend behavior (routes, models, streaming, workspace) → add pytest cases
  in `backend/tests/` alongside the closest existing module.
- Frontend store/component behavior → add vitest cases in `frontend/tests/`.
- Anything about perceived UI flow → add one line to the smoke checklist above
  rather than a brittle scripted test.
