"""
Tests for the supervise-fix-issue skill package (issue #57).

Filesystem-only — validates the skill files, the Hermes frontmatter
hardlines, the bilingual pairing gate (check_translation_pairing.py), the
four role schemas and the behaviour of the skill's four helper scripts.
No server required.
"""

import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "skills" / "leap-aide" / "supervise-fix-issue"
SKILL_MD = SKILL_DIR / "SKILL.md"
SKILL_ZH = SKILL_DIR / "SKILL.zh-CN.md"
SKILL_RECORD = SKILL_DIR / "SKILL.i18n.yaml"
REFERENCES_DIR = SKILL_DIR / "references"
SCRIPTS_DIR = SKILL_DIR / "scripts"
SCHEMAS_DIR = SKILL_DIR / "schemas"
CHECKER = REPO_ROOT / "backend" / "scripts" / "check_translation_pairing.py"

SCRIPTS = ("gh_roles.py", "preflight.py", "supervise_state.py", "arm_goal.py")

# phase -> (schema file, fields the schema must require)
SCHEMA_FILES = {
    "plan": ("plan.schema.json", ("summary", "steps", "acceptance_criteria")),
    "implementer": ("implementer.schema.json",
                    ("summary", "branch", "changed_files")),
    "tester": ("tester.schema.json", ("summary", "cases", "passed")),
    "reviewer": ("reviewer.schema.json", ("summary", "verdict", "findings")),
}

# A token that is obviously not a credential — used to prove that the
# wrapper injects and redacts without ever contacting GitHub.
FAKE_TOKEN = "ghp_FAKE_TOKEN_FOR_TESTS_0000000000"


# ── helpers ──────────────────────────────────────────────────────────────

def _run(script: str, *args, cwd=None, env=None):
    """Run one of the skill's helper scripts with the current interpreter."""
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), *args],
        capture_output=True, text=True, cwd=cwd, env=env,
    )


def _load_module(name: str):
    """Import one of the skill's helper scripts as a module."""
    path = SCRIPTS_DIR / name
    spec = importlib.util.spec_from_file_location(f"supervise_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frontmatter_block(raw: str) -> str:
    """Return the raw frontmatter block including both --- fences."""
    assert raw.startswith("---"), "frontmatter must start at byte 0"
    lines = raw.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[: i + 1])
    raise AssertionError("unterminated frontmatter")


def _frontmatter_fields(raw: str) -> dict:
    block = _frontmatter_block(raw)
    fields = {}
    for line in block.split("\n")[1:-1]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return fields


def _write_role_config(workspace: Path, roles=("supervisor", "planner",
                                              "implementer", "tester",
                                              "reviewer")):
    """Write a role config with a fake token for every role."""
    import yaml
    cfg = {
        "version": 1,
        "roles": {r: {"kind": "pat", "token": FAKE_TOKEN} for r in roles},
    }
    path = workspace / ".leap-aide" / "secrets" / "github" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return path


# ── package layout ───────────────────────────────────────────────────────

def test_skill_files_exist():
    """The skill package ships every file the loop needs."""
    for path in (SKILL_MD, SKILL_ZH, SKILL_RECORD):
        assert path.is_file(), f"missing {path}"
    for script in SCRIPTS:
        assert (SCRIPTS_DIR / script).is_file(), f"missing {SCRIPTS_DIR / script}"
    for name, _required in SCHEMA_FILES.values():
        assert (SCHEMAS_DIR / name).is_file(), f"missing {SCHEMAS_DIR / name}"
    assert (REFERENCES_DIR / "github-skill-map.md").is_file()
    assert (REFERENCES_DIR / "github-skill-map.zh-CN.md").is_file()
    assert (REFERENCES_DIR / "github-skill-map.i18n.yaml").is_file()


def test_scripts_compile():
    """Every helper script is syntactically valid Python."""
    for script in SCRIPTS:
        proc = subprocess.run(
            [sys.executable, "-m", "py_compile", str(SCRIPTS_DIR / script)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, f"{script} does not compile:\n{proc.stderr}"


def test_frontmatter_hardlines():
    """Frontmatter satisfies the Hermes skill-authoring hardlines."""
    raw = SKILL_MD.read_text(encoding="utf-8")
    fields = _frontmatter_fields(raw)
    assert fields.get("name") == "supervise-fix-issue"
    assert len(fields.get("name", "")) <= 64, "skill name must be <= 64 characters"
    desc = fields.get("description", "")
    assert desc, "description must be non-empty"
    assert len(desc) <= 60, f"description too long ({len(desc)} > 60)"
    assert desc.endswith("."), "description must end with a period"
    for field in ("version", "author", "license"):
        assert fields.get(field), f"frontmatter must carry `{field}`"
    body = raw.split("---", 2)[2] if raw.count("---") >= 2 else ""
    assert body.strip(), "skill body must be non-empty"


def test_frontmatter_declares_the_hermes_metadata():
    """The metadata block carries the tags and the related skills."""
    raw = SKILL_MD.read_text(encoding="utf-8")
    block = _frontmatter_block(raw)
    assert "hermes:" in block, "frontmatter must carry metadata.hermes"
    assert "tags:" in block, "metadata.hermes must carry tags"
    assert "related_skills:" in block, "metadata.hermes must carry related_skills"
    for skill in ("github-issues", "github-pr-workflow", "github-code-review"):
        assert skill in block, f"related_skills must mention {skill}"


def test_chinese_companion_mirrors_frontmatter_and_switcher():
    """The zh-CN companion mirrors the frontmatter and keeps the switcher."""
    en_raw = SKILL_MD.read_text(encoding="utf-8")
    zh_raw = SKILL_ZH.read_text(encoding="utf-8")
    en_fields = _frontmatter_fields(en_raw)
    zh_fields = _frontmatter_fields(zh_raw)
    assert {k: v for k, v in zh_fields.items() if k != "description"} == (
        {k: v for k, v in en_fields.items() if k != "description"}
    ), "companion frontmatter must mirror the English one (except description)"
    assert zh_fields.get("description"), "Chinese description must be non-empty"
    assert "[中文](SKILL.zh-CN.md)" in en_raw, (
        "English file must link to the Chinese companion")
    assert "[English](SKILL.md)" in zh_raw, (
        "Chinese file must link back to the English original")


@pytest.mark.parametrize("anchor", [
    SKILL_MD,
    REFERENCES_DIR / "github-skill-map.md",
])
def test_bilingual_pairing_gate_passes(anchor):
    """check_translation_pairing.py verify mode exits 0 for the pair."""
    proc = subprocess.run(
        [sys.executable, str(CHECKER), str(anchor)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, (
        f"bilingual pairing gate failed for {anchor}:\n"
        f"{proc.stderr or proc.stdout}")


def test_skills_tree_is_complete():
    """--check-tree skills finds no document missing its companion."""
    proc = subprocess.run(
        [sys.executable, str(CHECKER), "--check-tree", "skills"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, (
        f"--check-tree skills failed:\n{proc.stderr or proc.stdout}")


# ── the role schemas ─────────────────────────────────────────────────────

@pytest.mark.parametrize("phase", sorted(SCHEMA_FILES))
def test_role_schema_requires_its_core_fields(phase):
    """Each phase schema is a JSON Schema that requires its core fields."""
    name, required = SCHEMA_FILES[phase]
    schema = json.loads((SCHEMAS_DIR / name).read_text(encoding="utf-8"))
    assert schema.get("$schema") == "http://json-schema.org/draft-07/schema#"
    assert schema.get("type") == "object"
    assert isinstance(schema.get("properties"), dict) and schema["properties"]
    for field in required:
        assert field in schema["properties"], (
            f"{name} must describe the `{field}` property")
        assert field in schema.get("required", []), (
            f"{name} must require `{field}`")


def test_reviewer_verdict_is_binary():
    """Only an approve verdict may lead to an LGTM."""
    schema = json.loads(
        (SCHEMAS_DIR / "reviewer.schema.json").read_text(encoding="utf-8"))
    verdict = schema["properties"]["verdict"]
    assert verdict["enum"] == ["approve", "request_changes"], verdict
    assert schema["properties"]["lgtm"]["type"] == "boolean"


def test_every_schema_carries_the_blocked_pair():
    """A phase that cannot work says so instead of faking a result."""
    for name, _required in SCHEMA_FILES.values():
        schema = json.loads((SCHEMAS_DIR / name).read_text(encoding="utf-8"))
        props = schema["properties"]
        assert "blocked" in props, f"{name} must carry a `blocked` flag"
        assert "blocker_reason" in props, f"{name} must carry a `blocker_reason`"
        assert props["blocked"]["type"] == "boolean"


# ── gh_roles.py ──────────────────────────────────────────────────────────

def test_init_writes_a_private_config_and_a_gitignore(tmp_path):
    """`init` writes the config at 0600 and keeps the tree out of git."""
    proc = _run("gh_roles.py", "init", "--workspace", str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    config = tmp_path / ".leap-aide" / "secrets" / "github" / "config.yaml"
    assert config.is_file(), "the config was not written"
    mode = stat.S_IMODE(config.stat().st_mode)
    assert mode == 0o600, f"the config must be 0600, got {oct(mode)}"
    gitignore = tmp_path / ".leap-aide" / ".gitignore"
    assert gitignore.is_file(), "the .gitignore was not written"
    assert "*" in gitignore.read_text(encoding="utf-8"), (
        "the .gitignore must exclude the whole .leap-aide tree")


def test_init_never_overwrites_an_existing_config(tmp_path):
    """`init` leaves an existing config alone unless --force is given."""
    _run("gh_roles.py", "init", "--workspace", str(tmp_path))
    config = tmp_path / ".leap-aide" / "secrets" / "github" / "config.yaml"
    config.write_text("version: 1\nroles: {}\n", encoding="utf-8")
    proc = _run("gh_roles.py", "init", "--workspace", str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert "roles: {}" in config.read_text(encoding="utf-8"), (
        "init overwrote an existing config without --force")


def test_show_redacts_the_token(tmp_path):
    """`show` prints the config with every secret redacted."""
    _write_role_config(tmp_path)
    proc = _run("gh_roles.py", "show", "--workspace", str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert FAKE_TOKEN not in proc.stdout, "the token leaked into `show`"
    assert "..." in proc.stdout, "the token was not redacted"


def test_doctor_reports_a_missing_config_with_guidance(tmp_path):
    """`doctor` explains how to create the config instead of just failing."""
    proc = _run("gh_roles.py", "doctor", "--workspace", str(tmp_path))
    assert proc.returncode == 1, "doctor must fail without a config"
    assert "no role configuration" in proc.stdout, proc.stdout
    assert "gh_roles.py init" in proc.stdout, (
        "the guidance must name the command that fixes it")


def test_doctor_reports_a_role_without_a_token(tmp_path):
    """A role with no token is reported with the token-creation steps."""
    _write_role_config(tmp_path, roles=("supervisor",))
    proc = _run("gh_roles.py", "doctor", "--workspace", str(tmp_path))
    assert proc.returncode == 1, "doctor must fail for the unconfigured roles"
    assert "planner" in proc.stdout, proc.stdout
    assert "personal access token" in proc.stdout.lower(), (
        "the guidance must explain how to create the token")


def test_run_injects_the_role_environment(tmp_path):
    """`run` hands the role token to the command through the environment.

    The token never appears on the command line — it is injected as
    GH_TOKEN / GITHUB_TOKEN and the gh config directory is pointed at a
    role-private path.
    """
    _write_role_config(tmp_path)
    probe = ("import json, os; print(json.dumps({k: os.environ.get(k) for k in "
             "('GH_TOKEN', 'GITHUB_TOKEN', 'GH_CONFIG_DIR')}))")
    proc = _run("gh_roles.py", "run", "--role", "supervisor",
                "--workspace", str(tmp_path), "--", sys.executable, "-c", probe)
    assert proc.returncode == 0, proc.stderr
    env = json.loads(proc.stdout.strip().splitlines()[-1])
    assert env["GH_TOKEN"] == FAKE_TOKEN
    assert env["GITHUB_TOKEN"] == FAKE_TOKEN, (
        "the bundled GitHub skills look for GITHUB_TOKEN")
    assert env["GH_CONFIG_DIR"] == str(
        tmp_path / ".leap-aide" / "secrets" / "github" / "gh" / "supervisor"), (
        "the gh config directory must be private to the role")


def test_run_refuses_a_role_without_a_token(tmp_path):
    """`run` refuses to execute when the role has no credential."""
    _write_role_config(tmp_path, roles=("supervisor",))
    proc = _run("gh_roles.py", "run", "--role", "reviewer",
                "--workspace", str(tmp_path), "--", "true")
    assert proc.returncode == 2, "run must fail for an unconfigured role"
    assert "reviewer" in proc.stderr, proc.stderr


def test_run_needs_a_command(tmp_path):
    """`run` without a command after `--` is a usage error."""
    _write_role_config(tmp_path)
    proc = _run("gh_roles.py", "run", "--role", "supervisor",
                "--workspace", str(tmp_path))
    assert proc.returncode == 2
    assert "needs a command" in proc.stderr, proc.stderr


def test_role_env_never_points_at_the_user_gh_login(tmp_path):
    """The per-role gh config directory lives inside the workspace."""
    gh_roles = _load_module("gh_roles.py")
    env = gh_roles.role_env(tmp_path, "planner", FAKE_TOKEN)
    assert env["GH_CONFIG_DIR"].startswith(str(tmp_path))
    assert str(Path.home() / ".config" / "gh") not in env["GH_CONFIG_DIR"]


# ── supervise_state.py ───────────────────────────────────────────────────

def test_state_init_creates_the_loop_state(tmp_path):
    """`init` starts the loop in the plan phase with no attempts spent."""
    proc = _run("supervise_state.py", "init", "--workspace", str(tmp_path),
                "--issue", "https://github.com/OWNER/NAME/issues/57",
                "--repo", "OWNER/NAME", "--issue-number", "57")
    assert proc.returncode == 0, proc.stderr
    state = json.loads(proc.stdout)
    assert state["phase"] == "plan"
    assert state["attempts"] == {"plan": 0, "implement": 0, "test": 0,
                                  "review": 0}
    assert state["max_attempts"] == 3
    assert state["status"] == "running"
    assert state["issue_number"] == 57


def test_state_attempt_counts_and_reports_exhaustion(tmp_path):
    """The attempt counter reports when a phase has run out of attempts."""
    _run("supervise_state.py", "init", "--workspace", str(tmp_path),
         "--issue", "https://github.com/OWNER/NAME/issues/57")
    for attempt in (1, 2, 3):
        proc = _run("supervise_state.py", "attempt", "--workspace",
                    str(tmp_path), "--phase", "plan")
        assert proc.returncode == 0, proc.stderr
        result = json.loads(proc.stdout)
        assert result["attempt"] == attempt
        assert result["max_attempts"] == 3
        assert result["exhausted"] is (attempt == 3), result


def test_state_survives_between_processes(tmp_path):
    """The state lives on disk, so a fresh process sees the same loop."""
    _run("supervise_state.py", "init", "--workspace", str(tmp_path),
         "--issue", "https://github.com/OWNER/NAME/issues/57")
    _run("supervise_state.py", "set-branch", "--workspace", str(tmp_path),
         "--name", "fix/issue-57")
    _run("supervise_state.py", "set-pr", "--workspace", str(tmp_path),
         "--number", "58", "--url", "https://github.com/OWNER/NAME/pull/58")
    proc = _run("supervise_state.py", "show", "--workspace", str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    state = json.loads(proc.stdout)
    assert state["branch"] == "fix/issue-57"
    assert state["pr"]["number"] == 58
    assert state["status"] == "running"


def test_state_finish_marks_the_loop_done(tmp_path):
    """`finish` closes the loop."""
    _run("supervise_state.py", "init", "--workspace", str(tmp_path),
         "--issue", "https://github.com/OWNER/NAME/issues/57")
    proc = _run("supervise_state.py", "finish", "--workspace", str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    state = json.loads(proc.stdout)
    assert state["status"] == "done"


def test_state_rejects_an_unknown_phase(tmp_path):
    """An unknown phase is refused rather than silently recorded."""
    _run("supervise_state.py", "init", "--workspace", str(tmp_path),
         "--issue", "https://github.com/OWNER/NAME/issues/57")
    proc = _run("supervise_state.py", "attempt", "--workspace", str(tmp_path),
                "--phase", "deploy")
    assert proc.returncode != 0, "an unknown phase must be refused"


def test_state_file_is_not_committed(tmp_path):
    """The state lives under .leap-aide/, which the skill gitignores."""
    _run("gh_roles.py", "init", "--workspace", str(tmp_path))
    _run("supervise_state.py", "init", "--workspace", str(tmp_path),
         "--issue", "https://github.com/OWNER/NAME/issues/57")
    gitignore = tmp_path / ".leap-aide" / ".gitignore"
    assert gitignore.is_file(), "the .gitignore must cover the state file"
    state = tmp_path / ".leap-aide" / "supervise" / "state.json"
    assert state.is_file(), "the state file was not written"


# ── preflight.py ─────────────────────────────────────────────────────────

def test_preflight_reports_a_workspace_that_is_not_a_repository(tmp_path):
    """A workspace without a git repository is an error, not a warning."""
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    proc = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True)
    if proc.returncode == 0:
        pytest.skip("the tmp directory is inside a git repository")
    result = _run("preflight.py", "--workspace", str(tmp_path),
                  "--issue", "https://github.com/OWNER/NAME/issues/57")
    assert result.returncode == 1, "preflight must fail without a repository"
    report = json.loads(result.stdout)
    assert report["ok"] is False
    codes = [e["code"] for e in report["errors"]]
    assert "not-a-git-repository" in codes, report["errors"]
    for error in report["errors"]:
        assert error.get("fix"), "every error must carry a fix"


def test_preflight_reports_a_missing_role_config(tmp_path):
    """A workspace without the role config is an error."""
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    proc = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True)
    if proc.returncode == 0:
        pytest.skip("the tmp directory is inside a git repository")
    result = _run("preflight.py", "--workspace", str(tmp_path))
    assert result.returncode == 1
    report = json.loads(result.stdout)
    codes = [e["code"] for e in report["errors"]]
    assert "role-config-missing" in codes, report["errors"]


def test_preflight_rejects_a_malformed_issue_url(tmp_path):
    """An issue URL that is not a GitHub issue URL is refused."""
    result = _run("preflight.py", "--workspace", str(tmp_path),
                  "--issue", "not-a-url")
    assert result.returncode == 1
    report = json.loads(result.stdout)
    codes = [e["code"] for e in report["errors"]]
    assert "issue-url-invalid" in codes, report["errors"]
    assert report["issue_number"] is None


def test_preflight_parses_the_issue_number(tmp_path):
    """The issue number is extracted from the issue URL."""
    result = _run("preflight.py", "--workspace", str(tmp_path),
                  "--issue", "https://github.com/lingyue-lab/leap-aide/issues/57")
    report = json.loads(result.stdout)
    assert report["issue_number"] == 57


# ── arm_goal.py ──────────────────────────────────────────────────────────

def test_session_id_is_derived_from_the_webui_session_key():
    """The WebUI's session key carries the session id as its last segment."""
    arm_goal = _load_module("arm_goal.py")
    old_key = os.environ.get("HERMES_SESSION_KEY")
    old_id = os.environ.get("HERMES_SESSION_ID")
    try:
        os.environ["HERMES_SESSION_KEY"] = "agent:main:webui:dm:abc-123"
        assert arm_goal.resolve_session_id(None) == "abc-123"
        os.environ.pop("HERMES_SESSION_KEY")
        os.environ["HERMES_SESSION_ID"] = "plain-456"
        assert arm_goal.resolve_session_id(None) == "plain-456"
        assert arm_goal.resolve_session_id("explicit") == "explicit"
    finally:
        for name, value in (("HERMES_SESSION_KEY", old_key),
                            ("HERMES_SESSION_ID", old_id)):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@pytest.mark.skipif(
    not any((Path(p) / "run_agent.py").is_file()
            for p in (os.environ.get("HERMES_WEBUI_AGENT_DIR", ""),
                      "/usr/local/lib/hermes-agent")),
    reason="the hermes-agent SDK is not available")
def test_goal_can_be_armed_and_cleared(tmp_path):
    """The standing goal is set through the SDK's own GoalManager."""
    env = dict(os.environ)
    env["HERMES_HOME"] = str(tmp_path)
    env["HOME"] = str(tmp_path)
    session = "supervise-test-session"
    proc = _run("arm_goal.py", "--session-id", session, "arm",
                "--text", "Drive issue #57 to an LGTM'd PR.",
                "--outcome", "The PR carries an LGTM.",
                env=env)
    assert proc.returncode == 0, proc.stderr
    payload = proc.stdout[proc.stdout.index("{"):]
    state = json.loads(payload)
    assert state["goal"] == "Drive issue #57 to an LGTM'd PR."
    assert state["status"] == "active"
    assert state["contract"]["outcome"] == "The PR carries an LGTM."

    cleared = _run("arm_goal.py", "--session-id", session, "clear", env=env)
    assert cleared.returncode == 0, cleared.stderr
    assert json.loads(cleared.stdout[cleared.stdout.index("{"):])["status"] \
        == "cleared"


# ── the skill body itself ────────────────────────────────────────────────

def test_skill_always_delegates_as_a_leaf():
    """Every delegation uses role="leaf", so the tree stays one level deep."""
    raw = SKILL_MD.read_text(encoding="utf-8")
    assert 'role="leaf"' in raw, "the skill must pass role=\"leaf\""
    assert "orchestrator" not in raw, (
        "the skill must never delegate as an orchestrator")


def test_skill_never_passes_the_ignored_background_flag():
    """`background` is deprecated and ignored — the skill must omit it."""
    raw = SKILL_MD.read_text(encoding="utf-8")
    assert "background=" not in raw, (
        "the skill must not pass the ignored `background` parameter")


def test_skill_never_reads_the_credential_file_directly():
    """The token must reach the child as an env var, never as text."""
    raw = SKILL_MD.read_text(encoding="utf-8")
    assert "gh_roles.py" in raw, "the skill must use the credential wrapper"
    assert not re.search(r"cat\s+[^\n]*config\.yaml", raw), (
        "the skill must never read the credential config into the context")


def test_skill_covers_every_phase_and_schema():
    """Every phase, schema and helper script is documented in the skill."""
    raw = SKILL_MD.read_text(encoding="utf-8")
    for phase in SCHEMA_FILES:
        assert phase in raw, f"the skill must mention the {phase} phase"
        assert f"schemas/{phase}.schema.json" in raw, (
            f"the skill must point at the {phase} schema")
    for script in SCRIPTS:
        assert script in raw, f"the skill must document {script}"


def test_skill_states_the_loop_rules():
    """The rules the loop depends on are spelled out."""
    raw = SKILL_MD.read_text(encoding="utf-8")
    for rule in ("fix/issue-", "refs #", "LGTM"):
        assert rule in raw, f"the skill must state the `{rule}` rule"
    assert "approve" in raw, "the skill must tie the LGTM to an approve verdict"
