---
title: GitHub skill map
---

<div align="center">

**English** | [中文](github-skill-map.zh-CN.md)

</div>

# GitHub skill map

The Hermes Agent SDK ships a set of GitHub skills under `skills/github/`. The supervise loop borrows the ones that do GitHub mechanics, and adds nothing to the SDK itself: the loop only arranges the **environment** the borrowed skills already expect.

## The bundled skills

| Skill | What it does | In the loop |
|---|---|---|
| `github-auth` | Detect how GitHub is authenticated; export `GITHUB_TOKEN`, `GH_USER`, `GH_OWNER`, `GH_REPO`. | Borrowed — the loop's environment satisfies its detection. |
| `github-issues` | Read, create, triage and label issues. | Borrowed — the planner and the implementer work through it. |
| `github-pr-workflow` | Branch, commit, open a PR, follow CI, merge. | Borrowed — the implementer opens and updates the pull request. |
| `github-code-review` | Read a PR diff, post inline review comments. | Borrowed — the reviewer reads the diff and posts the findings. |
| `github-repo-management` | Clone, create, fork, manage remotes and releases. | Not borrowed — the workspace is cloned and added by the user. |
| `github-issue-to-pr` | A whole issue-to-PR discipline for one agent. | Not borrowed — it overlaps the loop. |
| `codebase-inspection` | LOC and language metrics with `pygount`. | Not borrowed — no phase needs them. |

## What the loop borrows

### `github-auth`

Its `scripts/gh-env.sh` is the entry point every other GitHub skill uses to learn who it is talking to. It detects authentication in this order:

```bash
if command -v gh &>/dev/null && gh auth status &>/dev/null 2>&1; then
    GH_AUTH_METHOD="gh"
    GH_USER=$(gh api user --jq '.login' 2>/dev/null)
elif [ -n "$GITHUB_TOKEN" ]; then
    GH_AUTH_METHOD="curl"
```

The first branch wins whenever `gh auth status` succeeds, and `gh auth status` succeeds on a `GH_TOKEN` that is present in the environment. The loop therefore never has to touch this script: it only exports the variables.

### `github-issues`

The planner reads the issue and posts the plan; the implementer posts its report. Both use the borrowed skill's issue mechanics with the role's own credential, so the comments are attributable.

### `github-pr-workflow`

The implementer creates the branch, commits, pushes and opens the pull request with this skill's conventions — conventional commit subjects, a body that links the issue.

### `github-code-review`

The reviewer reads the diff against the default branch and posts its findings as pull request comments with this skill's output template.

## The environment contract

The loop exports three variables for every command it runs as a role. `gh_roles.py` sets them; nothing else in the loop touches them:

```bash
GH_TOKEN=<role token>
GITHUB_TOKEN=<role token>
GH_CONFIG_DIR=<workspace>/.leap-aide/secrets/github/gh/<role>
```

Each one has a job:

- **`GH_TOKEN`** is what `gh` itself authenticates with. `gh` prefers it over any stored login.
- **`GITHUB_TOKEN`** is what the bundled skills look for. `gh-env.sh` falls back to it when `gh auth status` does not succeed, and `github-code-review` reads it directly.
- **`GH_CONFIG_DIR`** points `gh` at a directory private to this role. `gh auth login` writes there instead of into the user's own `~/.config/gh`, so the loop never overwrites the login the human already has.

Because `gh auth status` succeeds on `GH_TOKEN`, the borrowed skills take their first branch and report the role's identity — `GH_USER` is the role's account, not the human's.

## What the loop does not borrow

### `github-issue-to-pr`

It solves the same problem the loop solves, for a single agent: read the issue, sweep for duplicates, implement, prove the regression test bites, open the PR, shepherd the CI. Running it inside the Implement phase would give the implementer a second, conflicting plan; running it around the loop would duplicate the loop.

The loop takes what is reusable — the duplicate sweep, the acceptance criteria, the CI discipline — as instructions in the phase contexts, and borrows the mechanics from the three focused skills instead.

### `github-repo-management`

The loop never clones, forks or creates a repository. The workspace is prepared by the user before the loop starts, and the preflight check verifies that the remote matches the issue.

### `codebase-inspection`

LOC and language ratios do not decide whether a phase passed.
