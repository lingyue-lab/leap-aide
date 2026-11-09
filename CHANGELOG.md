---
title: Changelog
---

<div align="center">

**English** | [中文](CHANGELOG.zh-CN.md)

</div>

# Changelog

All notable changes to LeapAide are recorded in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-11-09

First public release. LeapAide is a self-hosted web interface for the Hermes
Agent framework: a chat front end with an isolated browser desktop per
session, a workspace file browser, scheduled tasks, and one-click access to
the mainstream LLM providers.

### Added

- **Streaming chat over WebSocket** — tool calls and reasoning traces render
  inline as they arrive; the composer carries model, reasoning effort,
  personality, skills and workspace pickers.
- **Isolated browser desktop per session** — pinning a session gives it a
  dedicated Chrome instance and an observer window, so you can log in to a
  site by hand (SMS code, mobile app) and watch the agent work; concurrent
  sessions stay isolated through per-connection tab ownership.
- **Unified model identities** — every model is identified as
  `@provider:model`, so the same model reached through two providers is never
  confused for one another.
- **Provider catalog with live refresh** — the model picker shows the
  providers you actually configured, validates a key when you save it, and
  reports a rejected key instead of failing at chat time.
- **Configurable default model** — pick the default model in Settings; it is
  written to the profile's `config.yaml` and used for new sessions.
- **Scheduled tasks** — run a session on a schedule, with its own model,
  workspace, skills and tool set.
- **GitHub CLI in the image** — `gh` and the `gh token` extension ship in the
  runtime image, so the agent can drive `gh pr`, `gh issue` and `gh api`
  directly.
- **Pinyin input in the desktop** — the fcitx5 input profile is seeded with
  `keyboard-us` and `pinyin`, so `Shift+Space` switches to Chinese.

### Changed

- The container user is now aligned with the host user, so files written in
  the container belong to you.
- The provider key gate shows on first boot instead of only after a trip to
  Settings, and the dead onboarding API is gone.
- Markdown rendering: tables align their columns, and a fenced block nested
  inside another fence no longer splits the reply into two code boxes.
- Bilingual pairs under `docs/` and the repository root are now checked for
  structural and prose-level drift in CI.

### Fixed

- The first chat after a cold start no longer waits tens of seconds; the
  model catalog, MCP discovery and tool definitions are warmed up during
  boot instead of on the request path.
- The model picker no longer freezes the page while the catalog loads, and
  switching panels stays responsive.
- The first page load during container startup no longer fails; the server
  answers as soon as the socket is listening.
- Workspace file create/save/delete no longer fails on Docker bind mounts
  with mismatched ownership, and save feedback is now consistent across
  every editor.
- The add-workspace path field now has a working autocomplete.
- The welcome-state composer applies the workspace you pick, and
  `HERMES_WEBUI_DEFAULT_WORKSPACE` is honored.

### Docker

Pull the released image and start the service:

```bash
podman pull ghcr.io/lingyue-lab/leap-aide-runtime:0.1.0
bash scripts/ctl.sh start-docker ghcr.io/lingyue-lab/leap-aide-runtime:0.1.0
```

Only `linux/amd64` is built — arm64 hosts need emulation. The `:latest` tag
points at this release too, and `docker-compose.yml` defaults to the pinned
`0.1.0` image.
