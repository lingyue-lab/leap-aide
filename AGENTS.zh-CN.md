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

## 构建与开发

构建、开发、调试只能在新创建的调试容器中，避免污染本地环境（你自身所处的宿主机/宿主容器环境）。创建新的调试容器使用 podman (没有先安装)。

参考下列步骤新启动一个调试容器进行构建、开发、调试——构建开发命令只在这个新容器内执行。

- 创建如下内容的临时文件 `docker-compose.debug.yml`，该文件将 workspace 映射到容器内的 `/app` 目录 (LeapAide 启动目录)，workspace 作为目标工作目录包含了所有项目改动。

```yml
services:
  leap-aide:
    entrypoint: ["bash", "/app/backend/scripts/docker-entrypoint.sh"]
    volumes:
      - <workspace>:/app
```

- 在根目录下执行 `bash scripts/ctl.sh start-docker -f /path/to/docker-compose.debug.yml <image-name>` 启动容器，容器启动后 LeapAide 自动拉起。

可用镜像：

- `ghcr.io/lingyue-lab/leap-aide-testing`：用于回归测试，体积较大，包含 node、npm、pytest 等完整工具依赖，`/app` 替换后可用于前后端构建、调试。
- `ghcr.io/lingyue-lab/leap-aide-runtime`：最终产品镜像，体积较小，工具依赖不完整，`/app` 替换后可用于最终产品效果的验证。

进入新容器（`podman exec -it <容器名> bash`）执行构建开发命令：

```bash
# Start the server
bash scripts/ctl.sh restart

# Build the service
bash scripts/build.sh
```

**警告：`ctl.sh stop` / `ctl.sh restart` 只能在新启动的调试容器内执行；禁止在宿主机上执行，也禁止在承载运行中 agent 会话的容器内（宿主容器）执行。**

- 纯前端改动无需重启调试容器：在宿主机/宿主容器仓库根目录执行 `podman run -v "$(pwd):/app" -u root --rm --entrypoint /bin/bash ghcr.io/lingyue-lab/leap-aide-testing:sha-c762d0f -c "bash scripts/build.sh --frontend"` 生成新的 `static/`（已在容器内则直接 `bash scripts/build.sh --frontend`）；`static/` 每次请求都从磁盘读取，浏览器硬刷新即可生效。
- 后端改动需要重启调试容器时，从宿主机/宿主容器执行 `podman restart <容器名>`（或删除旧的调试容器重新创建）。

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
- Markdown 一律不折行：一个段落一行，一个列表项一行，表格一行一行。散文是渲染后阅读的，源码不该按固定列宽断行。

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