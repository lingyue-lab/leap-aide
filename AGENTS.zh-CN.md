<div align="center">

[English](AGENTS.md) | **中文**

</div>

# 开发指南

---

## 项目概述

项目当前基于[Hermes Agent](https://hermes-agent.nousresearch.com/)框架开发，提供自托管浏览器界面。

项目后端基于Python标准库（无 Web 框架），前端为 React 18 + TypeScript + Vite，遵循 Material 3前端设计规范。

---

## 构建与开发命令

```bash
# 启动服务器
bash scripts/ctl.sh restart

# 构建服务
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

---

## 编码规范

- 后端：纯 Python stdlib（无 Flask/Django/FastAPI），唯一外部依赖是 `pyyaml`。
- 前端：TypeScript strict 模式。状态管理用 Zustand store，样式遵循Google Design M3前端规范。
- 禁止使用原生 `confirm()`/`prompt()` — 使用 `showConfirmDialog()`/`showPromptDialog()`。
- 路径穿越防护：文件操作始终使用 `safe_resolve(root, requested)`。

---

## 代码提交规范
不要主动创建git commit，除非用户要求。

代码commit创建分为以下几个步骤

步骤 1: 读取并理解代码变更
判断哪些文件需要被提交，使用 git add 添加这些变更文件，如果不确定哪些文件需要git add，向用户Clarify。
完成 git add后，然后执行git diff --staged，充分理解全部变更文件的主要变更内容，做了哪些改动。


步骤 2：读取提交规范模板
读取项目根目录下的 .github/commit-template.txt 文件内容，理解其中定义的规范：

- Type 类型（feat、fix、docs 等）
- Subject 格式要求（祈使语气、首字母小写、不超过 72 字符、不加句号）
- Body 必填（必须包含 Why、How、Impact 三部分）
- Footer 规范（Closes、BREAKING CHANGE、Co-Authored-By 等）
- 如果.github/commit-template.txt文件不存在，则输出错误并终止。

步骤 3：生成符合规范的提交信息
基于步骤 1 获取的变更diff内容和步骤 2 的模板规范，生成一条严格符合要求的 Git 提交信息。
生成要求：

- 语言：英文
- commit log示例：
```
feat(auth): add password reset functionality

Currently users cannot reset their password when they forget it,
leading to account lockouts and increased support tickets.

Implement a complete password reset flow:

Add forgot password endpoint that sends reset link via email

Create password reset form with token validation

Add email notification service with rate limiting

Closes #234
Co-Authored-By: Jane Doe jane@example.com
```

步骤 4: 使用生成的提交信息执行 git commit 

---

## 其它
- 所有功能都不得修改/usr/local/lib/hermes-agent(Hermes Agent SDK)，Hermes Agent SDK是外部依赖，不得修改。
- Leap Aide的实现尽可能参考Hermes Agent SDK CLI的实现。
- 代码comment必须使用英文。