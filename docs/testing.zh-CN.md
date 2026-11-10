---
title: LeapAide 测试指南
---

<div align="center">

[English](testing.md) | **中文**

</div>

# LeapAide 测试指南

本指南覆盖自动化测试分层、运行方式，以及一份精简的浏览器手动冒烟清单。贡献流程见 [CONTRIBUTING.zh-CN.md](../CONTRIBUTING.zh-CN.md)。

---

## 自动化单元测试

所有单元测试由仓库根目录的 `./scripts/run-test.sh` 驱动：

| 命令 | 层级 | 范围 | 典型耗时 |
|---|---|---|---|
| `./scripts/run-test.sh smoke` | L1 | 核心回归套件 | < 5 分钟 |
| `./scripts/run-test.sh function [DOMAIN]` | L2 | 单个功能域 | 10–30 分钟 |
| `./scripts/run-test.sh full` | L3 | 全量回归 | 数小时 |
| `./scripts/run-test.sh backend [SUBCMD]` | — | 后端 pytest（`smoke` / `function [DOMAIN]` / `full` / `sprint N` / `clean` / `setup`） | — |
| `./scripts/run-test.sh frontend [SUBCMD]` | — | 前端 vitest（`smoke` / `full` / `clean` / `setup`） | 分钟级 |
| `./scripts/run-test.sh manual` | L4 | 打印浏览器手动清单 | — |
| `./scripts/run-test.sh setup` | — | 安装全部测试依赖（Python + 前端） | — |
| `./scripts/run-test.sh clean` | — | 清理陈旧测试状态与端口占用 | — |

L2 域（DOMAIN）：`session` | `model` | `workspace` | `cron` | `i18n` | `ui` | `streaming` | `auth` | `all`。

CI 会在由仓库 `Dockerfile` 构建出的 testing 镜像里跑同样的套件（`.github/workflows/run-tests-in-testing-image.yml`）：先 `backend full`，再 `frontend full`。

每次向目标为 `main` 的 ready PR 推送、以及每次向 `main` 本身的推送，都会自动运行；草稿 PR 会跳过，存在合并冲突的 PR 则完全不会运行。该检查目前还不是合并的必需项—— 它只报告结果，不阻塞合并。

常用环境变量覆盖：

- `DRY_RUN=1` —— 只打印命令不执行
- `PYTEST_EXTRA="..."` / `VITEST_EXTRA="..."` —— 给执行器追加额外参数

以 `./scripts/run-test.sh help` 的输出为准，那是最新的权威用法说明。

测试结果文件写入 `backend/tests/.results/` 与 `frontend/tests/.results/`。

---

## 双语一致性门禁

每一篇双语文档都是三个文件：

| 文件 | 作用 |
|---|---|
| `docs/foo.md` | 英文文档（检查器指向的*锚点*） |
| `docs/foo.zh-CN.md` | 中文配套文档 |
| `docs/foo.i18n.yaml` | 一致性记录：每个章节、两种语言各一个 `sha256-16` 摘要 |

`backend/scripts/check_translation_pairing.py` 负责强制这套约定。推送前先跑一遍：

```bash
# Verify one pair
python3 backend/scripts/check_translation_pairing.py docs/architecture.md
# Verify every pair that has a record
python3 backend/scripts/check_translation_pairing.py --check-all
# Check that a directory has no document missing its companion or record
python3 backend/scripts/check_translation_pairing.py --check-tree docs
python3 backend/scripts/check_translation_pairing.py --check-tree . --depth 1
# Re-record the digests after aligning BOTH language files
python3 backend/scripts/check_translation_pairing.py --write docs/architecture.md
```

门禁强制的内容：

- 两个文件都必须以 YAML frontmatter 开头，且从字节 0 开始。
- 两个文件的前 20 行内都要有语言切换器。
- 两个文件的标题结构必须一致（层级相同、顺序相同）。
- 围栏代码块在两侧必须逐字节相同 —— 代码块里是事实（端口、路径、schema），绝不能漂移。围栏里的注释在中文版也保持英文，逐字节比较才成立。
- 每个章节的散文摘要都要与记录一致。

新增一篇文档：

1. 创建 `docs/foo.md` 与 `docs/foo.zh-CN.md`，各自以 frontmatter 和语言切换器开头。
2. 标题结构完全对齐 —— 层级相同、顺序相同。
3. 围栏代码块在两侧保持逐字节相同 —— 围栏里的注释在中文版也保持英文。
4. 执行 `--write docs/foo.md` 生成记录，三个文件一起提交。

有意为之的单边修改（例如某个 typo 没有对应的中文改动），在该配对的记录里用 `waiver:` 段落声明：

```yaml
waiver:
  side: en
  reason: "English typo fix only, no semantic change"
  expires: "2026-12-31"
```

豁免只对 co-change 门禁生效 —— 摘要仍然必须对得上，所以 `--write` 依然要跑。豁免会出现在 PR 的 diff 里，而且会过期，因此不会悄悄削弱门禁。

CI 在每个触及双语配对的 PR 上运行 `--check-all`、`--check-tree docs`、`--check-tree . --depth 1` 以及 git co-change 门禁。门禁只报错、不阻塞合并 —— `main` 目前还没开分支保护。

---

## 手动冒烟测试

涉及 UI 的改动之后，手动进行如下测试：

### 会话生命周期

- **空状态**: 无会话时首次加载显示空状态。
- **创建 / 重命名 / 删除**: 用 `+` 按钮创建会话，重命名，删除。删除**最后一个（唯一）**会话后回到空状态 。
- **置顶 / 归档**: 置顶把会话移到顶部。置顶后，标题栏中的 observe 按钮可见，点击后进入VNC桌面操作浏览器并观察浏览器行为。

### 聊天与流式

- **发送与流式**: 发一条消息，消息通过 WebSocket（`/api/ws/events`）实时流式出现。工具调用卡片内联渲染、可展开/收起。
- **流式中刷新**: 响应流式输出过程中刷新页面；进行中的回合应恢复，会话不丢失。
- **审批卡片**: 在 Manual 模式下触发一个需审批的 shell 命令，审批卡片提供允许一次 / 本会话 / 拒绝选项，Agent 据此继续。

### 工作区与面板

- **工作区浏览器**: 点击标题右上角展开目录，预览文本文件，创建 + 重命名 + 删除文件。工作区是 git 仓库时 git 徽章显示分支/脏文件数。
- **Markdown 表格预览**: 打开含长 inline-code 路径的 GFM 表格 `.md`（issue #41 复现内容）。表头与单元格列宽一致、表头左对齐、边框与表头底色可见、长路径在单元格内换行、表格不超出 768px 内容列；明暗主题各验一次。
- **Markdown prose 基线**: 打开含标题、段落、列表、引用、inline code、围栏代码块、链接与分隔线的 Markdown 文件（工作区预览）以及技能的 `SKILL.md`（技能详情）。两个界面的段落间距、代码块边框/底色、链接与引用配色应一致；明暗主题各验一次。
- **终端面板**: 打开终端面板，跑一条简单命令（`echo hi`）。
- **主题与语言**: 在设置里切换主题（system / dark / light）与语言（English / 简体中文）, 刷新后两者都应保持。

### 设置

- **设置持久化**: 改一个设置，刷新，确认保留。
- **默认模型**: 设置 → 智能体行为 → 选择默认模型并保存；确认当前 profile 的 `config.yaml`（`model.default` / `model.provider`）已更新，刷新后仍保持该选择。新建对话（欢迎页首条消息与 `+` 按钮）时 footer 与会话记录使用该模型。
- **同名模型的两个来源**: 同时配置 DeepSeek 与 OpenRouter 的 key，并让 DeepSeek 处于 active；在默认模型下拉框中选 **OpenRouter** 分组的 DeepSeek 条目保存后，`config.yaml` 应为 `provider: openrouter` + `model.default: deepseek/...`；改选 DeepSeek 分组的条目则应为 `provider: deepseek` + 裸 id。两种选择新建对话后都必须调用各自 provider 的端点和 key。
- **默认模型失效**: 把 `model.default` 改成 provider 已不再提供的 id，清空模型缓存并重启。设置页与 composer footer 显示失效提示，发送仍使用该配置值，`config.yaml` 不会被自动改写。
- **删除服务商 API Key**: 把一个由某 provider 提供的模型设为默认模型，然后在 设置 → 模型服务商 中删除该 provider 的 API Key。该 provider 的模型必须立即从 composer picker 消失（不得回退到 JSON 基线）；Settings 立即显示“API Key 已删除”的专用提示；composer 仍显示配置的默认模型并标注不可用，同时给出“重新添加 key”的说明；发送时显示简短的 “Default model is not valid. Please configure it in Settings.”，而不是 SDK 的 `config.yaml` / 环境变量原始文本。若用户在 composer footer 中选择其他模型，该提示消失，并显示所选模型。
- **既有会话中删除服务商 API Key**: 先用某个模型对话，删除该 provider 的 API Key，再重新打开该会话。composer 必须保留会话自身的模型并标注不可用，提示语为“当前模型不可用：…”（而不是“默认模型不可用：…”）；发送时显示 “Current model is not valid. …”——会话用的是用户为它选定的模型，与配置的默认模型是否相同无关。新建对话仍使用“默认模型 …”的说法。

---

## 增加测试

- 后端行为（路由、模型、流式、工作区）: 在 `backend/tests/` 中最接近的模块旁补 pytest 用例。
- 前端 store / 组件行为 : 在 `frontend/tests/` 补 vitest 用例。
- 涉及感知层面的 UI 流程 : 在上面的冒烟清单增加相关内容。