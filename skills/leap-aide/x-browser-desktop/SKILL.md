---
name: x-browser-desktop
description: All browser/web tasks must use the shared receptionist.
version: 2.0.0
author: LeapAide
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [browser, cdp, observer, shared-desktop, leap-aide]
---

<div align="center">

**English** | [中文](SKILL.zh-CN.md)

</div>

# x-browser-desktop Skill

The instance runs ONE shared browser desktop (Xvfb + x11vnc + one Chrome with a shared, logged-in profile) — a "public living room" for every session. LeapAide owns it: it starts at server boot and is supervised with automatic restarts.

Chrome has NO network debugging port. It is spawned over a parent-child control pipe, and the LeapAide backend exposes the only CDP surface through an in-process **receptionist**. Tabs created over a connection belong to that connection alone — visible and operable only to it — and they close when the connection drops; nothing is pre-created at connect time, your tools open their own tab when they start working. That isolation is enforced by the backend — not by hiding anything.

Your job is to complete all browser tasks by driving the shared Chrome through the receptionist, so the user can watch the process live in the browser-desktop panel. Never launch a Chrome instance yourself.

Every browser-related task MUST go through this skill — visiting URLs, opening pages, clicking, filling forms, logging in, reading what a rendered page shows. Never substitute your own automation code (Python with Selenium/Playwright/pyautogui/Xlib, shell scripts, raw CDP clients aimed anywhere but the receptionist, ...): it bypasses the user-visible Chrome and is forbidden.

## When to Use

Use this skill for ANY browser-related task, whenever one appears — including as a sub-step of a larger task: visiting an http/https URL, opening a web page, clicking page elements, filling forms, logging into a site, checking an account/balance page, or reading what a rendered page shows. These MUST be completed by driving the shared Chrome via this skill; writing your own automation code instead is forbidden.

Don't use for:

- Tasks that do not need a browser.

## Prerequisites

**How to tell whether the shared browser desktop is up (verify every time, never assume):**

1. Check whether `$HERMES_WEBUI_STATE_DIR/browser_desktop.json` exists and its `status` field is `"active"` (`HERMES_WEBUI_STATE_DIR` defaults to `~/.hermes/webui`). It is written/refreshed by the backend's desktop supervisor — the single source of truth.

Verdict:

- File exists, `status == "active"` → the desktop is up; use this skill's receptionist path.
- File absent or `status != "active"` → the desktop is down; follow the Desktop-Down section below.

Healthy desktop state:

```
$ cat $HERMES_WEBUI_STATE_DIR/browser_desktop.json
{"display": 99, "vnc_port": 5900, "receptionist_port": 6900, "receptionist_url": "http://127.0.0.1:6900", "status": "active", ...}
```

## How to Run

- Prefer the Hermes Agent SDK's `browser_exec` tool (Browser Use CLI mode — this instance's default browser surface). It resolves the receptionist endpoint automatically and attaches — no new Chrome needed — and its `session` argument defaults to your current session id, so every call lands in your session's own private tab.
- The `agent-browser` CLI is taken over by a LeapAide-installed wrapper (at `/usr/local/bin/agent-browser`): when the desktop is up, any `--cdp` or `--session` you pass is stripped and `--cdp` is replaced with the receptionist discovery URL (`http://127.0.0.1:6900/json/version?sid=<session_id>`). When the desktop is down, the wrapper passes your arguments through unchanged (local headless fallback). **Note: this instance has no node/npx installed, so invoking `agent-browser` fails — use `browser_exec` for all browser work.**
- To script against CDP yourself, connect ONLY to the receptionist (`http://127.0.0.1:6900`): fetch `/json/version?sid=<session_id>` — the `sid` query is **required** (without it the endpoint answers 400; your session id is the last segment of `HERMES_SESSION_KEY`, e.g. `SID="${HERMES_SESSION_KEY##*:}"`) — then use the returned `webSocketDebuggerUrl` as-is and drive CDP over that WebSocket. Your `sessionId`s and tabs are scoped to your connection automatically. Chrome itself has no address to dial — anything else will fail.

In short: the wrappers and LeapAide's SDK patches guarantee that every browser call attaches to the one shared Chrome through the receptionist. You only need to issue the calls; the connection target is not yours to control — and does not need to be.

## Quick Reference

| Resource | Value |
|---|---|
| Display | `:99` (the single shared desktop; `export DISPLAY=:99` binds a process to it — gated by the backend) |
| VNC port | `5900` (viewing is the webui browser-desktop panel's job — never dial it from a terminal) |
| Receptionist | `http://127.0.0.1:6900` (env `HERMES_RECEPTIONIST_PORT`; the ONLY CDP surface) |
| Desktop state | `$HERMES_WEBUI_STATE_DIR/browser_desktop.json` |
| Chrome profile | `$HERMES_WEBUI_STATE_DIR/chrome_profiles/global` (shared, logged-in) |
| Chrome window | 960×1080 at position (0,0) — left half of a 1920×1080 desktop; right half shows tool activity |
| Session key | `HERMES_SESSION_KEY=agent:main:webui:dm:<session_id>` |

Example desktop state:

```json
{
  "display": 99,
  "vnc_port": 5900,
  "receptionist_port": 6900,
  "receptionist_url": "http://127.0.0.1:6900",
  "status": "active",
  "started_at": 1730000000.0,
  "last_activity": 1730000000.0
}
```

## Procedure

### Desktop Up

1. Run the Prerequisites verdict first. If it says the desktop is down, go to the Desktop-Down section — never assume it is up.
2. Drive the **existing** shared Chrome through `browser_exec`, following these rules:
   - Reuse the shared profile and its logged-in state. The user may have operated Chrome manually through the browser-desktop panel; do not close tabs you do not own.
   - Tabs your tools create belong to your connection only. Other sessions' tabs are invisible to you and untouchable — by design. Do not try to reach around the receptionist.
   - Never launch a second Chrome — only LeapAide may start Chrome (it holds the control pipe).
   - If a CDP call fails with a connection error, Chrome may have crashed; LeapAide restarts it automatically. Wait briefly and retry once, then report the failure.
   - When your connection drops (turn ends, tool exits), your tabs close automatically — plan multi-step work inside one turn, or keep a long-lived client running for the duration of the task.

### Desktop Down

The desktop is down when the state file is absent or not `active` (server boot race, `HERMES_GLOBAL_DESKTOP=0`, or a hard failure).

- `browser_exec` falls back to its own local browser automatically — web tasks still complete, just without a user-visible window.
- Do NOT try to start the desktop yourself, bind displays, or dial VNC/CDP endpoints. The backend supervises the desktop; if it cannot recover, tell the user the shared desktop is down.

## Pitfalls

- **Writing your own automation code**: Selenium/Playwright/pyautogui scripts bypass the shared visible Chrome; browser work goes through this skill's receptionist path.
- **Launching Chrome yourself**: Chrome's control channel belongs to the receptionist; a Chrome you launch has no shareable state and `--remote-debugging-*` flags are blocked by the backend. Always attach, never launch.
- **Passing `--cdp` to agent-browser**: stripped and replaced with the receptionist endpoint when the desktop is up — not in your control; when it is down, the terminal strategy may still gate the command.
- **Passing `--session` to agent-browser**: conflicts with the shared desktop and is stripped by the wrapper — not in your control.
- **Expecting other sessions' tabs**: isolation is the point. Only your connection's tabs exist as far as you can see.
- **Closing shared tabs or covering the desktop view**: the desktop is the user's window into everyone's tasks; keep it usable.
