# Contributing to Leap Aide

**English** | [简体中文](CONTRIBUTING.zh-CN.md)

Thanks for contributing.

Leap Aide is deliberately boring to work on: Python stdlib on the server,
React + TypeScript + Vite on the client, no heavyweight frameworks anywhere.
The best pull requests preserve that simplicity while solving a real problem
cleanly.

## Two Paths to a Strong Pull Request

### Path 1: Small, Focused Changes

This is the fastest path to review and merge.

- Fix one clear bug or add one tightly scoped improvement
- Touch the fewest files you can
- Avoid drive-by refactors mixed into functional changes
- Run the relevant tests locally before opening the PR
- Keep the PR description concise and specific

These are the changes that are easiest to review and safest to merge quickly.

### Path 2: Bigger Changes

If you want to change architecture, reshape a workflow, add a substantial UI
feature, or alter core behavior, align on direction first.

- Open an issue, start a discussion, or open a draft PR early
- Explain the problem you are solving, not just the implementation you want
- Call out tradeoffs, migration risk, and any alternatives you considered
- Keep the final PR easy to review by separating unrelated work

Large changes are welcome, but surprise rewrites are hard to review well.

## What We Expect in Every PR

### 1. One Logical Change Per PR

Keep each PR focused. A small related group of fixes is fine. A bug fix plus
a CSS cleanup plus a refactor plus a docs rewrite is not.

### 2. Local Verification

Run the smoke suite locally before every PR:

```bash
./scripts/run-test.sh smoke
```

For a direct pytest run (the suite lives in `backend/tests/`):

```bash
cd backend && python -m pytest tests/ -v
```

CI runs the suite on Python 3.11, 3.12, and 3.13 plus the frontend vitest
suite — but treat CI as a backstop, not the first line of defense.

If your change affects browser behavior, also run the relevant manual checks
from [docs/testing.md](docs/testing.md).

### 3. Clear PR Description

There is currently no PR template in this repo, so include the important
sections yourself:

- Thinking Path
- What Changed
- Why It Matters
- Verification
- Risks / Follow-ups
- Model Used

If the change is user-visible, include screenshots or a short video.

For UI or UX changes, before/after images are required. PRs that change the
interface or interaction flow without before/after images will likely be
ignored, or closed in a regular maintainer sweep without review.

### 4. AI Usage Disclosure

If AI helped produce the change, say so in the PR description.

Include:

- Provider
- Exact model name or ID
- Any notable mode or tool use that mattered

If no AI was used, write: `None — human-authored`.

### 5. Keep the Docs Honest

If your change alters behavior, architecture, testing, setup, or user-facing
workflows, update the relevant docs in the same PR.

**Every doc exists as an English/Chinese pair** (`docs/foo.md` +
`docs/foo.zh-CN.md`). A content change must update both versions in the same
PR — a PR that edits only one language is incomplete.

Common files:

- [README.md](README.md) / [README.zh-CN.md](README.zh-CN.md) for setup,
  usage, configuration, and contributor-facing commands
- [docs/architecture.md](docs/architecture.md) for implementation details and
  design constraints
- [docs/testing.md](docs/testing.md) for manual and automated verification
- [docs/roadmap.md](docs/roadmap.md) for shipped capabilities and direction
- [docs/bugs.md](docs/bugs.md) for known limitations
- [CHANGELOG.md](CHANGELOG.md) when maintainers want release-note-ready
  entries (English only)

## Project-Specific Guidelines

### Preserve the Design Constraints

Leap Aide is deliberately:

- Python stdlib backend — no Flask/Django/FastAPI; the only external Python
  dependency is `pyyaml`
- WebSocket-first realtime streaming with a small SSE replay surface
- `static/` is **build output** — frontend sources live in `frontend/src`;
  never hand-edit files under `static/`
- i18n limited to English and 简体中文
- Docs as bilingual `*.md` / `*.zh-CN.md` pairs with language switchers
- Easy to modify and run from a terminal (`./scripts/ctl.sh`, `./scripts/build.sh`)

Do not introduce new infrastructure or dependencies unless the gain is clear
and the tradeoff is justified.

### Match the Existing Shape of the Codebase

- Server logic belongs in `backend/app/api/` with `server.py` staying thin
- Frontend behavior belongs in the existing Zustand stores and components
  under `frontend/src/`
- Prefer extending current patterns over introducing parallel abstractions
- Keep changes legible to future contributors working directly from the repo
  in a terminal

### Be Careful With User-Facing Changes

This project is heavily UI-driven. If you change interaction flows, session
behavior, workspace browsing, onboarding, or mobile layouts:

- test the happy path
- test reload behavior where relevant
- test narrow/mobile layouts where relevant
- include before/after images in the PR

### Security and Safety Matter

This app can expose workspace contents, run agent actions, and optionally sit
behind a reverse proxy or Docker deployment. Treat auth, path handling,
uploads, streaming, and environment handling as high-risk areas.

If your PR touches security-sensitive behavior, say so explicitly in the PR
description and explain how you verified it.

## Writing a Good PR Message

Start with a short Thinking Path that explains the chain from project goal to
the specific fix.

Example:

> - Leap Aide aims for near 1:1 parity with the Hermes CLI in a browser
> - Long-running chat turns rely on WebSocket streaming and session recovery
> - Reloading during an in-flight turn can leave the UI in an inconsistent state
> - The bug was that recovered sessions restored messages but not the live stream state
> - This PR fixes the recovery path so in-flight turns reconnect cleanly after reload
> - The benefit is that users can refresh or reconnect without losing visibility into active work

After that, cover:

- what you changed
- why you changed it
- how you verified it
- what risks remain

## Review Tips

Want the smoothest review?

- Keep diffs tight
- Name things clearly
- Avoid unnecessary rewrites
- Add short comments only where the code would otherwise be hard to follow
- Respond directly to review feedback and update the PR description if the
  scope changes

## Development References

- [README.md](README.md) — setup, configuration, commands
- [CLAUDE.md](CLAUDE.md) — working conventions and critical rules
- [docs/architecture.md](docs/architecture.md) — system design and ADRs
- [docs/testing.md](docs/testing.md) — test tiers and manual smoke checklist
- `./scripts/run-test.sh help` — authoritative test runner usage

Questions are best raised early, before a large change is finished.
