---
title: Model tiers
---

<div align="center">

**English** | [中文](model-tiers.zh-CN.md)

</div>

# Model tiers

Each phase of the loop gets a model chosen for the work that phase does, not the model the session happens to be running. A reviewer that reads a diff all the way through is not doing the same work as a tester that runs a suite and reports the numbers, and pricing them the same wastes one or the other.

## The tiers

| Phase | Tier | Why |
|---|---|---|
| `plan` | Mid to high | The plan is the requirement every later phase is graded against. A weak plan is a weak plan twice — once for the implementer, once for the reviewer. |
| `implement` | Mid to high | Writing correct code against a spec is the expensive part of the loop. This is not the place to economise. |
| `test` | Low to mid | Find the project's own test command, run it, report the numbers. Mechanical work with a clear success criterion. |
| `review` | High | Reading a whole diff, judging spec compliance, and calibrating severity is the seat that decides whether the loop ships. Underspend here and the review rubber-stamps. |

A fix round's re-review is scoped to a small diff and a findings list, so it takes the reviewer tier without needing anything more capable.

## Passing a model to a phase

The dispatch template carries a `model` field:

```
Subagent (general-purpose):
  description: "Review the pull request for [ISSUE_URL]"
  role: "leaf"
  model: [MODEL — the reviewer tier, see references/model-tiers.md]
```

Fill `[MODEL]` with a model id the deployment actually has, then drop the bracketed note. `delegate_task` takes one `model` per call and applies it to every child in that call, which is exactly the granularity a phase needs — a phase is one dispatch.

Leave `model` out and the children inherit the session's model. That is the correct choice when the session already runs the tier you would have picked; it is not a way to skip the decision.

## Checking a model id exists

An id that is not in the catalog does not fail loudly — the child just runs on whatever the deployment falls back to, and the tiering silently did nothing. Check before dispatching:

```
python3 - <<'PY'
import json, sys
from pathlib import Path
cat = json.loads(Path("backend/app/api/data/model_catalog.json").read_text())
want = sys.argv[1] if len(sys.argv) > 1 else "anthropic/claude-sonnet-4.6"
hits = [f"{slug}:{m['id']}" for slug, rec in cat["providers"].items()
        for m in (rec.get("models") or []) if want in str(m.get("id", ""))]
print("\n".join(hits) or "not in the catalog baseline")
PY
```

The catalog is the packaged baseline; a deployment may expose more models through live queries. Treat a miss as "ask the deployment", not as "make up an id".

## When routing is not available

Per-child model routing is a LeapAide compatibility shim, not an SDK feature: `backend/app/compat/delegate_model.py` injects a `model` property into the tool schema, captures it before the registry's argument whitelist drops it, and overlays it onto a copy of the delegation config at selection time.

Two consequences the supervisor has to know:

- **The shim may be absent.** If the server started without it — an SDK import failure, a patch that did not apply — the `model` field is silently ignored and every child runs on the session's model. Check the server log for `per-child model routing patch installed`.
- **Routing is per call, not per task.** One `model` covers every child in one `delegate_task` call. Do not try to vary models between tasks in a batch dispatch; dispatch the phases separately, which the loop does anyway.

If routing is unavailable and the tiers matter, say so in a comment on the issue and record it as a ruling. A loop that pretends it tiered the work when it did not is a loop whose cost report is fiction.

## What this does not change

Model choice does not change the contracts. The reviewer still does not dispatch subagents, the implementer still does not review its own work, and LGTM still only comes from a review that approved. A cheaper model makes those rules harder to follow, never optional.
