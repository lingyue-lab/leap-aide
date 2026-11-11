---
title: The fix loop
---

<div align="center">

**English** | [中文](fix-loop.zh-CN.md)

</div>

# The fix loop

A phase that comes back with findings does not restart the loop. It starts a fix round: the implementer addresses the findings, and a re-reviewer checks the fix diff against the findings list. The loop only stops when the reviewer approves, the attempts run out, or the supervisor decides the issue is beyond a fix.

## The round cap

Every phase gets `max_attempts` rounds, default 3. Counting is mechanical — `supervise_state.py attempt --phase PHASE` increments and reports `exhausted`. Do not hand-count in the conversation; a compacted context loses the tally and restarts a phase that had already run out.

The review phase is the exception that matters. Findings are normal in review, so a review that returns `request_changes` is not a failed review — it is a round that produced findings. Count the round, send the fix, re-review.

## Breaker adjudication

When a phase is exhausted, do not simply hand the issue back to the user. Adjudicate:

1. Read every finding that is still open.
2. Decide whether the remaining work is a fix (another round), a design question (ask the user), or an environment limit (report and stop).
3. Record the decision as a ruling before you act on it.

Exhausting attempts means the loop failed to converge, not that the issue is unfixable. Say which it is.

## Rulings, not stalls

A ruling is a decision you made because waiting would cost more than deciding. Every ruling goes in the ledger:

```
python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" rule \
  --what "kept the 4 fixed phases" \
  --why "issue #57 names them" \
  --cost "a fifth phase would stall the loop"
```

`--why` is mandatory: a ruling without a reason is a guess, and the script rejects it. `--cost` states what happens if you were wrong, which is what makes the ledger reviewable after a compaction.

Read the ledger back with `rule --list`. The summary is titled `Rulings I made` — paste it into your closing comment so the human reviewer sees every decision the loop took on their behalf.

Four stops are enough for any stall: a question only the user can answer, a decision only the user can make, a credential the loop does not have, or an environment that will not run the tests. Anything else is a ruling you can take yourself.

## Cannot verify

A finding you cannot settle from the diff alone is not a pass and not a failure — it is an open item. The reviewer puts it in `cannot_verify`; you resolve it by reading the code yourself, dispatching a narrow check, or ruling it out of scope with a stated reason.

Never let an unresolved `cannot_verify` ride into an approval. An unverified requirement is not a verified one, and a loop that approves over an open item has stopped supervising.

## Deferred minors

Minor and nit findings do not block an approval, but they are not free. Record each deferred minor as a ruling with `--cost`, so the closing summary can list what was consciously left undone. Silently dropping a minor finding is how a codebase accumulates the damage the review was supposed to prevent.

## What ends the loop

| Ending | When |
|---|---|
| LGTM | The reviewer approved and posted the comment. Only then is the issue done. |
| Hand back | Attempts exhausted and adjudication says the user must decide. |
| Blocked | A credential, environment or access problem the loop cannot resolve. |
| Abandoned | The issue turned out not to be a fix this loop should attempt. |

Never merge your own pull request, and never write LGTM on the user's behalf. External completion is what makes the approval mean something.
