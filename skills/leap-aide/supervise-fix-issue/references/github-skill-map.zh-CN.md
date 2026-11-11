---
title: GitHub 技能映射表
---

<div align="center">

[English](github-skill-map.md) | **中文**

</div>

# GitHub 技能映射表

Hermes Agent SDK 在 `skills/github/` 下自带一组 GitHub 技能。supervise 循环借用其中负责 GitHub 操作的技能，并且不给 SDK 增加任何东西：循环只是把借用来的技能本就期待的**环境变量**安排好。

## 自带技能一览

| 技能 | 作用 | 循环是否借用 |
|---|---|---|
| `github-auth` | 探测 GitHub 的认证方式；导出 `GITHUB_TOKEN`、`GH_USER`、`GH_OWNER`、`GH_REPO`。 | 借用——循环的环境变量就能满足它的探测。 |
| `github-issues` | 读取、创建、分诊和打标签 issue。 | 借用——planner 和 implementer 通过它工作。 |
| `github-pr-workflow` | 建分支、提交、开 PR、跟进 CI、合并。 | 借用——implementer 用它开和更新 PR。 |
| `github-code-review` | 读 PR 的 diff，发行内评审意见。 | 借用——reviewer 用它读 diff、发评审意见。 |
| `github-repo-management` | 克隆、创建、fork，管理远端和发布。 | 不借用——工作区由用户克隆并添加。 |
| `github-issue-to-pr` | 面向单个 agent 的完整 issue 到 PR 流程纪律。 | 不借用——它与循环职责重叠。 |
| `codebase-inspection` | 用 `pygount` 统计代码行数和语言占比。 | 不借用——没有哪个阶段需要它。 |

## 循环借用了什么

### `github-auth`

它的 `scripts/gh-env.sh` 是其他所有 GitHub 技能搞清"自己在跟谁说话"的入口。它按下面的顺序探测认证：

```bash
if command -v gh &>/dev/null && gh auth status &>/dev/null 2>&1; then
    GH_AUTH_METHOD="gh"
    GH_USER=$(gh api user --jq '.login' 2>/dev/null)
elif [ -n "$GITHUB_TOKEN" ]; then
    GH_AUTH_METHOD="curl"
```

只要环境里存在 `GH_TOKEN`，`gh auth status` 就会成功，第一个分支就会命中。因此循环根本不必改动这个脚本：它只需要导出变量。

### `github-issues`

planner 读 issue 并发方案；implementer 发实现报告。两者都用该技能自带的 issue 操作方式，配合角色自己的凭据，因此评论可追溯到具体角色。

### `github-pr-workflow`

implementer 用这个技能的约定建分支、提交、推送并开 PR——规范化的提交标题、正文里关联 issue。

### `github-code-review`

reviewer 用它对着默认分支读 diff，并把评审意见作为 PR 评论发出去。

## 环境变量约定

循环为每一条以角色身份执行的命令导出三个变量。这些变量只由 `gh_roles.py` 设置，循环里没有别的地方碰它们：

```bash
GH_TOKEN=<role token>
GITHUB_TOKEN=<role token>
GH_CONFIG_DIR=<workspace>/.leap-aide/secrets/github/gh/<role>
```

每个变量各司其职：

- **`GH_TOKEN`** 是 `gh` 自身用来认证的变量。`gh` 优先使用它，而不是任何已保存的登录态。
- **`GITHUB_TOKEN`** 是自带技能查找的变量。`gh auth status` 不成功时 `gh-env.sh` 会退回到它，`github-code-review` 也直接读它。
- **`GH_CONFIG_DIR`** 把 `gh` 指向这个角色私有的目录。`gh auth login` 会写在那里，而不是写进用户自己的 `~/.config/gh`，所以循环绝不会覆盖人类已有的登录态。

因为 `gh auth status` 会在 `GH_TOKEN` 存在时成功，借用来的技能会命中第一个分支并报出角色的身份——`GH_USER` 是那个角色的账号，而不是人类的那一个。

## 循环没有借用什么

### `github-issue-to-pr`

它解决的是和循环同一个问题，只是面向单个 agent：读 issue、扫重复、实现、证明回归测试真的会咬、开 PR、盯 CI。在 Implement 阶段里跑它，会给 implementer 一份相互冲突的第二套方案；在循环外层跑它，则是在重复这个循环本身。

循环把其中可复用的部分——重复扫描、验收标准、CI 纪律——写成阶段上下文里的指令，机制则向三个更专注的技能借用。

### `github-repo-management`

循环从不克隆、fork 或创建仓库。工作区在循环开始前由用户准备好，预检会检查远端与 issue 是否一致。

### `codebase-inspection`

代码行数和语言占比无法决定某个阶段是否通过。
