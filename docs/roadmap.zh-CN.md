---
title: LeapAide 路线图
---

<div align="center">

[English](roadmap.md) | **中文**

</div>

# LeapAide 路线图

主要描述各个版本的核心功能、重大架构调整。

---

## Sprint历史

### v0.1.0

- 完成初版功能开发，Docker镜像开箱可用。

### v0.2.0

- **更多LLM Provider支持：** 当前LLM Provider的支持还有缺失，比如小米、阶跃星辰、腾讯等都不支持，需要进一步补齐。
- **web-view Skill：** LeapAide为置顶Session提供了观察窗口的能力，用户可以在独立的浏览器桌面环境中实时观察模型行为，但独立浏览器桌面环境有一些特殊的设计，需要专门的Skill支持才能流畅使用。
- **监督式开发流程：** `supervise-fix-issue` 技能把一个 GitHub issue 推进到带评审意见的 PR——方案 → 实现 → 测试 → 评审，每个阶段都是一次委派出去的子会话，各角色凭据独立，issue 与 PR 就是通道。它与工作流引擎类做法的比较见 [监督式开发循环](architecture.zh-CN.md#监督式开发循环)。
- **由 goal 驱动的子会话：** SDK 的 standing goal 在顶层会话的一轮结束时才判断，因此无法让被委派的子会话跨轮存活。在 SDK 补上这个能力之前，supervise 循环用自己的 `supervise_state.py` 记录状态，顶层 goal 只当心跳。让 goal 驱动子会话是 SDK 侧改动，在那边跟踪。
- **BUG FIX：** 修复当前版本存在的重要BUG。 

### v0.3.0

- **AnyConnect功能：** 支持用户在任何Linux环境中执行 `any-connect --password secret url`，any-connect 将当前环境临时 Attach 到 LeapAide，为 LeapAide 排查诊断提供触达通路。