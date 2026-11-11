---
title: 修复循环
---

<div align="center">

[English](fix-loop.md) | **中文**

</div>

# 修复循环

某个阶段带回 finding 时，循环不会重来一遍，而是进入一轮修复：implementer 处理这些 finding，re-reviewer 拿修复 diff 对照 finding 清单复核。只有 reviewer 通过、轮次用尽，或者 supervisor 判定这个 issue 超出可修范围，循环才停。

## 轮次上限

每个阶段有 `max_attempts` 轮，默认 3。计数是机械的 —— `supervise_state.py attempt --phase PHASE` 会自增并报告 `exhausted`。不要在对话里心算：压缩过的上下文会丢掉计数，把一个已经用完轮次的阶段再跑一遍。

review 阶段是那个要紧的例外。评审里出现 finding 是常态，所以一次返回 `request_changes` 的评审不是失败的评审，而是产出 finding 的一轮。记一轮、送去修、复评。

## 熔断裁决

轮次用尽时，不要直接把 issue 甩回给用户。要裁决：

1. 读完所有仍然 open 的 finding。
2. 判定剩下的工作是修复（再来一轮）、设计问题（问用户），还是环境限制（报告后停止）。
3. 动手之前，先把决定记成一条 ruling。

轮次用尽说明循环没收敛，不说明这个 issue 修不了。要说清是哪一种。

## 要裁决，不要卡住

Ruling 是你替自己做的决定，因为等下去比决定更贵。每条 ruling 都进台账：

```
python3 "${HERMES_SKILL_DIR}/scripts/supervise_state.py" rule \
  --what "kept the 4 fixed phases" \
  --why "issue #57 names them" \
  --cost "a fifth phase would stall the loop"
```

`--why` 是必填的：没有理由的 ruling 就是猜测，脚本会拒绝。`--cost` 说明你错了会付出什么代价，这正是压缩之后台账还能被复审的原因。

用 `rule --list` 读回台账。摘要标题是 `Rulings I made` —— 贴进你的收尾评论里，让人类评审者看到循环替他做过的每一个决定。

任何卡顿最多四个停止条件：只有用户能回答的问题、只有用户能做的决定、循环没有的凭据、跑不起测试的环境。其余都是你自己能拿的 ruling。

## 无法核实

只靠 diff 搞不定的 finding 既不是通过也不是失败，而是一个 open 项。reviewer 把它放进 `cannot_verify`；你通过自己读代码、派一次窄范围核查，或者说明理由把它划出范围来解决。

绝不允许未解决的 `cannot_verify` 混进通过。没核实过的需求就是没核实过的需求，压着未决项放行的循环已经不再监督了。

## 延后的 minor

minor 和 nit 不阻塞通过，但不是免费的。把每条延后的 minor 记成一条带 `--cost` 的 ruling，好让收尾评论能列出哪些是有意识地留下的。悄悄丢掉一条 minor finding，正是代码库积累出评审本该拦下的损伤的方式。

## 循环怎么结束

| 结束方式 | 何时 |
|---|---|
| LGTM | reviewer 通过并发表了评论。只有这时 issue 才算完成。 |
| 交还 | 轮次用尽，且裁决结果是必须由用户决定。 |
| 阻塞 | 凭据、环境或权限问题，循环解决不了。 |
| 放弃 | 判定这个 issue 不是本循环该尝试的修复。 |

绝不自己合并自己的 pull request，绝不替用户写 LGTM。外部完成度才是这个"通过"有意义的原因。
