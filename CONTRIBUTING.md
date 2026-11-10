---
title: Contributing to LeapAide
---

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

### Every change goes through a pull request

Push a branch and open a pull request — never commit straight to `main`, and never push to `upstream` directly. There are no exceptions, including for maintainers.

### Logically related fixes go in the same PR

Keep each PR focused. A small cluster of related fixes is fine. A bug fix plus a CSS cleanup plus a refactor plus a docs rewrite is not.

### Local verification

Complete all tests locally before every PR; see [docs/testing.md](docs/testing.md) for how.

CI re-runs the same suites automatically on every push to a ready PR targeting `main`, and again on every push to `main` itself. That check reports the result but is not yet required to merge.

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

This is enforced mechanically, not just by convention. Each bilingual pair carries a consistency record (`docs/foo.i18n.yaml`) holding one `sha256-16` digest per section for both languages, and `backend/scripts/check_translation_pairing.py` checks it. CI runs that check on every pull request that touches a bilingual pair: it reports a failure when a pull request changes one language without the other, or adds a document without its Chinese companion or its record. `main` is not branch-protected yet, so the check reports rather than blocks the merge.

Adding a document:

1. Create `docs/foo.md` and `docs/foo.zh-CN.md`, each starting with a YAML frontmatter block and the language switcher.
2. Mirror the heading structure exactly — same levels, same order.
3. Keep fenced code blocks byte-identical across the pair — including the comments inside them, which stay in English on both sides.
4. Run `python3 backend/scripts/check_translation_pairing.py --write docs/foo.md` to create the record, and commit all three files.

A deliberate one-sided edit — a typo fix with no Chinese counterpart — is declared with a `waiver:` stanza in the pair's record (`side`, `reason`, `expires`). The waiver is honoured by the co-change gate only, it shows up in the pull-request diff, and it expires on its own so it cannot silently weaken the gate.

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

### Write markdown unwrapped

Markdown in this repository is written one line per paragraph, one line per list item, one line per table row. Do not hard-wrap prose at a fixed column.

Markdown is read rendered — on the repository page, in a pull request, in the release notes — and every reader wraps it to their own width. A hard-wrapped source breaks the text at the author's column instead of the reader's, and it splits a single edited sentence across two diff hunks. Code blocks, tables and the language switcher are unaffected; only prose is unwrapped.
