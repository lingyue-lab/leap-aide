---
name: supervise-fix-issue
description: Use when a GitHub issue must be fixed end to end.
version: 1.0.0
author: LeapAide
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [github, workflow, delegate, supervision, issue, pull-request]
    related_skills: [github-issues, github-pr-workflow, github-code-review]
---

<div align="center">

**English** | [中文](SKILL.zh-CN.md)

</div>

# supervise-fix-issue Skill

You are the **supervisor** of an automated development loop. You do not write the code yourself: you read the issue, decide what the next phase is, delegate that phase to a sub-session with `delegate_task`, check the sub-session's report, and either advance the loop or send the phase back for another attempt. The loop runs Plan → Implement → Test → Review, and it does not stop until the pull request carries an LGTM.

The issue and the pull request are the channel. Every phase leaves a comment there, so the whole development history survives the session that produced it.

The user starts the loop with a slash command:

```
/supervise-fix-issue https://github.com/OWNER/NAME/issues/57
```

The issue URL is the seed of the whole loop. Everything else — the plan, the branch, the pull request, the review — is derived from it.

## Why this loop is a skill

A workflow engine would give the loop an enforced graph, per-role profiles and a resume that does not depend on anyone reading a file. It would also give the project a second runtime to maintain, a DSL to learn, and a scheduler that has to agree with the Hermes Agent SDK about what a session is.

This loop keeps the SDK as the only runtime. The phases are ordinary `delegate_task` calls, the state is a JSON file in the workspace, and the record of what happened is the issue's own comment thread. What that costs is written down in [ADR-003](../../../docs/architecture.md): no enforced DAG, no per-role profile isolation, no cross-session resume. The compensations are a ruling ledger, a bounded fix loop and a resuming procedure — all three are sections below.

The core principle: **you supervise decisions, not text.** You read what a phase decided, you verify the decision against the repository, and you record your own decision when you take one. A phase's report is a claim, never a conclusion.

## When to Use

Use this skill when the user asks for an issue to be fixed end to end: planned, implemented, tested, reviewed, and submitted as a pull request.

Do not use it for:

- A single question about the repository — answer it directly.
- A change that is already fully specified and needs no planning — do it yourself.
- Work that is not tracked on GitHub — the loop has no channel to run on.

## Rulings, not stalls

Most of what looks like a stall is a decision you have not taken yet. When the loop stops making progress, take one of four stops and no more:

1. A question only the user can answer.
2. A decision only the user can make.
3. A credential the loop does not have.
4. An environment that will not run the tests.

Anything else — a scope question, a design preference, a file that grew past its intent — is a ruling you can take yourself. Record it before you act on it:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" rule \
  --what "kept the 4 fixed phases" \
  --why "issue #57 names them" \
  --cost "a fifth phase would stall the loop"
```

`--why` is mandatory; a ruling without a reason is a guess. Read the ledger back with `rule --list` and paste the `Rulings I made` block into your closing comment, so the human reviewer can audit every decision the loop took on their behalf. The full discipline — the round cap, breaker adjudication, deferred minors and what ends the loop — is in [The fix loop](references/fix-loop.md).

## Prerequisites

The loop needs two things that are **not** created for you: a workspace that holds the repository, and a set of per-role GitHub credentials. Check both before delegating anything — a half-prepared workspace wastes a whole delegation cycle.

### The workspace

The current session's workspace must be a git repository with a GitHub remote that matches the issue. The repository has to be cloned and added as a workspace in the Work Panel before the loop starts; the skill never clones anything.

### The credentials

Each role authenticates separately, so the comments a phase leaves are attributable to that role. The credentials live in the workspace, never in this conversation:

```
<workspace>/.leap-aide/secrets/github/config.yaml    (mode 0600)
<workspace>/.leap-aide/.gitignore                     (ignores the whole tree)
```

Read them only through the wrapper script. Never open the config file, never paste a token into a message, never write a token into a file that is not under `.leap-aide/`.

What each role needs permission for, how a personal access token differs from a GitHub App installation, and how to rotate a credential are written down in [Role tokens](references/role-tokens.md).

## Model selection

Each phase runs on a model chosen for the work that phase does — the plan and the implementation take a capable one, the test run takes a cheap one, the review takes the most capable one, because the review is the seat that decides whether the loop ships.

The dispatch templates in `prompts/` each carry a `model` field. Fill it with an id the deployment actually has, and check the id against the catalog first; an id that is not there does not fail, it silently runs on the session's model and the tiering does nothing. Per-role tiers, how `delegate_task` applies one model per call, and what to do when the deployment has no per-child routing are in [Model tiers](references/model-tiers.md).

Leave `model` out only when the session already runs the tier you would have picked.

## Procedure

### Step 0 — Preflight

Run the preflight check. It verifies the repository, the remote, the working tree, the `gh` CLI and every role credential, and prints one JSON report:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/preflight.py" --issue "<ISSUE_URL>"
```

When `ok` is false, stop and show the user the `errors` array. Each error carries a `fix` string that says exactly what to do — for example "the workspace is not a git repository" or "the `planner` token was rejected by GitHub". Do not start the loop on a failed preflight; a missing token discovered halfway through the loop means a phase that cannot report, and the loop stalls on a phase that never finishes.

When the credential file itself is missing, create the template and tell the user which values to fill in:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/gh_roles.py" init
```

`init` writes the template and the `.gitignore`; it never overwrites an existing config unless you pass `--force`. The user fills in the tokens and re-runs `gh_roles.py doctor`, which validates each role against GitHub and prints step-by-step guidance for every failure — which page to open, which permission to tick.

Warnings (a dirty working tree, a workspace that is a subdirectory of the repository root) are not fatal, but report them before starting.

### Step 1 — Read the issue

Fetch the issue with the supervisor's own credential and read it in full — the body, the labels and every comment:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/gh_roles.py" run --role supervisor -- \
  gh issue view "<ISSUE_URL>" --comments
```

If the issue already carries a plan (a comment with the implementation steps), treat that comment as the planner's output and skip the Plan phase — go straight to Implement. This is the common case when a human has already written the plan.

Record the loop state:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" init \
  --issue "<ISSUE_URL>" --repo "OWNER/NAME" --issue-number <N>
```

### Step 2 — Arm the standing goal

The loop spans several turns: you dispatch a phase, the conversation goes quiet while the sub-session works, and the report arrives later as a new message. A standing goal keeps the session alive across that gap — the WebUI re-evaluates it at the end of every turn and queues the next turn when the goal is not achieved yet.

```bash
python3 "${HERMES_SKILL_DIR}/scripts/arm_goal.py" arm \
  --text "Drive issue #<N> to an LGTM'd pull request: plan, implement, test, review." \
  --outcome "The pull request for issue #<N> carries an LGTM comment." \
  --verification "gh pr view <PR> shows a review comment containing LGTM." \
  --stop-when "The pull request has an LGTM, or a phase exhausts its attempts."
```

The goal is the loop's heartbeat, not its driver: it keeps the session alive between phases. It never advances a phase — only a phase report does that.

### Step 3 — Run the loop

```
Plan ──→ Implement ──→ Test ──→ Review ──→ LGTM
  ↑         ↑           ↑         ↑
  └─────────┴───────────┴─────────┘   a failed phase goes back to the phase that owns the failure
```

Each phase is one `delegate_task` call. Delegations from a top-level session always run in the background: the call returns immediately with a dispatch receipt, and the sub-session's report arrives later as a new message that starts your next turn. End your turn after dispatching, with one line naming the phase you dispatched.

#### Delegating a phase

Read the phase's dispatch template from `prompts/` and fill every placeholder before you dispatch. The template is a file, not something you reconstruct from memory — a compacted context paraphrases the contracts, and a paraphrased contract is a contract the child will not follow.

```
prompts/planner.md          prompts/planner.zh-CN.md
prompts/implementer.md      prompts/implementer.zh-CN.md
prompts/tester.md           prompts/tester.zh-CN.md
prompts/reviewer.md         prompts/reviewer.zh-CN.md
prompts/re-reviewer.md      prompts/re-reviewer.zh-CN.md
```

Always pass `role="leaf"`. A leaf child has no `delegate_task` tool at all, so the tree stays one level deep and a sub-session can never spawn a sub-sub-session.

Read the phase's schema and pass it as `output_schema`, so the report is machine-checked and a malformed report is retried once by the SDK before it reaches you:

```bash
# Read the schema first — its path is listed among this skill's supporting files.
cat "${HERMES_SKILL_DIR}/schemas/plan.schema.json"
```

Then delegate:

```
delegate_task(
  goal="<one self-contained sentence: what this phase must produce>",
  context="<the filled dispatch template, verbatim>",
  role="leaf",
  output_schema=<the contents of the phase schema file>,
  model="<the tier's model id, when routing is available>"
)
```

The `context` is the child's only knowledge of the world. It must contain:

- The absolute skill directory (`${HERMES_SKILL_DIR}`) and the role name, so the child can run the credential wrapper.
- The issue URL and the repository in `OWNER/NAME` form.
- The branch to work on, for the phases that touch code.
- The task itself, in enough detail that the child needs no other context.
- The reporting contract: post a comment with the role credential, then answer with the JSON object the schema describes.

Give the child this reporting template verbatim — it is what makes the loop work:

```
Post your report as a comment with your own credential:

    python3 "${HERMES_SKILL_DIR}/scripts/gh_roles.py" run --role <ROLE> -- \
      gh issue comment <ISSUE_NUMBER> --body "<your report>"

(Use `gh pr comment <PR_NUMBER> --body "..."` when the report belongs on the pull request.)

Then answer with a single JSON object matching the output schema. No prose
before or after the JSON. The comment and the JSON must agree: the comment is
the human-readable record, the JSON is what the supervisor acts on.
```

#### The four phases

| Phase | Role | Schema | What the child produces |
|---|---|---|---|
| Plan | `planner` | `schemas/plan.schema.json` | The implementation plan, posted on the issue. |
| Implement | `implementer` | `schemas/implementer.schema.json` | The change on a branch, committed and pushed, plus a pull request. |
| Test | `tester` | `schemas/tester.schema.json` | The test run and its outcome, posted on the issue or the pull request. |
| Review | `reviewer` | `schemas/reviewer.schema.json` | The review verdict, posted on the pull request. |

**Plan.** Delegate the planner with the issue body, the repository layout and the acceptance criteria. The planner posts the plan as an issue comment and answers with the schema. If the issue already carries a plan, skip this phase.

**Implement.** The implementer works on the branch `fix/issue-<N>` — create it from the default branch if it does not exist, and never touch the default branch. The implementer commits with `refs #<N>` in the commit message, pushes the branch, opens or updates the pull request against the default branch, and posts the report on the issue. Record the branch and the pull request number:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" set-branch --name "fix/issue-<N>"
python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" set-pr --number <PR> --url "<PR_URL>"
```

**Test.** The tester runs the project's own test command — read it from the repository's documentation rather than guessing — and reports every case with its expected and actual result. The tester must distinguish a failure caused by this change from a pre-existing failure, and say which is which; a red suite that was already red is not a failed phase.

**Review.** The reviewer reads the diff against the default branch and posts the findings on the pull request. **An LGTM is posted only when the reviewer's verdict is `approve`.** The severity calibration that decides the verdict — `blocker` and `major` mean `request_changes`, `minor` and `nit` do not, and an unresolved `cannot_verify` means `request_changes` — is part of the reviewer's dispatch template. A `request_changes` verdict starts a fix round.

#### Fix rounds

A review that requests changes is not a failed review. It starts a fix round:

1. Count the review's round: `supervise_state.py attempt --phase review`.
2. Hand the implementer the findings verbatim, as a new dispatch, and let it push onto the same branch.
3. Build the fix diff package so the re-reviewer reads one file instead of re-deriving the range:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/review_package.py" --base <FIX_BASE> --head <HEAD>
```

4. Dispatch `prompts/re-reviewer.md` with the plan, the findings and the printed diff path. The re-reviewer verdicts every finding `ADDRESSED` or `NOT ADDRESSED` and reports anything the fix itself broke.

Every open finding, every deferred minor and every `cannot_verify` you resolved yourself becomes a ruling. Do not let a finding disappear because the context that held it was compacted away.

#### Deciding the outcome

When a phase report arrives, judge it on three signals — the JSON, the comment, and the repository:

1. **The JSON.** The SDK validates it against the schema and retries once on failure. If `schema_valid` is false, treat the phase as failed and re-dispatch.
2. **The comment.** Check that the phase actually left its comment. A report without a comment means the phase did not finish its job, even if the JSON looks right:

   ```bash
   python3 "${HERMES_SKILL_DIR}/scripts/gh_roles.py" run --role supervisor -- \
     gh api "repos/OWNER/NAME/issues/<N>/comments" --jq '.[-1] | .body'
   ```

3. **The repository.** For Implement, confirm the branch exists, the commit is on it and the pull request is open:

   ```bash
   python3 "${HERMES_SKILL_DIR}/scripts/gh_roles.py" run --role supervisor -- \
     gh pr view <PR> --json state,headRefName,mergeable
   ```

A phase passed when its JSON says so, its comment is on GitHub and — for the code phases — the repository agrees. Then record it and move on:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" advance --phase <PHASE>
```

A report that comes back `DONE_WITH_CONCERNS`, `BLOCKED` or `NEEDS_CONTEXT` is not a pass and not a schema failure. What each one means and what you do about it is in [Handling reports](references/handling-reports.md).

#### Retrying a failed phase

Count the attempt before dispatching, and check the count it returns:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" attempt --phase <PHASE>
```

When the attempt count is below the maximum (three by default), re-dispatch the **same** phase with the failure as context — the previous report, the failing output, and what has to change. Say explicitly in the `context` what the previous attempt got wrong and what the child must do differently. A retry that repeats the same context repeats the same failure.

When the maximum is reached, do not simply hand the issue back. Adjudicate first: read every finding still open, decide whether the rest is another fix round, a question for the user, or an environment limit, and record the decision as a ruling. Only then report to the user — which phase failed, what each attempt produced, what you ruled, and what a human still needs to decide. The full adjudication procedure is in [The fix loop](references/fix-loop.md).

A failed phase goes back to the phase that owns the failure:

| Failure | Goes back to |
|---|---|
| The plan does not cover the issue | Plan |
| The build fails, the acceptance criteria are not met | Implement |
| The test suite fails because of the change | Implement |
| The review requests changes | Implement (fix round) |
| The reviewer cannot read the diff, or a credential is missing | blocked — report to the user |

### Step 4 — Close out

The loop is done when the pull request carries an LGTM. Verify it yourself rather than trusting the report:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/gh_roles.py" run --role supervisor -- \
  gh api "repos/OWNER/NAME/issues/<PR>/comments" \
  --jq '[.[] | select(.body | test("LGTM"))] | length'
```

Then:

1. Clear the standing goal — it has done its job and must not keep the session turning:

   ```bash
   python3 "${HERMES_SKILL_DIR}/scripts/arm_goal.py" clear
   ```

2. Mark the loop finished:

   ```bash
   python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" finish
   ```

3. Summarise for the user: the plan, the files changed, the test result, the review verdict, the rulings you took, and the pull request URL. Hand the artifacts over as files rather than as chat prose — `scripts/brief.py` writes the closing brief for you:

   ```bash
   python3 "${HERMES_SKILL_DIR}/scripts/brief.py" --out <PATH>
   ```

4. Ask whether to squash-and-merge the pull request. Do not merge on your own — merging is the user's call.

## Resuming after a compaction

The conversation is not the state. A compaction, a restart or a crashed sub-session loses the discussion; the loop loses nothing, because its state lives in `<workspace>/.leap-aide/supervise/state.json`.

Before you do anything else on a resumed run, read it:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" show
```

Then read the issue's comments: the state file says what the loop decided, the comments say what the children actually reported. Reconstruct the plan, the open findings and the diff range from those two sources — never from memory — and paste `rule --list` into your next comment so the decisions the previous context took survive the compaction too. The full recovery procedure, including what not to reconstruct, is in [Resuming the loop](references/resuming.md).

## Rules

- **One level of delegation.** Every `delegate_task` call passes `role="leaf"`. A leaf child has no `delegate_task` tool, so the tree cannot grow sideways or downwards.
- **The default branch is read-only.** All code work happens on `fix/issue-<N>`. Commits carry `refs #<N>`.
- **Only an approve verdict produces an LGTM.** Never write LGTM on the user's behalf and never let a phase that did not approve produce one.
- **The issue and the pull request are the channel.** Every phase leaves a comment. A phase that does not comment has not reported.
- **Credentials never enter the context.** Use `gh_roles.py run --role <ROLE> -- <command>`. Never read the config file, never echo a token, never paste one into a message or a commit.
- **The loop is bounded.** Three attempts per phase. When they are used up, adjudicate, record a ruling, and hand the problem back to the user.
- **Dispatch from the file, not from memory.** Read `prompts/<phase>.md` and fill it. A paraphrased contract is a contract the child will not follow.
- **Every decision is recorded.** A ruling without a `--why` is a guess; the script refuses to store one.

## Common rationalizations

| Rationalization | What it actually costs |
|---|---|
| "The report looks right, I'll skip checking the comment." | A phase that did not post its comment did not finish its job. The JSON is the child's opinion of its own work. |
| "I'll just fix this small thing myself." | You are now the implementer, and the reviewer will grade your own edit with your own context. Dispatch it. |
| "The reviewer said minor, so it's fine to drop." | Deferred minors accumulate. Record each one as a ruling with a `--cost` so the closing summary lists it. |
| "I'll re-run the phase, I can't remember whether it passed." | The state file is authoritative. Re-running a passed phase burns an attempt and rewrites the record. |
| "I'll retry with the same context, it was probably a fluke." | The same context produces the same failure. Say what was wrong and what must change. |
| "The model is expensive, I'll leave `model` unset." | The tiering does nothing and the review seat runs on whatever the session runs on. Decide the tier on purpose. |
| "I'll merge it, the user asked for the issue fixed." | Merging is the user's call. External completion is what makes the approval mean something. |

## Pitfalls

- **Starting the loop without a preflight**: a missing token surfaces as a phase that never reports, and the loop stalls instead of failing loudly.
- **Passing `background` to `delegate_task`**: the parameter is deprecated and ignored — top-level delegations always run in the background. Passing it changes nothing.
- **Waiting for the report in the same turn**: the dispatch returns immediately. End the turn; the report arrives as a new message.
- **Retrying with the same context**: the child will produce the same failure. Say what was wrong and what must change.
- **Reading the credential file directly**: the token lands in the conversation. Use the wrapper.
- **Reconstructing the plan after a compaction**: a paraphrase silently changes the requirements the reviewer grades against. Copy it from the issue.
- **Dispatching from memory**: read `prompts/<phase>.md`. The template carries contracts the conversation no longer holds.
- **Merging the pull request without asking**: squash-and-merge is the user's decision.
