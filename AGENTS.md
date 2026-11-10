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

## Build & Development Commands

```bash
# Start the server
bash scripts/ctl.sh restart

# Build the service
bash scripts/build.sh
```

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
