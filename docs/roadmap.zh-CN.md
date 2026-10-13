# Leap Aide 路线图

[English](roadmap.md) | **简体中文**

当前状态与近期方向。已发布历史见 [CHANGELOG.md](../CHANGELOG.md)，已知缺口见
[bugs.zh-CN.md](bugs.zh-CN.md)。

---

## 当前状态

**后端** — Python 标准库 HTTP 服务器（`backend/app/server.py` + `api/`）。
会话以 JSON 文件持久化，配合 `state.db` SQLite 存储（与 hermes-agent SDK
共享）。可选口令门、TLS 支持、上传处理、带两级信任级别的工作区文件操作、
Profile 管理。

**前端** — React 18 + TypeScript + Vite + Zustand + Tailwind，Material 3
设计语言，i18n（English、简体中文）。构建产物输出到 `static/`，由 Python
后端直接 serve。

**实时能力** — WebSocket 优先：单条 `/api/ws/events` 连接承载
token/tool/approval/done 事件，终端与 VNC/observer 面板各用专用的
`/api/ws/terminal`、`/api/ws/vnc` 通道。会话回放保留少量 SSE 接口用于
断线重连恢复。

**部署** — 多阶段 Docker 镜像（runtime + cicd 目标），hermes-agent venv
在构建期烘焙；`docker-compose.yml` 单服务部署；`scripts/install.sh`
一行安装器；`scripts/ctl.sh` 本地进程管理。

**测试** — 后端 pytest 套件、前端 vitest 套件、分层执行器
（`./scripts/run-test.sh`）、CI 覆盖 Python 3.11–3.13 矩阵。见
[testing.zh-CN.md](testing.zh-CN.md)。

---

## 近期方向

方向性条目，非排期承诺 —— 不带冲刺编号，不承诺日期。

- **API 表面清理** — 逐步把 `routes.py` 的路由表收敛到一致的资源命名与
  错误结构，继续以 routes.py 作为端点的唯一事实源。
- **可观测性** — 补完结构化日志 / insights 链路，让会话健康度与后台 goal
  失败无需翻原始日志即可见。
- **流式韧性** — 加固 `/api/ws/events` 的重连与多标签页行为，并关闭并发
  会话中残留的 os.environ 竞态（见 [bugs.zh-CN.md](bugs.zh-CN.md) #195）。
- **测试深度** — 在历史上回归多发的 L2 功能域加深覆盖（流式恢复、工作区
  信任边界、审批流）。

条目随时可能重新排序；本清单是意图陈述，不是承诺。

---

## 非目标

- 替换 hermes-agent SDK，或在 WebUI 里重新实现 CLI 逻辑。
- 多用户 / 多租户认证。口令门保护的是单个可信部署，不是账号体系。
- 公开的插件 / 主题市场 —— 主题系统刻意限定为 system / dark / light。
