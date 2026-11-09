---
title: LeapAide Release Process
---

<div align="center">

**English** | [中文](release.zh-CN.md)

</div>

# LeapAide Release Process

How a release is cut, published and announced — and why the GitHub Release
page is a mirror of the changelog instead of a second description of the
same release.

---

## One Source of Truth

The changelog *is* the release notes. `CHANGELOG.md` and
`CHANGELOG.zh-CN.md` carry the curated, bilingual description of every
release, and the GitHub Release page is rendered from them — never written
by hand.

GitHub can generate release notes from the merged pull requests. That view is
pull-request-level and English-only: it says what the pull requests were
called, not what the user gets. A release written that way would say nothing
about the shared browser desktop, because that change was a rework of how
the per-session display was set up, and no pull request title carries that.
Two descriptions of one release, maintained by hand, drift; one description,
derived from the other, cannot.

**Never tick "Generate release notes" when creating a release.** The CI job
publishes the release body itself.

---

## Cutting a Release

### 1. Write the changelog entry

Accumulate changes under `## [Unreleased]` in `CHANGELOG.md` and
`CHANGELOG.zh-CN.md`. Both sides change in the same pull request — the
bilingual-pair gate fails the build otherwise.

When the release is cut, rename the section in both files:

```
## [Unreleased]
```

becomes:

```
## [0.2.0] - 2026-12-01
```

Then re-record the consistency digests:

```bash
python3 backend/scripts/check_translation_pairing.py --write CHANGELOG.md
```

### 2. Merge through a pull request

Push the branch and open a pull request against `main`. `main` is reached
through review only — never push to it, and never to `upstream`, directly.

### 3. Tag the release

```bash
git tag -a v0.2.0 -m "LeapAide v0.2.0"
git push origin v0.2.0
```

The tag message becomes the release title, so keep it in the form
"LeapAide v0.2.0". A lightweight tag gets the same title anyway.

The tag push starts the runtime-image workflow. It builds the image for the
tag, pushes it to GHCR as `:<version>` and `:latest`, and — once the image
is in the registry — publishes the GitHub Release.

### 4. The release page appears

The `publish-release` job renders the bilingual body from the changelog
section and creates the release for the tag. It waits for the image job, so
the release page never points at an image that is not published yet.

An existing release is overwritten rather than skipped: the changelog is the
source of truth, so a hand-edited body is drift and has to be corrected.

---

## Previewing the Release Notes

Render the body locally before you tag:

```bash
python3 scripts/release_notes.py --version 0.2.0
```

The body is bilingual with a click-to-switch header: `[English] | [中文]`.
The English section starts open so the page is readable without a click; the
Chinese section starts closed, and the switcher jumps to its disclosure bar.

GitHub strips scripts and styles from release bodies, so a strict tab widget
is not available — the switch is built from two disclosure blocks plus
anchor links, which is as close to a toggle as a static page gets.

---

## What the Release Job Does Not Do

- It does not generate notes from pull requests, and it does not read the
  tag message for the body.
- It does not edit the changelog. A missing version is an error, not a
  reason to invent one.
- It does not move the `:latest` image tag — the image job owns that.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| The release job fails with "no section for version" | The changelog has no section for that version, or only one language side has it | Add the section to both files and re-record the digests |
| The release body is empty or monolingual | The section was extracted from the wrong file | Check that `CHANGELOG.md` and `CHANGELOG.zh-CN.md` both carry the section |
| The release page shows generated notes | Somebody ticked "Generate release notes" in the web UI | Re-run the release job, or replace the body with the rendered one |
| The `:latest` image tag does not move | The image job failed before the push | Re-run "Re-run all jobs" on the tag run |
