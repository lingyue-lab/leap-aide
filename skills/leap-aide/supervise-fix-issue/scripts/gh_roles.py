#!/usr/bin/env python3
"""gh_roles.py — per-role GitHub credential wrapper for supervise-fix-issue.

Every role in the supervise loop (supervisor, planner, implementer, tester,
reviewer) authenticates to GitHub with its OWN credential, so the comments a
phase leaves on the issue and the pull request are attributable to that role
rather than to one shared human account.

The credential lives in the WORKSPACE, never in the model's context:

    <workspace>/.leap-aide/secrets/github/config.yaml      (mode 0600)
    <workspace>/.leap-aide/.gitignore                        (ignores the whole tree)

Commands
--------
    gh_roles.py init [--workspace DIR] [--force]
        Write the config template and the .gitignore. Never overwrites an
        existing config unless --force is given.

    gh_roles.py doctor [--workspace DIR] [--role NAME]
        Validate every role's credential and print a detailed, actionable
        report. Exit code 0 when every checked role works, 1 otherwise.

    gh_roles.py show [--workspace DIR]
        Print the config with every secret redacted.

    gh_roles.py login --role NAME [--workspace DIR]
        Run `gh auth login --with-token` into a role-private GH_CONFIG_DIR,
        so the user's own `gh` login is never overwritten.

    gh_roles.py run --role NAME -- <command> [args...]
        Run <command> with the role's token in the environment. The token is
        injected as GH_TOKEN and GITHUB_TOKEN (gh honours the former, the
        bundled GitHub skills look for the latter) and never printed.

A GitHub App role is minted on demand: `gh token generate --key <pem>
--app-id <id> --installation-id <id> --token-only` (the `gh-token` extension
is part of the LeapAide image).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

CONFIG_REL = Path(".leap-aide") / "secrets" / "github" / "config.yaml"
GITIGNORE_REL = Path(".leap-aide") / ".gitignore"
GH_CONFIG_REL = Path(".leap-aide") / "secrets" / "github" / "gh"

ROLES = ("supervisor", "planner", "implementer", "tester", "reviewer")

CONFIG_TEMPLATE = """\
# GitHub credentials for the supervise-fix-issue skill.
#
# Every role authenticates separately so that the comments a phase leaves on
# the issue and the pull request are attributable to that role.
#
# kind: pat  -> a personal access token (classic or fine-grained).
# kind: app  -> a GitHub App installation; the token is minted on demand from
#               private_key_path + app_id + installation_id.
#
# This file is mode 0600 and lives under .leap-aide/, which .gitignore
# excludes from the repository. Never paste a token into a chat message.
version: 1
roles:
  supervisor:
    kind: pat
    token: ""          # Personal access token with `repo` scope
  planner:
    kind: pat
    token: ""          # Personal access token with `repo` scope
  implementer:
    kind: pat
    token: ""          # Personal access token with `repo` scope
  tester:
    kind: pat
    token: ""          # Personal access token with `repo` scope
  reviewer:
    kind: pat
    token: ""          # Personal access token with `repo` scope
    # kind: app       # Switch to a GitHub App installation instead:
    # private_key_path: /abs/path/to/app-private-key.pem
    # app_id: "000000"
    # installation_id: "00000000"
"""

# Detailed, copy-pasteable guidance for every failure mode `doctor` reports.
GUIDANCE = {
    "config-missing": (
        "No role configuration found.\n"
        "  1. Run:  python3 {skill}/scripts/gh_roles.py init\n"
        "  2. Open the generated file:  {config}\n"
        "  3. Fill in each role's `token:` (or the GitHub App fields).\n"
        "  4. Re-run:  python3 {skill}/scripts/gh_roles.py doctor"
    ),
    "token-missing": (
        "The `{role}` role has no token.\n"
        "  Create a personal access token:\n"
        "    - Classic PAT: https://github.com/settings/tokens/new\n"
        "      Tick the `repo` scope (it covers issues, pull requests and\n"
        "      code push on private repositories). Tick `read:org` only if\n"
        "      the repository lives in an organization with SSO enabled.\n"
        "    - Fine-grained PAT: https://github.com/settings/personal-access-tokens/new\n"
        "      Repository access: select the target repository only.\n"
        "      Permissions -> Repository permissions:\n"
        "        Contents: Read and write   (push the branch, open the PR)\n"
        "        Issues: Read and write     (comment on the issue)\n"
        "        Pull requests: Read and write (comment on / approve the PR)\n"
        "        Metadata: Read-only (always granted)\n"
        "      Generate the token, copy it once, and paste it into the\n"
        "      `token:` field of the `{role}` role in {config}."
    ),
    "token-invalid": (
        "The `{role}` token was rejected by GitHub.\n"
        "  Check each of the following, in order:\n"
        "    - The token was copied in full (GitHub shows it once; a partial\n"
        "      paste is the most common cause).\n"
        "    - The token has not expired or been revoked:\n"
        "        https://github.com/settings/tokens  (classic)\n"
        "        https://github.com/settings/personal-access-tokens (fine-grained)\n"
        "    - The token's owner still has write access to the repository, and\n"
        "      the repository's organization has not revoked the token.\n"
        "    - The token carries the `repo` scope (classic) or the Contents /\n"
        "      Issues / Pull requests write permissions (fine-grained)."
    ),
    "token-scope": (
        "The `{role}` token authenticated but is missing a permission.\n"
        "  GitHub answered the request but refused the operation, which means\n"
        "  the token is valid yet under-scoped. Grant the missing permission:\n"
        "    - To comment on an issue:      Issues: Read and write\n"
        "    - To comment on / approve a PR: Pull requests: Read and write\n"
        "    - To push a branch:            Contents: Read and write\n"
        "  Classic tokens cover all three with the single `repo` scope.\n"
        "  Edit the token at https://github.com/settings/tokens and copy the\n"
        "  new value into {config}."
    ),
    "app-key-missing": (
        "The `{role}` role is a GitHub App, but `private_key_path` does not\n"
        "point at a readable file.\n"
        "  1. Open the app's settings: https://github.com/settings/apps/<your-app>\n"
        "  2. Under 'Private key', click 'Generate a private key' and download\n"
        "     the .pem file.\n"
        "  3. Put the file somewhere outside the repository (for example\n"
        "     ~/.config/leap-aide/app.pem) and set `private_key_path:` to its\n"
        "     absolute path. The file must be mode 0600.\n"
        "  4. `app_id` is on the same settings page ('App ID');\n"
        "     `installation_id` is listed under 'Installations' (or run\n"
        "     `gh token installations --key <pem> --app-id <id>` to list them)."
    ),
    "app-id-missing": (
        "The `{role}` role is a GitHub App but `app_id` is not set.\n"
        "  The App ID is on the app's settings page:\n"
        "    https://github.com/settings/apps/<your-app>  -> 'App ID'\n"
        "  Copy the numeric value into {config}."
    ),
    "app-installation-missing": (
        "The `{role}` role is a GitHub App but `installation_id` is not set.\n"
        "  Install the app on the target repository:\n"
        "    https://github.com/settings/apps/<your-app>/installations/new\n"
        "  Then list the installation ids:\n"
        "    gh token installations --key <pem> --app-id <app_id>\n"
        "  Copy the id into {config}."
    ),
    "app-mint-failed": (
        "The GitHub App token could not be minted.\n"
        "  The usual causes, in order of likelihood:\n"
        "    - The app is not installed on the target repository, or the\n"
        "      installation was removed. Reinstall it at\n"
        "      https://github.com/settings/apps/<your-app>/installations/new\n"
        "    - The private key does not match the app (a key from another\n"
        "      app, or a regenerated key while the config still names the\n"
        "      old .pem file).\n"
        "    - The installation_id belongs to a different organization than\n"
        "      the repository.\n"
        "  Verify the plumbing by hand:\n"
        "    gh token installations --key <pem> --app-id <app_id>"
    ),
    "gh-missing": (
        "The `gh` CLI is not on PATH.\n"
        "  LeapAide ships `gh` in its runtime image; if this runs outside the\n"
        "  container, install the GitHub CLI:\n"
        "    https://cli.github.com/  ->  'Install gh'"
    ),
    "no-roles": (
        "The config file has no `roles:` mapping.\n"
        "  Add one entry per role (supervisor, planner, implementer, tester,\n"
        "  reviewer). Run `gh_roles.py init --force` to regenerate the\n"
        "  template, then paste the tokens back in."
    ),
}


def fail(message: str, code: int = 2) -> "int":
    print(f"error: {message}", file=sys.stderr)
    return code


def workspace_root(explicit: str | None = None) -> Path:
    """Resolve the workspace root that holds the .leap-aide tree."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    env_ws = os.environ.get("HERMES_WORKSPACE") or os.environ.get("WORKSPACE")
    if env_ws:
        return Path(env_ws).expanduser().resolve()
    return Path.cwd().resolve()


def skill_dir() -> Path:
    """Absolute path of this skill directory (scripts/ lives inside it)."""
    return Path(__file__).resolve().parent.parent


def config_path(workspace: Path) -> Path:
    return workspace / CONFIG_REL


def gh_config_dir(workspace: Path, role: str) -> Path:
    return workspace / GH_CONFIG_REL / role


def _yaml():
    try:
        import yaml  # type: ignore[import-any]
    except ImportError:
        print(
            "error: PyYAML is required to read the role config.\n"
            "       Install it with:  pip install pyyaml",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return yaml


def load_config(workspace: Path) -> dict[str, Any] | None:
    path = config_path(workspace)
    if not path.is_file():
        return None
    yaml = _yaml()
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"error: {path} is not valid YAML: {exc}", file=sys.stderr)
        raise SystemExit(2)
    if not isinstance(loaded, dict):
        print(f"error: {path} must contain a YAML mapping", file=sys.stderr)
        raise SystemExit(2)
    return loaded


def _role_entry(config: dict[str, Any], role: str) -> dict[str, Any]:
    roles = config.get("roles")
    if not isinstance(roles, dict):
        return {}
    entry = roles.get(role)
    return entry if isinstance(entry, dict) else {}


def _redact(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def redact_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *config* with every secret replaced by a placeholder."""
    out: dict[str, Any] = {"version": config.get("version", 1)}
    roles = config.get("roles")
    new_roles: dict[str, Any] = {}
    if isinstance(roles, dict):
        for role, entry in roles.items():
            if not isinstance(entry, dict):
                new_roles[role] = entry
                continue
            copy = dict(entry)
            for key in ("token", "private_key", "private_key_pem"):
                if copy.get(key):
                    copy[key] = _redact(str(copy[key]))
            new_roles[role] = copy
    out["roles"] = new_roles
    return out


def mint_app_token(entry: dict[str, Any]) -> tuple[str | None, str | None]:
    """Mint a GitHub App installation token.

    Returns ``(token, error)``. ``error`` is a human-readable reason, never a
    secret.
    """
    key_path = str(entry.get("private_key_path") or "").strip()
    app_id = str(entry.get("app_id") or "").strip()
    installation_id = str(entry.get("installation_id") or "").strip()
    if not key_path:
        return None, "app-key-missing"
    key_file = Path(key_path).expanduser()
    if not key_file.is_file():
        return None, "app-key-missing"
    if not app_id:
        return None, "app-id-missing"
    cmd = [
        "gh", "token", "generate",
        "--key", str(key_file),
        "--app-id", app_id,
        "--token-only",
    ]
    if installation_id:
        cmd += ["--installation-id", installation_id]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None, "gh-missing"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        hint = detail[-1] if detail else "gh token generate failed"
        return None, f"app-mint-failed ({hint})"
    token = (proc.stdout or "").strip()
    if not token:
        return None, "app-mint-failed (gh token generate printed nothing)"
    return token, None


def resolve_token(entry: dict[str, Any], role: str) -> tuple[str | None, str | None]:
    """Return ``(token, error_key)`` for a role entry."""
    kind = str(entry.get("kind") or "pat").strip().lower()
    if kind == "app":
        return mint_app_token(entry)
    token = str(entry.get("token") or "").strip()
    if not token:
        return None, "token-missing"
    return token, None


def role_env(workspace: Path, role: str, token: str) -> dict[str, str]:
    """Environment overlay that authenticates *role* as *token*."""
    env = {
        # gh honours GH_TOKEN; the bundled GitHub skills look for GITHUB_TOKEN.
        "GH_TOKEN": token,
        "GITHUB_TOKEN": token,
        # A role-private gh config directory: `gh auth login --with-token`
        # writes here and never touches the user's own gh login.
        "GH_CONFIG_DIR": str(gh_config_dir(workspace, role)),
    }
    return env


# ── commands ────────────────────────────────────────────────────────────────

def cmd_init(args: argparse.Namespace) -> int:
    workspace = workspace_root(args.workspace)
    cfg_path = config_path(workspace)
    if cfg_path.exists() and not args.force:
        print(f"{cfg_path} already exists — leaving it alone (use --force to overwrite).")
        return 0
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    os.chmod(cfg_path, stat.S_IRUSR | stat.S_IWUSR)

    ignore_path = workspace / GITIGNORE_REL
    ignore_path.parent.mkdir(parents=True, exist_ok=True)
    if not ignore_path.exists():
        ignore_path.write_text("# Created by the supervise-fix-issue skill.\n*\n", encoding="utf-8")
    os.chmod(ignore_path, stat.S_IRUSR | stat.S_IWUSR)

    print(f"Wrote {cfg_path} (mode 0600).")
    print(f"Wrote {ignore_path} — the whole .leap-aide/ tree stays out of git.")
    print("")
    print("Next: fill in each role's token, then run")
    print(f"  python3 {skill_dir() / 'scripts' / 'gh_roles.py'} doctor")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    workspace = workspace_root(args.workspace)
    config = load_config(workspace)
    if config is None:
        print(f"error: {config_path(workspace)} does not exist", file=sys.stderr)
        return 1
    yaml = _yaml()
    print(yaml.safe_dump(redact_config(config), sort_keys=False, allow_unicode=True))
    return 0


def _guidance(key: str, role: str, workspace: Path) -> str:
    template = GUIDANCE.get(key.split(" (")[0], GUIDANCE.get(key, ""))
    return template.format(
        role=role,
        skill=skill_dir(),
        config=config_path(workspace),
    )


def _check_role(workspace: Path, role: str, entry: dict[str, Any]) -> tuple[bool, str, str]:
    """Return ``(ok, error_key, detail)`` for one role."""
    kind = str(entry.get("kind") or "pat").strip().lower()
    if kind == "app":
        for field, key in (("private_key_path", "app-key-missing"),
                           ("app_id", "app-id-missing"),
                           ("installation_id", "app-installation-missing")):
            if not str(entry.get(field) or "").strip():
                return False, key, ""
    token, err = resolve_token(entry, role)
    if not token:
        return False, err or "token-missing", ""

    env = dict(os.environ)
    env.update(role_env(workspace, role, token))
    try:
        proc = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True, text=True, check=False, env=env,
        )
    except FileNotFoundError:
        return False, "gh-missing", ""
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        lowered = detail.lower()
        if "scoped" in lowered or "resource not accessible" in lowered:
            return False, "token-scope", detail
        return False, "token-invalid", detail
    login = (proc.stdout or "").strip()
    return True, "", login


def cmd_doctor(args: argparse.Namespace) -> int:
    workspace = workspace_root(args.workspace)
    config = load_config(workspace)
    if config is None:
        print(f"FAIL  no role configuration at {config_path(workspace)}")
        print("")
        print(_guidance("config-missing", "supervisor", workspace))
        return 1
    if not isinstance(config.get("roles"), dict) or not config.get("roles"):
        print(f"FAIL  {config_path(workspace)} has no `roles:` mapping")
        print("")
        print(_guidance("no-roles", "supervisor", workspace))
        return 1

    roles = config.get("roles") or {}
    wanted = [args.role] if args.role else list(ROLES)
    failures = 0
    for role in wanted:
        entry = _role_entry(config, role)
        if not entry:
            print(f"FAIL  {role}: not present in the config")
            print("")
            print(_guidance("token-missing", role, workspace))
            failures += 1
            continue
        ok, err_key, detail = _check_role(workspace, role, entry)
        if ok:
            print(f"OK    {role}: authenticated as {detail}")
        else:
            failures += 1
            print(f"FAIL  {role}: {_err_title(err_key)}")
            print("")
            print(_guidance(err_key, role, workspace))
            if detail:
                print("")
                print(f"  GitHub said: {detail}")
            print("")
    if failures:
        print(f"{failures} role(s) need attention.")
        return 1
    print("All checked roles authenticate against GitHub.")
    return 0


def _err_title(err_key: str) -> str:
    base = err_key.split(" (")[0]
    titles = {
        "config-missing": "no role configuration",
        "token-missing": "no token configured",
        "token-invalid": "GitHub rejected the token",
        "token-scope": "the token lacks a required permission",
        "app-key-missing": "the GitHub App private key is missing or unreadable",
        "app-id-missing": "the GitHub App id is missing",
        "app-installation-missing": "the GitHub App installation id is missing",
        "app-mint-failed": "the GitHub App token could not be minted",
        "gh-missing": "the gh CLI is not installed",
        "no-roles": "the config has no roles",
    }
    return titles.get(base, base)


def cmd_login(args: argparse.Namespace) -> int:
    workspace = workspace_root(args.workspace)
    config = load_config(workspace)
    if config is None:
        print(f"error: {config_path(workspace)} does not exist", file=sys.stderr)
        return 1
    entry = _role_entry(config, args.role)
    token, err = resolve_token(entry, args.role)
    if not token:
        print(f"error: cannot obtain a token for role '{args.role}': {_err_title(err)}",
              file=sys.stderr)
        print(_guidance(err, args.role, workspace), file=sys.stderr)
        return 1
    cfg_dir = gh_config_dir(workspace, args.role)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(role_env(workspace, args.role, token))
    try:
        proc = subprocess.run(
            ["gh", "auth", "login", "--hostname", "github.com", "--with-token"],
            input=token, capture_output=True, text=True, check=False, env=env,
        )
    except FileNotFoundError:
        print("error: the gh CLI is not installed", file=sys.stderr)
        return 1
    if proc.returncode != 0:
        print(f"error: gh auth login failed: {(proc.stderr or proc.stdout).strip()}",
              file=sys.stderr)
        return 1
    print(f"Role '{args.role}' is logged in under {cfg_dir}.")
    print("The user's own gh login is untouched.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    workspace = workspace_root(args.workspace)
    config = load_config(workspace)
    if config is None:
        print(f"error: {config_path(workspace)} does not exist", file=sys.stderr)
        print(_guidance("config-missing", args.role, workspace), file=sys.stderr)
        return 2
    entry = _role_entry(config, args.role)
    if not entry:
        print(f"error: role '{args.role}' is not configured in {config_path(workspace)}",
              file=sys.stderr)
        print(_guidance("token-missing", args.role, workspace), file=sys.stderr)
        return 2
    token, err = resolve_token(entry, args.role)
    if not token:
        print(f"error: no usable token for role '{args.role}': {_err_title(err)}",
              file=sys.stderr)
        print(_guidance(err, args.role, workspace), file=sys.stderr)
        return 2
    command = list(getattr(args, "argv_command", None) or [])
    if not command:
        print("error: `run` needs a command after `--`", file=sys.stderr)
        return 2
    env = dict(os.environ)
    env.update(role_env(workspace, args.role, token))
    env["GH_ROLE"] = args.role
    try:
        proc = subprocess.run(command, env=env, check=False)
    except FileNotFoundError:
        print(f"error: command not found: {command[0]}", file=sys.stderr)
        return 127
    return proc.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gh_roles.py",
        description="Per-role GitHub credentials for the supervise-fix-issue skill.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--workspace", default=None,
                       help="workspace root (defaults to $HERMES_WORKSPACE or the cwd)")

    p_init = sub.add_parser("init", help="write the config template")
    add_common(p_init)
    p_init.add_argument("--force", action="store_true",
                        help="overwrite an existing config")
    p_init.set_defaults(func=cmd_init)

    p_doctor = sub.add_parser("doctor", help="validate every role's credential")
    add_common(p_doctor)
    p_doctor.add_argument("--role", default=None, help="check only this role")
    p_doctor.set_defaults(func=cmd_doctor)

    p_show = sub.add_parser("show", help="print the config with secrets redacted")
    add_common(p_show)
    p_show.set_defaults(func=cmd_show)

    p_login = sub.add_parser("login", help="log a role in to its own gh config dir")
    add_common(p_login)
    p_login.add_argument("--role", required=True)
    p_login.set_defaults(func=cmd_login)

    p_run = sub.add_parser("run", help="run a command as a role")
    add_common(p_run)
    p_run.add_argument("--role", required=True)
    p_run.set_defaults(func=cmd_run, argv_command=[])

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw = list(sys.argv[1:] if argv is None else argv)
    # `run` takes the command after a literal `--`. A subparser cannot express
    # that (argparse rejects the trailing words as unknown arguments), so the
    # split happens here, before parsing.
    trailing: list[str] = []
    if "--" in raw:
        idx = raw.index("--")
        trailing = raw[idx + 1:]
        raw = raw[:idx]

    args = parser.parse_args(raw)
    if args.subcommand == "run":
        if not trailing:
            print("error: `run` needs a command after `--`", file=sys.stderr)
            return 2
        args.argv_command = trailing
    if shutil.which("gh") is None and args.subcommand in ("doctor", "login"):
        print("warning: the gh CLI is not on PATH", file=sys.stderr)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
