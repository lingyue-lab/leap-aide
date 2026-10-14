<div align="center">

**English** | [中文](testing.zh-CN.md)

</div>

# LeapAide Testing Guide

This guide covers the automated test tiers, how to run them, and a lean manual browser smoke checklist. For the contribution workflow see [CONTRIBUTING.md](../CONTRIBUTING.md).

---

## Automated Unit Tests

All unit tests are driven by `./scripts/run-test.sh` from the repo root:

| Command | Tier | Scope | Typical duration |
|---|---|---|---|
| `./scripts/run-test.sh smoke` | L1 | Core regression suite | < 5 min |
| `./scripts/run-test.sh function [DOMAIN]` | L2 | One functional area | 10–30 min |
| `./scripts/run-test.sh full` | L3 | Full regression | hours |
| `./scripts/run-test.sh frontend [SUBCMD]` | — | Frontend vitest (`smoke` / `full` / `clean` / `setup`) | minutes |
| `./scripts/run-test.sh manual` | L4 | Prints the browser manual checklist | — |
| `./scripts/run-test.sh setup` | — | Install all test dependencies (Python + frontend) | — |
| `./scripts/run-test.sh clean` | — | Clean up stale test state and occupied ports | — |

L2 domains (DOMAIN): `session` | `model` | `workspace` | `cron` | `i18n` | `ui`
`streaming` | `auth` | `all`.

Useful environment overrides:

- `DRY_RUN=1` — print the commands without executing them
- `PYTEST_EXTRA="..."` / `VITEST_EXTRA="..."` — append extra arguments to the runner

The output of `./scripts/run-test.sh help` is authoritative; it is the latest usage documentation.

Test result files are written to `backend/tests/.results/` and `frontend/tests/.results/`.

---

## Manual Smoke Tests

After changes that touch the UI, run the following tests manually:

### Session Lifecycle

- **Empty state**: the first load with no sessions shows an empty state.
- **Create / rename / delete**: create a session with the `+` button, rename it, delete it. Deleting the **last (only)** session returns to the empty state.
- **Pin / archive**: pinning moves a session to the top. After pinning, the observe button in the title bar is visible; clicking it opens the VNC desktop to operate the browser and observe its behavior.

### Chat & Streaming

- **Send & stream**: send a message; it appears as a real-time stream over WebSocket (`/api/ws/events`). Tool call cards render inline and expand/collapse.
- **Reload mid-stream**: refresh the page while a response is streaming; the in-flight turn should recover and the conversation is not lost.
- **Approval card**: in Manual mode, trigger a shell command that needs approval; the approval card offers allow once / this session / deny, and the agent continues accordingly.

### Workspace & Panels

- **Workspace browser**: click the top-right of the title to expand the directory tree, preview a text file, create + rename + delete files. When the workspace is a git repo, the git badge shows the branch and dirty-file count.
- **Terminal panel**: open the terminal panel and run a simple command (`echo hi`).
- **Theme & language**: switch the theme (system / dark / light) and language (English / 简体中文) in settings; both should persist across reload.

### Settings

- **Settings persistence**: change a setting, reload, confirm it is retained.

---

## Adding Tests

- Backend behavior (routes, models, streaming, workspace): add pytest cases in `backend/tests/` next to the closest existing module.
- Frontend store / component behavior: add vitest cases in `frontend/tests/`.
- UI flows at the perceived level: add the relevant entry to the smoke checklist above.
