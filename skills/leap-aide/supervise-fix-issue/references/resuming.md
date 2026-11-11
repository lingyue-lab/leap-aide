---
title: Resuming the loop
---

<div align="center">

**English** | [中文](resuming.zh-CN.md)

</div>

# Resuming the loop

The conversation is not the state. A compaction, a restart, or a crashed sub-session loses the discussion; the loop does not lose anything, because its state is a file:

```
<workspace>/.leap-aide/supervise/state.json
```

Read it before you do anything else on a resumed run.

## The recovery read

```
python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" show
```

One JSON object tells you:

| Field | What it answers |
|---|---|
| `phase` | Which phase is current. |
| `attempts` | How many rounds each phase has used, and against what `max_attempts`. |
| `branch` / `pr` | Whether a branch and pull request already exist. |
| `status` | `running`, `done`, or handed back. |
| `rulings` | Every decision the previous context took, and what it cost if wrong. |
| `history` | The last 200 events, newest last. |

Then read the issue's comments. The state file says what the loop decided; the comments say what the children actually reported. Both are needed — a state file can claim a phase passed while the comment that was supposed to record it never got posted.

## What to reconstruct

1. **The plan.** Find the planner's comment on the issue and copy it verbatim into the next dispatch. Never rewrite the plan from memory after a compaction; a paraphrase silently changes the requirements the reviewer will grade against.
2. **The findings.** For a fix round, copy every open finding verbatim. A dropped finding is a finding the loop already paid for and then forgot.
3. **The diff range.** Read `pr` and `branch` from the state, then confirm the head SHA against the pull request rather than against your memory of it.
4. **The rulings.** Paste `rule --list` into your next comment. The decisions you took are the part of the loop most likely to be lost, and the part a human most needs to audit.

## What not to reconstruct

Do not re-run a phase that already passed, because you cannot remember it passing. The state file is authoritative; your memory of it is not.

Do not re-run preflight against a state that is already `running` — a re-init without `--force` reuses the existing state on purpose, so credentials are not re-collected mid-loop.

Do not restart the attempts counter. An exhausted phase stays exhausted after a resume; if the loop could reset its own tally, the cap would not be a cap.

## Resuming mid-round

If you resumed between an implementer's report and the reviewer's dispatch, the phase is still `implement` in the state. Post a comment saying you resumed and what you are about to dispatch, so the issue timeline shows the gap. Then dispatch the review with the plan, the findings and the diff file — all read from the issue and the state, none from memory.

## When the state file is gone

If `state.json` does not exist but the issue has comments and a pull request, the loop was interrupted before `init` or the workspace was wiped. Rebuild by reading the issue, then `init` with `--force`. The attempt counters start again — say so in a comment, because the old rounds are no longer counted and the human should know the cap was reset.
