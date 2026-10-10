#!/usr/bin/env python3
"""One-shot bootstrap launcher for Hermes Web UI."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import venv
import webbrowser
from pathlib import Path


INSTALLER_URL = "https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh"
# bootstrap.py lives at <repo>/backend/app/bootstrap.py after the FastAPI-style
# layout refactor. `.parents[2]` walks from backend/app/ → backend/ → repo root
# so REPO_ROOT paths (.env lookup, requirements.txt, server.py, .venv) resolve
# against the checkout root instead of `backend/app/`.
REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_repo_dotenv() -> None:
    """Load REPO_ROOT/.env into os.environ.

    Fills in only variables that are not already present in the environment.
    A value already set in the environment (docker-compose ``environment:``,
    an exported shell variable, the image ENV) wins over .env — the .env line
    is skipped for that key. This matches the loader in scripts/ctl.sh so the
    precedence ``environment > .env`` holds however the server is launched.

    Only loads the webui repo .env — not ~/.hermes/.env, which the server
    loads independently at startup for provider credentials.
    """
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            # Strip optional 'export' prefix (common in copy-pasted shell snippets)
            if k.startswith("export "):
                k = k[7:].strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception as exc:
        import sys as _sys
        print(f"[bootstrap] Warning: could not load .env — {exc}", file=_sys.stderr)


# Side effect: loads REPO_ROOT/.env into os.environ on import.
# Must run before DEFAULT_HOST / DEFAULT_PORT so os.getenv() picks up
# values from .env even when bootstrap.py is invoked directly (not via start.sh).
_load_repo_dotenv()

DEFAULT_HOST = os.getenv("HERMES_WEBUI_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("HERMES_WEBUI_PORT", "8787"))
# Set HERMES_WEBUI_SKIP_ONBOARDING=1 to bypass the first-run wizard when
# the environment is already fully configured (e.g. managed hosting).


def info(msg: str) -> None:
    print(f"[bootstrap] {msg}", flush=True)


def is_wsl() -> bool:
    if platform.system() != "Linux":
        return False
    release = platform.release().lower()
    return (
        "microsoft" in release or "wsl" in release or bool(os.getenv("WSL_DISTRO_NAME"))
    )


def ensure_supported_platform() -> None:
    if platform.system() == "Windows" and not is_wsl():
        raise RuntimeError(
            "Native Windows is not supported for this bootstrap yet. "
            "Please run it from Linux, macOS, or inside WSL2."
        )


def discover_agent_dir() -> Path | None:
    home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()
    candidates = [
        os.getenv("HERMES_WEBUI_AGENT_DIR", ""),
        str(home / "hermes-agent"),
        str(REPO_ROOT.parent / "hermes-agent"),
        str(Path.home() / ".hermes" / "hermes-agent"),
        str(Path.home() / "hermes-agent"),
    ]
    for raw in candidates:
        if not raw:
            continue
        candidate = Path(raw).expanduser().resolve()
        if candidate.exists() and (candidate / "run_agent.py").exists():
            return candidate
    return None


def discover_launcher_python(agent_dir: Path | None) -> str:
    env_python = os.getenv("HERMES_WEBUI_PYTHON")
    if env_python:
        return env_python
    if agent_dir:
        for rel in ("venv/bin/python", "venv/Scripts/python.exe", ".venv/bin/python", ".venv/Scripts/python.exe"):
            candidate = agent_dir / rel
            if candidate.exists():
                return str(candidate)
    for rel in (".venv/bin/python", ".venv/Scripts/python.exe"):
        candidate = REPO_ROOT / rel
        if candidate.exists():
            return str(candidate)
    return shutil.which("python3") or shutil.which("python") or sys.executable


def ensure_python_has_webui_deps(python_exe: str) -> str:
    check = subprocess.run(
        [python_exe, "-c", "import yaml"],
        capture_output=True,
        text=True,
    )
    if check.returncode == 0:
        return python_exe

    venv_dir = REPO_ROOT / ".venv"
    venv_python = venv_dir / (
        "Scripts/python.exe" if platform.system() == "Windows" else "bin/python"
    )
    if not venv_python.exists():
        info(f"Creating local virtualenv at {venv_dir}")
        venv.EnvBuilder(with_pip=True).create(venv_dir)

    info("Installing WebUI dependencies into local virtualenv")
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
        check=True,
    )
    subprocess.run(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "--quiet",
            "-r",
            str(REPO_ROOT / "backend" / "requirements.txt"),
        ],
        check=True,
    )
    return str(venv_python)


def hermes_command_exists() -> bool:
    return shutil.which("hermes") is not None


def install_hermes_agent() -> None:
    info(f"Hermes Agent not found. Attempting install via {INSTALLER_URL}")
    subprocess.run(
        ["/bin/bash", "-lc", f"curl -fsSL {INSTALLER_URL} | bash"], check=True
    )


def wait_for_health(url: str, timeout: float = 40.0, log_file: str = '') -> bool:
    """Wait for server to become healthy. Logs diagnostics on failure."""
    t0 = time.monotonic()
    deadline = time.time() + timeout
    # Validate URL scheme to prevent file:// and other dangerous schemes
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"Invalid health check URL: {url}")
    # For https:// with self-signed certs, disable certificate verification.
    _ctx = None
    if url.startswith("https://"):
        import ssl
        _ctx = ssl.create_default_context()
        _ctx.check_hostname = False
        _ctx.verify_mode = ssl.CERT_NONE
    last_error = ''
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            with urllib.request.urlopen(url, timeout=2, context=_ctx) as response:  # nosec B310
                body = response.read()
                try:
                    import json as _json
                    if _json.loads(body).get("status") == "ok":
                        elapsed = round(time.monotonic() - t0, 1)
                        print(f'[bootstrap] Health check OK ({elapsed}s, {attempt} attempts)', flush=True)
                        return True
                except (ValueError, KeyError):
                    last_error = f'Invalid response: {body[:200]}'
        except urllib.error.URLError as e:
            last_error = f'URLError: {e.reason}'
        except ConnectionRefusedError:
            last_error = 'Connection refused (port not yet listening)'
        except Exception as e:
            last_error = f'{type(e).__name__}: {e}'
        time.sleep(0.4)

    # Diagnostic output
    elapsed = round(time.monotonic() - t0, 1)
    print(f'[bootstrap] Health check failed after {attempt} attempts over {elapsed}s', flush=True)
    print(f'[bootstrap] Last error: {last_error}', flush=True)
    # Try to read server.py log tail
    if log_file:
        try:
            log_path = Path(log_file)
            if log_path.exists():
                tail = log_path.read_text()[-500:]
                print(f'[bootstrap] Server log tail:\n{tail}', flush=True)
        except Exception:
            pass
    return False


def open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception as exc:
        info(f"Could not open browser automatically: {exc}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap Hermes Web UI onboarding.")
    parser.add_argument("port", nargs="?", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open a browser tab automatically.",
    )
    parser.add_argument(
        "--skip-agent-install",
        action="store_true",
        help="Fail instead of attempting the official Hermes installer.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_supported_platform()

    agent_dir = discover_agent_dir()
    if not agent_dir and not hermes_command_exists():
        if args.skip_agent_install:
            raise RuntimeError(
                "Hermes Agent was not found and auto-install was disabled."
            )
        install_hermes_agent()
        agent_dir = discover_agent_dir()

    python_exe = ensure_python_has_webui_deps(discover_launcher_python(agent_dir))
    state_dir = Path(
        os.getenv("HERMES_WEBUI_STATE_DIR", str(Path.home() / ".hermes" / "webui"))
    ).expanduser()
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / f"bootstrap-{args.port}.log"

    env = os.environ.copy()
    env["HERMES_WEBUI_HOST"] = args.host
    env["HERMES_WEBUI_PORT"] = str(args.port)
    env.setdefault("HERMES_WEBUI_STATE_DIR", str(state_dir))
    if agent_dir:
        env["HERMES_WEBUI_AGENT_DIR"] = str(agent_dir)

    info(f"Starting leap-aide on http://{args.host}:{args.port}")
    # Run server.py via ``python -m app.server`` with cwd=backend/. This treats
    # the ``app`` package as importable (the FastAPI-style launch contract) so
    # ``from app.api.X import Y`` in server.py resolves regardless of how the
    # host invokes bootstrap.py.
    with log_path.open("ab") as log_file:
        proc = subprocess.Popen(
            [python_exe, "-m", "app.server"],
            cwd=str(REPO_ROOT / "backend"),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    # Write PID file for clean shutdown signaling
    import atexit
    pid_file = state_dir / f"server-{args.port}.pid"
    pid_file.write_text(str(proc.pid))

    def _cleanup_pid():
        try:
            pid_file.unlink(missing_ok=True)
        except Exception:
            pass

    atexit.register(_cleanup_pid)

    health_url = f"http://{args.host}:{args.port}/health"
    if args.host == "0.0.0.0":
        health_url = f"http://localhost:{args.port}/health"
    # If TLS is enabled, switch health check and app URL to https://
    _tls_cert = os.environ.get("HERMES_WEBUI_TLS_CERT", "").strip()
    _tls_key  = os.environ.get("HERMES_WEBUI_TLS_KEY",  "").strip()
    _tls_on   = bool(_tls_cert and _tls_key)
    if _tls_on:
        health_url = health_url.replace("http://", "https://")
    if not wait_for_health(health_url, log_file=str(log_path)):
        raise RuntimeError(
            f"Server did not become healthy at {health_url}. "
            f"Check the log at {log_path}. Server PID: {proc.pid}"
        )

    _scheme = "https" if _tls_on else "http"
    app_url = (
        f"{_scheme}://localhost:{args.port}"
        if args.host in ("127.0.0.1", "localhost")
        else f"{_scheme}://{args.host}:{args.port}"
    )
    info(f"Web UI is ready: {app_url}")
    info(f"Log file: {log_path}")
    if not args.no_browser:
        open_browser(app_url)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[bootstrap] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
