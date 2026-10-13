# Leap Aide 测试指南

[English](testing.md) | **简体中文**

本指南覆盖自动化测试分层、运行方式，以及一份精简的浏览器手动冒烟清单。
贡献流程见 [CONTRIBUTING.zh-CN.md](../CONTRIBUTING.zh-CN.md)。

---

## 测试分层

所有自动化测试由仓库根目录的 `./scripts/run-test.sh` 驱动：

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

---

## 直接运行 pytest

后端测试位于 `backend/tests/`（`backend/pytest.ini` 在 `backend/` 下）：

```bash
cd backend
python -m pytest tests/ -v
python -m pytest tests/ -k <name>      # 单个测试或模块
```

不要在仓库根目录执行 `pytest tests/` —— 根目录没有 `tests/` 目录。

结果文件写入 `backend/tests/.results/` 与 `frontend/tests/.results/`。

### CI

`.github/workflows/tests.yml` 在 Python 3.11 / 3.12 / 3.13 上跑后端套件，
另有前端 vitest 套件和容器内任务（buildah 构建 `cicd` 镜像后在容器内跑
pytest）。依赖 hermes-agent SDK 的测试在 SDK 不可用时自动跳过。推送前先
让本地套件通过 —— CI 是兜底，不是第一道防线。

---

## 测试隔离

- 测试在端口 **8788** 上启动隔离服务器，使用独立的临时状态目录（由
  `backend/tests/conftest.py` 为每个测试会话创建）。
- 生产状态（`~/.hermes/webui`）和真实 cron 任务绝不会被触碰。
- 若测试被中断留下残留进程，`./scripts/run-test.sh clean` 可恢复干净状态。

---

## 手动冒烟清单（L4）

涉及 UI 的改动之后跑一遍。每项一两句话，目标是快速覆盖自动化测试最少
覆盖到的界面。

### 会话生命周期

1. **空状态** —— 无会话时首次加载显示空状态，Boot 流程不会自动创建会话。
2. **创建 / 重命名 / 删除** —— 用 `+` 按钮创建会话，重命名，删除。删除
   *最后一个（唯一）* 会话后回到空状态 —— 绝不能落到半创建的会话上。
3. **置顶 / 归档** —— 置顶把会话移到顶部；归档隐藏它，切换后可再显示。
   置顶后，标题栏中的 observe/置顶控件应立即反映新状态（切换会话后 UI
   不滞后）。

### 聊天与流式

4. **发送与流式** —— 发一条消息；token 通过 WebSocket（`/api/ws/events`）
   实时流式出现。工具调用卡片内联渲染、可展开/收起。
5. **流式中刷新** —— 响应流式输出过程中刷新页面；进行中的回合应恢复，
   会话不丢失。
6. **审批卡片** —— 触发一个受门控的 shell 命令；审批卡片提供
   允许一次 / 本会话 / 总是 / 拒绝，agent 据此继续。

### 工作区与面板

7. **工作区浏览器** —— 展开目录，预览文本文件与图片，创建 + 重命名 +
   删除文件。工作区是 git 仓库时 git 徽章显示分支/脏文件数。
8. **终端面板** —— 打开终端面板，跑一条简单命令（`echo hi`），调整面板
   大小。
9. **主题与语言** —— 在设置里切换主题（system / dark / light）与语言
   （English / 简体中文）；刷新后两者都应保持。

### 设置与设备

10. **设置持久化** —— 改一个设置，刷新，确认保留。
11. **移动端宽度** —— 窄视口（< 640px）下侧边栏收进抽屉；聊天与输入框
    保持可用。

---

## 何时扩展哪一层

- 后端行为（路由、模型、流式、工作区）→ 在 `backend/tests/` 中最接近的
  模块旁补 pytest 用例。
- 前端 store / 组件行为 → 在 `frontend/tests/` 补 vitest 用例。
- 涉及感知层面的 UI 流程 → 在上面的冒烟清单加一行，而不是写脆弱的脚本
  测试。
