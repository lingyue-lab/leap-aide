# 为 Leap Aide 做贡献

[English](CONTRIBUTING.md) | **简体中文**

感谢你的贡献。

Leap Aide 的技术栈刻意保持"无聊"：服务端只用 Python 标准库，客户端用 React + TypeScript + Vite，任何地方都没有重型框架。最好的 PR 会延续这种简洁，同时干净利落地解决真实问题。

## 通往优质 PR 的两条路径

### 路径一：小而聚焦的改动

这是最快进入评审并合并的方式。

- 修一个明确的 bug，或加一个范围严格受限的改进
- 尽量少动文件
- 避免把顺手重构混进功能性改动
- 开 PR 前先在本地跑相关测试
- PR 描述保持简洁具体

这类改动最容易评审，也最适合快速合并。

### 路径二：较大的改动

如果你想调整架构、重塑工作流、添加重量级 UI 功能，或改变核心行为，请先对齐方向。

- 尽早开 issue、发起讨论，或开一个 draft PR
- 讲清楚你要解决的问题，而不只是你想做的实现
- 说明权衡取舍、迁移风险，以及你考虑过的替代方案
- 把不相关的工作拆开，让最终的 PR 便于评审

欢迎大改动，但突如其来的重写很难得到认真评审。

## 每个 PR 的基本要求

### 1. 一个 PR 只做一件逻辑上的事

每个 PR 保持聚焦。一小簇相关的修复没问题。但一个 bug 修复加一次 CSS 清理加一次重构再加一次文档重写，就不行。

### 2. 本地验证

每次提 PR 前先在本地跑冒烟套件：

```bash
./scripts/run-test.sh smoke
```

直接跑 pytest（测试位于 `backend/tests/`）：

```bash
cd backend && python -m pytest tests/ -v
```

CI 会在 Python 3.11、3.12、3.13 上运行测试套件，并运行前端 vitest 套件 —— 但要把 CI 当作兜底，而不是第一道防线。

如果你的改动影响浏览器行为，还要按照 [docs/testing.zh-CN.md](docs/testing.zh-CN.md) 完成相关的手动检查。

### 3. 清晰的 PR 描述

本仓库目前没有 PR 模板，请自行把这些重要部分写全：

- 思考路径（Thinking Path）
- 改了什么（What Changed）
- 为什么重要（Why It Matters）
- 验证方式（Verification）
- 风险 / 后续工作（Risks / Follow-ups）
- 使用的模型（Model Used）

如果改动对用户可见，请附上截图或一段短视频。

UI 或 UX 改动必须附带前后对比图。改变了界面或交互流程却没有前后对比图的 PR，大概率会被忽略，或在维护者例行清理时未经评审就被关闭。

### 4. AI 使用披露

如果 AI 参与产出了这次改动，请在 PR 描述中说明。

包括：

- 提供方（Provider）
- 确切的模型名称或 ID
- 起过关键作用的模式或工具使用

如果没有使用 AI，请写：`None — human-authored`。

### 5. 让文档保持诚实

如果你的改动改变了行为、架构、测试、安装方式或面向用户的工作流，请在同一 PR 中更新相关文档。

**每篇文档都是中英成对存在的**（`docs/foo.md` + `docs/foo.zh-CN.md`）。内容改动必须在同一 PR 中更新两个语言版本 —— 只改一种语言的 PR 是不完整的。

常用文件：

- [README.zh-CN.md](README.zh-CN.md) —— 安装、使用、配置和面向贡献者的命令
- [docs/architecture.zh-CN.md](docs/architecture.zh-CN.md) —— 实现细节与设计约束
- [docs/testing.zh-CN.md](docs/testing.zh-CN.md) —— 手动与自动化验证
- [docs/roadmap.zh-CN.md](docs/roadmap.zh-CN.md) —— 已交付的能力与方向
- [docs/bugs.zh-CN.md](docs/bugs.zh-CN.md) —— 已知限制
- [CHANGELOG.md](CHANGELOG.md) —— 当维护者需要可直接用作 release note 的条目时（仅英文）

## 项目特定规范

### 遵守设计约束

Leap Aide 刻意保持：

- Python 标准库后端 —— 不用 Flask/Django/FastAPI；唯一的外部 Python 依赖是 `pyyaml`
- WebSocket 优先的实时流式传输，附带一个小的 SSE 回放面
- `static/` 是**构建产物** —— 前端源码在 `frontend/src`；永远不要手改 `static/` 下的文件
- i18n 仅限 English 和简体中文
- 文档以 `*.md` / `*.zh-CN.md` 双语成对存在，并带语言切换器
- 易于在终端中修改和运行（`./scripts/ctl.sh`、`./scripts/build.sh`）

除非收益明确、权衡合理，否则不要引入新的基础设施或依赖。

### 与现有代码形态保持一致

- 服务端逻辑放在 `backend/app/api/`，`server.py` 保持精简
- 前端行为放在 `frontend/src/` 下现有的 Zustand store 和组件中
- 优先扩展现有模式，而不是引入平行的抽象
- 保持改动对直接在终端里读仓库的未来贡献者可读

### 谨慎对待面向用户的改动

这个项目高度 UI 驱动。如果你改动了交互流程、会话行为、工作区浏览、引导流程或移动端布局：

- 测试正常路径
- 在相关处测试刷新/重载行为
- 在相关处测试窄屏/移动端布局
- 在 PR 中附上前后对比图

### 安全至关重要

这个应用会暴露工作区内容、执行 agent 操作，并且可能部署在反向代理或 Docker 之后。请把认证、路径处理、上传、流式传输和环境变量处理都视为高风险区域。

如果你的 PR 触及安全敏感行为，请在 PR 描述中明确说明，并解释你是如何验证的。

## 写好 PR 信息

先写一段简短的"思考路径"，说明从项目目标到具体修复的推理链条。

示例：

> - Leap Aide 的目标是在浏览器中实现与 Hermes CLI 近乎 1:1 的对等能力
> - 长时间运行的聊天轮次依赖 WebSocket 流式传输与会话恢复
> - 在轮次进行中刷新页面可能让 UI 进入不一致状态
> - 问题在于恢复的会话还原了消息，却没有还原实时流的状态
> - 本 PR 修复了恢复路径，让进行中的轮次在刷新后能干净地重连
> - 收益是用户可以刷新或重连，而不会对正在进行的工作失去可见性

之后覆盖：

- 改了什么
- 为什么改
- 如何验证
- 还剩什么风险

## 评审建议

想让评审最顺畅？

- diff 保持紧凑
- 命名清晰
- 避免不必要的重写
- 只在代码本身难以跟读的地方加简短注释
- 直接回应评审反馈；如果范围变了，就更新 PR 描述

## 开发参考

- [README.zh-CN.md](README.zh-CN.md) —— 安装、配置、命令
- [CLAUDE.md](CLAUDE.md) —— 工作约定与关键规则
- [docs/architecture.zh-CN.md](docs/architecture.zh-CN.md) —— 系统设计与 ADR
- [docs/testing.zh-CN.md](docs/testing.zh-CN.md) —— 测试层级与手动冒烟清单
- `./scripts/run-test.sh help` —— 测试运行器的权威用法

疑问最好尽早提出，不要等大改动做完才问。
