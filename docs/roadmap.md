---
title: LeapAide Roadmap
---

<div align="center">

**English** | [中文](roadmap.zh-CN.md)

</div>

# LeapAide Roadmap

Mainly describes the core features and major architectural changes of each version.

---

## Sprint History

### v0.1.0

- Completed the initial feature development; the Docker image works out of the box.

### v0.2.0

- **More LLM provider support:** Provider coverage still has gaps — Xiaomi, StepFun, Tencent, and others are not supported and need to be filled in.
- **web-view Skill:** LeapAide gives pinned sessions an observer window so users can watch model behavior live in an isolated browser desktop environment, but that environment has some special design constraints and needs a dedicated Skill to be used smoothly.
- **Supervised development flow:** The `supervise-fix-issue` skill drives a GitHub issue to a reviewed pull request — Plan → Implement → Test → Review, each phase a delegated sub-session, with per-role credentials and the issue and pull request as the channel. See [Supervised development loops](architecture.md#supervised-development-loops) for how it compares to the workflow-engine approaches.
- **Goal-driven child sessions:** The SDK's standing goal is judged at the end of the top-level turn, so it cannot keep a delegate child alive across turns. Until the SDK grows that, the supervise loop keeps its own state in `supervise_state.py` and the top-level goal is only the heartbeat. Making a goal steer a child session is an SDK-side change and is tracked there.
- **Bug fixes:** Fix the important bugs present in the current version.

### v0.3.0

- **AnyConnect:** Users can run `any-connect --password secret url` in any Linux environment; any-connect temporarily attaches that environment to LeapAide, providing a reach-through path for LeapAide troubleshooting and diagnosis.
