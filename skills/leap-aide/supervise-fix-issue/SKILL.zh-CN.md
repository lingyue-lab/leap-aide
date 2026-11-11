---
name: supervise-fix-issue
description: Drive a GitHub issue to a reviewed pull request.
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

[English](SKILL.md) | **中文**

</div>

# supervise-fix-issue 技能

你是自动化开发循环的**监督者（supervisor）**。代码不由你亲手写：你负责读取 issue、判断下一个阶段、用 `delegate_task` 把该阶段派给子会话、检查子会话的报告，然后要么推进循环，要么把该阶段打回去重试。循环按 Plan → Implement → Test → Review 推进，在 PR 拿到 LGTM 之前不会停下。

issue 和 PR 是信息交换的通道。每个阶段都会在那边留下评论，于是整段开发历史不会随产生它的那次会话一起消失。

用户用斜杠命令启动循环：

```
/supervise-fix-issue https://github.com/OWNER/NAME/issues/57
```

issue 的 URL 是整个循环的种子。其余一切——方案、分支、PR、评审——都由它推导出来。

## 使用时机

当用户要求把一个 issue 从头到尾修完时使用本技能：出方案、写实现、跑测试、做评审，并以 PR 提交。

不要用于：

- 关于本仓库的单个问题——直接回答。
- 已经完全明确、无需规划的改动——自己动手做。
- 不在 GitHub 上跟踪的工作——循环没有可运行的通道。

## 前置条件

循环需要两样**不会替你准备好**的东西：一个装着仓库的工作区，以及一组按角色划分的 GitHub 凭据。派发任何任务之前先把两者都检查一遍——工作区只准备好一半，会白白浪费一整轮派发。

### 工作区

当前会话的工作区必须是一个 git 仓库，且 GitHub 远端与 issue 所属仓库一致。仓库必须事先克隆好并在工作面板里添加为工作区；本技能从不克隆任何东西。

### 凭据

每个角色单独认证，这样各阶段留下的评论可以追溯到具体角色。凭据存放在工作区里，绝不进入本轮对话：

```
<workspace>/.leap-aide/secrets/github/config.yaml    (mode 0600)
<workspace>/.leap-aide/.gitignore                     (ignores the whole tree)
```

只通过包装脚本读取它们。绝不 `cat` 该配置，绝不把 token 贴进消息，绝不把 token 写进 `.leap-aide/` 之外的文件。

各角色分别需要什么权限、个人访问令牌与 GitHub App 安装有何不同、以及怎样轮换凭据，都写在 [角色令牌](references/role-tokens.zh-CN.md) 里。

## 流程

### 步骤 0 —— 预检

运行预检。它会检查仓库、远端、工作树、`gh` CLI 以及每个角色的凭据，并输出一份 JSON 报告：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/preflight.py" --issue "<ISSUE_URL>"
```

当 `ok` 为 false 时，停下来并把 `errors` 数组展示给用户。每条错误都带一个 `fix` 字符串，写清楚该做什么——例如"工作区不是 git 仓库"或"GitHub 拒绝了 `planner` 的 token"。预检失败就不要启动循环：跑到一半才发现缺 token，意味着某个阶段无法汇报，循环会卡在一个永远完不成的阶段上。

当凭据文件本身不存在时，创建模板并告诉用户要填哪些值：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/gh_roles.py" init
```

`init` 会写入模板和 `.gitignore`；除非显式传 `--force`，它绝不覆盖已有配置。用户填好 token 后重新运行 `gh_roles.py doctor`，它会逐个角色向 GitHub 验证，并为每个失败打印分步指引——该打开哪个页面、该勾选哪项权限。

警告（工作树不干净、工作区是仓库根目录的子目录等）不是致命问题，但要在开始前向用户说明。

### 步骤 1 —— 读取 issue

用 supervisor 自己的凭据取回 issue，完整阅读——正文、标签以及每一条评论：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/gh_roles.py" run --role supervisor -- \
  gh issue view "<ISSUE_URL>" --comments
```

如果 issue 里已经有方案（一条包含实施步骤的评论），就把那条评论当作 planner 的产出，跳过 Plan 阶段——直接进入 Implement。人工已经写好方案时，这是常见情形。

记录循环状态：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" init \
  --issue "<ISSUE_URL>" --repo "OWNER/NAME" --issue-number <N>
```

### 步骤 2 —— 装填常驻目标

循环横跨多个回合：你派发一个阶段，对话在子会话干活期间静默，报告稍后作为一条新消息到达。常驻目标（standing goal）让会话在这段空隙里保持活着——WebUI 在每个回合结束时重新评估它，目标尚未达成就排入下一个回合。

```bash
python3 "${HERMES_SKILL_DIR}/scripts/arm_goal.py" arm \
  --text "Drive issue #<N> to an LGTM'd pull request: plan, implement, test, review." \
  --outcome "The pull request for issue #<N> carries an LGTM comment." \
  --verification "gh pr view <PR> shows a review comment containing LGTM." \
  --stop-when "The pull request has an LGTM, or a phase exhausts its attempts."
```

目标是循环的心跳，不是它的驱动器：它负责让会话在阶段之间活着。它绝不推进阶段——只有阶段报告才能推进。

### 步骤 3 —— 跑循环

```
Plan ──→ Implement ──→ Test ──→ Review ──→ LGTM
  ↑         ↑           ↑         ↑
  └─────────┴───────────┴─────────┘   a failed phase goes back to the phase that owns the failure
```

每个阶段就是一次 `delegate_task`。顶层会话发起的委派总是后台运行：调用立刻返回一张派发回执，子会话的报告稍后作为一条新消息到达并开启你的下一个回合。派发之后结束当前回合，用一行说明你派了哪个阶段。

#### 派发一个阶段

始终传 `role="leaf"`。叶子子会话根本没有 `delegate_task` 工具，因此这棵树保持一层深，子会话永远无法再生出子子会话。

读取该阶段的 schema 并作为 `output_schema` 传入，这样报告会被机器校验，格式不合规的报告在送到你这里之前由 SDK 重试一次：

```bash
# Read the schema first — its path is listed among this skill's supporting files.
cat "${HERMES_SKILL_DIR}/schemas/plan.schema.json"
```

然后委派：

```
delegate_task(
  goal="<one self-contained sentence: what this phase must produce>",
  context="<everything the child needs — see below>",
  role="leaf",
  output_schema=<the contents of the phase schema file>
)
```

`context` 是子会话对世界的全部认知。它必须包含：

- 技能目录的绝对路径（`${HERMES_SKILL_DIR}`）和角色名，好让子会话能运行凭据包装脚本。
- issue 的 URL，以及 `OWNER/NAME` 形式的仓库名。
- 要动的分支——对于会改代码的阶段。
- 任务本身，细节要足够，让子会话不需要任何其他上下文。
- 汇报约定：用角色凭据发一条评论，然后按 schema 描述的内容回答那个 JSON 对象。

把下面这段汇报模板逐字交给子会话——循环能跑起来靠的就是它：

```
Post your report as a comment with your own credential:

    python3 "${HERMES_SKILL_DIR}/scripts/gh_roles.py" run --role <ROLE> -- \
      gh issue comment <ISSUE_NUMBER> --body "<your report>"

(Use `gh pr comment <PR_NUMBER> --body "..."` when the report belongs on the pull request.)

Then answer with a single JSON object matching the output schema. No prose
before or after the JSON. The comment and the JSON must agree: the comment is
the human-readable record, the JSON is what the supervisor acts on.
```

#### 四个阶段

| 阶段 | 角色 | Schema | 子会话产出什么 |
|---|---|---|---|
| Plan | `planner` | `schemas/plan.schema.json` | 实施方案，作为评论发在 issue 上。 |
| Implement | `implementer` | `schemas/implementer.schema.json` | 分支上的改动，已提交并推送，外加一个 PR。 |
| Test | `tester` | `schemas/tester.schema.json` | 测试执行过程及其结论，发在 issue 或 PR 上。 |
| Review | `reviewer` | `schemas/reviewer.schema.json` | 评审结论，发在 PR 上。 |

**Plan。** 把 issue 正文、仓库结构和验收标准交给 planner。planner 把方案作为 issue 评论发出来，并按 schema 回答。若 issue 里已有方案，跳过这个阶段。

**Implement。** implementer 在 `fix/issue-<N>` 分支上干活——不存在就从默认分支建，绝不碰默认分支。implementer 提交时在提交信息里带上 `refs #<N>`，推送分支，对着默认分支开或更新 PR，并把报告发在 issue 上。记录分支和 PR 号：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" set-branch --name "fix/issue-<N>"
python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" set-pr --number <PR> --url "<PR_URL>"
```

**Test。** tester 运行项目自带的测试命令——从仓库文档里读出来，而不是靠猜——并逐个用例汇报期望结果与实际结果。tester 必须区分"这次改动导致的失败"和"本来就存在的失败"，并说清是哪种；本来就红的测试套件不算这个阶段失败。

**Review。** reviewer 阅读对着默认分支的完整 diff，并把发现发在 PR 上。**只有在 reviewer 的结论是 `approve` 时才发 LGTM。** 结论为 `request_changes` 时，循环退回 Implement，把这些发现作为新的上下文。

#### 判定结果

阶段报告到达时，依据三个信号判断——JSON、评论、仓库：

1. **JSON。** SDK 会按 schema 校验，失败时重试一次。若 `schema_valid` 为 false，视为该阶段失败并重新派发。
2. **评论。** 确认该阶段确实留下了评论。没有评论的报告说明这个阶段没干完活，哪怕 JSON 看起来没问题：

   ```bash
   python3 "${HERMES_SKILL_DIR}/scripts/gh_roles.py" run --role supervisor -- \
     gh api "repos/OWNER/NAME/issues/<N>/comments" --jq '.[-1] | .body'
   ```

3. **仓库。** 对 Implement，确认分支存在、提交在上面、PR 处于打开状态：

   ```bash
   python3 "${HERMES_SKILL_DIR}/scripts/gh_roles.py" run --role supervisor -- \
     gh pr view <PR> --json state,headRefName,mergeable
   ```

当 JSON 说通过、评论确实在 GitHub 上、且——对涉及代码的阶段——仓库状况也吻合时，该阶段通过。记录并继续：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" advance --phase <PHASE>
```

#### 重试失败阶段

派发前先计数，看它返回的计数：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" attempt --phase <PHASE>
```

当计数还没到上限（默认三次），用失败信息作为上下文**重新派发同一个**阶段——上一轮的报告、失败的输出，以及需要改掉的地方。在 `context` 里明确写出上一轮错在哪、这一轮必须怎么改。上下文不变的重试只会重演同一个失败。

达到上限时，停止循环，不要再派发。清掉目标并向用户汇报：哪个阶段失败、每一轮尝试产出了什么、需要人来决定什么。一遍遍重试注定失败的阶段，只会烧 token 而不会收敛。

失败阶段退回拥有该失败的那个阶段：

| 失败情形 | 退回 |
|---|---|
| 方案没有覆盖 issue 的要求 | Plan |
| 构建失败，验收标准没达成 | Implement |
| 测试套件因这次改动而失败 | Implement |
| 评审要求修改 | Implement |
| reviewer 读不到 diff，或缺少凭据 | 阻塞——汇报给用户 |

### 步骤 4 —— 收尾

PR 拿到 LGTM 时循环结束。自己核实，不要只信报告：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/gh_roles.py" run --role supervisor -- \
  gh api "repos/OWNER/NAME/issues/<PR>/comments" \
  --jq '[.[] | select(.body | test("LGTM"))] | length'
```

然后：

1. 清掉常驻目标——它已经完成使命，不能再让会话空转：

   ```bash
   python3 "${HERMES_SKILL_DIR}/scripts/arm_goal.py" clear
   ```

2. 标记循环结束：

   ```bash
   python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" finish
   ```

3. 向用户总结：方案、改动的文件、测试结果、评审结论和 PR 的 URL。

4. 询问是否要 squash-and-merge 这个 PR。不要自行合并——合并是用户的决定。

## 规则

- **委派只有一层。** 每次 `delegate_task` 都传 `role="leaf"`。叶子子会话没有 `delegate_task` 工具，这棵树无法横向或向下生长。
- **默认分支只读。** 所有代码改动都在 `fix/issue-<N>` 上。提交带上 `refs #<N>`。
- **只有 approve 结论才产出 LGTM。** 绝不代用户写 LGTM，也绝不让未通过评审的阶段产出 LGTM。
- **issue 与 PR 就是通道。** 每个阶段都留评论。没评论的阶段就是没汇报。
- **凭据绝不进入上下文。** 用 `gh_roles.py run --role <ROLE> -- <command>`。绝不直接读配置文件，绝不回显 token，绝不把 token 贴进消息或提交。
- **循环是有界的。** 每个阶段三次机会。用完了就把问题交回给用户。

## 常见坑

- **不做预检就启动循环**：缺 token 会表现为某个阶段永远不汇报，循环卡住而不是干脆地失败。
- **给 `delegate_task` 传 `background`**：该参数已废弃且被忽略——顶层委派一律后台运行，传了也不会有任何变化。
- **在同一回合里等报告**：派发立刻返回。结束回合；报告会作为新消息到达。
- **用同样的上下文重试**：子会话会产出同样的失败。说清哪里错了、必须改什么。
- **直接读凭据文件**：token 会进入对话。用包装脚本。
- **不打招呼就合并 PR**：squash-and-merge 是用户的决定。
