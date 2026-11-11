---
title: Handling reports
---

<div align="center">

**English** | [中文](handling-reports.zh-CN.md)

</div>

# Handling reports

Every phase answers with one JSON object matching its schema, and posts the same content as a GitHub comment. The JSON is what the supervisor acts on; the comment is what a human reads. When they disagree, the JSON wins and the discrepancy is itself a finding.

A phase can also report that it did not finish. Those reports are not failures of the schema — they are the loop's pressure valves.

## `DONE_WITH_CONCERNS`

The work is done and pushed, but something about it is not right: a file grew past the plan's intent, a deviation was necessary, a design question came up mid-implementation.

What to do:

1. Record the concern as a ruling if you decided to accept it.
2. Carry the concern into the next phase's dispatch — the reviewer must see it, not discover it.
3. Never treat `DONE_WITH_CONCERNS` as a clean pass. It is a pass with a note attached, and the note has to travel.

The implementer must not split files, widen scope or restructure on its own to make the concern go away. That is how a fix turn into a rewrite.

## `BLOCKED`

The phase cannot proceed at all: an architectural decision with several valid answers, a credential the role does not have, an environment that will not run the tests, a plan that asks for something the repository cannot support.

What to do:

1. Stop the phase. Do not retry a `BLOCKED` report — a retry burns an attempt against a wall.
2. Read what the child said it was stuck on, what it already tried, and what would unblock it.
3. Decide: this is a ruling you can take, a question for the user, or an environment problem to report.
4. Record the decision as a ruling, then act.

`BLOCKED` with no explanation of what was tried is a report you should send back once. The second one without progress is a child that is lost — dispatch again with a narrower brief rather than a longer one.

## `NEEDS_CONTEXT`

The dispatch was missing something: an unclear requirement, a file the plan named but never described, a test command nobody supplied.

What to do:

1. Treat it as a defect in your dispatch, not in the child.
2. Supply the missing information and re-dispatch the same phase.
3. Count the round — `NEEDS_CONTEXT` still consumes an attempt, so a phase that keeps needing context is telling you the plan is underspecified.

Do not answer `NEEDS_CONTEXT` with "use your judgement". If the child could have judged, it would not have asked; a vague reply produces a confident wrong answer.

## Reporting the reports

Whatever the phase returned, the supervisor's own comment on the issue says:

- which phase ran and how many attempts it has used,
- what the phase returned, in one line,
- what you decided, and the ruling behind it if there was one.

The human reading the issue should never have to reconstruct the loop's state from the raw JSON.
