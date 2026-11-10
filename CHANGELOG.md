---
title: Changelog
---

<div align="center">

**English** | [中文](CHANGELOG.zh-CN.md)

</div>

# Changelog

All notable changes to LeapAide are recorded in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- The release notes are no longer hard-wrapped: the renderer joins the changelog prose into one line per paragraph and per list item, so the notes fill the width of the release page instead of stopping at the changelog's column.
- Markdown in the repository is no longer hard-wrapped either: every document is now written one line per paragraph and per list item.
- The language label on the release page is now a heading: a disclosure summary renders in the body font size, which made "English" and "中文" read as captions above the notes instead of the section titles they are.

## [0.1.0] - 2026-11-09

First public release. A chat front end with a shared, observable browser desktop, a workspace file browser, scheduled tasks, and one-click access to the mainstream LLM providers.

### Added

- **Streaming chat over WebSocket** — tool calls and reasoning traces render inline as they arrive; the composer carries model, reasoning effort, personality, skills and workspace pickers.
- **Shared, observable browser desktop** — one always-on Chrome desktop backs every session, and the observer window shows it live so you can watch the agent work and log in to a site by hand (SMS code, mobile app) instead of burning tokens on login attempts. Concurrent sessions stay isolated: each one owns its own tabs, and the receptionist refuses to let a session touch another one's.
- **Unified model identities** — every model is identified as `@provider:model`, so the same model reached through two providers is never confused for one another.
- **Provider catalog with live refresh** — the model picker shows the providers you actually configured, validates a key when you save it, and reports a rejected key instead of failing at chat time.
- **Configurable default model** — pick the default model in Settings; it is written to the profile's `config.yaml` and used for new sessions.
- **Scheduled tasks** — run a session on a schedule, with its own model, workspace, skills and tool set.
- **GitHub CLI in the image** — `gh` and the `gh token` extension ship in the runtime image, so the agent can drive `gh pr`, `gh issue` and `gh api` directly.
- **Pinyin input in the desktop** — the fcitx5 input profile is seeded with `keyboard-us` and `pinyin`, so `Shift+Space` switches to Chinese.
- **Bilingual release notes from the changelog** — the GitHub Release page is rendered from the `## [x.y.z]` section of `CHANGELOG.md` and `CHANGELOG.zh-CN.md` by a CI job, so the release page and the changelog cannot drift apart. The body carries a click-to-switch language header.

### Changed

- The container user is now aligned with the host user, so files written in the container belong to you.
- The provider key gate shows on first boot instead of only after a trip to Settings, and the dead onboarding API is gone.
- Markdown rendering: tables align their columns, and a fenced block nested inside another fence no longer splits the reply into two code boxes.
- Bilingual pairs under `docs/` and the repository root are now checked for structural and prose-level drift in CI.

### Fixed

- The first chat after a cold start no longer waits tens of seconds; the model catalog, MCP discovery and tool definitions are warmed up during boot instead of on the request path.
- The model picker no longer freezes the page while the catalog loads, and switching panels stays responsive.
- The first page load during container startup no longer fails; the server answers as soon as the socket is listening.
- Workspace file create/save/delete no longer fails on Docker bind mounts with mismatched ownership, and save feedback is now consistent across every editor.
- The add-workspace path field now has a working autocomplete.
- The welcome-state composer applies the workspace you pick, and `HERMES_WEBUI_DEFAULT_WORKSPACE` is honored.

### Docker

Pull the released image and start the service:

```bash
podman pull ghcr.io/lingyue-lab/leap-aide-runtime:0.1.0
bash scripts/ctl.sh start-docker ghcr.io/lingyue-lab/leap-aide-runtime:0.1.0
```

Only `linux/amd64` is built — arm64 hosts need emulation. The `:latest` tag points at this release too, and `docker-compose.yml` defaults to the pinned `0.1.0` image.
