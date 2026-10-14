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
- **BUG FIX：** 修复当前版本存在的重要BUG。 

### v0.3.0

- **AnyConnect功能：** 支持用户在任何Linux环境中执行 `any-connect --password secret url`，any-connect 将当前环境临时 Attach 到 LeapAide，为 LeapAide 排查诊断提供触达通路。