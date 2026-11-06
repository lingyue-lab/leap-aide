---
title: LeapAide Testing Guide
---

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
| `./scripts/run-test.sh backend [SUBCMD]` | — | Backend pytest (`smoke` / `function [DOMAIN]` / `full` / `sprint N` / `clean` / `setup`) | — |
| `./scripts/run-test.sh frontend [SUBCMD]` | — | Frontend vitest (`smoke` / `full` / `clean` / `setup`) | minutes |
| `./scripts/run-test.sh manual` | L4 | Prints the browser manual checklist | — |
| `./scripts/run-test.sh setup` | — | Install all test dependencies (Python + frontend) | — |
| `./scripts/run-test.sh clean` | — | Clean up stale test state and occupied ports | — |

L2 domains (DOMAIN): `session` | `model` | `workspace` | `cron` | `i18n` | `ui`
`streaming` | `auth` | `all`.

CI runs the same suites inside the testing image built from the repo `Dockerfile`
(`.github/workflows/run-tests-in-testing-image.yml`): `backend full`, then
`frontend full`.

Every push to a ready PR targeting `main` runs them automatically, and so does
every push to `main` itself. Draft PRs are skipped, and a PR with a merge
conflict gets no run at all. This check is not yet required to merge — it
reports, it does not block.

Useful environment overrides:

- `DRY_RUN=1` — print the commands without executing them
- `PYTEST_EXTRA="..."` / `VITEST_EXTRA="..."` — append extra arguments to the runner

The output of `./scripts/run-test.sh help` is authoritative; it is the latest usage documentation.

Test result files are written to `backend/tests/.results/` and `frontend/tests/.results/`.

---

## Bilingual Pair Consistency Gate

Every bilingual document is a triple of files:

| File | Role |
|---|---|
| `docs/foo.md` | English document (the *anchor* the checker is pointed at) |
| `docs/foo.zh-CN.md` | Chinese companion |
| `docs/foo.i18n.yaml` | Consistency record: one `sha256-16` digest per section, for both languages |

`backend/scripts/check_translation_pairing.py` enforces the pair. Run it before pushing:

```bash
# Verify one pair
python3 backend/scripts/check_translation_pairing.py docs/architecture.md
# Verify every pair that has a record
python3 backend/scripts/check_translation_pairing.py --check-all
# Check that a directory has no document missing its companion or record
python3 backend/scripts/check_translation_pairing.py --check-tree docs
python3 backend/scripts/check_translation_pairing.py --check-tree . --depth 1
# Re-record the digests after aligning BOTH language files
python3 backend/scripts/check_translation_pairing.py --write docs/architecture.md
```

What the gate enforces:

- Both files open with a YAML frontmatter block at byte 0.
- Both files carry a language switcher in their first 20 lines.
- Both files have the same heading structure (same levels, in the same order).
- Fenced code blocks are byte-identical across the pair — code blocks carry
  facts (ports, paths, schemas) and must never drift. Comments inside a fence
  stay in English in both files, so that the byte comparison holds.
- Every section's prose digest matches the record.

Adding a new document:

1. Create `docs/foo.md` and `docs/foo.zh-CN.md`, each starting with the frontmatter
   block and the language switcher.
2. Mirror the heading structure exactly — same levels, same order.
3. Keep fenced code blocks byte-identical across the pair — including the
   comments inside them, which stay in English on both sides.
4. Run `--write docs/foo.md` to create the record, and commit all three files.

A deliberate one-sided edit (a typo fix with no Chinese counterpart) is declared
with a `waiver:` stanza in the pair's record:

```yaml
waiver:
  side: en
  reason: "English typo fix only, no semantic change"
  expires: "2026-12-31"
```

The waiver is honoured by the co-change gate only — the digests still have to
match, so `--write` is still required. It shows up in the pull-request diff, and
it expires, so it cannot silently weaken the gate.

CI runs `--check-all`, `--check-tree docs`, `--check-tree . --depth 1`, and the
git co-change gate on every pull request that touches a bilingual pair. The gate
reports rather than blocks the merge — `main` is not branch-protected yet.

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
- **Markdown table preview**: open a `.md` file containing the GFM table from issue #41 (a long inline-code path in the first column). Headers and cells share column widths, headers are left-aligned, borders and the header tint are visible, long paths wrap inside their cell, and the table stays within the 768px content column. Check both dark and light themes.
- **Markdown prose baseline**: open a Markdown file (workspace preview) and a skill's `SKILL.md` (skill detail) containing headings, paragraphs, lists, a blockquote, inline code, a fenced code block, a link, and a horizontal rule. Both surfaces should share the same spacing, code-block border/background, and link/quote colors; check dark and light themes.
- **Terminal panel**: open the terminal panel and run a simple command (`echo hi`).
- **Theme & language**: switch the theme (system / dark / light) and language (English / 简体中文) in settings; both should persist across reload.

### Settings

- **Settings persistence**: change a setting, reload, confirm it is retained.
- **Default model**: Settings → Agent Behavior → pick a default model and Save; confirm the active profile's `config.yaml` (`model.default` / `model.provider`) was updated and reloading keeps the selection. Start a new chat (welcome first message and the `+` button) — the footer and the session record use that model.
- **Same model, two providers**: with both DeepSeek and OpenRouter keys configured and DeepSeek active, pick the **OpenRouter** group's DeepSeek entry as default and Save. `config.yaml` must record `provider: openrouter` + `model.default: deepseek/...`; picking the DeepSeek group entry must record `provider: deepseek` + the bare id. A new chat using either choice must call that provider's endpoint with its own key.
- **Removed default model**: point `model.default` at an id the provider no longer offers, clear the models cache and restart. Settings and the composer footer mark it unavailable, sending still uses the configured value, and `config.yaml` is not rewritten automatically.
- **Deleted provider key**: set the default model to one served by a provider, then delete that provider's key in Settings → Model Providers. The provider's models must disappear from the composer picker immediately (no baseline fallback), Settings must warn right away with the key-specific message, the composer must keep showing the configured default marked unavailable plus a "re-add the key" line, and sending must show the short "Default model is not valid. Please configure it in Settings." message instead of the SDK's `config.yaml` / environment-variable text. Picking another model in the composer footer must dismiss that warning and show the picked model.
- **Deleted provider key in an existing session**: run a chat with a model, delete that provider's key, then reopen the session. The composer must keep the session's own model marked unavailable and say "Current model unavailable: …" (not "Default model unavailable: …"), and sending must show "Current model is not valid. …" — the session runs the model the user picked for it, whatever the configured default happens to be. A brand-new chat keeps the "Default model …" wording.

---

## Adding Tests

- Backend behavior (routes, models, streaming, workspace): add pytest cases in `backend/tests/` next to the closest existing module.
- Frontend store / component behavior: add vitest cases in `frontend/tests/`.
- UI flows at the perceived level: add the relevant entry to the smoke checklist above.
