<div align="center">

**English** | [中文](CONTRIBUTING.zh-CN.md)

</div>

# Contributing to LeapAide

Thank you for your contribution.

LeapAide's tech stack is deliberately "lean": Python standard library on the server, React + TypeScript + Vite on the client, no heavyweight frameworks anywhere. Please keep that leanness while cleanly solving real problems.

---

## Two Paths to a Strong Pull Request

### Path 1: Small, Focused Changes

This is the fastest path to review and merge.

- Fix one clear bug, or add one tightly scoped improvement
- Touch as few files as possible
- Avoid mixing drive-by refactors into functional changes
- Run the relevant tests locally before opening the PR
- Keep the PR description concise and specific

These changes are the easiest to review and the best fit for a quick merge.

### Path 2: Bigger Changes

If you want to adjust the architecture, reshape workflows, add heavyweight UI features, or change core behavior, align on direction first.

- Open an issue early, start a discussion, or open a draft PR
- Explain the problem you are solving, not just the implementation you want
- Call out trade-offs, migration risk, and the alternatives you considered
- Split unrelated work so the final PR is easy to review

---

## What We Expect in Every PR

### Logically related fixes go in the same PR

Keep each PR focused. A small cluster of related fixes is fine. A bug fix plus a CSS cleanup plus a refactor plus a docs rewrite is not.

### Local verification

Complete all tests locally before every PR; see [docs/testing.md](docs/testing.md) for how.

CI re-runs the same suites automatically on every push to a ready PR targeting `main`.
That check reports the result but is not yet required to merge.

### Clear PR description

For PR message content, refer to [.github/commit-template.txt](.github/commit-template.txt).

In addition to meeting the requirements of commit-template.txt, feel free to provide more precise information in the git commit log, including but not limited to:

- Thinking Path
- Why It Matters
- Verification
- Risks / Follow-ups

The git commit log must be in English.

Besides the git commit log, if the change is user-visible, attaching a screenshot or a short video on the GitHub PR page is strongly recommended.

### Keep docs consistent

If your change alters behavior, architecture, testing, installation, or user-facing workflows, update the relevant docs in the same PR.

**Every doc exists as a bilingual pair** (`docs/foo.md` + `docs/foo.zh-CN.md`). A content change must update both language versions in the same PR — a PR that edits only one language is incomplete.

Common files:

- [README.md](README.md) — setup, usage, configuration, and contributor-facing commands
- [docs/architecture.md](docs/architecture.md) — implementation details and design constraints
- [docs/testing.md](docs/testing.md) — manual and automated verification

---

## Project-Specific Guidelines

### Preserve the design constraints

LeapAide is deliberately:

- Python stdlib backend: no Flask/Django/FastAPI, and external dependencies are introduced with restraint.
- All docs exist as bilingual `*.md` / `*.zh-CN.md` pairs with language switchers.
- Do not introduce new infrastructure or dependencies unless the gain is clear and the trade-off justified.

### Match the existing shape of the codebase

- Server logic belongs in `backend/app/api/`; keep `server.py` thin.
- Frontend behavior belongs in the existing Zustand stores and components under `frontend/src/`.
- Prefer extending existing patterns over introducing parallel abstractions.
