<div align="center">

[English](testing.md) | **中文**

</div>

# Leap Aide 测试指南

本指南覆盖自动化测试分层、运行方式，以及一份精简的浏览器手动冒烟清单。贡献流程见 [CONTRIBUTING.zh-CN.md](../CONTRIBUTING.zh-CN.md)。

---

## 自动化单元测试

所有单元测试由仓库根目录的 `./scripts/run-test.sh` 驱动：

| 命令 | 层级 | 范围 | 典型耗时 |
|---|---|---|---|
| `./scripts/run-test.sh smoke` | L1 | 核心回归套件 | < 5 分钟 |
| `./scripts/run-test.sh function [DOMAIN]` | L2 | 单个功能域 | 10–30 分钟 |
| `./scripts/run-test.sh full` | L3 | 全量回归 | 数小时 |
| `./scripts/run-test.sh frontend [SUBCMD]` | — | 前端 vitest（`smoke` / `full` / `clean` / `setup`） | 分钟级 |
| `./scripts/run-test.sh manual` | L4 | 打印浏览器手动清单 | — |
| `./scripts/run-test.sh setup` | — | 安装全部测试依赖（Python + 前端） | — |
| `./scripts/run-test.sh clean` | — | 清理陈旧测试状态与端口占用 | — |

L2 域（DOMAIN）：`session` | `model` | `workspace` | `cron` | `i18n` | `ui` |
`streaming` | `auth` | `all`。

常用环境变量覆盖：

- `DRY_RUN=1` —— 只打印命令不执行
- `PYTEST_EXTRA="..."` / `VITEST_EXTRA="..."` —— 给执行器追加额外参数

以 `./scripts/run-test.sh help` 的输出为准，那是最新的权威用法说明。

测试结果文件写入 `backend/tests/.results/` 与 `frontend/tests/.results/`。

---

## 手动冒烟测试

涉及 UI 的改动之后，手动进行如下测试：

### 会话生命周期

- **空状态**: 无会话时首次加载显示空状态。
- **创建 / 重命名 / 删除**: 用 `+` 按钮创建会话，重命名，删除。删除*最后一个（唯一）* 会话后回到空状态 。
- **置顶 / 归档**: 置顶把会话移到顶部。置顶后，标题栏中的 observe 按钮可见，点击后进入VNC桌面观察浏览器。

### 聊天与流式

- **发送与流式**: 发一条消息，消息通过 WebSocket（`/api/ws/events`）实时流式出现。工具调用卡片内联渲染、可展开/收起。
- **流式中刷新**: 响应流式输出过程中刷新页面；进行中的回合应恢复，会话不丢失。
- **审批卡片**: 在Manual模式下触发一个需审批的 shell 命令，审批卡片提供允许一次 / 本会话 / 拒绝选项，agent 据此继续。

### 工作区与面板

- **工作区浏览器**: 点击标题右上角展开目录，预览文本文件，创建 + 重命名 + 删除文件。工作区是 git 仓库时 git 徽章显示分支/脏文件数。
- **终端面板**: 打开终端面板，跑一条简单命令（`echo hi`）。
- **主题与语言**: 在设置里切换主题（system / dark / light）与语言（English / 简体中文）, 刷新后两者都应保持。

### 设置

- **设置持久化**: 改一个设置，刷新，确认保留。

---

## 增加测试

- 后端行为（路由、模型、流式、工作区）: 在 `backend/tests/` 中最接近的模块旁补 pytest 用例。
- 前端 store / 组件行为 : 在 `frontend/tests/` 补 vitest 用例。
- 涉及感知层面的 UI 流程 : 在上面的冒烟清单增加相关内容。