---
title: LeapAide Developer Guide
---

<div align="center">

**English** | [中文](AGENTS.zh-CN.md)

</div>

# Developer Guide

---

## Project Overview

The project is currently built on the [Hermes Agent](https://hermes-agent.nousresearch.com/) framework and provides a self-hosted browser interface.

The backend is Python standard library (no web framework); the frontend is React 18 + TypeScript + Vite, following the Material 3 frontend design specification.

---

## Build & Development

Build, development, and debugging may only run in a newly created debug container, to avoid polluting the local environment (the host or host-container environment you are in). Create the debug container with podman (install it first if missing).

Follow the steps below to start a fresh debug container for building, developing, and debugging — all build/development commands run only inside this new container.

- Create a temporary file `docker-compose.debug.yml` with the following content; it maps the workspace to the `/app` directory inside the container (LeapAide's startup directory), and the workspace — as the target working directory — contains all project changes.

```yml
services:
  leap-aide:
    entrypoint: ["bash", "/app/backend/scripts/docker-entrypoint.sh"]
    volumes:
      - <workspace>:/app
```

- From the repo root, run `bash scripts/ctl.sh start-docker -f /path/to/docker-compose.debug.yml <image-name>` to start the container; LeapAide comes up automatically once the container is running.

Available images:

- `ghcr.io/lingyue-lab/leap-aide-testing`: for regression testing; a large image with the full toolchain (node, npm, pytest, etc.); with `/app` replaced it can be used for frontend and backend builds and debugging.
- `ghcr.io/lingyue-lab/leap-aide-runtime`: the final product image; a small image with an incomplete toolchain; with `/app` replaced it can be used to verify the final product experience.

Enter the new container (`podman exec -it <container-name> bash`) and run the build/development commands:

```bash
# Start the server
bash scripts/ctl.sh restart

# Build the service
bash scripts/build.sh
```

**Warning: `ctl.sh stop` / `ctl.sh restart` may only run inside a freshly started debug container; they are forbidden on the host, and also forbidden inside the container that hosts live agent sessions (the host-container).**

- Frontend-only changes need no restart of the debug container: from the repo root on the host or host-container, run `podman run -v "$(pwd):/app" -u root --rm --entrypoint /bin/bash ghcr.io/lingyue-lab/leap-aide-testing:sha-c762d0f -c "bash scripts/build.sh --frontend"` to produce a new `static/` (if you are already inside the debug container, just run `bash scripts/build.sh --frontend`); `static/` is read from disk on every request, so a hard browser refresh is enough.
- If a backend change requires restarting the debug container, run `podman restart <container-name>` from the host or host-container (or delete the old debug container and recreate it).

---

## Architecture

See [docs/architecture.md](docs/architecture.md).

---

## Docs

| Document | English | 中文 |
|---|---|---|
| Architecture + ADRs | [docs/architecture.md](docs/architecture.md) | [docs/architecture.zh-CN.md](docs/architecture.zh-CN.md) |
| Testing guide | [docs/testing.md](docs/testing.md) | [docs/testing.zh-CN.md](docs/testing.zh-CN.md) |
| Roadmap | [docs/roadmap.md](docs/roadmap.md) | [docs/roadmap.zh-CN.md](docs/roadmap.zh-CN.md) |
| Release process | [docs/release.md](docs/release.md) | [docs/release.zh-CN.md](docs/release.zh-CN.md) |

---

## Coding Standards

- Backend: pure Python stdlib (no Flask/Django/FastAPI); dependencies are `pyyaml` plus one approved exception: `aiohttp` (browser-desktop receptionist, issue #37).
- Frontend: TypeScript strict mode. State management uses Zustand stores; styling follows the Google Material 3 frontend specification.
- Never use raw `confirm()`/`prompt()` — use `showConfirmDialog()`/`showPromptDialog()`.
- Path-traversal protection: always use `safe_resolve(root, requested)` for file operations.
- Bilingual pairs are enforced by `backend/scripts/check_translation_pairing.py` — run `--check-all`, `--check-tree docs` and `--check-tree . --depth 1` before pushing; every new doc needs a `.zh-CN.md` companion and an `.i18n.yaml` record.
- Markdown is written unwrapped: one line per paragraph, one line per list item, one line per table row. Prose is read rendered, so the source must not break lines at a fixed column.

---

## Commit Conventions
- Do not create git commits proactively unless the user asks.
- Every change goes through a pull request — never push directly to `main`, and never to `upstream`. Push the branch to `origin` (your fork) and open a PR against `main` on `upstream`. There are no exceptions.
- Pull requests are merged with squash and merge: the squashed commit message must reflect every commit in the pull request. The merge dialog's `Commit title` holds the subject, and its `Commit message` field must not repeat it.
- A pull request does not have to be linked to an issue.
- The commit log must follow `.github/commit-template.txt`.
- The commit log must be written in English.
- Creating a commit goes through these steps:
  1. Read and understand the code changes — stage them with `git add` (ask the user which files belong when unsure), then run `git diff --staged` and understand what changed across all staged files and why.
  2. Read `.github/commit-template.txt` in the repo root and follow the conventions it defines: type (feat, fix, docs, etc.); subject format (imperative mood, lowercase first letter, at most 72 characters, no trailing period); body (required — must include Why, How, Impact); footer conventions (Closes, BREAKING CHANGE, Co-Authored-By, etc.).
  3. Run `git commit` with the generated message.

---

## Misc
- No feature may modify /usr/local/lib/hermes-agent (the Hermes Agent SDK). The Hermes Agent SDK is an external dependency and must not be modified.
- LeapAide implementations should follow the Hermes Agent SDK CLI implementation wherever possible.
- Code comments must be in English.
