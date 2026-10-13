# Bugs 积压清单

[English](bugs.md) | **简体中文**

本文件跟踪 UI bug 与打磨项。已修复条目保留作参考。产品层面的方向与已知缺口见
[roadmap.zh-CN.md](roadmap.zh-CN.md)。

---

## 待处理 Bug

*当前无待处理 bug。*

---

## 已知限制

- **两容器 Docker 部署：工具在 WebUI 容器内执行** — 在两容器部署
  （hermes-agent 与 Leap Aide WebUI 分开为两个容器）下，由 WebUI 发起的
  agent 会话把工具跑在 WebUI 容器里，而不是 agent 容器。这是已知的架构
  约束。变通方案：使用单一合并镜像的部署方式，或在 agent 容器里通过 CLI
  发起会话。（#681）

- **聊天内嵌图片与保存到工作区不一致** — 当 agent 展示一张内嵌图片（来自
  URL）而用户要求保存它时，agent 会重新下载一次；如果源 URL 有 CDN 轮换或
  带时效参数，可能下载到不同的文件。WebUI 侧渲染的是 agent 提供的 URL，
  行为正确。修复需要 agent 侧做 URL 缓存。（#641）

- **WebUI 会话中不可用 MCP 工具** — MCP 服务器必须配置在当前 profile 的
  config.yaml 的 `mcp_servers:` 下。若 MCP 工具不出现，检查 profile 是否
  正确、MCP 服务器进程是否可从 WebUI 容器内访问。（#628）

- **并发会话的 os.environ 竞态** — 并发 agent 会话共享进程级 os.environ 中
  的 TERMINAL_CWD、HERMES_SESSION_KEY、HERMES_HOME。_ENV_LOCK 串行化了写入，
  但无法在 agent 执行期间完全隔离环境变量。上游修复在 hermes-agent 侧待定。
  （#195）

---

## 已修复

### ~~会话标题截断 / 悬停操作~~ -- 已修复（Sprint 16）

- **现象：** 操作图标即使不可见也占约 30px 空间，把标题挤截断。
- **修复：** 所有操作按钮包进 `position:absolute` 的 `.session-actions`
  浮层容器。标题现在可用满宽度；操作按钮在悬停时以右侧渐隐方式浮现。

### ~~文件夹/项目分配交互显得粘滞~~ -- 已修复（Sprint 16）

- **现象：** 会话属于某项目时，文件夹图标常驻显示（蓝色，60% 不透明度）。
- **修复：** 用与项目色一致的彩色左边框替代 `.has-project` 常驻按钮。文件夹
  按钮现在与其他操作一样只在悬停浮层中出现。

### ~~项目选择器裁切与宽度问题~~ -- 已修复（v0.17.3）

- **现象：** 选择器被 `.session-item` 祖先的 `overflow:hidden` 裁切；改用
  `position:fixed` 后没有包含块约束宽度，选择器拉伸到整个视口。
- **修复：** 动态宽度计算（最小 160px、最大 220px）。事件监听顺序调整。
  清理时序修正。（PR #25）

### ~~模型发现中的 NameError 崩溃~~ -- 已修复（v0.17.3）

- **现象：** 自定义 endpoint 的 `except` 块调用了 `logger.debug()`，但
  `config.py` 从未导入 `logger`。每次 endpoint 拉取失败都会以 `NameError`
  崩溃。
- **修复：** 改为静默 `pass` —— 未配置本地 LLM 时 endpoint 不可达是预期
  情况。（PR #24）

---

## 备注

- Sprint 16 将所有 emoji HTML 实体替换为单色 SVG 线性图标（`sessions.js`
  中的 `ICONS` 常量）。
- 所有会话操作按钮现统一使用浮层模式，保证 UX 一致。
