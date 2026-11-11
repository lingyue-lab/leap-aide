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
- **Per-phase model routing:** Each phase of the supervise loop now picks its own model — a cheap one for the mechanical test run, a capable one for the review. The SDK's `delegate_task` exposes no `model` property and its registry handler whitelists the arguments it forwards, so LeapAide carries the value with a runtime shim in `backend/app/compat/delegate_model.py` rather than modifying the SDK. An SDK-native `model` parameter on `delegate_task` would delete that shim and is tracked as an SDK-side follow-up, alongside goal-driven child sessions.
- **Bug fixes:** Fix the important bugs present in the current version.

### v0.3.0

- **AnyConnect:** Users can run `any-connect --password secret url` in any Linux environment; any-connect temporarily attaches that environment to LeapAide, providing a reach-through path for LeapAide troubleshooting and diagnosis.
