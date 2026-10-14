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

---

## Coding Standards

- Backend: pure Python stdlib (no Flask/Django/FastAPI); the only external dependency is `pyyaml`.
- Frontend: TypeScript strict mode. State management uses Zustand stores; styling follows the Google Material 3 frontend specification.
- Never use raw `confirm()`/`prompt()` — use `showConfirmDialog()`/`showPromptDialog()`.
- Path-traversal protection: always use `safe_resolve(root, requested)` for file operations.

---

## Commit Conventions
Do not create git commits proactively unless the user asks.

Creating a code commit goes through the following steps.

Step 1: Read and understand the code changes
Decide which files need to be committed and stage them with `git add`. If you are unsure which files to `git add`, clarify with the user.
After `git add`, run `git diff --staged` and fully understand the main changes across all staged files — what was changed and why.

Step 2: Read the commit convention template
Read the `.github/commit-template.txt` file in the repo root and understand the conventions it defines:

- Type (feat, fix, docs, etc.)
- Subject format (imperative mood, lowercase first letter, at most 72 characters, no trailing period)
- Body is required (must include Why, How, Impact)
- Footer conventions (Closes, BREAKING CHANGE, Co-Authored-By, etc.)
- If .github/commit-template.txt does not exist, print an error and abort.

Step 3: Generate a commit message that follows the conventions
Based on the change diff from Step 1 and the template conventions from Step 2, generate a git commit message that strictly satisfies the requirements.
Requirements:

- Language: English
- Example commit log:
```
feat(auth): add password reset functionality

Currently users cannot reset their password when they forget it,
leading to account lockouts and increased support tickets.

Implement a complete password reset flow:

Add forgot password endpoint that sends reset link via email

Create password reset form with token validation

Add email notification service with rate limiting

Closes #234
Co-Authored-By: Jane Doe jane@example.com
```

Step 4: Run `git commit` with the generated message

---

## Misc
- No feature may modify /usr/local/lib/hermes-agent (the Hermes Agent SDK). The Hermes Agent SDK is an external dependency and must not be modified.
- LeapAide implementations should follow the Hermes Agent SDK CLI implementation wherever possible.
- Code comments must be in English.
