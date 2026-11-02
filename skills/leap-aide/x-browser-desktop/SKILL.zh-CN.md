---
name: x-browser-desktop
description: 所有浏览器相关任务（访问网址、打开页面、点击元素、登录网站、查账户页等）必须通过共享浏览器桌面的接收员（receptionist）完成。
version: 2.0.0
author: LeapAide
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [browser, cdp, observer, shared-desktop, leap-aide]
---

<div align="center">

[English](SKILL.md) | **中文**

</div>

# x-browser-desktop 技能

整个实例只运行**一份**共享浏览器桌面（Xvfb + x11vnc + 一个共享登录态 Profile 的 Chrome）——所有会话共用的"公共客厅"。它由 LeapAide 掌管：服务启动时拉起，并有自动重启的健康监督。

Chrome **没有任何网络调试端口**。它通过父子进程控制管道（pipe）被拉起，LeapAide 后端通过进程内**接收员（receptionist）**暴露出唯一的 CDP 入口。在某条连接上创建的标签页只归该连接专属——只有该连接能看到和操作它，连接断开时自动关闭；接入时不会预建标签页——你的工具开工时会开自己的标签页。这套隔离由后端强制执行，而不是靠藏地址。

你的职责是经由接收员驱动这份共享 Chrome 完成浏览器任务，让用户在 browser-desktop 面板中实时看到操作过程。任何情况下都不要自己拉起 Chrome 实例。

一切浏览器相关任务都**必须**经由本技能完成——访问网址、打开页面、点击元素、填写表单、登录网站、读取渲染后的页面内容，一律如此。绝不用自己的自动化代码替代（Python + Selenium/Playwright/pyautogui/Xlib、shell 脚本、直连接收员以外地址的裸 CDP 客户端……）：那样会绕过用户可见的 Chrome，属于禁止行为。

## 使用时机

任何浏览器相关任务出现时都使用本技能——包括作为更大任务的子步骤：访问 http/https 网址、打开网页、点击页面元素、填写表单、登录网站、查看账户/余额页面、读取渲染后的页面内容。这些都**必须**通过本技能驱动共享 Chrome 完成；禁止改写自己的自动化代码。

不适用场景：

- 不需要浏览器的任务。

## 前置条件

**如何判断共享浏览器桌面是否在线（每次都验证，绝不假设）：**

1. 检查 `$HERMES_WEBUI_STATE_DIR/browser_desktop.json` 是否存在且 `status` 字段为 `"active"`（`HERMES_WEBUI_STATE_DIR` 默认为 `~/.hermes/webui`）。该文件由后端的桌面监督线程写入/刷新——是唯一事实来源。

判定：

- 文件存在且 `status == "active"` → 桌面在线；走本技能的接收员路径。
- 文件不存在或 `status != "active"` → 桌面离线；走下方「桌面离线」一节。

健康的桌面状态文件：

```
$ cat $HERMES_WEBUI_STATE_DIR/browser_desktop.json
{"display": 99, "vnc_port": 5900, "receptionist_port": 6900, "receptionist_url": "http://127.0.0.1:6900", "status": "active", ...}
```

## 使用方法

- 优先使用 Hermes Agent SDK 的 `browser_exec` 工具（Browser Use CLI 模式——本实例默认的浏览器表面）。它会自动解析接收员地址并接入共享 Chrome——无需新起 Chrome——且 `session` 参数默认取你当前的 session id，每次调用都落在你自己会话的私有标签页里。
- `agent-browser` 命令行被 LeapAide 安装的 wrapper 接管（位于 `/usr/local/bin/agent-browser`）：桌面在线时，你传入的任何 `--cdp` / `--session` 都会被剥离，`--cdp` 被替换为接收员发现地址（`http://127.0.0.1:6900/json/version?sid=<session_id>`）；桌面离线时，wrapper 原样透传参数（回退本地 headless）。**注意：本实例未安装 node/npx，调用 `agent-browser` 会失败——浏览器任务一律走 `browser_exec`。**
- 如需自己写 CDP 脚本，**只能**连接收员（`http://127.0.0.1:6900`）：请求 `/json/version?sid=<session_id>` —— `sid` 查询参数是**必需的**（缺失时返回 400；session id 就是 `HERMES_SESSION_KEY` 的最后一段，例如 `SID="${HERMES_SESSION_KEY##*:}"`）——再原样使用返回的 `webSocketDebuggerUrl`，在该 WebSocket 上驱动 CDP。你的 `sessionId` 和标签页会自动限定在你的连接范围内。Chrome 本身没有任何可拨的地址——连别处必然失败。

简言之：wrapper 与 LeapAide 的 SDK 补丁保证每次浏览器调用都经由接收员附着到那一份共享 Chrome 上。你只管发起调用；连接目标不由你控制——也不需要你控制。

## 速查表

| 资源 | 值 |
|---|---|
| Display | `:99`（唯一共享桌面；`export DISPLAY=:99` 会把进程绑到该桌面——由后端管控） |
| VNC 端口 | `5900`（查看是 webui browser-desktop 面板的事——绝不在终端里拨它） |
| 接收员 | `http://127.0.0.1:6900`（环境变量 `HERMES_RECEPTIONIST_PORT`；唯一 CDP 入口） |
| 桌面状态 | `$HERMES_WEBUI_STATE_DIR/browser_desktop.json` |
| Chrome Profile | `$HERMES_WEBUI_STATE_DIR/chrome_profiles/global`（共享、已登录） |
| Chrome 窗口 | 960×1080，位于 (0,0)——1920×1080 桌面的左半边；右半边显示工具活动 |
| Session key | `HERMES_SESSION_KEY=agent:main:webui:dm:<session_id>` |

桌面状态示例：

```json
{
  "display": 99,
  "vnc_port": 5900,
  "receptionist_port": 6900,
  "receptionist_url": "http://127.0.0.1:6900",
  "status": "active",
  "started_at": 1730000000.0,
  "last_activity": 1730000000.0
}
```

## 操作流程

### 桌面在线

1. 先跑前置条件判定。若判定为离线，转到「桌面离线」一节——绝不假设在线。
2. 通过 `browser_exec` 驱动**既有**的共享 Chrome，遵守以下规则：
   - 复用共享 Profile 及其登录态。用户可能通过 browser-desktop 面板手动操作过 Chrome；不要关闭不属于你的标签页。
   - 你的工具创建的标签页只属于你的连接。其他会话的标签页对你不可见、不可触碰——这是设计使然。不要试图绕过接收员。
   - 绝不再起第二个 Chrome——只有 LeapAide 能启动 Chrome（它持有控制管道）。
   - 若 CDP 调用报连接错误，可能是 Chrome 崩了；LeapAide 会自动重启。稍等重试一次，然后报告失败。
   - 连接断开时（回合结束、工具退出）你的标签页会自动关闭——多步任务请在一个回合内完成，或让长驻客户端在整个任务期间保持运行。

### 桌面离线

状态文件缺失或非 `active` 即为离线（服务启动竞态、`HERMES_GLOBAL_DESKTOP=0`，或严重故障）。

- `browser_exec` 会自动回退到它自己的本地浏览器——网页任务仍能完成，只是没有用户可见窗口。
- **不要**尝试自己启动桌面、绑定显示或拨 VNC/CDP 端点。桌面由后端监督；若无法恢复，请告知用户共享桌面已离线。

## 常见陷阱

- **自己写自动化代码**：Selenium/Playwright/pyautogui 脚本会绕过用户可见的共享 Chrome；浏览器任务走本技能的接收员路径。
- **自己启动 Chrome**：Chrome 的控制通道属于接收员；你拉起的 Chrome 没有可共享的状态，且 `--remote-debugging-*` 参数会被后端拦截。始终附着，绝不启动。
- **给 agent-browser 传 `--cdp`**：桌面在线时会被剥离并替换为接收员地址——不由你控制；桌面离线时终端策略仍可能拦截该命令。
- **给 agent-browser 传 `--session`**：与共享桌面冲突，会被 wrapper 剥离——不由你控制。
- **期待看到其他会话的标签页**：隔离正是要点。在你眼里只存在你自己连接的标签页。
- **关闭共享标签页或遮挡桌面视图**：桌面是用户观察所有会话任务的窗口；保持它可用。
