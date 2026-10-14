<div align="center">

**English** | [中文](roadmap.zh-CN.md)

</div>

# Leap Aide Roadmap

Mainly describes the core features and major architectural changes of each version.

---

## Sprint History

### v0.1.0

- Completed the initial feature development; the Docker image works out of the box.

### v0.2.0

- **More LLM provider support:** Provider coverage still has gaps — Xiaomi, StepFun, Tencent, and others are not supported and need to be filled in.
- **web-view Skill:** Leap Aide gives pinned sessions an observer window so users can watch model behavior live in an isolated browser desktop environment, but that environment has some special design constraints and needs a dedicated Skill to be used smoothly.
- **Bug fixes:** Fix the important bugs present in the current version.

### v0.3.0

- **AnyConnect:** Users can run `any-connect --password secret url` in any Linux environment; any-connect temporarily attaches that environment to Leap Aide, providing a reach-through path for Leap Aide troubleshooting and diagnosis.
