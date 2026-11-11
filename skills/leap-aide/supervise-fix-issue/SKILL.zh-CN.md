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

## 为什么这个循环是一个技能

一个工作流引擎能给这个循环一张强制执行的依赖图、按角色隔离的 profile、以及不依赖任何人去读文件的恢复能力。它同时也会给项目带来第二套要维护的运行时、一门要学的 DSL，以及一个必须和 Hermes Agent SDK 就"什么是一次会话"达成一致的调度器。

这个循环只保留 SDK 作为唯一运行时。各阶段是普通的 `delegate_task` 调用，状态是 workspace 里的一个 JSON 文件，发生过什么的记录就是 issue 自己的评论串。这样做的代价写在 [ADR-003](../../../docs/architecture.zh-CN.md) 里：没有强制的 DAG、没有按角色隔离的 profile、没有跨会话恢复。补偿手段是一本裁定台账、一个有界的修复循环和一套恢复流程——三者都是下面的章节。

核心原则：**你监督的是决定，不是文字。** 你读某个阶段做了什么决定，你对着仓库核实这个决定，而当你自己做出决定时，你把它记录下来。阶段的报告是一份主张，绝不是结论。

## 使用时机

当用户要求把一个 issue 从头到尾修完——规划、实现、测试、评审，并提交成一个 pull request——就使用这个技能。

以下情况不要用它：

- 只是关于仓库的一个问题——直接回答。
- 变更已经被完全规定、不需要规划——自己做。
- 工作没有在 GitHub 上跟踪——这个循环没有可以跑的通道。

## 要裁决，不要卡住

大多数看起来像卡住的情况，其实是一个你还没做的决定。当循环停止推进时，最多取下面四个停止条件，一个都不许多：

1. 只有用户能回答的问题。
2. 只有用户能做的决定。
3. 循环没有的凭据。
4. 跑不起测试的环境。

其余的一切——范围问题、设计偏好、某个文件超出了它的预设规模——都是你自己能拿的 ruling。动手之前先记录下来：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" rule \
  --what "kept the 4 fixed phases" \
  --why "issue #57 names them" \
  --cost "a fifth phase would stall the loop"
```

`--why` 是必填的；没有理由的 ruling 就是猜测。用 `rule --list` 读回台账，把 `Rulings I made` 那一段贴进你的收尾评论，让人类评审者能审计循环替他做过的每一个决定。完整的纪律——轮次上限、熔断裁决、延后的 minor 以及循环如何结束——在[修复循环](references/fix-loop.zh-CN.md)里。

## 前置条件

循环需要两样**不会**替你创建的东西：一个装着仓库的 workspace，以及一套按角色划分的 GitHub 凭据。派发任何东西之前先检查这两样——准备了一半的 workspace 会白烧掉一整轮委派。

### workspace

当前会话的 workspace 必须是一个带 GitHub 远端的 git 仓库，而且远端要和这个 issue 对得上。循环开始之前，仓库必须先被克隆并作为 workspace 加进 Work 面板；这个技能从不克隆任何东西。

### 凭据

每个角色各自认证，所以某个阶段留下的评论可以归到那个角色名下。凭据住在 workspace 里，从不进这段对话：

```
<workspace>/.leap-aide/secrets/github/config.yaml    (mode 0600)
<workspace>/.leap-aide/.gitignore                     (ignores the whole tree)
```

只能通过包装脚本读取它们。绝不要打开配置文件，绝不要把 token 粘进消息，绝不要把 token 写进 `.leap-aide/` 之外的任何文件。

每个角色需要哪些权限、个人访问令牌和 GitHub App 安装有什么区别、以及如何轮换凭据，都写在[角色令牌](references/role-tokens.zh-CN.md)里。

## 模型选择

每个阶段都按该阶段要干的活来选模型——方案和实现用能力强的，测试跑用便宜的，评审用最强的，因为评审就是决定这个循环出不出货的那个席位。

`prompts/` 里的派发模板各自带一个 `model` 字段。把它填成这个部署确实有的 id，而且先对着目录核对这个 id；不在目录里的 id 不会报错，它会静默地跑在会话的模型上，分档就白做了。按角色的分档、`delegate_task` 如何一次调用一个模型、以及部署没有按子代理路由时怎么办，都在[模型分档](references/model-tiers.zh-CN.md)里。

只有当会话本来就跑在你会选的那一档上时，才把 `model` 留空。

## 流程

### 第 0 步 —— 预检

跑预检。它会核对仓库、远端、工作树、`gh` CLI 以及每一个角色凭据，并打印一份 JSON 报告：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/preflight.py" --issue "<ISSUE_URL>"
```

当 `ok` 为 false 时，停下来把 `errors` 数组展示给用户。每条错误都带一个 `fix` 字符串，写明到底该做什么——比如"workspace 不是 git 仓库"或"GitHub 拒绝了 `planner` 这个 token"。预检失败就不要启动循环；循环跑到一半才发现缺 token，意味着某个阶段没法汇报，循环会卡在一个永远完不成的阶段上。

当凭据文件本身缺失时，创建模板并告诉用户要填哪些值：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/gh_roles.py" init
```

`init` 会写入模板和 `.gitignore`；除非传了 `--force`，它绝不会覆盖已有的配置。用户填好 token 后重跑 `gh_roles.py doctor`，它会逐个角色对 GitHub 校验，并对每一次失败打印分步指引——打开哪个页面、勾哪个权限。

警告（工作树不干净、workspace 是仓库根的子目录）不是致命的，但要在开始之前报告出来。

### 第 1 步 —— 读 issue

用 supervisor 自己的凭据取回 issue 并完整读一遍——正文、标签和每一条评论：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/gh_roles.py" run --role supervisor -- \
  gh issue view "<ISSUE_URL>" --comments
```

如果 issue 上已经带了方案（一条写着实现步骤的评论），就把那条评论当作 planner 的产出，跳过 Plan 阶段，直接进 Implement。当方案已经由人写好时，这是常见情况。

记录循环状态：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" init \
  --issue "<ISSUE_URL>" --repo "OWNER/NAME" --issue-number <N>
```

### 第 2 步 —— 架设常驻目标

循环横跨多个回合：你派出一个阶段，子会话工作时对话安静下来，报告稍后作为一条新消息到达。一个常驻目标让会话在这段空档里活着——WebUI 在每个回合结束时重新评估它，目标尚未达成时就排下一个回合。

```bash
python3 "${HERMES_SKILL_DIR}/scripts/arm_goal.py" arm \
  --text "Drive issue #<N> to an LGTM'd pull request: plan, implement, test, review." \
  --outcome "The pull request for issue #<N> carries an LGTM comment." \
  --verification "gh pr view <PR> shows a review comment containing LGTM." \
  --stop-when "The pull request has an LGTM, or a phase exhausts its attempts."
```

目标是循环的心跳，不是它的驱动力：它让会话在阶段之间活着。它从不推进阶段——只有阶段报告才能推进。

### 第 3 步 —— 跑循环

```
Plan ──→ Implement ──→ Test ──→ Review ──→ LGTM
  ↑         ↑           ↑         ↑
  └─────────┴───────────┴─────────┘   a failed phase goes back to the phase that owns the failure
```

每个阶段就是一次 `delegate_task` 调用。从顶层会话发起的委派总是以后台方式运行：调用立即返回一张派发回执，子会话的报告稍后作为一条新消息到达并开启你的下一个回合。派发之后就结束你的回合，用一行话说明你派了哪个阶段。

#### 派发一个阶段

派发之前，从 `prompts/` 读取该阶段的派发模板并填好每一个占位符。模板是一个文件，不是你凭记忆重建的东西——压缩过的上下文会转述这些契约，而被转述过的契约是子代理不会遵守的契约。

```
prompts/planner.md          prompts/planner.zh-CN.md
prompts/implementer.md      prompts/implementer.zh-CN.md
prompts/tester.md           prompts/tester.zh-CN.md
prompts/reviewer.md         prompts/reviewer.zh-CN.md
prompts/re-reviewer.md      prompts/re-reviewer.zh-CN.md
```

永远传 `role="leaf"`。叶子子代理根本没有 `delegate_task` 工具，所以树始终只有一层深，子会话永远生不出子子会话。

先读该阶段的 schema 并把它作为 `output_schema` 传入，这样报告会被机器校验，格式不对的报告会在到达你之前被 SDK 重试一次：

```bash
# Read the schema first — its path is listed among this skill's supporting files.
cat "${HERMES_SKILL_DIR}/schemas/plan.schema.json"
```

然后委派：

```
delegate_task(
  goal="<one self-contained sentence: what this phase must produce>",
  context="<the filled dispatch template, verbatim>",
  role="leaf",
  output_schema=<the contents of the phase schema file>,
  model="<the tier's model id, when routing is available>"
)
```

`context` 是子代理对这个世界唯一的认知。它必须包含：

- 技能目录的绝对路径（`${HERMES_SKILL_DIR}`）和角色名，好让子代理能运行凭据包装脚本。
- issue URL，以及 `OWNER/NAME` 形式的仓库名。
- 要在哪个分支上工作——对那些会碰代码的阶段而言。
- 任务本身，详细到子代理不需要其他上下文。
- 汇报契约：用角色凭据发一条评论，然后回答 schema 所描述的那个 JSON 对象。

把这个汇报模板逐字交给子代理——正是它让这个循环得以运转：

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

| 阶段 | 角色 | Schema | 子代理产出什么 |
|---|---|---|---|
| Plan | `planner` | `schemas/plan.schema.json` | 实现方案，发在 issue 上。 |
| Implement | `implementer` | `schemas/implementer.schema.json` | 分支上的改动，已提交并推送，外加一个 pull request。 |
| Test | `tester` | `schemas/tester.schema.json` | 测试运行及其结果，发在 issue 或 pull request 上。 |
| Review | `reviewer` | `schemas/reviewer.schema.json` | 评审结论，发在 pull request 上。 |

**Plan.** 把 issue 正文、仓库结构和验收标准交给 planner 委派。planner 把方案作为 issue 评论发出来，并按 schema 作答。如果 issue 上已经有方案，跳过这个阶段。

**Implement.** implementer 在分支 `fix/issue-<N>` 上工作——该分支不存在就从默认分支创建，且绝不碰默认分支。implementer 的提交信息里带 `refs #<N>`，推送分支，对着默认分支打开或更新 pull request，并把报告发在 issue 上。记录分支和 pull request 编号：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" set-branch --name "fix/issue-<N>"
python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" set-pr --number <PR> --url "<PR_URL>"
```

**Test.** tester 跑项目自己的测试命令——从仓库的文档里读出来，而不是猜——并报告每一个用例的预期结果和实际结果。tester 必须分清由这次改动引起的失败和本来就存在的失败，并说明哪个是哪个；本来就是红的测试套件不算一个失败的阶段。

**Review.** reviewer 通读对着默认分支的 diff，并把 finding 发在 pull request 上。**只有当 reviewer 的结论是 `approve` 时才发 LGTM。** 决定这个结论的严重度校准——`blocker` 和 `major` 意味着 `request_changes`，`minor` 和 `nit` 不意味着，而一条未解决的 `cannot_verify` 意味着 `request_changes`——是 reviewer 派发模板的一部分。一个 `request_changes` 结论会开启一轮修复。

#### 修复轮

一个要求修改的评审不是失败的评审。它开启一轮修复：

1. 记下这次评审的轮次：`supervise_state.py attempt --phase review`。
2. 把 finding 逐字交给 implementer，作为一次新的派发，让它推到同一个分支上。
3. 生成修复 diff 包，好让 re-reviewer 读一个文件就够，不必自己去拼范围：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/review_package.py" --base <FIX_BASE> --head <HEAD>
```

4. 派发 `prompts/re-reviewer.md`，带上方案、finding 和打印出来的 diff 路径。re-reviewer 会把每条 finding 判成 `ADDRESSED` 或 `NOT ADDRESSED`，并报告修复本身弄坏了什么。

每一条仍然 open 的 finding、每一条延后的 minor、以及每一条你自己解决掉的 `cannot_verify`，都要变成一条 ruling。不要让某条 finding 因为持有它的上下文被压缩掉而凭空消失。

#### 判定结果

当一份阶段报告到达时，用三个信号来判断——JSON、评论，以及仓库：

1. **JSON。** SDK 会拿它对着 schema 校验，失败时重试一次。如果 `schema_valid` 为 false，就把该阶段当作失败并重新派发。
2. **评论。** 检查这个阶段是否真的留下了评论。没有评论的报告意味着这个阶段没有完成它的工作，哪怕 JSON 看起来是对的：

   ```bash
   python3 "${HERMES_SKILL_DIR}/scripts/gh_roles.py" run --role supervisor -- \
     gh api "repos/OWNER/NAME/issues/<N>/comments" --jq '.[-1] | .body'
   ```

3. **仓库。** 对 Implement 而言，确认分支存在、提交在它上面、pull request 是开着的：

   ```bash
   python3 "${HERMES_SKILL_DIR}/scripts/gh_roles.py" run --role supervisor -- \
     gh pr view <PR> --json state,headRefName,mergeable
   ```

当 JSON 这么说、它的评论在 GitHub 上、而且——对代码阶段而言——仓库也同意时，这个阶段就算通过了。然后记录它并继续：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" advance --phase <PHASE>
```

一份返回 `DONE_WITH_CONCERNS`、`BLOCKED` 或 `NEEDS_CONTEXT` 的报告既不是通过，也不是 schema 失败。每一种各是什么意思、你该怎么处理，写在[处理汇报](references/handling-reports.zh-CN.md)里。

#### 重试一个失败的阶段

派发之前先记一次尝试，并检查它返回的计数：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" attempt --phase <PHASE>
```

当尝试次数低于上限（默认三次）时，把失败作为上下文重新派发**同一个**阶段——上一份报告、失败的输出，以及必须改变的东西。要在 `context` 里明说上一次尝试错在哪里、这次必须怎么做不一样。用相同上下文重试，只会重复相同的失败。

达到上限时，不要直接把 issue 甩回给用户。先裁决：读完所有仍然 open 的 finding，判定剩下的是再来一轮修复、问用户的问题，还是环境限制，然后把这个决定记成一条 ruling。之后才向用户报告——哪个阶段失败了、每次尝试产出了什么、你裁决了什么、以及人类还需要决定什么。完整的裁决流程在[修复循环](references/fix-loop.zh-CN.md)里。

一个失败的阶段回到拥有该失败的阶段：

| 失败 | 回到 |
|---|---|
| 方案没有覆盖这个 issue | Plan |
| 构建失败、验收标准未达成 | Implement |
| 测试套件因这次改动而失败 | Implement |
| 评审要求修改 | Implement（修复轮） |
| reviewer 读不到 diff，或缺少凭据 | 阻塞——报告给用户 |

### 第 4 步 —— 收尾

当 pull request 带上 LGTM 时，循环就完成了。亲自核实，而不是相信报告：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/gh_roles.py" run --role supervisor -- \
  gh api "repos/OWNER/NAME/issues/<PR>/comments" \
  --jq '[.[] | select(.body | test("LGTM"))] | length'
```

然后：

1. 清掉常驻目标——它已经完成了自己的工作，不该让会话继续空转：

   ```bash
   python3 "${HERMES_SKILL_DIR}/scripts/arm_goal.py" clear
   ```

2. 标记循环结束：

   ```bash
   python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" finish
   ```

3. 给用户做总结：方案、改动的文件、测试结果、评审结论、你做过的裁定，以及 pull request 的 URL。把交付物当作文件交出去，而不是当作聊天散文——`scripts/brief.py` 会替你写好收尾简报：

   ```bash
   python3 "${HERMES_SKILL_DIR}/scripts/brief.py" --out <PATH>
   ```

4. 询问是否要 squash-and-merge 这个 pull request。不要自己合并——合并是用户的决定。

## 压缩之后恢复

对话不是状态。压缩、重启或子会话崩溃会丢掉讨论内容，但循环什么都不会丢，因为它的状态放在 `<workspace>/.leap-aide/supervise/state.json` 里。

恢复运行之后，先读它，再做任何别的事：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" show
```

然后读 issue 的评论：状态文件说循环决定了什么，评论说子代理实际报告了什么。从这两个来源重建方案、仍然 open 的 finding 和 diff 范围——绝不凭记忆——并把 `rule --list` 贴进你的下一条评论，好让上一个上下文做过的决定也能挺过这次压缩。完整的恢复流程，包括哪些东西不该重建，写在[恢复循环](references/resuming.zh-CN.md)里。

## 规则

- **只委派一层。** 每次 `delegate_task` 调用都传 `role="leaf"`。叶子子代理没有 `delegate_task` 工具，所以这棵树既不会横向长也不会向下长。
- **默认分支只读。** 所有代码工作都在 `fix/issue-<N>` 上进行。提交带 `refs #<N>`。
- **只有 approve 结论才产生 LGTM。** 绝不替用户写 LGTM，也绝不让一个没有通过的阶段产生它。
- **issue 和 pull request 是通道。** 每个阶段都留下评论。不评论的阶段就是没汇报。
- **凭据绝不进上下文。** 用 `gh_roles.py run --role <ROLE> -- <command>`。绝不读配置文件，绝不回显 token，绝不把它粘进消息或提交。
- **循环是有界的。** 每个阶段三次尝试。用完之后，先裁决、记一条 ruling，再把问题交还给用户。
- **从文件派发，不从记忆派发。** 读 `prompts/<phase>.md` 并填好它。被转述过的契约是子代理不会遵守的契约。
- **每个决定都要记录。** 没有 `--why` 的 ruling 就是猜测；脚本拒绝存储它。

## 常见的自我合理化

| 合理化 | 实际代价 |
|---|---|
| "报告看起来没问题，我跳过检查评论吧。" | 没发评论的阶段没有完成它的工作。JSON 是子代理对自己工作的看法。 |
| "这点小东西我自己改了算了。" | 你现在就是 implementer，而 reviewer 会拿你自己的上下文给你自己的编辑打分。派出去。 |
| "reviewer 说是 minor，丢掉没关系。" | 延后的 minor 会累积。把每一条都记成带 `--cost` 的 ruling，好让收尾简报列出来。 |
| "我重跑一遍这个阶段吧，记不清它过没过。" | 状态文件是权威。重跑一个已通过的阶段会烧掉一次尝试并改写记录。 |
| "我用同样的上下文重试吧，刚才是意外。" | 相同的上下文产生相同的失败。说清错在哪、必须改什么。 |
| "模型太贵了，我把 `model` 留空吧。" | 分档什么也没做，评审席位跑在会话跑的模型上。要有意识地决定档位。 |
| "我直接合并吧，用户要的就是把 issue 修了。" | 合并是用户的决定。外部完成度才是这个"通过"有意义的原因。 |

## 陷阱

- **不预检就启动循环**：缺失的 token 会表现为一个永远不汇报的阶段，循环会卡住而不是响亮地失败。
- **给 `delegate_task` 传 `background`**：这个参数已被弃用并被忽略——顶层委派总是后台运行。传不传都没区别。
- **在同一个回合里等报告**：派发立即返回。结束回合；报告会作为新消息到达。
- **用相同上下文重试**：子代理会产出相同的失败。说清错在哪、必须改什么。
- **直接读凭据文件**：token 会落进对话里。用包装脚本。
- **压缩之后重建方案**：一次转述会悄悄改变 reviewer 拿来打分的需求。从 issue 上复制它。
- **凭记忆派发**：读 `prompts/<phase>.md`。模板里带着对话已经不再持有的契约。
- **不问就合并 pull request**：squash-and-merge 是用户的决定。
