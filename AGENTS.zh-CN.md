---
title: LeapAide 开发指南
---

<div align="center">

[English](AGENTS.md) | **中文**

</div>

# 开发指南

---

## 项目概述

项目当前基于 [Hermes Agent](https://hermes-agent.nousresearch.com/) 框架开发，提供自托管的浏览器界面。

项目后端基于 Python 标准库（无 Web 框架），前端为 React 18 + TypeScript + Vite，遵循 Material 3前端设计规范。

---

## 构建与开发命令

```bash
# Start the server
bash scripts/ctl.sh restart

# Build the service
bash scripts/build.sh
```

---

## 架构

参见 [docs/architecture.zh-CN.md](docs/architecture.zh-CN.md)。

---

## 文档

| 文档 | English | 中文 |
|---|---|---|
| 架构 + ADR | [docs/architecture.md](docs/architecture.md) | [docs/architecture.zh-CN.md](docs/architecture.zh-CN.md) |
| 测试指南 | [docs/testing.md](docs/testing.md) | [docs/testing.zh-CN.md](docs/testing.zh-CN.md) |
| 路线图 | [docs/roadmap.md](docs/roadmap.md) | [docs/roadmap.zh-CN.md](docs/roadmap.zh-CN.md) |
| 发布流程 | [docs/release.md](docs/release.md) | [docs/release.zh-CN.md](docs/release.zh-CN.md) |

---

## 编码规范

- 后端：纯 Python stdlib（无 Flask/Django/FastAPI），依赖为 `pyyaml`，另有一项已批准的例外：`aiohttp`（browser-desktop 接收员，issue #37）。
- 前端：TypeScript strict 模式。状态管理用 Zustand store，样式遵循Google Design M3前端规范。
- 禁止使用原生 `confirm()`/`prompt()` — 使用 `showConfirmDialog()`/`showPromptDialog()`。
- 路径穿越防护：文件操作始终使用 `safe_resolve(root, requested)`。
- 双语配对由 `backend/scripts/check_translation_pairing.py` 强制 —— 推送前跑 `--check-all`、`--check-tree docs` 与 `--check-tree . --depth 1`；每篇新增文档都要带上 `.zh-CN.md` 配套文件和 `.i18n.yaml` 记录。

---

## 代码提交规范

- 不要主动创建 git commit，除非用户要求。
- 所有改动都必须经过 PR——不要直接推送到 `main`，也不要直接推送到 `upstream`。把分支推送到 `origin`（你的 fork），再向 `upstream` 的 `main` 发起 PR。没有例外。
- Pull Request 合并采用 `squash and merge` 模式：合并后的 commit message 必须准确反映该 Pull Request 中的全部 commit；合并对话框的 `Commit title` 写 subject，`Commit message` 字段中不得重复 subject。
- Pull Request 可以不关联 Issue。
- git commit log 必须遵守 `.github/commit-template.txt` 模板要求。
- git commit log 必须使用英文。
- 代码 commit 创建分为以下几个步骤：
  1. 读取并理解代码变更：使用 `git add` 添加变更文件（不确定哪些文件需要 `git add` 时向用户 Clarify），然后执行 `git diff --staged`，充分理解全部变更文件的主要变更内容，做了哪些改动。
  2. 读取项目根目录下的 `.github/commit-template.txt` 并遵守其中定义的规范：Type 类型（feat、fix、docs 等）；Subject 格式（祈使语气、首字母小写、不超过 72 字符、不加句号）；Body 必填（必须包含 Why、How、Impact 三部分）；Footer 规范（Closes、BREAKING CHANGE、Co-Authored-By 等）。
  3. 使用生成的提交信息执行 `git commit`。

---

## 其它
- 所有功能都不得修改 /usr/local/lib/hermes-agent (Hermes Agent SDK)，Hermes Agent SDK是外部依赖，不得修改。
- LeapAide 的实现尽可能参考Hermes Agent SDK CLI的实现。
- 代码 Comment 必须使用英文。