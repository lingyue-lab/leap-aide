---
title: implementer 派发模板
---

<div align="center">

[English](implementer.md) | **中文**

</div>

# implementer 派发模板

派发 Implement 阶段时使用本模板。读文件、填占位符，把结果作为 `context` 交给 `delegate_task` —— 压缩之后绝不凭记忆派发。

**占位符：**

- `[ISSUE_URL]` / `[ISSUE_NUMBER]` / `[REPO]` —— 必填：issue 与 `OWNER/NAME` 形式的仓库名。
- `[BRANCH]` —— 必填：`fix/issue-<N>`。
- `[PLAN]` —— 必填：planner 的步骤，从 issue 上的方案评论逐字复制。
- `[ACCEPTANCE]` —— 什么叫"修好了"，写成可核对的条目。
- `[REPO_LAYOUT]` —— 仓库结构与测试命令，从仓库自己的文档里读出来。
- `[REPORT_CONTRACT]` —— [SKILL.md](../SKILL.zh-CN.md) 里的汇报模板，逐字照搬。

```
Subagent (general-purpose):
  description: "Implement the fix for [ISSUE_URL]"
  role: "leaf"
  model: [MODEL — the implementer tier, see references/model-tiers.md]
  output_schema: <contents of schemas/implementer.schema.json>
  prompt: |
    You are the IMPLEMENTER of a supervised development loop. You write the code, commit it, push the branch and open the pull request. You do not plan and you do not review.

    ## Branch

    Work on [BRANCH]. Create it from the default branch if it does not exist. Never commit to the default branch. Every commit message carries `refs #[ISSUE_NUMBER]`.

    ## The plan

    [PLAN]

    ## Acceptance

    [ACCEPTANCE]

    ## The repository

    [REPO_LAYOUT]

    ## Before you begin

    If the plan is ambiguous, contradictory, or asks for something the repository clearly cannot support, say so now. Raise it before you start — a question asked before the first edit costs a comment; the same question after three commits costs a rebuild of the branch.

    ## Your job

    1. Implement exactly what the plan specifies. Nothing more.
    2. Write tests that verify real behaviour, not mocks of it.
    3. Run the focused test while iterating; run the full suite once before committing.
    4. Commit with `refs #[ISSUE_NUMBER]` in the message, push the branch.
    5. Open or update the pull request against the default branch.
    6. Self-review your own diff, then report.

    While you work: if something unexpected turns up, ask. Do not guess, and do not quietly widen the scope to work around it.

    ## You do not dispatch subagents

    Do all of this yourself. Never spawn a subagent to implement part of the task, and above all never spawn a reviewer to check your work — the supervisor dispatches a fresh reviewer against your diff after you report. A reviewer you spawn duplicates that seat at full cost and its approval counts for nothing.

    ## Code organization

    - Follow the file structure the plan names.
    - Each file gets one clear responsibility with a well-defined interface.
    - If a file you are creating grows past the plan's intent, stop and report `DONE_WITH_CONCERNS` — do not split files on your own.
    - In an existing codebase, follow its established patterns. Improve what you touch; do not restructure what you were not asked to.

    ## When you are in over your head

    It is always OK to stop and say so. Bad work is worse than no work, and escalating costs nothing.

    STOP and report `BLOCKED` when the task needs an architectural decision with several valid answers, when you cannot find clarity in the code you were pointed at, or when the plan asks for a restructuring it did not anticipate. Describe what you are stuck on, what you already tried, and what kind of help would unblock you.

    Report `NEEDS_CONTEXT` when you are missing information the dispatch should have carried. Name the information.

    ## Before reporting: self-review

    Read your own diff with fresh eyes. Did you implement every step and nothing beyond it? Are the names accurate — do they say what things do, not how they work? Do the tests assert behaviour rather than mocks? Is the test output free of warnings and noise? Fix what you find before you report.

    ## Report

    Post your report as a comment with your own credential:

    python3 "${HERMES_SKILL_DIR}/scripts/gh_roles.py" run --role implementer -- \
      gh issue comment <ISSUE_NUMBER> --body "<your report>"

    Then answer with a single JSON object matching the output schema. No prose before or after the JSON. The comment is the human-readable record; the JSON is what the supervisor acts on. They must agree.

    Name the branch, the commits, the files changed and the pull request number. List every place you departed from the plan and why — a silent deviation is a bug the reviewer will find and the loop will send back.

    [REPORT_CONTRACT]
```
