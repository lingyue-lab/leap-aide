"""Failing (RED) tests for scripts/install.sh Step 2c default plugin install.

Spec: docs/superpowers/plans/install-default-plugins.md Task 1 (brief:
.superpowers/sdd/install-default-plugins/task-1-brief.md). Pure stdlib.

Static tests read scripts/install.sh directly. Behaviour tests extract the
helper block with

    sed -n '/leap default plugins helpers (begin)/,/leap default plugins helpers (end)/p'

and run it through ``bash -c 'source <file>; install_default_plugins'`` with a
stub ``hermes`` (and a stub ``gh``) first on PATH; every stub call is recorded
to a log file together with GIT_ASKPASS/HERMES_HOME and, for install calls,
the value of plugins.scan_on_install in $HERMES_HOME/config.yaml at call time.

install.sh's top-of-file log helpers (warn/info/ok/die/say) are NOT part of
the extracted block, so the bash preamble re-defines them with the same output
markers (warn prints '!'); behaviour tests assert against those markers.
"""
import os
import re
import shlex
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"

_read_cache: dict = {}


def _install_sh() -> str:
    """Read scripts/install.sh (cached, lazy — a missing file fails only the caller)."""
    if "install.sh" not in _read_cache:
        assert INSTALL_SH.is_file(), f"scripts/install.sh is missing under {REPO_ROOT}"
        _read_cache["install.sh"] = INSTALL_SH.read_text(encoding="utf-8")
    return _read_cache["install.sh"]


def _first_line(text: str, needle: str):
    """1-based line number of the first line containing needle, else None."""
    for number, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return number
    return None


def _step2c_section(text: str) -> str:
    """The Step 2c feature text: helpers begin (or Step 2c marker) .. Step 3 marker."""
    lines = text.splitlines(keepends=True)
    start = None
    for index, line in enumerate(lines):
        if "leap default plugins helpers (begin)" in line or "Step 2c" in line:
            start = index
            break
    assert start is not None, (
        "install.sh has neither the Step 2c marker "
        "(' # ── Step 2c: Install default plugins ── ') nor the helpers "
        "begin marker ('# --- leap default plugins helpers (begin) ---')"
    )
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if "Step 3:" in lines[index]:
            end = index
            break
    return "".join(lines[start:end])


_STUB_HERMES = """#!/usr/bin/env bash
# Stub `hermes` CLI for behaviour tests: records every call (args + the env
# the installer scopes to it + plugins.scan_on_install at call time).
scan_value="absent"
if [ -f "${HERMES_HOME:-}/config.yaml" ]; then
  raw="$(grep -E '^[[:space:]]*scan_on_install:' "${HERMES_HOME}/config.yaml" 2>/dev/null | head -n 1 | sed -E 's/^[[:space:]]*scan_on_install:[[:space:]]*//')"
  if [ -n "$raw" ]; then scan_value="$raw"; fi
fi
{
  printf 'CALL'
  for arg in "$@"; do printf ' | %s' "$arg"; done
  printf ' | GIT_ASKPASS=%s | HERMES_HOME=%s | SCAN_ON_INSTALL=%s\\n' \\
    "${GIT_ASKPASS:-}" "${HERMES_HOME:-}" "$scan_value"
} >> "$STUB_LOG"
case "${1:-} ${2:-}" in
  "plugins show")
    if [ "${STUB_SHOW_RC:-1}" = "0" ]; then
      printf 'name: %s\\nenabled: false\\n' "${3:-}"
      exit 0
    fi
    exit 1
    ;;
  "plugins install"|"plugins enable")
    exit "${STUB_PLUGIN_RC:-0}"
    ;;
esac
exit 0
"""

_STUB_GH = """#!/usr/bin/env bash
# Stub `gh`: `gh auth token` prints a token or fails per STUB_GH_RC.
if [ "${1:-}" = "auth" ] && [ "${2:-}" = "token" ]; then
  if [ "${STUB_GH_RC:-0}" = "0" ]; then
    printf '%s\\n' "${STUB_GH_TOKEN:-gh-stub-token}"
    exit 0
  fi
  exit "${STUB_GH_RC}"
fi
exit 0
"""

# install.sh's log helpers live at the top of the file (outside the extracted
# block); mirror their output markers so behaviour tests can assert on them.
_LOG_HELPERS = (
    'warn() { printf "! %s\\n" "$*"; }; '
    'info() { printf "  %s\\n" "$*"; }; '
    'ok() { printf "ok %s\\n" "$*"; }; '
    'say() { printf "%s\\n" "$*"; }; '
    'die() { printf "die %s\\n" "$*" >&2; exit 1; }; '
    '_log() { :; }; '
)


class _Fixture:
    """Sandbox: extracted helpers + stub bin dir + call log + scratch TMPDIR."""

    def __init__(self, root: Path, env: dict, log: Path, scratch: Path, helpers: Path):
        self.root = root
        self.env = env
        self.log = log
        self.scratch = scratch
        self.helpers = helpers

    @property
    def hermes_home(self) -> Path:
        return Path(self.env["HERMES_HOME"])

    def config_text(self) -> str:
        return (self.hermes_home / "config.yaml").read_text(encoding="utf-8")


def _extract_helpers(dest: Path) -> Path:
    """sed-extract the helper block from scripts/install.sh into dest."""
    result = subprocess.run(
        [
            "sed",
            "-n",
            "/leap default plugins helpers (begin)/,/leap default plugins helpers (end)/p",
            str(INSTALL_SH),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    dest.write_text(result.stdout, encoding="utf-8")
    return dest


def _fixture(
    tmp_path: Path,
    *,
    show_rc: int = 1,
    gh_rc: int = 0,
    gh_token: str = "gh-stub-token",
) -> _Fixture:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    for name, body in (("hermes", _STUB_HERMES), ("gh", _STUB_GH)):
        stub = bin_dir / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o755)
    log = tmp_path / "calls.log"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    hermes_home = tmp_path / "hermes_home"
    hermes_home.mkdir()
    helpers = _extract_helpers(tmp_path / "helpers.sh")

    env = dict(os.environ)
    # Credentials and mode must come from the test, never from the runner.
    for var in ("HERMES_GITHUB_TOKEN_FILE", "GITHUB_TOKEN", "GH_TOKEN", "IN_DOCKER"):
        env.pop(var, None)
    env.update(
        PATH=f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
        STUB_LOG=str(log),
        STUB_SHOW_RC=str(show_rc),
        STUB_GH_RC=str(gh_rc),
        STUB_GH_TOKEN=gh_token,
        HERMES_HOME=str(hermes_home),
        TMPDIR=str(scratch),
    )
    return _Fixture(tmp_path, env, log, scratch, helpers)


def _run(fx: _Fixture, body: str) -> "subprocess.CompletedProcess":
    """bash -c 'log helpers; source <extracted block>; <body>' with fixture env."""
    command = _LOG_HELPERS + f"source {shlex.quote(str(fx.helpers))}; " + body
    return subprocess.run(
        ["bash", "-c", command],
        env=fx.env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _install(fx: _Fixture) -> "subprocess.CompletedProcess":
    return _run(fx, "install_default_plugins")


def _calls(fx: _Fixture) -> list:
    if not fx.log.exists():
        return []
    return [
        line
        for line in fx.log.read_text(encoding="utf-8").splitlines()
        if line.startswith("CALL")
    ]


def _field(line: str, name: str) -> str:
    """Value of a ``NAME=value`` field from a recorded stub call."""
    for part in line.split(" | "):
        if part.startswith(name + "="):
            return part[len(name) + 1:]
    return ""


def test_step2c_position():
    """Line order must be Step 2 < Step 2b < Step 2c < Step 3 (strictly)."""
    text = _install_sh()
    markers = {
        "Step 2:": _first_line(text, "Step 2:"),
        "Step 2b": _first_line(text, "Step 2b"),
        "Step 2c": _first_line(text, "Step 2c"),
        "Step 3:": _first_line(text, "Step 3:"),
    }
    missing = [name for name, number in markers.items() if number is None]
    assert not missing, f"install.sh is missing marker line(s): {missing}"
    n2 = markers["Step 2:"]
    n2b = markers["Step 2b"]
    n2c = markers["Step 2c"]
    n3 = markers["Step 3:"]
    assert n2 < n2b < n2c < n3, (
        "Step 2c must be inserted between Step 2b and Step 3 with strictly "
        f"increasing line numbers; got Step 2={n2}, Step 2b={n2b}, "
        f"Step 2c={n2c}, Step 3={n3}"
    )


def test_install_commands_present():
    """install.sh must carry the exact install commands from the spec."""
    text = _install_sh()
    assert "plugins install obra/superpowers --enable" in text, (
        "install.sh must contain the literal command "
        "'plugins install obra/superpowers --enable'"
    )
    assert "lingyue-lab/wiserkit" in text, (
        "install.sh must reference the wiserkit plugin 'lingyue-lab/wiserkit'"
    )
    assert "--enable" in text, "plugins must be installed with --enable"


def test_no_token_in_persistent_state():
    """Nothing in the Step 2c feature may persist the token (static scan)."""
    section = _step2c_section(_install_sh())
    env_appends = [
        line.strip()
        for line in section.splitlines()
        if ">>" in line and ".env" in line
    ]
    assert not env_appends, (
        "Step 2c must not append to $HERMES_HOME/.env: " + "; ".join(env_appends)
    )
    url = re.search(r"""https://[^\s"'`]*@""", section)
    assert url is None, (
        "Step 2c must not build a token-bearing URL (https://<token>@...): "
        + url.group(0)
    )
    export = re.search(r"\bexport\s+GITHUB_TOKEN\b", section)
    assert export is None, (
        "Step 2c must not export GITHUB_TOKEN into persistent state: "
        + export.group(0)
    )


def test_wiserkit_token_resolution_order(tmp_path):
    """_leap_resolve_wiserkit_token: file > GITHUB_TOKEN > GH_TOKEN > gh; else rc 1."""
    # (a) HERMES_GITHUB_TOKEN_FILE content wins over GITHUB_TOKEN (newline trimmed)
    fx = _fixture(tmp_path / "file_first")
    token_file = fx.root / "token_file"
    token_file.write_text("file-token\n", encoding="utf-8")
    fx.env["HERMES_GITHUB_TOKEN_FILE"] = str(token_file)
    fx.env["GITHUB_TOKEN"] = "env-token"
    fx.env["GH_TOKEN"] = "gh-token"
    proc = _run(fx, "_leap_resolve_wiserkit_token")
    assert proc.returncode == 0, f"expected a token, rc={proc.returncode}: {proc.stderr}"
    assert proc.stdout.strip() == "file-token", (
        f"file content must beat GITHUB_TOKEN, got {proc.stdout!r}"
    )

    # (b) GITHUB_TOKEN wins over GH_TOKEN and over `gh auth token`
    fx = _fixture(tmp_path / "env_token")
    fx.env["GITHUB_TOKEN"] = "env-token"
    fx.env["GH_TOKEN"] = "gh-token"
    proc = _run(fx, "_leap_resolve_wiserkit_token")
    assert proc.returncode == 0, f"expected a token, rc={proc.returncode}: {proc.stderr}"
    assert proc.stdout.strip() == "env-token", (
        f"GITHUB_TOKEN must beat GH_TOKEN, got {proc.stdout!r}"
    )

    # (c) GH_TOKEN wins over `gh auth token`
    fx = _fixture(tmp_path / "gh_token")
    fx.env["GH_TOKEN"] = "gh-token"
    proc = _run(fx, "_leap_resolve_wiserkit_token")
    assert proc.returncode == 0, f"expected a token, rc={proc.returncode}: {proc.stderr}"
    assert proc.stdout.strip() == "gh-token", (
        f"GH_TOKEN must beat `gh auth token`, got {proc.stdout!r}"
    )

    # (d) fall back to `gh auth token`
    fx = _fixture(tmp_path / "gh_auth")
    proc = _run(fx, "_leap_resolve_wiserkit_token")
    assert proc.returncode == 0, f"expected a token, rc={proc.returncode}: {proc.stderr}"
    assert proc.stdout.strip() == "gh-stub-token", (
        f"`gh auth token` must be the last resort, got {proc.stdout!r}"
    )

    # (e) no credential anywhere (gh auth token fails) -> return 1
    fx = _fixture(tmp_path / "nothing", gh_rc=1)
    proc = _run(fx, "_leap_resolve_wiserkit_token")
    assert proc.returncode == 1, (
        f"with no credential available rc must be 1, got {proc.returncode}"
    )
    assert proc.stdout.strip() == "", (
        f"no token may be printed when nothing resolves, got {proc.stdout!r}"
    )

    # (f) an empty token file counts as no credential -> falls through
    fx = _fixture(tmp_path / "empty_file")
    token_file = fx.root / "empty_token_file"
    token_file.write_text("", encoding="utf-8")
    fx.env["HERMES_GITHUB_TOKEN_FILE"] = str(token_file)
    fx.env["GITHUB_TOKEN"] = "env-token"
    proc = _run(fx, "_leap_resolve_wiserkit_token")
    assert proc.returncode == 0, f"expected a token, rc={proc.returncode}: {proc.stderr}"
    assert proc.stdout.strip() == "env-token", (
        f"an empty token file must fall through to GITHUB_TOKEN, got {proc.stdout!r}"
    )

def _assert_scan_off_during_install(fx: _Fixture) -> None:
    """Gate A mechanism 2: config must read scan_on_install: false at install time."""
    installs = [c for c in _calls(fx) if " | plugins | install |" in c]
    assert installs, (
        "the fixture must observe `hermes plugins install` calls via the stub "
        "hermes — nothing was installed, so the scan toggle cannot be verified"
    )
    for call in installs:
        assert _field(call, "SCAN_ON_INSTALL") == "false", (
            "plugins.scan_on_install must be false in $HERMES_HOME/config.yaml "
            "while `hermes plugins install` runs (Gate A mechanism 2): " + call
        )


def test_no_credential_degrades(tmp_path):
    """No credentials: wiserkit skipped with a warn, superpowers installed, rc 0."""
    fx = _fixture(tmp_path, gh_rc=1)
    proc = _install(fx)
    calls = _calls(fx)
    wiserkit = [c for c in calls if "wiserkit" in c]
    assert not wiserkit, (
        "wiserkit must never be invoked without credentials, got: " + str(wiserkit)
    )
    assert any("superpowers" in c for c in calls), (
        "superpowers must still be installed when wiserkit is skipped; "
        f"observed calls: {calls}"
    )
    output = proc.stdout + proc.stderr
    assert "!" in output, (
        "install_default_plugins must warn when wiserkit has no credentials "
        "(install.sh's warn() prints '!'); output was: " + output
    )
    assert proc.returncode == 0, (
        f"a credential-less run must exit 0 (degrade, never die), "
        f"rc={proc.returncode}: {output}"
    )


def test_credential_scoped_askpass(tmp_path):
    """With credentials: GIT_ASKPASS wraps only the wiserkit install; all temp files cleaned."""
    fx = _fixture(tmp_path)
    fx.env["GITHUB_TOKEN"] = "test-secret-token"
    proc = _install(fx)
    calls = _calls(fx)
    wiserkit_installs = [
        c for c in calls if " | plugins | install |" in c and "wiserkit" in c
    ]
    superpowers_calls = [c for c in calls if "superpowers" in c]
    assert wiserkit_installs, (
        "wiserkit must be installed when credentials are available; "
        f"observed calls: {calls}"
    )
    assert superpowers_calls, f"superpowers must be installed; observed calls: {calls}"

    wiserkit_call = wiserkit_installs[0]
    askpass = _field(wiserkit_call, "GIT_ASKPASS")
    assert askpass, (
        "the wiserkit install must run with GIT_ASKPASS set: " + wiserkit_call
    )
    assert _field(wiserkit_call, "HERMES_HOME"), (
        "the wiserkit install must receive HERMES_HOME explicitly: " + wiserkit_call
    )
    for call in superpowers_calls:
        assert not _field(call, "GIT_ASKPASS"), (
            "superpowers is a public clone and must never see GIT_ASKPASS: " + call
        )
    assert not os.path.exists(askpass), (
        f"the askpass script {askpass} must be deleted after the install"
    )
    leftovers = sorted(p.name for p in fx.scratch.iterdir())
    assert leftovers == [], (
        "askpass/token temp files must be cleaned up on every path "
        f"(trap-style cleanup); left behind: {leftovers}"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_idempotent_skip(tmp_path):
    """show rc 0: no install call; installed-but-disabled plugins only get `plugins enable`."""
    fx = _fixture(tmp_path, show_rc=0)
    fx.env["GITHUB_TOKEN"] = "test-secret-token"
    proc = _install(fx)
    calls = _calls(fx)
    shows = [c for c in calls if " | plugins | show |" in c]
    enables = [c for c in calls if " | plugins | enable |" in c]
    installs = [c for c in calls if " | plugins | install |" in c]
    assert shows, (
        "install_default_plugins must probe with `hermes plugins show <name>`; "
        f"observed calls: {calls}"
    )
    assert not installs, (
        "`plugins install` must be skipped when `plugins show` returns 0, got: "
        + str(installs)
    )
    assert enables, (
        "an installed-but-disabled plugin must be brought up with "
        "`hermes plugins enable <name>`; observed calls: " + str(calls)
    )
    others = [
        c
        for c in calls
        if " | plugins | show |" not in c
        and " | plugins | enable |" not in c
        and " | plugins | install |" not in c
    ]
    assert not others, f"no other hermes subcommands may run: {others}"
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_scan_toggle_restored(tmp_path):
    """Gate A: plugins.scan_on_install is false only during install, then restored exactly."""
    base = "display:\n  show_reasoning: true\n"

    # (a) config without the key: the key must still be absent afterwards
    fx_a = _fixture(tmp_path / "no_key")
    (fx_a.hermes_home / "config.yaml").write_text(base, encoding="utf-8")
    proc_a = _install(fx_a)
    assert proc_a.returncode == 0, proc_a.stdout + proc_a.stderr
    _assert_scan_off_during_install(fx_a)
    assert "scan_on_install" not in fx_a.config_text(), (
        "a config that lacked plugins.scan_on_install must not gain the key: "
        + fx_a.config_text()
    )

    # (b) config with the key true: the value must be restored to true afterwards
    fx_b = _fixture(tmp_path / "key_true")
    (fx_b.hermes_home / "config.yaml").write_text(
        base + "plugins:\n  scan_on_install: true\n", encoding="utf-8"
    )
    proc_b = _install(fx_b)
    assert proc_b.returncode == 0, proc_b.stdout + proc_b.stderr
    _assert_scan_off_during_install(fx_b)
    assert re.search(r"scan_on_install:\s*true", fx_b.config_text()), (
        "plugins.scan_on_install: true must be restored after the install: "
        + fx_b.config_text()
    )


# ---------------------------------------------------------------------------
# Task 2: Dockerfile — sdk-stage github_token secret mount for plugin install.
# Spec: docs/superpowers/plans/install-default-plugins.md Task 2 (brief:
# .superpowers/sdd/install-default-plugins/task-2-brief.md). Pure stdlib.
#
# Static tests: they read the RUN block whose body invokes
# `bash /app/scripts/install.sh` and assert (a) the github_token secret
# mount is appended to the existing git_identity_key mount, (b) the RUN body
# conditionally exports HERMES_GITHUB_TOKEN_FILE from that mount path, and
# (c) no --build-arg line mentions TOKEN (credentials must never enter the
# image config/history).
# ---------------------------------------------------------------------------

DOCKERFILE = REPO_ROOT / "Dockerfile"


def _dockerfile() -> str:
    """Read the Dockerfile (cached, lazy — mirrors _install_sh)."""
    if "Dockerfile" not in _read_cache:
        assert DOCKERFILE.is_file(), f"Dockerfile is missing under {REPO_ROOT}"
        _read_cache["Dockerfile"] = DOCKERFILE.read_text(encoding="utf-8")
    return _read_cache["Dockerfile"]


def _install_sh_run() -> str:
    """The RUN instruction whose body invokes `bash /app/scripts/install.sh`.

    Walks the Dockerfile from every ``RUN `` line and follows backslash
    continuations; full-line ``#`` comments inside a continuation remain
    part of the instruction (matching BuildKit's parser, which strips them
    before execution but keeps the instruction open). Returns the first
    block containing the install.sh invocation.
    """
    lines = _dockerfile().splitlines()
    index = 0
    while index < len(lines):
        if not lines[index].startswith("RUN "):
            index += 1
            continue
        block = [lines[index]]
        index += 1
        if block[0].rstrip().endswith("\\"):
            while index < len(lines):
                line = lines[index]
                block.append(line)
                index += 1
                if line.strip().startswith("#"):
                    continue  # comment inside a continuation stays in-block
                if not line.rstrip().endswith("\\"):
                    break
        text = "\n".join(block)
        if "bash /app/scripts/install.sh" in text:
            return text
    raise AssertionError(
        "no Dockerfile RUN block invokes `bash /app/scripts/install.sh`"
    )


def test_dockerfile_github_token_mount():
    """install.sh's RUN mounts github_token in addition to git_identity_key."""
    block = _install_sh_run()
    token_mount = (
        "--mount=type=secret,id=github_token,"
        "dst=/run/secrets/leap-aide-github-token,mode=0400,required=false"
    )
    key_mount = (
        "--mount=type=secret,id=git_identity_key,"
        "dst=/root/.ssh/id_ed25519_install,mode=0400,required=false"
    )
    assert token_mount in block, (
        "the RUN invoking install.sh must mount the github_token secret at "
        "/run/secrets/leap-aide-github-token (mode=0400, required=false); "
        f"block was:\n{block}"
    )
    assert key_mount in block, (
        "the github_token mount must be APPENDED to the existing "
        f"id=git_identity_key mount, not replace it; block was:\n{block}"
    )
    assert block.index(key_mount) < block.index(token_mount), (
        "the github_token mount must come after the git_identity_key mount "
        "(append, do not replace)"
    )


def test_dockerfile_exports_token_file_conditionally():
    """Same RUN body: guarded conditional export of HERMES_GITHUB_TOKEN_FILE."""
    block = _install_sh_run()
    assert "[ -f /run/secrets/leap-aide-github-token ] &&" in block, (
        "the install.sh RUN must guard the export with the literal "
        "`[ -f /run/secrets/leap-aide-github-token ] &&` so a secret-less "
        f"build leaves the variable empty; block was:\n{block}"
    )
    assert (
        "export HERMES_GITHUB_TOKEN_FILE=/run/secrets/leap-aide-github-token"
        in block
    ), (
        "the install.sh RUN must export HERMES_GITHUB_TOKEN_FILE to the "
        f"secret path when the file exists; block was:\n{block}"
    )
    assert 'export HERMES_GITHUB_TOKEN_FILE=""' in block, (
        "HERMES_GITHUB_TOKEN_FILE must default to the empty string when the "
        f"secret was not provided; block was:\n{block}"
    )


def test_no_token_build_arg():
    """No --build-arg may share a line with TOKEN — creds never in history."""
    offenders = [
        (number, line)
        for number, line in enumerate(_dockerfile().splitlines(), 1)
        if "--build-arg" in line and "TOKEN" in line.upper()
    ]
    assert not offenders, (
        "credentials must never be passed as --build-arg (they land in the "
        f"image config/history); offending lines: {offenders}"
    )


# ---------------------------------------------------------------------------
# Task 3: scripts/build.sh — forward the github_token secret to the image
# build. Spec: docs/superpowers/plans/install-default-plugins.md Task 3
# (brief: .superpowers/sdd/install-default-plugins/task-3-brief.md). Pure
# stdlib.
#
# Static tests over scripts/build.sh: (a) the secrets array gains
# `--secret id=github_token,src=<file>` in the same segment as both
# credential sources (BUILD_GITHUB_TOKEN_FILE, else GITHUB_TOKEN/GH_TOKEN),
# (b) the env-token temp file is chmod 600 and removed at build end
# (trap/rm), (c) the build_args block carries no credential TOKEN — the
# only TOKEN-bearing entry is BUILD_GH_TOKEN_DOWNLOAD_HOST, a download
# host name the brief keeps untouched.
# ---------------------------------------------------------------------------

BUILD_SH = REPO_ROOT / "scripts" / "build.sh"


def _build_sh() -> str:
    """Read scripts/build.sh (cached, lazy — mirrors _install_sh)."""
    if "build.sh" not in _read_cache:
        assert BUILD_SH.is_file(), f"scripts/build.sh is missing under {REPO_ROOT}"
        _read_cache["build.sh"] = BUILD_SH.read_text(encoding="utf-8")
    return _read_cache["build.sh"]


def _buildsh_github_token_section() -> str:
    """The contiguous segment from the first credential source
    (BUILD_GITHUB_TOKEN_FILE) through the `--secret id=github_token,src=`
    append line — the 'same segment' the forwarding must live in."""
    text = _build_sh()
    start = text.find("BUILD_GITHUB_TOKEN_FILE")
    assert start != -1, (
        "build.sh must consume BUILD_GITHUB_TOKEN_FILE as github_token "
        "credential source #1 (not found)"
    )
    marker = "id=github_token,src="
    end = text.find(marker, start)
    assert end != -1, (
        "build.sh must append `--secret id=github_token,src=<file>` after "
        f"its credential-source logic (marker {marker!r} not found after "
        "BUILD_GITHUB_TOKEN_FILE)"
    )
    line_end = text.find("\n", end)
    return text[start:] if line_end == -1 else text[start:line_end]


def _buildsh_build_args_block() -> str:
    """The `local -a build_args=( ... )` array literal, verbatim."""
    lines = _build_sh().splitlines()
    start = next(
        (i for i, line in enumerate(lines) if "local -a build_args=(" in line),
        None,
    )
    assert start is not None, "build.sh has no `local -a build_args=( ... )` array"
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].rstrip() == "  )"),
        None,
    )
    assert end is not None, "build.sh's build_args array has no closing `  )` line"
    return "\n".join(lines[start : end + 1])


def test_buildsh_forwards_github_token_secret():
    """`--secret id=github_token,src=` must appear in the same segment as
    both credential sources: BUILD_GITHUB_TOKEN_FILE, else GITHUB_TOKEN/GH_TOKEN."""
    section = _buildsh_github_token_section()
    assert '--secret "id=github_token,src=' in section, (
        "build.sh must append `--secret \"id=github_token,src=<file>\"` to "
        f"the secrets array in the credential-source segment; section:\n{section}"
    )
    # $-anchored so BUILD_GITHUB_TOKEN_FILE's own name cannot satisfy the
    # GITHUB_TOKEN check; the env vars must actually be referenced.
    assert re.search(r"\$\{GITHUB_TOKEN:-", section), (
        "the github_token segment must read GITHUB_TOKEN as a credential "
        f"source; section:\n{section}"
    )
    assert re.search(r"\$\{GH_TOKEN:-", section), (
        "the github_token segment must read GH_TOKEN as a credential "
        f"source; section:\n{section}"
    )


def test_buildsh_temp_file_is_0600_and_cleaned():
    """The GITHUB_TOKEN/GH_TOKEN temp file: mktemp + chmod 600 + removed at
    build end (trap/rm)."""
    text = _build_sh()
    start = text.find("mktemp")
    assert start != -1, (
        "build.sh must materialise GITHUB_TOKEN/GH_TOKEN into a mktemp file "
        "for the --secret src= path (no mktemp call found)"
    )
    end = text.find("local -a build_args=(")
    assert end != -1 and start < end, (
        "the token temp file must be set up before the build_args array "
        f"(mktemp@{start}, build_args@{end})"
    )
    section = text[start:end]
    assert re.search(r"\bchmod\s+600\b", section), (
        "the token temp file must be chmod 600 before it is used as "
        f"--secret src; section:\n{section}"
    )
    trap_lines = [
        line for line in section.splitlines() if re.search(r"\btrap\b", line)
    ]
    assert trap_lines, (
        "the token temp file must be removed at build end by a registered "
        f"trap; section:\n{section}"
    )
    assert any("EXIT" in line for line in trap_lines), (
        "the cleanup trap must fire on EXIT (build end): " + str(trap_lines)
    )
    assert any(re.search(r"\brm\b", line) for line in trap_lines), (
        "the EXIT trap must rm the temp file: " + str(trap_lines)
    )


def test_buildsh_no_token_build_arg():
    """build_args must carry no credential TOKEN: token values never become
    build-args (they would land in image config/history)."""
    block = _buildsh_build_args_block()
    # The only TOKEN-bearing entry is BUILD_GH_TOKEN_DOWNLOAD_HOST — a
    # download host name, not a credential; the brief keeps build_args
    # unchanged, so it is the single allowed occurrence.
    offenders = [
        line.strip()
        for line in block.splitlines()
        if "TOKEN" in line and "BUILD_GH_TOKEN_DOWNLOAD_HOST" not in line
    ]
    assert not offenders, (
        "no credential TOKEN may appear in build_args (build-args land in "
        f"image history); offending lines: {offenders}"
    )
    cred_refs = [
        line.strip()
        for line in block.splitlines()
        if re.search(r"\$\{?(GITHUB_TOKEN|GH_TOKEN)\b", line)
    ]
    assert not cred_refs, (
        "build_args must never expand GITHUB_TOKEN/GH_TOKEN; "
        f"offending lines: {cred_refs}"
    )
