---
title: 更新日志
---

<div align="center">

[English](CHANGELOG.md) | **中文**

</div>

# 更新日志

本文件记录 LeapAide 的所有重要变更。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循
[语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.1.0] - 2026-11-09

首个公开发布版。LeapAide 是 Hermes Agent 框架的自托管 Web 界面：聊天前端配一套共享
且可观察的浏览器桌面，另有工作区文件浏览、定时任务，以及对主流 LLM 提供商的一键
接入。

### Added

- **基于 WebSocket 的流式聊天** —— 工具调用与思考过程随到达随渲染；输入区页脚集成了
  模型、推理力度、个性、Skills 与 Workspace 选择器。
- **共享且可观察的浏览器桌面** —— 一套常驻的 Chrome 桌面为所有会话提供支撑，观察者窗口
  实时显示它，你可以观察 Agent 的行为，也可以人工完成登录（短信验证码、手机 App），
  而不必把 Token 浪费在注定失败的登录尝试上。并发会话之间仍然隔离：每个会话只拥有
  自己的标签页，接待员会拒绝它触碰别人的标签页。
- **统一的模型标识** —— 所有模型统一标识为 `@provider:model`，同一个模型经由不同
  提供商接入时不会被混淆。
- **带实时刷新的提供商目录** —— 模型选择器只展示你真正配置过的提供商，保存时即校验
  密钥，被拒绝的密钥会明确报错，而不是等到聊天时才失败。
- **可配置的默认模型** —— 在设置中指定默认模型，它会写入当前 profile 的 `config.yaml`，
  并用于新建会话。
- **定时任务** —— 按计划运行会话，可单独指定模型、Workspace、技能与工具集。
- **镜像内置 GitHub CLI** —— `gh` 与 `gh token` 扩展随运行时镜像一起发布，Agent 可以
  直接执行 `gh pr`、`gh issue`、`gh api`。
- **桌面端拼音输入** —— fcitx5 输入法 profile 会预置 `keyboard-us` 与 `pinyin`，
  `Shift+Space` 即可切换到中文。

### Changed

- 容器用户现在与宿主机用户对齐，容器内写出的文件归属于你自己。
- 提供商密钥门槛在首次启动时即展示，而不再需要先去设置页转一圈；同时删除了已无人
  使用的 onboarding API。
- Markdown 渲染：表格各列对齐，嵌套在另一个围栏里的代码块不再把一条回复拆成两个
  代码框。
- `docs/` 与仓库根目录下的双语配对，现在会在 CI 中检查结构与正文是否漂移。

### Fixed

- 冷启动后的第一轮对话不再要等待几十秒；模型目录、MCP 发现与工具定义改为在启动
  阶段预热，而不是在请求路径上。
- 加载模型目录时选择器不再卡住页面，切换面板也保持响应。
- 容器启动期间的首屏加载不再失败；socket 一进入监听状态，服务端就开始应答。
- Docker bind mount 属主不匹配时，Workspace 的文件创建/保存/删除不再失败；各编辑器
  的保存反馈也统一了。
- 添加 Workspace 的路径输入框现在有了可用的自动补全。
- 欢迎态输入框会应用你选择的 Workspace，`HERMES_WEBUI_DEFAULT_WORKSPACE` 也会被
  正确遵守。

### Docker

拉取发布的镜像并启动服务：

```bash
podman pull ghcr.io/lingyue-lab/leap-aide-runtime:0.1.0
bash scripts/ctl.sh start-docker ghcr.io/lingyue-lab/leap-aide-runtime:0.1.0
```

仅构建 `linux/amd64` —— arm64 主机需要模拟执行。`:latest` tag 也指向本次发布，
`docker-compose.yml` 的默认镜像即为固定的 `0.1.0` 镜像。
