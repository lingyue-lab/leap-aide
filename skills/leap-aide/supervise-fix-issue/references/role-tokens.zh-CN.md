---
title: 角色令牌
---

<div align="center">

[English](role-tokens.md) | **中文**

</div>

# 角色令牌

supervise 循环为它的五个角色各自配一份 GitHub 凭据。这样带来两点：issue 上的评论能看出是哪个阶段写的；某个角色出了岔子，也无法冒充 supervisor 去操作。

## 五个角色

| 角色 | 在 GitHub 上写什么 | 需要什么权限 |
|---|---|---|
| `supervisor` | 方案、各阶段结论、收尾总结（发在 issue 上）。 | Issues：写。Pull requests：读。 |
| `planner` | 实现方案，作为 issue 评论。 | Issues：写。 |
| `implementer` | 分支、提交、PR、实现报告。 | Issues：写。Pull requests：写。Contents：写。 |
| `tester` | PR 上的测试报告。 | Pull requests：写。Contents：读。 |
| `reviewer` | 评审结论及其行内意见。 | Pull requests：写。Contents：读。 |

预检会在循环开始前检查全部五个角色：某个角色没有可用凭据算错误，不算警告。

## 配置文件

凭据放在工作区里一个被 git 忽略的目录下：

```
<workspace>/.leap-aide/secrets/github/config.yaml    (mode 0600)
<workspace>/.leap-aide/.gitignore                     (ignores the whole tree)
```

`gh_roles.py init` 会写出这两个文件。除非显式传 `--force`，它绝不会覆盖已有配置。

该文件是一个 YAML 映射，每个角色一个条目：

```yaml
version: 1
roles:
  supervisor:
    kind: pat
    token: "ghp_..."
  implementer:
    kind: app
    private_key_path: /abs/path/to/app.pem
    app_id: "000000"
    installation_id: "00000000"
```

`kind` 决定凭据的取得方式：`pat` 读取 `token` 字段，`app` 则用 App 的字段现场签发 installation token。

## 个人访问令牌

经典 PAT 需要 `repo` 权限范围，它覆盖 issue、PR 以及向私有仓库推送。只有当仓库位于启用了 SSO 的组织下时，才需要再加 `read:org`。

细粒度 PAT 更窄、也更容易吊销。只授权循环要处理的那一个仓库，并给出这些权限：

| 角色要做的事 | 仓库权限 |
|---|---|
| 在 issue 上评论 | Issues：读和写 |
| 在 PR 上评论或批准 | Pull requests：读和写 |
| 推送分支 | Contents：读和写 |

Metadata 权限始终授予，不必列出。

## GitHub App 安装

当多人共用同一个仓库时，App 是更好的凭据：installation token 有效期很短，App 自身的权限就是上限，吊销安装即吊销全部 token。

角色切换到 App 的方式是设 `kind: app` 并填三个字段：

| 字段 | 从哪里取 |
|---|---|
| `private_key_path` | App 设置页的 "Private key" → "Generate a private key"。把 `.pem` 放在仓库之外，权限 0600。 |
| `app_id` | 同一个设置页上的 "App ID"。 |
| `installation_id` | App 设置页的 "Installations"，或 `gh token installations --key <pem> --app-id <id>`。 |

token 本身从不落盘。它按需签发——每次以该角色执行命令时签一次：

```bash
gh token generate --key <pem> --app-id <app_id> --installation-id <installation_id> --token-only
```

`gh-token` 扩展随 LeapAide 运行时镜像一起提供。installation token 有效期一小时，足够跑完一个阶段，又足够短，泄露的 token 自己就会过期。

## 凭据如何到达 GitHub

包装脚本把凭据注入它所执行命令的环境变量，仅此一处：

```bash
GH_TOKEN=<role token>
GITHUB_TOKEN=<role token>
GH_CONFIG_DIR=<workspace>/.leap-aide/secrets/github/gh/<role>
GH_ROLE=<role name>
```

`GH_TOKEN` 是 `gh` 自身用来认证的变量，`GITHUB_TOKEN` 是自带 GitHub 技能查找的变量，`GH_CONFIG_DIR` 让每个角色的 `gh` 状态留在各自独立的目录里，`GH_ROLE` 则告诉子会话自己正在扮演哪个角色。token 不会被打印，不会写进对话，也不会存进 agent 能读回来的文件。

`gh_roles.py show` 会把配置打印出来，其中的机密全部替换为占位符。它是在对话中查看该文件的唯一方式：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/gh_roles.py" show
```

## 轮换

轮换凭据就是改配置文件：没有任何缓存，下一条命令就会用上新值。

PAT 的话，把新 token 粘进该角色的 `token:` 字段，再重跑 doctor。App 的话，只要安装还在，配置无需改动——token 是每条命令现签的；当 App 的私钥重新生成后，把 `private_key_path` 指向新的 `.pem` 文件即可。

```bash
python3 "${HERMES_SKILL_DIR}/scripts/gh_roles.py" doctor
```

doctor 会拿每个角色去 GitHub 验证，并为每次失败打印该打开哪个页面、该勾选哪项权限。

## 规则

- 凭据绝不进入模型上下文。使用 `gh_roles.py run --role <ROLE> -- <command>`；绝不读取配置文件，绝不回显 token，绝不把 token 粘进消息、提交或 PR 正文。
- 配置文件留在 `.leap-aide/` 下，权限 0600，目录被 git 忽略。token 一旦进了提交就是泄露的 token。
- 容器自身环境变量里的 `GH_TOKEN` 是另一回事：它属于运行 LeapAide 的人，不属于任何角色。角色命令一律经过包装脚本执行，而包装脚本会覆盖它。
- 五个角色，五种身份。多个角色可以共用同一个 token，但那样每条评论都是同一个署名，循环存在的归因意义也就没了。
