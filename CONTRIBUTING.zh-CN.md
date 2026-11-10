---
title: 为 LeapAide 做贡献
---

<div align="center">

[English](CONTRIBUTING.md) | **中文**

</div>

# 为 LeapAide 做贡献

感谢您的贡献。

LeapAide 的技术栈刻意保持"简洁"：服务端只用 Python 标准库，客户端用 React + TypeScript + Vite，任何地方都没有重型框架。请延续这种简洁，同时干净利落地解决真实问题。

---

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

---

## 每个 PR 的基本要求

### 所有改动都走 PR

推送分支并发起 PR——不要直接提交到 `main`，也不要直接推送到 `upstream`。没有例外，维护者同样如此。

### 逻辑上相关的修复放到同一个PR中

每个 PR 保持聚焦。一小簇相关的修复没问题。但一个 bug 修复加一次 CSS 清理加一次重构再加一次文档重写，就不行。

### 本地验证

每次提 PR 前先在本地完成所有测试，测试方法参考 [docs/testing.zh-CN.md](docs/testing.zh-CN.md) 。

CI 也会在目标为 `main` 的 ready PR 每次推送时自动重跑同样的套件，并在每次推送到 `main` 本身时再跑一次。该检查只报告结果，目前还不是合并的必需项。

### 清晰的 PR 描述

PR信息描述请具体参考 [.github/commit-template.txt](.github/commit-template.txt) 。

除了遵守 commit-template.txt 的要求，欢迎在 git commit log 里提供更多的准确信息，包括但不局限于以下内容：

- 思考路径（Thinking Path）
- 为什么重要（Why It Matters）
- 验证方式（Verification）
- 风险 / 后续工作（Risks / Follow-ups）

git commit log 必须为英文。

除了 git commit log，如果改动对用户可见，强烈建议在 github PR 页面中附上截图或一段短视频。

### 保持文档一致性

如果你的改动改变了行为、架构、测试、安装方式或面向用户的工作流，请在同一 PR 中更新相关文档。

**每篇文档都是中英成对存在的**（`docs/foo.md` + `docs/foo.zh-CN.md`）。内容改动必须在同一 PR 中更新两个语言版本 —— 只改一种语言的 PR 是不完整的。

这一条不是靠自觉，而是有机器强制的。每个双语配对都带一份一致性记录（`docs/foo.i18n.yaml`），里面按章节存放两种语言各自的 `sha256-16` 摘要，由 `backend/scripts/check_translation_pairing.py` 校验。CI 会在每个触及双语配对的 PR 上跑这个检查：只改了一种语言的 PR、或新增文档却没带中文版 / 记录的 PR，都会报错。`main` 目前还没开分支保护，所以门禁只报错、不阻塞合并。

新增一篇文档：

1. 创建 `docs/foo.md` 与 `docs/foo.zh-CN.md`，各自以 YAML frontmatter 块和语言切换器开头。
2. 标题结构完全对齐 —— 层级相同、顺序相同。
3. 围栏代码块在两侧保持逐字节相同 —— 围栏里的注释在中文版也保持英文。
4. 执行 `python3 backend/scripts/check_translation_pairing.py --write docs/foo.md` 生成记录，三个文件一起提交。

有意为之的单边修改 —— 比如某个 typo 没有对应的中文改动 —— 在该配对的记录里用 `waiver:` 段落声明（`side`、`reason`、`expires`）。豁免只对 co-change 门禁生效，会出现在 PR 的 diff 里，而且自带过期时间，不会悄悄削弱门禁。

常用文件：

- [README.zh-CN.md](README.zh-CN.md) —— 安装、使用、配置和面向贡献者的命令
- [docs/architecture.zh-CN.md](docs/architecture.zh-CN.md) —— 实现细节与设计约束
- [docs/testing.zh-CN.md](docs/testing.zh-CN.md) —— 手动与自动化验证

---

## 项目特定规范

### 遵守设计约束

LeapAide 刻意保持：

- Python 标准库后端：不使用 Flask/Django/FastAPI，对外部依赖的引入保持克制。
- 所有文档以 `*.md` / `*.zh-CN.md` 双语成对存在，并带语言切换器。
- 除非收益明确、权衡合理，否则不要引入新的基础设施或依赖。

### 与现有代码形态保持一致

- 服务端逻辑放在 `backend/app/api/`，`server.py` 保持精简。
- 前端行为放在 `frontend/src/` 下现有的 Zustand store 和组件中。
- 优先扩展现有模式，而不是引入平行的抽象。

### Markdown 一律不折行

仓库里的 Markdown 一个段落一行、一个列表项一行、表格一行一行，散文不要按固定列宽硬折行。

Markdown 是渲染后阅读的——仓库页面、Pull Request、发布说明都是如此，每个读者都按自己的宽度折行。硬折行的源码把断行位置定在作者的列宽上，而不是读者的窗口上；改一句话还会让 diff 裂成两段。代码块、表格和语言切换器不受影响，只有散文会被解开。