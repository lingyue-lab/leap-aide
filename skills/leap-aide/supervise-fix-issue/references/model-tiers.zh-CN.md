---
title: 模型分档
---

<div align="center">

[English](model-tiers.md) | **中文**

</div>

# 模型分档

循环的每个阶段都按该阶段要干的活来选模型，而不是按会话碰巧在跑的那个模型。一个把 diff 从头读到尾的 reviewer，干的活和一个跑测试套件报数字的 tester 不是一回事，给它们定同一个价，要么浪费前者要么浪费后者。

## 分档

| 阶段 | 档位 | 为什么 |
|---|---|---|
| `plan` | 中到高 | 方案是后面所有阶段拿来打分的需求。一个弱方案会弱两次 —— 一次坑 implementer，一次坑 reviewer。 |
| `implement` | 中到高 | 按规格写出正确的代码是循环里最贵的部分。这不是省钱的地方。 |
| `test` | 低到中 | 找出项目自己的测试命令、跑一遍、报出数字。机械活，成功标准清楚。 |
| `review` | 高 | 通读整份 diff、判定规格符合度、校准严重度，这是决定循环出不出货的那个席位。这里省下来，评审就成了橡皮图章。 |

修复轮的复评只对着一小份 diff 和一份 finding 清单，所以它用 reviewer 档位就够，不需要更强的。

## 给阶段传模型

派发模板里带一个 `model` 字段：

```
Subagent (general-purpose):
  description: "Review the pull request for [ISSUE_URL]"
  role: "leaf"
  model: [MODEL — the reviewer tier, see references/model-tiers.md]
```

把 `[MODEL]` 填成这个部署确实有的模型 id，然后删掉括号里的说明。`delegate_task` 一次调用一个 `model`，应用到该次调用里的每个子代理 —— 这正好是一个阶段需要的粒度，因为一个阶段就是一次派发。

不填 `model`，子代理就继承会话的模型。当会话本来就跑在你会选的那一档上，这是正确选择；但它不是跳过这个决定的办法。

## 核对模型 id 存在

不在目录里的 id 不会大声失败 —— 子代理只会跑在部署的回退模型上，分档就悄悄白做了。派发之前先核对：

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

目录是随包发布的基线；部署可能通过实时查询暴露更多模型。查不到要当作"去问这个部署"，而不是"编一个 id"。

## 分档不可用的时候

按子代理分模型是 LeapAide 的兼容垫片，不是 SDK 的特性：`backend/app/compat/delegate_model.py` 把 `model` 属性注入工具 schema、在注册表的参数白名单丢掉它之前截获、再在选型时叠加到 delegation 配置的副本上。

supervisor 必须知道两个后果：

- **垫片可能不在。** 如果服务启动时没装上它 —— SDK 导入失败、补丁没生效 —— `model` 字段会被静默忽略，所有子代理都跑会话的模型。查服务日志里有没有 `per-child model routing patch installed`。
- **按调用分，不按任务分。** 一个 `model` 覆盖一次 `delegate_task` 调用里的全部子代理。别想在一批派发里给不同任务用不同模型；分开派发各阶段 —— 循环本来就是这么做的。

如果分档不可用而它又重要，在 issue 上发评论说明，并记成一条 ruling。一个假装分过档其实没分的循环，它的成本报告就是编的。

## 这改变不了什么

模型选择不改变契约。reviewer 依然不派子代理，implementer 依然不评审自己的工作，LGTM 依然只来自一次通过的评审。一个更便宜的模型只会让这些规则更难遵守，绝不会让它们变成可选的。
