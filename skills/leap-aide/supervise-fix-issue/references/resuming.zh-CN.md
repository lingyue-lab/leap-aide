---
title: 恢复循环
---

<div align="center">

[English](resuming.md) | **中文**

</div>

# 恢复循环

对话不是状态。压缩、重启或子会话崩溃会丢掉讨论内容，但循环什么都不会丢，因为它的状态是一个文件：

```
<workspace>/.leap-aide/supervise/state.json
```

恢复运行后，先读它，再做任何别的事。

## 恢复时的读取

```
python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" show
```

一个 JSON 对象回答：

| 字段 | 回答什么 |
|---|---|
| `phase` | 当前是哪个阶段。 |
| `attempts` | 每个阶段用了几轮，对照 `max_attempts` 是多少。 |
| `branch` / `pr` | 分支和 pull request 是否已经存在。 |
| `status` | `running`、`done`，还是已经交还。 |
| `rulings` | 上一个上下文做过的每个决定，以及错了要付什么代价。 |
| `history` | 最近 200 个事件，最新的在最后。 |

然后读 issue 的评论。状态文件说循环决定了什么，评论说子代理实际报告了什么。两者都要 —— 状态文件可能声称某个阶段通过了，而本该记录它的那条评论根本没发出去。

## 要重建什么

1. **方案。** 在 issue 上找到 planner 的评论，逐字复制进下一次派发。压缩之后绝不凭记忆重写方案；一次转述就会悄悄改变 reviewer 要拿来打分的需求。
2. **finding。** 修复轮要把每条 open 的 finding 逐字复制。丢掉一条 finding，就是循环已经付过钱又忘了的东西。
3. **diff 范围。** 从状态里读 `pr` 和 `branch`，然后对着 pull request 确认 head SHA，而不是对着自己的记忆。
4. **ruling。** 把 `rule --list` 贴进你的下一条评论。你做过的决定是循环里最可能丢失的部分，也是人类最需要审计的部分。

## 不要重建什么

不要因为记不清某个阶段是否通过，就重跑一个已经通过的阶段。状态文件是权威，你对它的记忆不是。

不要对一个已经是 `running` 的状态重跑 preflight —— 不带 `--force` 的 re-init 会特意复用已有状态，好让凭据不在循环中途被重新收集。

不要重置尝试计数。用尽的阶段在恢复之后依然用尽；如果循环能重置自己的计数，这个上限就不是上限了。

## 在一轮中途恢复

如果你是在 implementer 报告之后、reviewer 派发之前恢复的，状态里这个阶段仍然是 `implement`。发一条评论说明你恢复了、接下来要派什么，好让 issue 的时间线显示出这个空档。然后带着方案、finding 和 diff 文件派评审 —— 全部从 issue 和状态里读，没有一样来自记忆。

## 状态文件没了怎么办

如果 `state.json` 不存在，但 issue 上有评论和 pull request，说明循环在 `init` 之前就被打断了，或者 workspace 被清空了。靠读 issue 重建，然后带 `--force` 执行 `init`。尝试计数会重新开始 —— 要在评论里说明，因为之前的轮次不再计入，人类应该知道这个上限被重置了。
