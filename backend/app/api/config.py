"""
Hermes Web UI -- Shared configuration, constants, and global state.
Imported by all other api/* modules and by server.py.

Discovery order for all paths:
  1. Explicit environment variable
  2. Filesystem heuristics (sibling checkout, parent dir, common install locations)
  3. Hardened defaults relative to $HOME
  4. Fail loudly with a human-readable fix-it message if required modules are missing
"""

import collections
import copy
import ipaddress
import json
import logging
import os
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.api import model_catalog

# ── Basic layout ──────────────────────────────────────────────────────────────
HOME = Path.home()
# config.py now lives at backend/app/api/config.py after the FastAPI-style
# layout refactor. ``.parents[3]`` walks api/ → app/ → backend/ → repo root so
# static/, requirements.txt, and other checkout-root resources resolve correctly.
REPO_ROOT = Path(__file__).resolve().parents[3]

# ── Network config (env-overridable) ─────────────────────────────────────────
HOST = os.getenv("HERMES_WEBUI_HOST", "127.0.0.1")
PORT = int(os.getenv("HERMES_WEBUI_PORT", "8787"))

# ── TLS/HTTPS config (optional, env-overridable) ────────────────────────────
TLS_CERT = os.getenv("HERMES_WEBUI_TLS_CERT", "").strip() or None
TLS_KEY = os.getenv("HERMES_WEBUI_TLS_KEY", "").strip() or None
TLS_ENABLED = TLS_CERT is not None and TLS_KEY is not None

# ── State directory (env-overridable, never inside repo) ──────────────────────
STATE_DIR = (
    Path(os.getenv("HERMES_WEBUI_STATE_DIR", str(HOME / ".hermes" / "webui")))
    .expanduser()
    .resolve()
)

SESSION_DIR = STATE_DIR / "sessions"
WORKSPACES_FILE = STATE_DIR / "workspaces.json"
SESSION_INDEX_FILE = SESSION_DIR / "_index.json"
SETTINGS_FILE = STATE_DIR / "settings.json"
PROJECTS_FILE = STATE_DIR / "projects.json"
logger = logging.getLogger(__name__)


def _resolve_default_workspace() -> Path:
    """Single source of truth for the default workspace (issue #7).

    HERMES_WEBUI_DEFAULT_WORKSPACE wins when set and usable: an existing
    directory (covers read-only :ro mounts — docker-entrypoint.sh supports
    them) or one we can create. Otherwise the managed fallback
    STATE_DIR/workspaces/default.
    """
    fallback = STATE_DIR / "workspaces" / "default"
    raw = os.getenv("HERMES_WEBUI_DEFAULT_WORKSPACE")
    if not raw:
        return fallback
    try:
        path = Path(raw).expanduser().resolve()
        if path.is_dir():
            return path
        path.mkdir(parents=True, exist_ok=True)
        if path.is_dir():
            return path
        logger.warning(
            "HERMES_WEBUI_DEFAULT_WORKSPACE is not a directory: %s (using %s)",
            path, fallback,
        )
    except OSError as exc:
        logger.warning(
            "Cannot use HERMES_WEBUI_DEFAULT_WORKSPACE=%s: %s (using %s)",
            raw, exc, fallback,
        )
    return fallback


DEFAULT_WORKSPACE = _resolve_default_workspace()


# ── Compression constants ────────────────────────────────────────────────────
# Minimum messages required before manual compression is allowed
MIN_MESSAGES_FOR_COMPRESSION = 4

# Rough token estimation ratio (characters ÷ N ≈ tokens)
TOKEN_ESTIMATION_CHARS_PER_TOKEN = 4

# Context usage colour thresholds (frontend-driven; mirrored here for reference)
# Green: < 60%, Yellow: 60%-85%, Red: > 85%
COMPRESSION_COLOR_THRESHOLD_WARN = 0.60   # 60% — bar turns yellow
COMPRESSION_COLOR_THRESHOLD_DANGER = 0.85  # 85% — bar turns red


# ── Hermes agent directory discovery ─────────────────────────────────────────
def _discover_agent_dir() -> Path:
    """
    Locate the hermes-agent checkout using a multi-strategy search.

    Priority:
      1. HERMES_WEBUI_AGENT_DIR env var  -- explicit override always wins
      2. HERMES_HOME / hermes-agent      -- e.g. ~/.hermes/hermes-agent
      3. Sibling of this repo            -- ../hermes-agent
      4. Parent of this repo             -- ../../hermes-agent (nested layout)
      5. Common install paths            -- ~/.hermes/hermes-agent (again as fallback)
      6. HOME / hermes-agent             -- ~/hermes-agent (simple flat layout)
    """
    candidates = []

    # 1. Explicit env var
    if os.getenv("HERMES_WEBUI_AGENT_DIR"):
        candidates.append(
            Path(os.getenv("HERMES_WEBUI_AGENT_DIR")).expanduser().resolve()
        )

    # 2. HERMES_HOME / hermes-agent
    hermes_home = os.getenv("HERMES_HOME", str(HOME / ".hermes"))
    candidates.append(Path(hermes_home).expanduser() / "hermes-agent")

    # 3. Sibling: <repo-root>/../hermes-agent
    candidates.append(REPO_ROOT.parent / "hermes-agent")

    # 4. Parent is the agent repo itself (repo cloned inside hermes-agent/)
    if (REPO_ROOT.parent / "run_agent.py").exists():
        candidates.append(REPO_ROOT.parent)

    # 5. ~/.hermes/hermes-agent (explicit common path)
    candidates.append(HOME / ".hermes" / "hermes-agent")

    # 6. ~/hermes-agent
    candidates.append(HOME / "hermes-agent")

    # 7. XDG_DATA_HOME / hermes-agent  (e.g. ~/.local/share/hermes-agent)
    xdg_data = Path(os.getenv("XDG_DATA_HOME", str(HOME / ".local" / "share")))
    candidates.append(xdg_data.expanduser() / "hermes-agent")

    # 8. System-wide install paths (e.g. /opt/hermes-agent, /usr/local/hermes-agent,
    #    /usr/local/lib/hermes-agent for testbox / pip-installed layouts — see
    #    tests/conftest.py:_discover_agent_dir for the same set).
    for sys_prefix in ("/opt", "/usr/local", "/usr/local/share", "/usr/local/lib"):
        candidates.append(Path(sys_prefix) / "hermes-agent")

    for path in candidates:
        if path.exists() and (path / "run_agent.py").exists():
            return path.resolve()

    return None


def _discover_python(agent_dir: Path) -> str:
    """
    Locate a Python executable that has the Hermes agent dependencies installed.

    Priority:
      1. HERMES_WEBUI_PYTHON env var
      2. Agent venv at <agent_dir>/venv/bin/python
      3. Local .venv inside this repo
      4. System python3
    """
    if os.getenv("HERMES_WEBUI_PYTHON"):
        return os.getenv("HERMES_WEBUI_PYTHON")

    if agent_dir:
        venv_py = agent_dir / "venv" / "bin" / "python"
        if venv_py.exists():
            return str(venv_py)
        
        venv_py = agent_dir / ".venv" / "bin" / "python"
        if venv_py.exists():
            return str(venv_py)

        # Windows layout
        venv_py_win = agent_dir / "venv" / "Scripts" / "python.exe"
        if venv_py_win.exists():
            return str(venv_py_win)
        
        venv_py_win = agent_dir / ".venv" / "Scripts" / "python.exe"
        if venv_py_win.exists():
            return str(venv_py_win)

    # Local .venv inside this repo
    local_venv = REPO_ROOT / ".venv" / "bin" / "python"
    if local_venv.exists():
        return str(local_venv)

    # Fall back to system python3
    import shutil

    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            return found

    return "python3"


# Run discovery
_AGENT_DIR = _discover_agent_dir()
PYTHON_EXE = _discover_python(_AGENT_DIR)

# ── Inject agent dir into sys.path so Hermes modules are importable ──────────

# When users (or CI builds) run `pip install --target .` or
# `pip install -t .` inside the hermes-agent checkout, third-party
# package directories (openai/, pydantic/, requests/, etc.) end up
# alongside real Hermes source files.  Putting _AGENT_DIR at the
# FRONT of sys.path means Python resolves `import pydantic` from that
# local directory — which breaks whenever the host platform differs
# from the container (e.g. macOS .so files inside a Linux image).
#
# Fix: insert _AGENT_DIR at the END of sys.path.  Python searches
# entries in order, so site-packages resolves pip packages correctly,
# and Hermes-specific modules (run_agent, hermes/, etc.) still
# resolve because they do not exist in site-packages.

if _AGENT_DIR is not None:
    if str(_AGENT_DIR) not in sys.path:
        sys.path.append(str(_AGENT_DIR))
    _HERMES_FOUND = True
else:
    _HERMES_FOUND = False

# ── Config file (reloadable -- supports profile switching) ──────────────────
_cfg_cache = {}
_cfg_lock = threading.Lock()
_cfg_mtime: float = 0.0  # last known mtime of config.yaml; 0 = never loaded


def _get_config_path() -> Path:
    """Return config.yaml path for the active profile."""
    env_override = os.getenv("HERMES_CONFIG_PATH")
    if env_override:
        return Path(env_override).expanduser()
    try:
        from app.api.profiles import get_active_hermes_home

        return get_active_hermes_home() / "config.yaml"
    except ImportError:
        return HOME / ".hermes" / "config.yaml"


def get_config() -> dict:
    """Return the cached config dict, loading from disk if needed."""
    if not _cfg_cache:
        reload_config()
    return _cfg_cache


def reload_config() -> None:
    """Reload config.yaml from the active profile's directory."""
    global _cfg_mtime
    with _cfg_lock:
        _cfg_cache.clear()
        config_path = _get_config_path()
        # Remember the old mtime so we can tell whether config actually changed
        # vs. first-ever load (mtime == 0.0, e.g. server start or profile switch).
        _old_cfg_mtime = _cfg_mtime
        try:
            import yaml as _yaml

            if config_path.exists():
                loaded = _yaml.safe_load(config_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    _cfg_cache.update(loaded)
                    try:
                        _cfg_mtime = Path(config_path).stat().st_mtime
                    except OSError:
                        _cfg_mtime = 0.0
        except Exception:
            logger.debug("Failed to load yaml config from %s", config_path)
        # Bust the models cache so the next request sees fresh config values.
        # Only delete the disk cache when config has actually changed -- not on
        # first-ever load (when _old_cfg_mtime == 0.0, i.e. server start or
        # profile switch) -- preserving the disk cache so the next restart
        # still hits the fast path without a cold run.
        if _old_cfg_mtime != 0.0:
            _delete_models_cache_on_disk()


def _load_yaml_config_file(config_path: Path) -> dict:
    try:
        import yaml as _yaml
    except ImportError:
        return {}

    if not config_path.exists():
        return {}
    try:
        loaded = _yaml.safe_load(config_path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        logger.debug("Failed to parse yaml config from %s", config_path)
        return {}


def _save_yaml_config_file(config_path: Path, config_data: dict) -> None:
    try:
        import yaml as _yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to write Hermes config.yaml") from exc

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        _yaml.safe_dump(config_data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


# Initial load
reload_config()
cfg = _cfg_cache  # alias for backward compat with existing references


DEFAULT_MODEL = os.getenv("HERMES_WEBUI_DEFAULT_MODEL", "")  # Empty = use provider default; avoids showing unavailable OpenAI model to non-OpenAI users (#646)


# ── Startup diagnostics ───────────────────────────────────────────────────────
def print_startup_config() -> None:
    """Print detected configuration at startup so the user can verify what was found."""
    ok = "[ok]"
    err = "[fail]"

    lines = [
        "",
        "  startup config",
        "  --------------------------------",
        f"  repo root   : {REPO_ROOT}",
        f"  agent dir   : {_AGENT_DIR if _AGENT_DIR else 'NOT FOUND'}  {ok if _AGENT_DIR else err}",
        f"  python      : {PYTHON_EXE}",
        f"  state dir   : {STATE_DIR}",
        f"  workspace   : {DEFAULT_WORKSPACE}",
        f"  host:port   : {HOST}:{PORT}",
        f"  config file : {_get_config_path()}  {'(found)' if _get_config_path().exists() else '(not found, using defaults)'}",
        "  --------------------------------"
    ]
    logger.info('\n'.join(lines))

    if not _HERMES_FOUND:
        logger.error(
            "%s  Could not find the Hermes agent directory.\n"
            "      The server will start but agent features will not work.\n"
            "\n"
            "      To fix, set one of:\n"
            "        export HERMES_WEBUI_AGENT_DIR=/path/to/hermes-agent\n"
            "        export HERMES_HOME=/path/to/.hermes\n"
            "\n"
            "      Or clone hermes-agent as a sibling of this repo:\n"
            "        git clone <hermes-agent-repo> ../hermes-agent",
            err
        )


def apply_network_preferences() -> bool:
    """Apply network.* process-wide preferences from hermes config.yaml (issue #8).

    The SDK only calls apply_ipv4_preference() from gateway/run.py and
    cron/scheduler.py — its WebUI/server paths never do, so the documented
    network.force_ipv4 switch had no effect for WebUI LLM calls.

    IPv4 preference is ON by default here: with broken/unreachable IPv6,
    Python tries AAAA records first and hangs for the full TCP timeout before
    falling back to IPv4 — the suspected trigger of the 30s ConnectTimeout on
    first chat in issue #8.  The SDK's patch falls back to unfiltered
    resolution for hosts without an A record, so pure-IPv6 hosts keep working.
    Disable with HERMES_WEBUI_FORCE_IPV4=0 (or network.force_ipv4: false).

    Returns True when the preference was applied.
    """
    try:
        env = str(os.environ.get("HERMES_WEBUI_FORCE_IPV4", "")).strip().lower()
        if env in ("0", "false", "no"):
            logger.info("IPv4 preference: disabled via HERMES_WEBUI_FORCE_IPV4")
            return False
        if env in ("1", "true", "yes"):
            force = True
        else:
            network_cfg = cfg.get("network")
            force = not (isinstance(network_cfg, dict) and network_cfg.get("force_ipv4") is False)
        if not force:
            logger.info("IPv4 preference: disabled via config network.force_ipv4: false")
            return False

        from hermes_constants import apply_ipv4_preference

        # Public SDK helper — idempotent, and force=False would be a no-op.
        apply_ipv4_preference(force=True)
        logger.info(
            "IPv4 preference: enabled (getaddrinfo prefers A records; "
            "HERMES_WEBUI_FORCE_IPV4=0 disables)"
        )
        return True
    except ImportError:
        logger.warning("IPv4 preference: skipped — hermes_constants not importable")
        return False
    except Exception:
        logger.warning("IPv4 preference: failed", exc_info=True)
        return False


# Idempotence guard for apply_aws_metadata_preference (boot-time only).
_aws_metadata_probe_done = False


def apply_aws_metadata_preference() -> bool:
    """Probe for the EC2 IMDS endpoint and disable it when unreachable (issue #16).

    On non-AWS hosts the bedrock provider's credential check (botocore
    ``has_aws_credentials``) probes the link-local IMDS endpoint
    169.254.169.254 and eats a 1-2s connection timeout per model-catalog
    build — one of the slow legs of the 48-160s cold build.  A single
    0.5s TCP probe at boot tells us whether IMDS is worth consulting; when
    it is not, set the standard botocore opt-outs so every later credential
    check short-circuits:

      AWS_EC2_METADATA_DISABLED=true
      AWS_MAX_ATTEMPTS=1
      AWS_METADATA_SERVICE_NUM_ATTEMPTS=1

    Env vars set by the operator always win (no override via
    os.environ.setdefault); disable the probe entirely with
    HERMES_WEBUI_AWS_IMDS_PROBE=0.  On real EC2/ECS the probe succeeds and
    nothing is changed.  Does not modify the SDK.  Call from main() before
    any botocore import (i.e. before the model-catalog prewarm starts).
    """
    global _aws_metadata_probe_done
    if _aws_metadata_probe_done:
        return False
    _aws_metadata_probe_done = True
    try:
        if str(os.environ.get("HERMES_WEBUI_AWS_IMDS_PROBE", "")).strip().lower() in ("0", "false", "no"):
            logger.info("AWS IMDS probe: disabled via HERMES_WEBUI_AWS_IMDS_PROBE")
            return False
        if os.environ.get("AWS_EC2_METADATA_DISABLED"):
            # Operator already opted out (or in) — don't second-guess.
            return False

        import socket as _socket

        try:
            # Short connect timeout: an unreachable link-local address is
            # normally refused/routed-to-nowhere in well under 500ms.
            with _socket.create_connection(("169.254.169.254", 80), timeout=0.5):
                pass
            logger.info("AWS IMDS probe: reachable — leaving credential discovery untouched")
            return True
        except OSError:
            os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
            os.environ.setdefault("AWS_MAX_ATTEMPTS", "1")
            os.environ.setdefault("AWS_METADATA_SERVICE_NUM_ATTEMPTS", "1")
            logger.info(
                "AWS IMDS probe: unreachable — disabled IMDS credential "
                "discovery for this process (AWS_EC2_METADATA_DISABLED=true; "
                "set it yourself to override, HERMES_WEBUI_AWS_IMDS_PROBE=0 "
                "to skip this probe)"
            )
            return False
    except Exception:
        logger.warning("AWS IMDS probe: failed", exc_info=True)
        return False


def verify_hermes_imports() -> tuple:
    """
    Attempt to import the key Hermes modules.
    Returns (ok: bool, missing: list[str], errors: dict[str, str]).
    """
    required = ["run_agent"]
    missing = []
    errors = {}
    for mod in required:
        try:
            __import__(mod)
        except Exception as e:
            missing.append(mod)
            # Capture the full error message so startup logs show WHY
            # (e.g. pydantic_core .so mismatch) instead of just the name.
            errors[mod] = f"{type(e).__name__}: {e}"
    return (len(missing) == 0), missing, errors


# ── Limits ───────────────────────────────────────────────────────────────────
MAX_FILE_BYTES = 200_000
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# ── File type maps ───────────────────────────────────────────────────────────
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp"}
MD_EXTS = {".md", ".markdown", ".mdown"}
CODE_EXTS = {
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".css",
    ".html",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".sh",
    ".bash",
    ".txt",
    ".log",
    ".env",
    ".csv",
    ".xml",
    ".sql",
    ".rs",
    ".go",
    ".java",
    ".c",
    ".cpp",
    ".h",
}
MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".bmp": "image/bmp",
    ".pdf": "application/pdf",
    ".json": "application/json",
    ".html": "text/html",
    ".htm": "text/html",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# ── Toolsets (from config.yaml or hardcoded default) ─────────────────────────
_DEFAULT_TOOLSETS = [
    "browser",
    "clarify",
    "code_execution",
    "cronjob",
    "delegation",
    "file",
    "image_gen",
    "memory",
    "session_search",
    "skills",
    "terminal",
    "todo",
    "web",
    "webhook",
]
def _resolve_cli_toolsets(cfg=None):
    """Resolve CLI toolsets using the agent's _get_platform_tools() so that
    MCP server toolsets are automatically included, matching CLI behaviour."""
    if cfg is None:
        cfg = get_config()
    try:
        from hermes_cli.tools_config import _get_platform_tools
        return list(_get_platform_tools(cfg, "cli"))
    except Exception:
        # Fallback: read raw list from config (MCP toolsets will be missing)
        return cfg.get("platform_toolsets", {}).get("cli", _DEFAULT_TOOLSETS)

CLI_TOOLSETS = _resolve_cli_toolsets()

# ── Model / provider discovery ───────────────────────────────────────────────
#
# Provider display names, aliases, baseline model lists, endpoints, auth
# headers and refresh policy all live in the JSON catalog
# (app/api/data/model_catalog.json) loaded via app.api.model_catalog —
# the Python tables that used to mirror them (_FALLBACK_MODELS,
# _PROVIDER_MODELS, _PROVIDER_DISPLAY, _PROVIDER_ALIASES,
# _PROVIDER_CARD_ALIASES, get_provider_card_models) were removed in the
# issue #10 refactor.


def _resolve_provider_alias(name: str) -> str:
    """Return the canonical provider slug for *name*.

    Applies the JSON catalog's alias table (model_catalog.json), then
    merges any additional aliases the agent provides (when hermes_cli is
    on sys.path). Lookup is case-insensitive and whitespace-trimmed.
    Unknown names pass through unchanged.
    """
    if not name:
        return name
    raw = str(name).strip().lower()
    # Prefer the agent's table when available so new aliases added there
    # work automatically; otherwise fall through to the catalog.
    try:
        from hermes_cli.models import _PROVIDER_ALIASES as _agent_aliases
        if raw in _agent_aliases:
            return _agent_aliases[raw]
    except Exception:
        pass
    return model_catalog.resolve_alias(name)


_AMBIENT_GH_CLI_MARKERS = frozenset({"gh_cli", "gh auth token"})


def _is_ambient_gh_cli_entry(source: str, label: str, key_source: str) -> bool:
    """True when a credential-pool entry is a seeded gh-cli token rather than
    one the user added explicitly. Filter these so Copilot doesn't appear in
    the dropdown just because `gh` is installed on the system.
    """
    return (
        source.strip().lower() in _AMBIENT_GH_CLI_MARKERS
        or label.strip().lower() == "gh auth token"
        or key_source.strip().lower() == "gh auth token"
    )


def _is_env_sourced_pool_entry(source: str) -> bool:
    """True when a credential-pool entry merely mirrors an environment variable.

    Env-sourced entries (``source: env:VAR``) are not independent credentials:
    ``_provider_has_key()`` already reads ``VAR`` from the environment / the
    ``.env`` file. When that key is deleted the mirror lingers in auth.json but
    no longer represents a usable credential, so it must not bypass the
    key-presence post-filter (otherwise a deleted key still shows its provider
    in the model picker with nothing to authenticate against).
    """
    return source.strip().lower().startswith("env:")


def _format_ollama_label(mid: str) -> str:
    """Turn an Ollama model id (Ollama tag format) into a readable display label.

    Examples: 'kimi-k2.5' → 'Kimi K2.5', 'qwen3-vl:235b-instruct' → 'Qwen3 VL (235B Instruct)'
    """
    name_part, _, variant = mid.partition(":")

    def _fmt(s: str) -> str:
        tokens = s.replace("-", " ").replace("_", " ").split()
        out = []
        for t in tokens:
            alpha_only = t.replace(".", "")
            if alpha_only.isalpha() and len(t) <= 3:
                out.append(t.upper())  # short acronym: glm → GLM, vl → VL, gpt → GPT
            elif alpha_only.isalnum() and alpha_only and alpha_only[0].isdigit():
                out.append(t.upper())  # size param: 235b → 235B, 1t → 1T
            else:
                out.append(t[0].upper() + t[1:] if t else t)  # capitalize: kimi → Kimi
        return " ".join(out)

    label = _fmt(name_part)
    if variant:
        label += f" ({_fmt(variant)})"
    return label


def _apply_provider_prefix(
    raw_models: list[dict],
    provider_id: str,
    active_provider: str | None,
) -> list[dict]:
    """Return *raw_models* with @provider: prefixes applied where needed.

    All non-active-provider model IDs are normalized to ``@provider:model``
    so downstream paths (session storage, drain, resolve_model_provider)
    can always extract the provider hint.  When *provider_id* equals
    *active_provider* the bare ID is preserved — the active provider is
    the default route, so prefixing is redundant and breaks dropdown UX
    (the bare name is what `resolve_model_provider` returns).  Models that
    already carry ``@`` or ``/`` are passed through unchanged.
    """
    result = []
    is_active = bool(active_provider) and provider_id == active_provider
    for m in raw_models:
        mid = m["id"]
        if mid.startswith("@") or "/" in mid or is_active:
            result.append({"id": mid, "label": m["label"]})
        else:
            result.append({"id": f"@{provider_id}:{mid}", "label": m["label"]})
    return result


def resolve_gateway_variant(provider_hint: str) -> tuple | None:
    """Translate a gateway-variant provider hint into SDK-routable parts.

    Some vendors expose multiple gateways that the Hermes SDK models as a
    single provider (xiaomi api vs xiaomi token-plan both route through the
    SDK's ``xiaomi`` provider with different base_urls). LeapAide's catalog
    lists each gateway as its own provider entry — separate env var, separate
    Settings card, separately usable at the same time — and marks the entry
    with ``sdk_provider``. The SDK registry knows nothing about the variant
    slug (``xiaomi-token-plan``) or its private env var
    (``XIAOMI_TOKEN_PLAN_API_KEY``), so callers must translate before
    invoking the SDK and pass the returned base_url / api_key as explicit
    arguments.

    Returns ``(sdk_provider, base_url, api_key)`` or None when
    *provider_hint* is not a gateway variant.  ``api_key`` may be "" when the
    variant's env var is unset; the SDK explicit path then falls back to the
    base provider's own credentials.
    """
    if not provider_hint:
        return None
    sdk_provider = model_catalog.provider_sdk_provider(provider_hint)
    if not sdk_provider:
        return None
    base_url = model_catalog.provider_base_url(provider_hint) or ""
    api_key = model_catalog.provider_api_key(provider_hint)
    return sdk_provider, base_url, api_key


def resolve_runtime_for_provider(
    provider_hint: str,
    base_url_hint: str | None = None,
) -> tuple:
    """Resolve SDK runtime parts, translating gateway variants (Pattern B).

    Wraps the SDK's ``resolve_runtime_provider`` for every WebUI call site
    that turns a provider slug into API credentials. Handles the two shapes
    a gateway variant (catalog entry with ``sdk_provider``, e.g.
    ``xiaomi-token-plan`` → ``xiaomi``) can arrive in:

    1. The provider slug itself is the variant (session stored
       ``@xiaomi-token-plan:model``) — translate to the SDK provider and
       pass the variant's base_url / api_key explicitly.
    2. The provider is the SDK slug but *base_url_hint* matches a variant's
       endpoint (config.yaml persists a default as
       ``provider: xiaomi`` + ``base_url: https://token-plan-...`` so the
       hermes CLI stays compatible) — recover the variant and use its
       private key, so a dual-gateway user's token-plan default does not
       silently route to the plain api gateway with the wrong key.

    Returns ``(runtime_dict, provider, base_url)`` — the same triple the
    call sites previously assembled by hand around
    ``resolve_runtime_provider``. ``provider``/``base_url`` are only
    filled in for the variant translation; for regular providers the
    caller keeps its existing fill-from-runtime logic, preserving prior
    behavior exactly.
    """
    from hermes_cli.runtime_provider import resolve_runtime_provider

    sdk_provider = (provider_hint or "").strip() or None
    variant_key = ""
    variant_base = None
    try:
        if provider_hint:
            sdk = model_catalog.provider_sdk_provider(provider_hint)
            if sdk:
                # Shape 1: slug is the variant itself.
                sdk_provider = sdk
                variant_base = model_catalog.provider_base_url(provider_hint)
                variant_key = model_catalog.provider_api_key(provider_hint)
            elif base_url_hint:
                # Shape 2: SDK slug + config base_url pointing at a variant.
                variant_slug = model_catalog.find_gateway_variant(
                    provider_hint, base_url_hint
                )
                if variant_slug:
                    variant_base = (
                        model_catalog.provider_base_url(variant_slug)
                        or str(base_url_hint)
                    )
                    variant_key = model_catalog.provider_api_key(variant_slug)
    except Exception as _e:
        print(f"[webui] WARNING: gateway variant lookup failed: {_e}", flush=True)
        sdk_provider = (provider_hint or "").strip() or None
        variant_key = ""
        variant_base = None

    if variant_base or variant_key:
        runtime = resolve_runtime_provider(
            requested=sdk_provider,
            explicit_api_key=variant_key or None,
            explicit_base_url=variant_base,
        )
        return runtime, sdk_provider, variant_base or str(base_url_hint or "") or None

    runtime = resolve_runtime_provider(requested=provider_hint)
    return runtime, provider_hint, base_url_hint


def resolve_model_provider(model_id: str) -> tuple:
    """Resolve model name, provider, and base_url for AIAgent.

    Model IDs from the dropdown can be in several formats:
      - 'claude-sonnet-4.6'            (bare name, uses config default provider)
      - 'anthropic/claude-sonnet-4.6'  (OpenRouter-style provider/model)
      - '@minimax:MiniMax-M2.7'        (explicit provider hint from dropdown)

    The @provider:model format is used for models from non-default provider
    groups in the dropdown, so we can route them through the correct provider
    via resolve_runtime_provider(requested=provider) instead of the default.

    Custom OpenAI-compatible endpoints are special: their model IDs often look
    like provider/model (for example ``google/gemma-4-26b-a4b``), which would be
    mistaken for an OpenRouter model if we only looked at the slash. To avoid
    that, first check whether the selected model matches an entry in
    config.yaml -> custom_providers and route it through that named custom
    provider.

    Returns (model, provider, base_url) where provider and base_url may be None.
    """
    config_provider = None
    config_base_url = None
    model_cfg = cfg.get("model", {})
    if isinstance(model_cfg, dict):
        config_provider = model_cfg.get("provider")
        config_base_url = model_cfg.get("base_url")

    model_id = (model_id or "").strip()
    if not model_id:
        return model_id, config_provider, config_base_url

    # Custom providers declared in config.yaml should win over slash-based
    # OpenRouter heuristics. Their model IDs commonly contain '/' too.
    custom_providers = cfg.get("custom_providers", [])
    if isinstance(custom_providers, list):
        for entry in custom_providers:
            if not isinstance(entry, dict):
                continue
            entry_model = (entry.get("model") or "").strip()
            entry_name = (entry.get("name") or "").strip()
            entry_base_url = (entry.get("base_url") or "").strip()
            if entry_model and entry_name and model_id == entry_model:
                provider_hint = "custom:" + entry_name.lower().replace(" ", "-")
                return model_id, provider_hint, entry_base_url or None

    # @provider:model format — explicit provider hint from the dropdown.
    # Route through that provider directly (resolve_runtime_provider will
    # resolve credentials in streaming.py).
    if model_id.startswith("@") and ":" in model_id:
        provider_hint, bare_model = model_id[1:].split(":", 1)
        return bare_model, provider_hint, None

    if "/" in model_id:
        prefix, bare = model_id.split("/", 1)
        # OpenRouter always needs the full provider/model path (e.g. openrouter/free,
        # anthropic/claude-sonnet-4.6). Never strip the prefix for OpenRouter.
        if config_provider == "openrouter":
            return model_id, "openrouter", config_base_url
        # If prefix matches config provider exactly, strip it and use that provider directly.
        # e.g. config=anthropic, model=anthropic/claude-... → bare name to anthropic API
        if config_provider and prefix == config_provider:
            return bare, config_provider, config_base_url
        # Portal providers (Nous, OpenCode) serve models from multiple upstream
        # namespaces — check them BEFORE the config_base_url branch so that a
        # Nous user whose config.yaml also has a base_url doesn't accidentally
        # fall into the prefix-stripping path (#894: minimax/minimax-m2.7 → bare
        # name sent to Nous → 404 because Nous requires the full namespace path).
        _PORTAL_PROVIDERS = {"nous", "opencode-zen", "opencode-go"}
        if config_provider in _PORTAL_PROVIDERS:
            return model_id, config_provider, config_base_url
        # If a custom endpoint base_url is configured, don't reroute through OpenRouter
        # just because the model name contains a slash (e.g. google/gemma-4-26b-a4b).
        # The user has explicitly pointed at a base_url, so trust their routing config.
        if config_base_url:
            # Only strip the provider prefix when it's a known provider namespace
            # (e.g. "openai/gpt-5.4" → "gpt-5.4" for a custom OpenAI-compatible proxy).
            # Unknown prefixes (e.g. "zai-org/GLM-5.1" on DeepInfra) are intrinsic to
            # the model ID and must be preserved — stripping them causes model_not_found.
            if model_catalog.get_provider_record(prefix) is not None:
                return bare, config_provider, config_base_url
            # Unknown prefix (not a named provider) — pass full model_id through.
            return model_id, config_provider, config_base_url

        # If prefix does NOT match config provider, the user picked a cross-provider model
        # from the OpenRouter dropdown (e.g. config=anthropic but picked openai/gpt-5.4-mini).
        # In this case always route through openrouter with the full provider/model string.
        if (
            model_catalog.get_provider_record(prefix) is not None
            and prefix != config_provider
        ):
            return model_id, "openrouter", None

    return model_id, config_provider, config_base_url


def get_effective_default_model(config_data: dict | None = None) -> str:
    """Resolve the effective Hermes default model from config, then env overrides."""
    active_cfg = config_data if config_data is not None else cfg
    default_model = DEFAULT_MODEL

    model_cfg = active_cfg.get("model", {})
    if isinstance(model_cfg, str):
        default_model = model_cfg.strip()
    elif isinstance(model_cfg, dict):
        cfg_default = str(model_cfg.get("default") or "").strip()
        if cfg_default:
            default_model = cfg_default

    env_model = (
        os.getenv("HERMES_MODEL") or os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL")
    )
    if env_model:
        default_model = env_model.strip()
    return default_model


def get_first_available_model() -> str:
    """Return the first available model ID from the model list.

    Uses peek_available_models() (non-blocking; issue #8) and picks the
    first model from the first provider group.  Falls back to
    get_effective_default_model() when the model list is empty or
    unavailable — on a cold cache this returns the default immediately
    instead of triggering a live catalog build on the chat-start path.
    """
    try:
        available = peek_available_models()
        groups = available.get('groups', [])
        if groups:
            models = groups[0].get('models', [])
            if models:
                return models[0]['id']
    except Exception:
        pass
    return get_effective_default_model()


def normalize_model_prefix(model_id: str, *, catalog: dict | None = None) -> str:
    """Ensure bare model names carry an ``@provider:`` prefix when applicable.

    Models from non-default provider groups are presented in the dropdown as
    ``@provider:model`` (see ``_prefix_models_if_needed``).  Sessions created
    before this convention — or imported from CLI/gateway — may store the bare
    name (e.g. ``deepseek-v4-pro``), which causes the frontend model selector
    to show no selection.  This helper normalizes the stored value so it
    matches the dropdown format.

    Rules:
      - Already has ``@provider:`` → return as-is.
      - Contains ``/`` (OpenRouter-style) → return as-is.
      - Bare name → look up in catalog; if found under a non-default provider
        group, return ``@provider:model``.  Otherwise return as-is.

    Args:
        catalog: pre-fetched catalog to consult (batch callers such as
            GET /api/sessions pass one peek result for all rows, issue #16).
            When None, a single-row non-blocking peek is done per call.
    """
    model = (model_id or "").strip()
    if not model:
        return model
    # Already prefixed or namespaced — nothing to do.
    if model.startswith("@") or "/" in model:
        return model

    if catalog is None:
        try:
            # Non-blocking peek (issue #8): groups are best-effort here — on a
            # cold cache the bare name is returned unchanged and normalized on a
            # later request once the catalog is warm.
            catalog = peek_available_models()
        except Exception:
            return model

    active_provider = (catalog.get("active_provider") or "").lower()
    for group in catalog.get("groups") or []:
        gid = (group.get("provider_id") or "").lower()
        for m in group.get("models") or []:
            mid = m.get("id", "")
            # Match either the bare name or a stored @gid:bare form.
            if mid == model or (mid.startswith("@") and ":" in mid
                                and mid.split(":", 1)[1] == model):
                # Always return @provider:model for consistent identification.
                return f"@{gid}:{model}"
    return model


# ── Reasoning config (CLI parity for /reasoning) ─────────────────────────────
# Mirrors hermes_constants.parse_reasoning_effort so WebUI can validate without
# importing from the agent tree (which may not be installed).  Any drift here
# will show up in the shared test suite since both sides accept the same set.
VALID_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")


def parse_reasoning_effort(effort):
    """Parse an effort level into the dict the agent expects.

    Returns None when *effort* is empty or unrecognised (caller interprets as
    "use default"), ``{"enabled": False}`` for ``"none"``, and
    ``{"enabled": True, "effort": <level>}`` for any of
    ``VALID_REASONING_EFFORTS``.
    """
    if not effort or not str(effort).strip():
        return None
    eff = str(effort).strip().lower()
    if eff == "none":
        return {"enabled": False}
    if eff in VALID_REASONING_EFFORTS:
        return {"enabled": True, "effort": eff}
    return None


def get_reasoning_status() -> dict:
    """Return current reasoning configuration from the active profile's
    config.yaml — the same source of truth the CLI reads from.

    Keys:
      - show_reasoning: bool — from ``display.show_reasoning`` (default True)
      - reasoning_effort: str — from ``agent.reasoning_effort`` ('' = default)
    """
    config_data = _load_yaml_config_file(_get_config_path())
    display_cfg = config_data.get("display") or {}
    agent_cfg = config_data.get("agent") or {}
    show_raw = display_cfg.get("show_reasoning") if isinstance(display_cfg, dict) else None
    effort_raw = agent_cfg.get("reasoning_effort") if isinstance(agent_cfg, dict) else None
    return {
        # Match CLI default (True if unset in config.yaml)
        "show_reasoning": bool(show_raw) if isinstance(show_raw, bool) else True,
        "reasoning_effort": str(effort_raw or "").strip().lower(),
    }


def set_reasoning_display(show: bool) -> dict:
    """Persist ``display.show_reasoning`` to the active profile's config.yaml.

    Mirrors CLI ``/reasoning show|hide``: writes the same key that the CLI
    writes, so the preference is shared across the WebUI and the terminal
    REPL for the same profile.
    """
    config_path = _get_config_path()
    with _cfg_lock:
        config_data = _load_yaml_config_file(config_path)
        display_cfg = config_data.get("display")
        if not isinstance(display_cfg, dict):
            display_cfg = {}
        display_cfg["show_reasoning"] = bool(show)
        config_data["display"] = display_cfg
        _save_yaml_config_file(config_path, config_data)
    reload_config()
    return get_reasoning_status()


def set_reasoning_effort(effort: str) -> dict:
    """Persist ``agent.reasoning_effort`` to the active profile's config.yaml.

    Mirrors CLI ``/reasoning <level>``: same key, same valid values
    (``none`` | ``minimal`` | ``low`` | ``medium`` | ``high`` | ``xhigh``).
    Raises ``ValueError`` on an unrecognised level so callers can return 400.
    """
    raw = str(effort or "").strip().lower()
    if not raw:
        raise ValueError("effort is required")
    if raw != "none" and raw not in VALID_REASONING_EFFORTS:
        raise ValueError(
            f"Unknown reasoning effort '{effort}'. "
            f"Valid: none, {', '.join(VALID_REASONING_EFFORTS)}."
        )
    config_path = _get_config_path()
    with _cfg_lock:
        config_data = _load_yaml_config_file(config_path)
        agent_cfg = config_data.get("agent")
        if not isinstance(agent_cfg, dict):
            agent_cfg = {}
        agent_cfg["reasoning_effort"] = raw
        config_data["agent"] = agent_cfg
        _save_yaml_config_file(config_path, config_data)
    reload_config()
    return get_reasoning_status()


def set_hermes_default_model(model_id: str) -> dict:
    """Persist the Hermes default model in config.yaml and reload runtime config."""
    selected_model = str(model_id or "").strip()
    if not selected_model:
        raise ValueError("model is required")

    config_path = _get_config_path()
    # Hold _cfg_lock only around the read-modify-write of the YAML file.
    # reload_config() acquires _cfg_lock internally (it's not reentrant) so
    # it must be called AFTER releasing the lock to avoid deadlock.
    with _cfg_lock:
        config_data = _load_yaml_config_file(config_path)
        model_cfg = config_data.get("model", {})
        if not isinstance(model_cfg, dict):
            model_cfg = {}

        previous_provider = str(model_cfg.get("provider") or "").strip()
        resolved_model, resolved_provider, resolved_base_url = resolve_model_provider(
            selected_model
        )
        # Persist the resolved bare/slash form, NOT the `@provider:` prefix. The
        # prefix is a WebUI-internal routing hint that the hermes-agent CLI does
        # not understand — if we wrote `@nous:anthropic/claude-opus-4.6` to
        # config.yaml, a user who ran `hermes` in the terminal right after
        # saving via WebUI would have the agent send that literal string to the
        # Nous API, which would reject it (Nous expects `anthropic/claude-opus-4.6`,
        # not the prefixed form). The Settings picker handles the resulting
        # CLI-shaped bare form via `_applyModelToDropdown()`'s normalising
        # matcher — see `static/panels.js` (#895).
        persisted_model = str(resolved_model or selected_model).strip()
        persisted_provider = str(resolved_provider or previous_provider or "").strip()
        # Gateway variant (e.g. xiaomi-token-plan): config.yaml is shared with
        # the hermes CLI, which raises AuthError for slugs its registry does not
        # know. Persist the SDK provider slug plus the variant's base_url — the
        # same shape the CLI already honors for custom base URLs. The WebUI
        # recovers the variant (and its private key) via
        # resolve_runtime_for_provider()'s base_url reverse-lookup on each chat.
        _variant_parts = resolve_gateway_variant(persisted_provider)
        if _variant_parts is not None:
            _sdk_provider, _variant_base, _variant_key = _variant_parts
            persisted_provider = _sdk_provider
            if _variant_base:
                resolved_base_url = _variant_base

        model_cfg["default"] = persisted_model
        if persisted_provider:
            model_cfg["provider"] = persisted_provider

        if resolved_base_url:
            model_cfg["base_url"] = str(resolved_base_url).strip().rstrip("/")
        elif persisted_provider != previous_provider:
            if persisted_provider == "openai":
                model_cfg["base_url"] = "https://api.openai.com/v1"
            elif not persisted_provider.startswith("custom:"):
                model_cfg.pop("base_url", None)
        else:
            # Same SDK provider as before, no new base_url (selection was the
            # plain gateway, not a variant): drop a stale gateway-variant
            # base_url left by a previous default — otherwise picking
            # @xiaomi:... after @xiaomi-token-plan:... would keep routing to
            # the token-plan endpoint.
            _stale = str(model_cfg.get("base_url") or "").strip().rstrip("/")
            if _stale and model_catalog.find_gateway_variant(
                persisted_provider, _stale
            ):
                model_cfg.pop("base_url", None)

        config_data["model"] = model_cfg
        _save_yaml_config_file(config_path, config_data)
    # Reload outside the lock — reload_config() acquires _cfg_lock itself.
    reload_config()
    # Invalidate the TTL cache so the next /api/models call returns fresh data
    # with the new default model. Do NOT call get_available_models() here —
    # it triggers a live provider fetch (up to 8s) that blocks the HTTP response
    # to the browser, causing a visible freeze on every Settings save (#895).
    invalidate_models_cache()
    return {"ok": True, "model": persisted_model}


# ── TTL cache for get_available_models() ─────────────────────────────────────
_available_models_cache: dict | None = None
_available_models_cache_ts: float = 0.0
_AVAILABLE_MODELS_CACHE_TTL: float = 86400.0  # 24 hours
# Cache lock guards ONLY millisecond-scale cache-state ops (reference swaps,
# timestamps, the build-in-progress flag, generation counter).  The live
# catalog build (SDK provider discovery, IMDS/HTTP probes) runs OUTSIDE this
# lock on a background worker thread — readers never park behind a build
# (issue #16: the old design held this lock across the whole ~160s build).
_available_models_cache_lock = threading.RLock()
_cache_build_cv = threading.Condition(_available_models_cache_lock)  # shares underlying RLock so notify_all() is safe inside with _available_models_cache_lock
# Single-flight guard for the live build: the caller that flips
# _cache_build_in_progress False->True (under the cache lock) is the one that
# spawns the worker; everyone else is a pure CV waiter.  The flag is only ever
# cleared by the worker when the build has fully finished (published, failed,
# or abandoned), so waiters never observe "not building" while a worker is
# still winding down.
_cache_build_in_progress = False
# Incremented on every invalidation (config change, provider edit, manual
# invalidate).  A build started under an older generation discards its result
# instead of publishing stale data over the invalidation.
_models_cache_generation = 0
# Error raised by the most recent failed build (cleared when a new build
# starts).  Lets the call that spawned the failed worker re-raise it.
_models_build_error: BaseException | None = None

# Bound on how long a request-path caller waits for an in-flight build
# before degrading to a stale/disk/config-only answer flagged
# ``building: True`` (issue #16: /api/models used to block for the full
# 48-160s cold build).  The boot prewarm passes wait_seconds=None and waits
# for the real catalog.
_API_MODELS_WAIT_S = float(os.getenv("HERMES_WEBUI_MODELS_WAIT_S", "3.0"))

# Cache for credential pool results -- calling load_pool() per-provider per-server
# session is expensive (~10s for zai due to endpoint probing).  The credential pool
# only changes when the user adds/removes credentials, which is rare; a 24h TTL
# is plenty safe and ensures get_available_models() cold paths are fast.
_CREDENTIAL_POOL_CACHE: dict[str, tuple[float, "CredentialPool"]] = {}  # pid -> (ts, pool)
_provider_models_invalidated_ts: dict[str, float] = {}  # provider_id -> timestamp of last invalidation

# Disk-backed in-memory cache for get_available_models().
# Written to disk on every cache population so the cache survives server restarts.
# Invalidated (file deleted) whenever a provider is added/changed/removed or
# config.yaml changes.  A TTL is still used as a fallback in case the invalidation
# signal is somehow missed, but the cache will always be warm after the first
# page load following a server start.
# Cache file lives inside STATE_DIR so each server instance (different
# HERMES_WEBUI_STATE_DIR / port) has its own file and test runs never
# pollute the production server's cache. Also works on macOS and Windows
# where /dev/shm does not exist.
_models_cache_path = STATE_DIR / "models_cache.json"

# Per-provider model lists from the last successful build, read from the
# disk snapshot envelope (issue #10).  Used as the "last success" prior
# when a live refresh fails — the dropdown keeps showing these instead of
# collapsing to the baseline or clearing entirely.
_disk_provider_models: dict[str, list] = {}

# Last known mtime of model_catalog.json; 0 = never loaded.  A catalog
# edit is applied on the next access without a server restart (same
# cheap-guard pattern as _cfg_mtime for config.yaml).
_model_catalog_mtime: float = 0.0


def _delete_models_cache_on_disk() -> None:
    try:
        os.unlink(str(_models_cache_path))
    except OSError:
        pass  # already absent


def _load_models_cache_from_disk() -> dict | None:
    """Load the catalog snapshot from disk if it exists and is valid.

    Returns the public cache shape ``{active_provider, default_model,
    groups}`` (issue #10 fix: older snapshots only stored ``groups``; the
    missing fields are synthesized so the first response after a restart
    is never incomplete).  Per-provider model lists from the envelope are
    stashed in ``_disk_provider_models`` so a failed live refresh can fall
    back to the last successful result.
    """
    global _disk_provider_models
    try:
        import json as _j
        if not _models_cache_path.exists():
            return None
        with open(_models_cache_path, encoding="utf-8") as f:
            cache = _j.load(f)
        if not isinstance(cache, dict) or "groups" not in cache:
            return None
        provider_models = cache.get("provider_models")
        if isinstance(provider_models, dict):
            _disk_provider_models = {
                str(pid): list(models or [])
                for pid, models in provider_models.items()
                if pid
            }
        else:
            _disk_provider_models = {}
        return {
            "active_provider": cache.get("active_provider"),
            "default_model": cache.get("default_model") or "",
            "groups": cache.get("groups", []),
        }
    except Exception:
        return None


def _save_models_cache_to_disk(cache: dict) -> None:
    """Save the full catalog envelope to disk so it survives restarts.

    Persists ``active_provider`` and ``default_model`` alongside
    ``groups`` (issue #10: the old snapshot only kept ``groups``, so the
    first /api/models response after a restart was missing those fields),
    plus a per-provider model map used as the last-success fallback when
    a live refresh fails after a restart.
    """
    try:
        tmp = str(_models_cache_path) + f".{os.getpid()}.tmp"
        groups = cache.get("groups", []) or []
        payload = {
            "schema_version": 1,
            "active_provider": cache.get("active_provider"),
            "default_model": cache.get("default_model") or "",
            "groups": groups,
            "provider_models": {
                g.get("provider_id"): g.get("models", [])
                for g in groups
                if isinstance(g, dict) and g.get("provider_id")
            },
            "fetched_at": time.time(),
        }
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.rename(tmp, str(_models_cache_path))
    except Exception:
        pass  # Non-fatal -- cache will rebuild on next call


def invalidate_models_cache():
    """Force the TTL cache for get_available_models() to be cleared.

    Call this after modifying config.cfg in-memory (e.g. in tests) so
    the next call to get_available_models() picks up the changes rather
    than returning a stale cached result.

    Also deletes the on-disk cache so that a subsequent cold build does
    not immediately reload a stale disk snapshot and skip the fresh build.
    This is essential for test isolation: without the disk delete, tests
    that call invalidate_models_cache() still get back the previous test's
    result from the disk cache because the disk hit is checked before the memory
    cache rebuild runs.
    """
    global _available_models_cache, _available_models_cache_ts, _models_cache_generation
    with _available_models_cache_lock:
        _available_models_cache = None
        _available_models_cache_ts = 0.0
        # Bump the generation so an in-flight build discards its (now stale)
        # result and retries.  Do NOT clear _cache_build_in_progress here —
        # that flag is owned by the build worker; clobbering it would wake
        # waiters while the stale build is still running (issue #16).
        _models_cache_generation += 1
        # Clear the credential pool cache too. The cache key is provider_id
        # only, so without this, tests (and live provider key edits) see a
        # stale CredentialPool from a prior auth_store payload — the test_
        # credential_pool_providers suite was hitting this directly.
        _CREDENTIAL_POOL_CACHE.clear()
    # Also delete the disk cache so the next cold build starts fresh.
    # Disk delete is outside the lock — file I/O shouldn't block other readers.
    _delete_models_cache_on_disk()


def invalidate_provider_models_cache(provider_id: str):
    """Invalidate cached models for a single provider.

    Also invalidates the full cache so that the next get_available_models()
    call rebuilds all groups cleanly (the rebuilt provider is merged with any
    other cached groups from the 24h TTL window).  After the next
    get_available_models() call, _provider_models_invalidated_ts[provider_id]
    is cleared so the provider's fresh models are used.

    Args:
        provider_id: canonical provider id (e.g. 'openai', 'anthropic', 'custom:my-key')
    """
    global _available_models_cache, _available_models_cache_ts, _CREDENTIAL_POOL_CACHE, _models_cache_generation
    with _available_models_cache_lock:
        _available_models_cache = None
        _available_models_cache_ts = 0.0
        # Invalidate-vs-build race guard (issue #16): an in-flight build
        # started before this point must not publish over the invalidation.
        _models_cache_generation += 1
        _provider_models_invalidated_ts[provider_id] = time.time()
        # Also evict the credential pool so the next cold path re-loads it.
        # Must evict both the original key and its canonical form (load_pool
        # may be called with either, and both paths cache under their own key).
        _CREDENTIAL_POOL_CACHE.pop(provider_id, None)
        _CREDENTIAL_POOL_CACHE.pop(_resolve_provider_alias(provider_id), None)
    _delete_models_cache_on_disk()


def _get_label_for_model(model_id: str, existing_groups: list) -> str:
    """Return a human-friendly label for *model_id*.

    Resolution order:
    1. If the model already appears in *existing_groups* with a label, use it.
    2. Strip @provider: prefix and namespace prefix, then title-case.

    This ensures the injected default model entry in the dropdown always shows
    the same label as the live-fetched or static-catalog version, rather than
    the raw lowercase ID string (#909).
    """
    # Strip @provider: prefix for lookup
    lookup_id = model_id
    if lookup_id.startswith("@") and ":" in lookup_id:
        lookup_id = lookup_id.split(":", 1)[1]

    # Check existing groups for a matching label
    _norm = lambda s: (s.split("/", 1)[-1] if "/" in s else s).replace("-", ".").lower()
    norm_lookup = _norm(lookup_id)
    for g in existing_groups:
        for m in g.get("models", []):
            if m.get("label") and _norm(str(m.get("id", ""))) == norm_lookup:
                return m["label"]

    # Fall back: capitalize each hyphen-separated word, preserve dots in version numbers.
    # The catalog lookup above handles well-known models; this only fires for unlisted IDs.
    bare = lookup_id.split("/")[-1] if "/" in lookup_id else lookup_id
    return " ".join(
        w.upper() if (len(w) <= 3 and w.replace(".", "").isalnum() and not w.isdigit()) else w.capitalize()
        for w in bare.replace("_", "-").split("-")
    )


def _model_supports_extended_thinking(model_id: str, provider: str = "") -> bool:
    """Return whether a model is known to support extended thinking / configurable reasoning effort.

    This is a best-effort heuristic.  Models not recognised here default to False
    (chip disabled) — the agent will silently ignore the setting regardless.
    """
    mid = (model_id or "").lower()
    pid = (provider or "").lower()
    # Anthropic: Opus & Sonnet support extended thinking; Haiku does not
    if "claude" in mid or "anthropic" in pid:
        if "haiku" in mid:
            return False
        if "opus" in mid or "sonnet" in mid:
            return True
    # DeepSeek R1 (reasoning model) and V4 family
    if "deepseek" in pid or "deepseek" in mid:
        if "r1" in mid or "reasoner" in mid or "v4" in mid:
            return True
    # Gemini 2.5+ thinking models
    if "gemini" in mid and ("2.5" in mid or "3." in mid or "3-" in mid):
        return True
    # xAI Grok
    if "grok" in mid:
        return True
    # Qwen3 family
    if "qwen3" in mid:
        return True
    # OpenAI GPT-5.x (reasoning capable)
    if ("gpt-5" in mid or "gpt-5." in mid) and "openai" in pid:
        return True
    return False


def _config_only_catalog_fields() -> tuple:
    """Local-only catalog fields shared by the cold build and the peek accessor.

    Reads config.yaml's ``model`` section plus the auth.json
    ``active_provider`` fallback — pure disk reads, no network.  Extracted
    verbatim from the prefix of get_available_models()'s cold path so the
    latency-critical chat-start path can answer without triggering a live
    provider fetch (issue #8).

    Returns ``(default_model, active_provider, auth_store)``.
    """
    active_provider = None
    default_model = get_effective_default_model(cfg)

    # 1. Read config.yaml model section
    model_cfg = cfg.get("model", {})
    if isinstance(model_cfg, str):
        pass  # default_model already set by get_effective_default_model
    elif isinstance(model_cfg, dict):
        active_provider = model_cfg.get("provider")
        cfg_default = model_cfg.get("default", "")
        if cfg_default:
            default_model = cfg_default

    # Normalize active_provider to its canonical key
    if active_provider:
        active_provider = _resolve_provider_alias(active_provider)
        # config.yaml persists a gateway variant as its SDK provider plus the
        # variant base_url (hermes CLI compatibility). Remap back to the
        # variant slug so group ordering, @provider: prefixing and default
        # injection all treat the variant as the active provider.
        _cfg_base = ""
        if isinstance(model_cfg, dict):
            _cfg_base = str(model_cfg.get("base_url") or "").strip()
        if _cfg_base:
            try:
                _variant = model_catalog.find_gateway_variant(active_provider, _cfg_base)
            except Exception:
                _variant = None
            if _variant:
                active_provider = _variant

    # 2. Read auth store (active_provider fallback)
    auth_store = {}
    try:
        from app.api.profiles import get_active_hermes_home as _gah

        auth_store_path = _gah() / "auth.json"
    except ImportError:
        auth_store_path = HOME / ".hermes" / "auth.json"
    if auth_store_path.exists():
        try:
            import json as _j

            auth_store = _j.loads(auth_store_path.read_text(encoding="utf-8"))
            if not active_provider:
                active_provider = _resolve_provider_alias(auth_store.get("active_provider"))
        except Exception:
            logger.debug("Failed to load auth store from %s", auth_store_path)

    return default_model, active_provider, auth_store


def _prior_provider_models() -> dict[str, list]:
    """Return ``{provider_id: models}`` from the last successful builds.

    Sources: the current memory cache (when present) and the disk
    snapshot envelope.  Used as the "last success" prior when a live
    refresh fails, so a group keeps its previous contents instead of
    collapsing to the baseline or clearing (issue #10).
    """
    prior: dict[str, list] = {}
    mem = _available_models_cache
    if isinstance(mem, dict):
        for g in mem.get("groups", []) or []:
            if isinstance(g, dict) and g.get("provider_id") and g.get("models"):
                prior[g["provider_id"]] = copy.deepcopy(g["models"])
    for _pid, _models in (_disk_provider_models or {}).items():
        if _models:
            prior.setdefault(_pid, copy.deepcopy(_models))
    return prior


def _resolve_detected_provider_models(
    cfg: dict,
    detected: set,
    active_provider,
    custom_fetch_provider: str = "",
) -> tuple[dict[str, list], set[str]]:
    """Live-resolve model lists for eligible detected providers (issue #10).

    Runs inside the catalog build — never on a request path and never a
    new long-lived thread: providers with ``refresh.mode=api`` are queried
    concurrently (bounded thread pool, per-provider timeout from the
    catalog) and resolved through the single deterministic rule in
    ``model_catalog`` (a successful live query is the source of truth;
    the JSON baseline only donates display labels).  A failed query keeps
    the last successful list (memory / disk envelope) or the baseline; a
    group is never cleared by a failed query — except an auth failure
    (HTTP 401/403), which the returned ``auth_failed`` set reports so the
    group builder can omit that provider entirely.

    Explicit user config (``providers.<id>.models`` in config.yaml) is
    never queried here — the group builder applies the override directly,
    preserving its historical semantics.  Providers whose models were
    already fetched from a user-declared ``model.base_url`` (custom
    gateways / ollama on a non-default port) are skipped: that fetch
    already reflects the user's exact endpoint.

    Returns ``({provider_id: models}, auth_failed)``; providers absent
    from the first element fall back to baseline-or-override handling in
    the group builder, while providers in ``auth_failed`` must be skipped
    by it.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if not detected:
        return {}, set()

    model_cfg = cfg.get("model", {}) if isinstance(cfg.get("model", {}), dict) else {}
    providers_cfg = cfg.get("providers", {}) if isinstance(cfg.get("providers", {}), dict) else {}
    prior = _prior_provider_models()

    def _lookup_key(pid: str) -> str:
        try:
            from app.api.providers import _resolve_probe_key

            return _resolve_probe_key(cfg, pid, model_cfg)
        except Exception:
            return ""

    targets: list[str] = []
    for pid in sorted(detected):
        if pid.startswith("custom:") or pid == custom_fetch_provider:
            continue
        rec = model_catalog.get_provider_record(pid)
        if rec is None or rec["refresh"]["mode"] != "api" or not rec.get("endpoint"):
            continue
        provider_cfg = providers_cfg.get(pid, {})
        if isinstance(provider_cfg, dict) and "models" in provider_cfg:
            continue  # explicit user config wins; no query needed
        auth_type = (rec.get("auth") or {}).get("type", "none")
        if auth_type == "none" or _lookup_key(pid):
            targets.append(pid)

    if not targets:
        return {}, set()

    def _resolve_one(pid: str) -> tuple[str, list]:
        models = model_catalog.resolve_provider_models(
            pid,
            api_key=_lookup_key(pid),
            prior=prior.get(pid),
        )
        return pid, models

    resolved: dict[str, list] = {}
    auth_failed: set[str] = set()
    # Overall budget for the fan-out (issue: a single hung provider query —
    # e.g. unresponsive DNS in the SSRF guard — used to pin this loop and
    # the whole catalog build forever).  Each query is individually bounded
    # (DNS + socket timeouts in model_catalog), so this is a backstop: on
    # expiry keep whatever completed and let the rest fall back to
    # prior/baseline via the group builder.  No `with` block here — its
    # __exit__ joins all workers, which would re-block on a hung query
    # after we already gave up on it.
    _budget_s = float(os.getenv("HERMES_WEBUI_LIVE_QUERY_BUDGET_S", "30.0"))
    executor = ThreadPoolExecutor(max_workers=min(6, len(targets)))
    try:
        futures = [executor.submit(_resolve_one, pid) for pid in targets]
        try:
            for fut in as_completed(futures, timeout=_budget_s):
                try:
                    pid, models = fut.result()
                except model_catalog.ProviderAuthError as exc:
                    # Rejected credential: report it so the group builder
                    # omits the provider — must be caught before the generic
                    # handler below, which would swallow it.  exc.slug is the
                    # provider id that was resolved (the target pid).
                    logger.info(
                        "live model resolve: %s rejected the API key (HTTP %s) — "
                        "hiding its models",
                        exc.slug, exc.status,
                    )
                    auth_failed.add(exc.slug)
                    continue
                except Exception:
                    logger.debug("live model resolve task failed", exc_info=True)
                    continue
                if models:
                    resolved[pid] = models
        except TimeoutError:
            pending = sum(1 for f in futures if not f.done())
            logger.warning(
                "live model resolve budget (%.0fs) exceeded; %d provider(s) "
                "still pending — keeping prior/baseline for those",
                _budget_s, pending,
            )
            for fut in futures:
                fut.cancel()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return resolved, auth_failed


def _build_available_models_uncached() -> dict:
    """Build the full model catalog from providers (the slow path).

    Runs on the dedicated background build worker thread — never on an HTTP
    request thread and never inside ``_available_models_cache_lock`` (issue
    #16: the old design held that lock across this entire function, parking
    every /api/models and peek caller for the whole SDK provider loop,
    boto3/IMDS probes, and custom-endpoint fetches).

    Pure producer: returns the catalog dict; publication into the module
    cache is handled by the worker under the cache lock.
    """

    default_model, active_provider, auth_store = _config_only_catalog_fields()
    groups = []

    # 1. Read config.yaml model section (base_url only — provider/default
    #    were resolved by _config_only_catalog_fields above)
    cfg_base_url = ""  # must be defined before conditional blocks (#117)
    model_cfg = cfg.get("model", {})
    if isinstance(model_cfg, str):
        pass  # default_model already set by get_effective_default_model
    elif isinstance(model_cfg, dict):
        cfg_base_url = model_cfg.get("base_url", "")

    # 3. Detect available providers.
    detected_providers = set()
    # Providers added via an explicit credential_pool entry. These have
    # no env var or config.yaml key, so _provider_has_key() returns False
    # even though the credential is valid. Track them so the post-filter
    # below does not drop them.
    _pool_explicit_providers: set[str] = set()
    if active_provider:
        detected_providers.add(active_provider)

    try:
        _pool = auth_store.get("credential_pool", {}) if isinstance(auth_store, dict) else {}
        if isinstance(_pool, dict) and _pool:
            try:
                from agent.credential_pool import load_pool as _load_pool

                for _pid in list(_pool.keys()):
                    try:
                        _canonical_pid = _resolve_provider_alias(str(_pid))
                        # Check credential pool cache first
                        _cached = _CREDENTIAL_POOL_CACHE.get(_pid)
                        if _cached is not None:
                            _cp_ts, _cp_pool = _cached
                            if (time.time() - _cp_ts) < 86400.0:
                                _all_entries = _cp_pool.entries()
                            else:
                                _lp_t0 = time.monotonic()
                                _cp_pool = _load_pool(_pid)
                                _CREDENTIAL_POOL_CACHE[_pid] = (time.time(), _cp_pool)
                                _all_entries = _cp_pool.entries()
                        else:
                            _lp_t0 = time.monotonic()
                            _cp_pool = _load_pool(_pid)
                            _CREDENTIAL_POOL_CACHE[_pid] = (time.time(), _cp_pool)
                            _all_entries = _cp_pool.entries()
                        _explicit = [
                            e for e in _all_entries
                            if not _is_ambient_gh_cli_entry(
                                str(getattr(e, "source", "") or ""),
                                str(getattr(e, "label", "") or ""),
                                str(getattr(e, "key_source", "") or ""),
                            )
                            # Env-sourced entries are only mirrors of env vars;
                            # _provider_has_key() already reads those vars, so
                            # a deleted key must not linger here as "explicit".
                            and not _is_env_sourced_pool_entry(
                                str(getattr(e, "source", "") or "")
                            )
                        ]
                        if _explicit:
                            detected_providers.add(_canonical_pid)
                            _pool_explicit_providers.add(_canonical_pid)
                    except Exception:
                        logger.debug("credential_pool.load_pool(%s) failed", _pid)
            except ImportError:
                for _pid, _entries in _pool.items():
                    if not isinstance(_entries, list) or len(_entries) == 0:
                        continue
                    _has_explicit_cred = any(
                        isinstance(_entry, dict)
                        and not _is_ambient_gh_cli_entry(
                            str(_entry.get("source", "") or ""),
                            str(_entry.get("label", "") or ""),
                            str(_entry.get("key_source", "") or ""),
                        )
                        # Same rule as the load_pool path: env-sourced mirrors
                        # are covered by _provider_has_key(), not "explicit".
                        and not _is_env_sourced_pool_entry(
                            str(_entry.get("source", "") or "")
                        )
                        for _entry in _entries
                    )
                    if _has_explicit_cred:
                        _canonical = _resolve_provider_alias(str(_pid))
                        detected_providers.add(_canonical)
                        _pool_explicit_providers.add(_canonical)
    except Exception:
        logger.debug("Failed to inspect credential_pool from auth store")

    all_env: dict = {}

    try:
        from hermes_cli.models import list_available_providers as _lap
        from hermes_cli.auth import get_auth_status as _gas

        for _p in _lap():
            if not _p.get("authenticated"):
                continue
            try:
                _src = _gas(_p["id"]).get("key_source", "")
                if _src == "gh auth token":
                    continue
            except Exception:
                logger.debug("Failed to get key source for provider %s", _p.get("id", "unknown"))
            detected_providers.add(_p["id"])
    except Exception:
        logger.debug("Failed to detect auth providers from hermes")

    # Catalog env-var scan — runs UNCONDITIONALLY, not gated on hermes_cli
    # failing. hermes_cli only knows providers it has registered itself;
    # catalog-declared providers (issue #10) and gateway variants (e.g.
    # xiaomi-token-plan / XIAOMI_TOKEN_PLAN_API_KEY) are invisible to it. With
    # the old `if not _hermes_auth_used:` gate the variant was never detected
    # whenever hermes_cli imported cleanly, so a configured Token-Plan key
    # never surfaced in the model picker.
    try:
        from app.api.profiles import get_active_hermes_home as _gah2

        hermes_env_path = _gah2() / ".env"
    except ImportError:
        hermes_env_path = HOME / ".hermes" / ".env"
    env_keys = {}
    if hermes_env_path.exists():
        try:
            for line in hermes_env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env_keys[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            logger.debug("Failed to parse hermes env file")
    all_env = {**env_keys}
    # Scan the env vars declared by the JSON catalog so newly added
    # providers (e.g. ark / ARK_API_KEY) are detected without Python
    # changes (issue #10).  Providers with an empty baseline and
    # refresh.mode=none have nothing to render, so they are skipped
    # here (e.g. meta-llama) — their env vars remain available for key
    # management via providers.py.  api-mode providers with empty
    # baselines (ark, ollama, huggingface, ollama-cloud) are NOT
    # skipped: their models come from the live query once a key exists.
    for _slug, _env_vars in model_catalog.catalog_env_vars().items():
        _rec = model_catalog.get_provider_record(_slug)
        if _rec is not None and not _rec["models"] and _rec["refresh"]["mode"] != "api":
            continue
        for _var in _env_vars:
            val = os.getenv(_var)
            if val:
                all_env[_var] = val
        if any(all_env.get(_var) for _var in _env_vars):
            detected_providers.add(_slug)
    if all_env.get("OPENAI_API_KEY"):
        # openai-codex uses ChatGPT OAuth (not OPENAI_API_KEY) for its default endpoint.
        # Detecting it here lets users who have both credentials configured find it in the
        # picker without a manual config.yaml edit. Users without Codex OAuth will see
        # picker entries but hit auth errors at inference time (#1189 known limitation).
        detected_providers.add("openai-codex")

    # Also detect providers explicitly listed in config.yaml providers section.
    # A user may configure a provider key via config.yaml providers.<name>.api_key
    # without setting the corresponding env var. (#604)
    _cfg_providers = cfg.get("providers", {})
    if isinstance(_cfg_providers, dict):
        for _pid_key in _cfg_providers:
            if (
                model_catalog.get_provider_record(_pid_key) is not None
                or _pid_key in _cfg_providers
            ):
                detected_providers.add(_pid_key)

    # Post-filter: only keep providers that actually have a configured API key
    # or are OAuth-based.  This removes false positives from hermes_cli's
    # get_auth_status() which may report providers as authenticated even when
    # no key is configured.
    try:
        from app.api.providers import _provider_has_key, _provider_is_oauth

        _filtered = set()
        for _pid in detected_providers:
            if _pid == active_provider:
                _filtered.add(_pid)
            elif _pid in _pool_explicit_providers:
                # Provider has at least one explicit credential_pool entry.
                # _provider_has_key() only checks env vars + config.yaml, so
                # it returns False here even though the credential is valid.
                _filtered.add(_pid)
            elif _provider_is_oauth(_pid):
                _filtered.add(_pid)
            elif _provider_has_key(_pid):
                _filtered.add(_pid)
        detected_providers = _filtered
    except ImportError:
        logger.debug("Could not import providers module for key filtering")

    # 4. Fetch models from custom endpoint if base_url is configured
    auto_detected_models = []
    # Provider slug inferred from the model.base_url fetch (ollama /
    # lmstudio / local / custom).  Its models come from that fetch — the
    # catalog live resolver must not re-query a default endpoint for it.
    _custom_fetch_provider = ""
    if cfg_base_url:
        try:
            base_url = cfg_base_url.strip()
            if base_url.endswith("/v1"):
                endpoint_url = base_url + "/models"
            else:
                endpoint_url = base_url.rstrip("/") + "/v1/models"

            provider = "custom"
            parsed = urlparse(base_url if "://" in base_url else f"http://{base_url}")
            host = (parsed.netloc or parsed.path).lower()

            if parsed.hostname:
                try:
                    addr = ipaddress.ip_address(parsed.hostname)
                    if addr.is_private or addr.is_loopback or addr.is_link_local:
                        if "ollama" in host or "127.0.0.1" in host or "localhost" in host:
                            provider = "ollama"
                        elif "lmstudio" in host or "lm-studio" in host:
                            provider = "lmstudio"
                        else:
                            provider = "local"
                except ValueError:
                    pass

            api_key = ""
            if isinstance(model_cfg, dict):
                api_key = (model_cfg.get("api_key") or "").strip()
            if not api_key:
                providers_cfg = cfg.get("providers", {})
                if isinstance(providers_cfg, dict):
                    for provider_key in filter(None, [active_provider, "custom"]):
                        provider_cfg = providers_cfg.get(provider_key, {})
                        if isinstance(provider_cfg, dict):
                            api_key = (provider_cfg.get("api_key") or "").strip()
                            if api_key:
                                break
            if not api_key:
                api_key_vars = (
                    "HERMES_API_KEY",
                    "HERMES_OPENAI_API_KEY",
                    "OPENAI_API_KEY",
                    "LOCAL_API_KEY",
                    "OPENROUTER_API_KEY",
                    "API_KEY",
                )
                for key in api_key_vars:
                    api_key = (all_env.get(key) or os.getenv(key) or "").strip()
                    if api_key:
                        break
            if not api_key and provider not in ("ollama", "lmstudio", "local"):
                # Public endpoint without API key — skip model fetch
                # to avoid listing models that would fail at runtime.
                raise ValueError(f"No API key configured for {base_url}")

            # Build set of hostnames from custom_providers config — these are
            # user-explicitly configured endpoints and should not be blocked by SSRF.
            _ssrf_trusted_hosts: set[str] = set()
            _custom_providers_cfg = cfg.get("custom_providers", [])
            if isinstance(_custom_providers_cfg, list):
                for _cp in _custom_providers_cfg:
                    if not isinstance(_cp, dict):
                        continue
                    _cp_base = (_cp.get("base_url") or "").strip()
                    if _cp_base:
                        _cp_parsed = urlparse(_cp_base if "://" in _cp_base else f"http://{_cp_base}")
                        if _cp_parsed.hostname:
                            _ssrf_trusted_hosts.add(_cp_parsed.hostname.lower())

            # Shared OpenAI-compatible fetch + SSRF validation (issue #10:
            # the guard moved into model_catalog so catalog-driven live
            # queries reuse the exact same rules).
            entries = model_catalog.query_openai_compat(
                endpoint_url,
                api_key=api_key,
                timeout_s=10.0,
                trust_local_hosts=_ssrf_trusted_hosts,
                label_fn=(
                    (lambda mid: _format_ollama_label(mid))
                    if provider in ("ollama", "ollama-cloud")
                    else None
                ),
            )
            for _entry in entries:
                auto_detected_models.append({"id": _entry["id"], "label": _entry["label"]})
                detected_providers.add(provider.lower())
            _custom_fetch_provider = provider.lower()
        except Exception:
            logger.debug("Custom endpoint unreachable or misconfigured for provider: %s", provider)

    _custom_providers_cfg = cfg.get("custom_providers", [])
    _named_custom_groups: dict = {}
    if isinstance(_custom_providers_cfg, list):
        _seen_custom_ids = {m["id"] for m in auto_detected_models}
        for _cp in _custom_providers_cfg:
            if not isinstance(_cp, dict):
                continue
            _cp_name = (_cp.get("name") or "").strip()
            _slug = ("custom:" + _cp_name.lower().replace(" ", "-")) if _cp_name else None

            # Collect model IDs: singular "model" field first, then "models" dict keys
            _cp_model_ids: list[str] = []
            _cp_model = _cp.get("model", "")
            if _cp_model:
                _cp_model_ids.append(_cp_model)
            _cp_models_dict = _cp.get("models")
            if isinstance(_cp_models_dict, dict):
                for _m_id in _cp_models_dict:
                    if isinstance(_m_id, str) and _m_id.strip() and _m_id not in _cp_model_ids:
                        _cp_model_ids.append(_m_id.strip())

            for _cp_model in _cp_model_ids:
                if _cp_model and _cp_model not in _seen_custom_ids:
                    _cp_label = _get_label_for_model(_cp_model, [])
                    _seen_custom_ids.add(_cp_model)
                    if _slug:
                        if _slug not in _named_custom_groups:
                            _named_custom_groups[_slug] = (_cp_name, [])
                        detected_providers.add(_slug)
                        _named_custom_groups[_slug][1].append(
                            {"id": _cp_model, "label": _cp_label}
                        )
                    else:
                        auto_detected_models.append({"id": _cp_model, "label": _cp_label})
                        detected_providers.add("custom")

    _has_custom_providers = isinstance(_custom_providers_cfg, list) and len(_custom_providers_cfg) > 0
    if active_provider and active_provider != "custom" and not _has_custom_providers:
        detected_providers.discard("custom")
        for _slug in list(detected_providers):
            if _slug.startswith("custom:") and not _has_custom_providers:
                detected_providers.discard(_slug)
    elif active_provider == "custom" and _has_custom_providers:
        _has_unnamed = any(
            isinstance(_cp, dict) and not (_cp.get("name") or "").strip()
            for _cp in _custom_providers_cfg
        )
        if not _has_unnamed:
            detected_providers.discard("custom")

    # 5. Live-resolve model lists for eligible providers (issue #10), then
    #    build model groups from the JSON catalog + cfg overrides + live
    #    merge results.
    resolved_models, auth_failed = _resolve_detected_provider_models(
        cfg, detected_providers, active_provider, _custom_fetch_provider
    )
    if detected_providers:
        for pid in sorted(detected_providers):
            if pid in auth_failed:
                # Rejected credential (HTTP 401/403): omit the group — a bad
                # key must not surface baseline models in the picker.
                continue
            if pid.startswith("custom:") and pid in _named_custom_groups:
                _nc_display, _nc_models = _named_custom_groups[pid]
                if _nc_models:
                    groups.append({"provider": _nc_display, "provider_id": pid, "models": _nc_models})
                continue
            provider_name = model_catalog.provider_display(pid)
            if auto_detected_models and pid == _custom_fetch_provider:
                # Models from the user-declared model.base_url fetch — the
                # exact endpoint/port the user configured wins over any
                # catalog default (ollama/lmstudio/local/custom gateways).
                models = _apply_provider_prefix(auto_detected_models, pid, active_provider)
                groups.append(
                    {"provider": provider_name, "provider_id": pid, "models": models}
                )
            elif (
                model_catalog.get_provider_record(pid) is not None
                or pid in cfg.get("providers", {})
            ):
                raw_models = resolved_models.get(pid)
                if raw_models is None:
                    # No live result (query failed with no prior, or the
                    # provider is refresh.mode=none): JSON baseline, with
                    # an explicit config.yaml override taking priority.
                    raw_models = model_catalog.provider_baseline_models(pid)
                    provider_cfg = cfg.get("providers", {}).get(pid, {})
                    if isinstance(provider_cfg, dict) and "models" in provider_cfg:
                        cfg_models = provider_cfg["models"]
                        if isinstance(cfg_models, dict):
                            raw_models = [{"id": k, "label": k} for k in cfg_models.keys()]
                        elif isinstance(cfg_models, list):
                            raw_models = [{"id": k, "label": k} for k in cfg_models]
                if not raw_models and pid == "ollama-cloud":
                    # Historical behavior: Ollama Cloud only renders a group
                    # when models were actually discovered (baseline is []).
                    continue
                models = _apply_provider_prefix(raw_models, pid, active_provider)
                groups.append(
                    {
                        "provider": provider_name,
                        "provider_id": pid,
                        "models": models,
                    }
                )
            else:
                if auto_detected_models:
                    groups.append(
                        {
                            "provider": provider_name,
                            "provider_id": pid,
                            "models": auto_detected_models,
                        }
                    )
    else:
        if default_model:
            # Only show default model if its provider has a configured key
            _dm_provider = default_model.split("/")[0] if "/" in default_model else active_provider
            _dm_has_key = False
            try:
                from app.api.providers import _provider_has_key as _phk
                _dm_has_key = _phk(_dm_provider) if _dm_provider else False
            except ImportError:
                _dm_has_key = True  # conservative: show if can't check
            if _dm_has_key:
                label = _get_label_for_model(default_model, groups)
                groups.append(
                    {"provider": "Default", "provider_id": "default", "models": [{"id": default_model, "label": label}]}
                )

    if default_model:
        # Only inject default model if its provider has a configured key
        # that a live query has not rejected (auth_failed providers stay
        # hidden — no phantom "Default" group for a revoked key).
        _dm_prov2 = default_model.split("/")[0] if "/" in default_model else active_provider
        _dm_ok2 = False
        try:
            from app.api.providers import _provider_has_key as _phk2
            _dm_ok2 = _phk2(_dm_prov2) if _dm_prov2 else False
        except ImportError:
            _dm_ok2 = True
        if _dm_prov2 in auth_failed or active_provider in auth_failed:
            _dm_ok2 = False
        _norm = lambda mid: (mid.split("/", 1)[-1] if "/" in mid else mid).replace("-", ".")
        all_ids_norm = {_norm(m["id"]) for g in groups for m in g.get("models", [])}
        if _norm(default_model) not in all_ids_norm and _dm_ok2:
            label = _get_label_for_model(default_model, groups)
            target_display = (
                model_catalog.provider_display(active_provider).lower()
                if active_provider
                else ""
            )
            injected = False
            for g in groups:
                if target_display and g.get("provider", "").lower() == target_display:
                    g["models"].insert(0, {"id": default_model, "label": label})
                    injected = True
                    break
            if not injected and groups:
                groups.append(
                    {
                        "provider": "Default",
                        "provider_id": active_provider or "default",
                        "models": [{"id": default_model, "label": label}],
                    }
                )

    # Enrich all models with supports_extended_thinking flag for UI chip state
    for g in groups:
        provider_id = g.get("provider_id", "")
        for m in g.get("models", []):
            if "supports_extended_thinking" not in m:
                m["supports_extended_thinking"] = _model_supports_extended_thinking(
                    m.get("id", ""), provider_id
                )

    return {
        "active_provider": active_provider,
        "default_model": default_model,
        "groups": groups,
    }


def get_available_models(*, wait_seconds: float | None = None) -> dict:
    """
    Return available models grouped by provider.

    Discovery order:
      1. Read config.yaml 'model' section for active provider info
      2. Check for known API keys in env or ~/.hermes/.env
      3. Fetch models from custom endpoint if base_url is configured
      4. Fall back to hardcoded model list (OpenRouter-style)

    Args:
        wait_seconds: upper bound on how long this call blocks waiting for
            an in-flight catalog build before degrading to the best cached
            answer (stale memory, disk snapshot, or config-only catalog)
            flagged ``building: True`` so the frontend keeps polling.
            ``None`` waits indefinitely (boot prewarm, tests).  The live
            build itself always runs on a background worker thread, so no
            caller ever pays the provider-discovery cost on its own thread
            (issue #16).

    Returns: {
        'active_provider': str|None,
        'default_model': str,
        'groups': [{'provider': str, 'models': [{'id': str, 'label': str}]}]
    }
    """
    global _cache_build_in_progress, _available_models_cache, _available_models_cache_ts, _cache_build_cv, _models_cache_generation, _models_build_error, _model_catalog_mtime
    # Config mtime check — must come before any config reads.
    # (Test #585 verifies _current_mtime appears before active_provider = None)
    try:
        _current_mtime = Path(_get_config_path()).stat().st_mtime
    except OSError:
        _current_mtime = 0.0
    if _current_mtime != _cfg_mtime:
        reload_config()

    # ── FAST PATH ─────────────────────────────────────────────────────────────
    # Check config mtime OUTSIDE the lock so this cheap check doesn't serialize
    # concurrent requests.
    try:
        _current_mtime = Path(_get_config_path()).stat().st_mtime
    except OSError:
        _current_mtime = 0.0
    _cfg_changed = _current_mtime != _cfg_mtime
    # model_catalog.json mtime — same cheap guard (issue #10): a catalog
    # edit is picked up on the next access without a server restart.
    _catalog_json_changed = model_catalog.catalog_mtime() != _model_catalog_mtime

    with _available_models_cache_lock:
        # Reload config if changed; an in-flight build read the old config,
        # so bump the generation to make it discard its result (issue #16).
        if _cfg_changed:
            reload_config()
            _available_models_cache = None
            _available_models_cache_ts = 0.0
            _models_cache_generation += 1
        if _catalog_json_changed:
            # Mirror the config-change handling: force-reload the catalog,
            # bust the memory cache, discard any in-flight build's result,
            # and drop the disk snapshot (its model lists came from the
            # old catalog).
            model_catalog.load_catalog(force=True)
            _model_catalog_mtime = model_catalog.catalog_mtime()
            _available_models_cache = None
            _available_models_cache_ts = 0.0
            _models_cache_generation += 1
            _delete_models_cache_on_disk()
        # Serve from memory cache if fresh.  Hold the lock only for this
        # reference grab + deepcopy (milliseconds) — never for the build.
        now = time.monotonic()
        if _available_models_cache is not None and (now - _available_models_cache_ts) < _AVAILABLE_MODELS_CACHE_TTL:
            return copy.deepcopy(_available_models_cache)

    # Disk load OUTSIDE the lock (~0.1ms read): a warm disk snapshot avoids
    # the build entirely right after a restart.  Memory is only consulted
    # when None — a TTL-*expired* memory cache still forces a live rebuild
    # so stale provider data is never resurrected from disk (test_ttl_cache).
    if _available_models_cache is None:
        _gen_before_disk = _models_cache_generation
        disk_groups = _load_models_cache_from_disk()
        if disk_groups is not None and _gen_before_disk == _models_cache_generation:
            landed = False
            with _available_models_cache_lock:
                now = time.monotonic()
                if _available_models_cache is None:
                    _available_models_cache = disk_groups
                    _available_models_cache_ts = now
                    landed = True
                elif (now - _available_models_cache_ts) < _AVAILABLE_MODELS_CACHE_TTL:
                    return copy.deepcopy(_available_models_cache)
            if landed:
                # Rewrite via tmp+rename so a concurrent reader never sees a
                # torn file (same as the old in-lock path).
                _save_models_cache_to_disk(disk_groups)
                return copy.deepcopy(disk_groups)

    # ── COLD PATH: single-flight background build ─────────────────────────────
    # The caller that flips the in-progress flag spawns the worker; everyone
    # (the spawner included) then waits on the CV, bounded by wait_seconds.
    # The flag flip is the single-flight guard — no second worker can start
    # until this one clears it.
    spawned = False
    with _available_models_cache_lock:
        if not _cache_build_in_progress:
            _cache_build_in_progress = True
            _models_build_error = None
            spawned = True
    if spawned:
        try:
            threading.Thread(
                target=_models_build_worker, name="models-catalog-build", daemon=True
            ).start()
        except Exception:
            # Thread spawn failed (resource exhaustion): unwedge the flag so
            # later callers are not parked on the CV forever.
            with _available_models_cache_lock:
                _cache_build_in_progress = False
                _cache_build_cv.notify_all()
            raise

    # ── WAITER PATH ───────────────────────────────────────────────────────────
    # Never blocks on the cache lock (the build runs outside it) — only on
    # the CV, for at most wait_seconds.
    with _cache_build_cv:
        if wait_seconds is None:
            _cache_build_cv.wait_for(lambda: not _cache_build_in_progress)
        else:
            _cache_build_cv.wait_for(
                lambda: not _cache_build_in_progress, timeout=wait_seconds
            )

    with _available_models_cache_lock:
        now = time.monotonic()
        if (_available_models_cache is not None
                and (now - _available_models_cache_ts) < _AVAILABLE_MODELS_CACHE_TTL):
            return copy.deepcopy(_available_models_cache)
        # Not fresh: either the build is still running (bounded waiter gave
        # up), it failed, or it was invalidated mid-flight.
        stale = copy.deepcopy(_available_models_cache) if _available_models_cache is not None else None
        error = _models_build_error if spawned else None

    if error is not None:
        # The build this call started failed — surface it to the caller the
        # same way the old in-thread builder did (boot prewarm logs the
        # failure).  Non-spawning waiters degrade instead (issue #16: one
        # broken provider must not take the whole picker down).
        raise error

    result = stale
    if result is None:
        disk_groups = _load_models_cache_from_disk()
        if disk_groups is not None:
            result = copy.deepcopy(disk_groups)
    if result is None:
        default_model, active_provider, _auth_store = _config_only_catalog_fields()
        result = {
            "active_provider": active_provider,
            "default_model": default_model,
            "groups": [],
            # Flags a fallback so consumers (the frontend store) keep
            # polling instead of treating the empty catalog as final.
            "config_only": True,
        }
    result["building"] = True
    return result


def _models_build_worker() -> None:
    """Background worker that owns the live catalog build (issue #16).

    Exactly one worker runs at a time — the spawner flipped
    ``_cache_build_in_progress`` to True under the cache lock before
    starting us, and we are the only one allowed to clear it.  If an
    invalidation lands mid-build (generation moved), the stale result is
    discarded and the build retried once while the flag stays set so
    waiters remain parked.  Never raises — failures are recorded in
    ``_models_build_error`` and surfaced by get_available_models() to the
    call that spawned us.
    """
    global _cache_build_in_progress, _available_models_cache, _available_models_cache_ts, _cache_build_cv, _models_cache_generation, _models_build_error
    # Ownership of the single-flight flag: every normal exit path below clears
    # the flag itself and sets `released`.  The finally backstop then only
    # fires if this worker died abnormally (still holding the flag) — it must
    # never clear the flag owned by a NEWER worker spawned after we published
    # (that race fired during our post-publish disk save and made the next
    # caller degrade instead of joining the fresh build).
    released = False
    try:
        for _attempt in range(2):
            with _available_models_cache_lock:
                gen = _models_cache_generation
            try:
                result = _build_available_models_uncached()
            except Exception as exc:
                with _available_models_cache_lock:
                    _models_build_error = exc
                    _cache_build_in_progress = False
                    _cache_build_cv.notify_all()
                released = True
                logger.warning("models catalog build failed", exc_info=True)
                return
            with _available_models_cache_lock:
                published = gen == _models_cache_generation
                if published:
                    _available_models_cache = result
                    _available_models_cache_ts = time.monotonic()
                    _cache_build_in_progress = False
                    _cache_build_cv.notify_all()
            if published:
                released = True
                # Disk write stays outside the lock (tmp+rename atomic swap).
                _save_models_cache_to_disk(result)
                return
            logger.debug(
                "models catalog invalidated mid-build; rebuilding "
                "(attempt %d/2)", _attempt + 1
            )
        # Two invalidated attempts — give up; waiters degrade with
        # building:True and the next call retries.
        with _available_models_cache_lock:
            _cache_build_in_progress = False
            _cache_build_cv.notify_all()
        released = True
        logger.warning("models catalog build abandoned after repeated invalidation")
    finally:
        # Backstop: if this worker died abnormally the flag is still ours
        # (True, no newer worker can have spawned) — unwedge it so later
        # callers are not parked on the CV forever.
        if not released:
            with _available_models_cache_lock:
                _cache_build_in_progress = False
                _cache_build_cv.notify_all()


# How long peek_available_models() waits for an in-flight cold build before
# falling back to a config-only catalog.  Since the issue #16 restructure the
# live build runs OUTSIDE _available_models_cache_lock, so this budget is
# spent on the CV wait (joining the build) plus the (now millisecond-scale)
# lock acquisition.  Kept short: up to three such calls can occur on one
# chat-start request (new_session, model resolve, prefix normalize), so
# waits stack — 1.0s each keeps the worst case ~3s, under the 5s client
# timeouts used by the test suite and the frontend (issue #8 smoke
# regression: an unbounded lock wait timed out session/new).
_PEEK_WAIT_S = 1.0


def peek_available_models(*, wait_seconds: float = _PEEK_WAIT_S) -> dict:
    """Non-blocking catalog accessor for latency-critical request paths.

    Mirrors get_available_models()'s fast paths (config mtime reload, memory
    cache, disk cache, bounded wait for an in-flight build) but NEVER
    triggers a live provider fetch: with nothing cached it answers from the
    config-only catalog (default_model + active_provider, empty groups,
    ``config_only: True`` so consumers can tell a fallback from a real
    empty catalog).

    For callers that only need ``default_model``/``active_provider`` and can
    treat ``groups`` as best-effort (chat-start model resolution, prefix
    normalization).  ``/api/models`` uses get_available_models() with a
    bounded wait instead.  Never raises (issue #8: /api/chat/start must not
    block on the cold model catalog).
    """
    global _available_models_cache, _available_models_cache_ts, _model_catalog_mtime, _models_cache_generation

    def _config_only() -> dict:
        default_model, active_provider, _auth_store = _config_only_catalog_fields()
        return {
            "active_provider": active_provider,
            "default_model": default_model,
            "groups": [],
            # Flags a config-only fallback (no live catalog was consulted).
            # Consumers that need group-level routability — cross-provider
            # session-model rewrites — must skip their decision instead of
            # guessing on empty groups (issue #8: a valid anthropic/... model
            # was rewritten to the default while the boot prewarm still held
            # the catalog lock).
            "config_only": True,
        }

    try:
        # Config mtime check — same cheap guard as get_available_models()
        # so a config edit is observed without building anything.
        try:
            _current_mtime = Path(_get_config_path()).stat().st_mtime
        except OSError:
            _current_mtime = 0.0
        if _current_mtime != _cfg_mtime:
            reload_config()
        # model_catalog.json mtime — same cheap guard (issue #10).  Peek
        # never triggers a build; it only picks up the reloaded catalog
        # for the config-only fallback and any later rebuild.
        if model_catalog.catalog_mtime() != _model_catalog_mtime:
            model_catalog.load_catalog(force=True)
            _model_catalog_mtime = model_catalog.catalog_mtime()
            _available_models_cache = None
            _available_models_cache_ts = 0.0
            _models_cache_generation += 1

        # Disk load BEFORE lock (fast), then memory cache under the lock —
        # same ordering as get_available_models()'s fast path.
        should_wait = _cache_build_in_progress
        disk = None
        if _available_models_cache is None:
            disk = _load_models_cache_from_disk()

        # Bounded lock acquisition.  Since the issue #16 restructure the
        # live build no longer runs under this lock (it lives on a background
        # worker), but keep the acquisition bounded anyway: this helper
        # exists so a latency-critical request never queues behind anything
        # (issue #8: /api/session/new timed out at 5s waiting on the boot
        # prewarm's build).  If the lock isn't free within wait_seconds,
        # answer from the config-only catalog instead of queueing.
        _t_acquire = time.monotonic()
        acquired = _available_models_cache_lock.acquire(timeout=wait_seconds)
        try:
            if not acquired:
                return _config_only()
            # Rare race: we got the lock but a build slipped in — spend only
            # the REMAINING budget on the cv wait so the total stays bounded.
            if should_wait:
                _cache_build_cv.wait_for(
                    lambda: not _cache_build_in_progress,
                    timeout=max(0.0, wait_seconds - (time.monotonic() - _t_acquire)),
                )
            now = time.monotonic()
            if _available_models_cache is not None and (
                now - _available_models_cache_ts
            ) < _AVAILABLE_MODELS_CACHE_TTL:
                return copy.deepcopy(_available_models_cache)
            if disk is not None:
                _available_models_cache = disk
                _available_models_cache_ts = now
                _save_models_cache_to_disk(disk)
                return copy.deepcopy(disk)

            return _config_only()
        finally:
            if acquired:
                _available_models_cache_lock.release()
    except Exception:
        logger.warning(
            "peek_available_models failed; returning config-only catalog",
            exc_info=True,
        )
        try:
            return _config_only()
        except Exception:
            return {
                "active_provider": None,
                "default_model": str(DEFAULT_MODEL or ""),
                "groups": [],
                "config_only": True,
            }


# ── Static file path ─────────────────────────────────────────────────────────
_INDEX_HTML_PATH = REPO_ROOT / "static" / "index.html"

# ── Thread synchronisation ───────────────────────────────────────────────────
LOCK = threading.Lock()
SESSIONS_MAX = 100_000  # Increased from 100 — notify stream replaced polling, no eviction pressure
CHAT_LOCK = threading.Lock()
class ReplayableStream:
    """Fan-out SSE stream with full event history for page-refresh replay.

    Replaces the plain queue.Queue used previously so that:
    - Multiple SSE consumers (tabs) can subscribe without stealing each other's events.
    - A reconnecting client can retrieve all past events via replay() and then
      join the live feed seamlessly.

    Thread-safety: all mutable state is protected by a single internal lock.
    Memory: _history is capped at MAX_HISTORY entries.  When the cap is hit,
    low-value events (token, metering) are evicted first; structural events
    (message_start, tool, tool_complete, done, etc.) are preserved.
    """
    MAX_HISTORY = 20000000

    # Events that are essential for rebuilding the message structure during
    # replay.  When the history buffer overflows, we drop token/metering
    # events before touching these.
    _STRUCTURAL_EVENTS = frozenset({
        'message_start', 'tool', 'tool_complete',
        'done', 'stream_end', 'error', 'cancel', 'apperror',
        'clarify', 'approval', 'compressed', 'title',
    })

    def __init__(self):
        import queue as _queue
        self._history: list[tuple[str, dict]] = []
        self._subscribers: list[_queue.Queue] = []
        self._lock = threading.Lock()
        self._done = False
        self._closed = False

    def put(self, event: str, data: dict) -> None:  # noqa: D401
        """Write one event to history and fan-out to all live subscribers."""
        import queue as _queue
        with self._lock:
            if self._closed:
                return
            if len(self._history) >= self.MAX_HISTORY:
                # Smart eviction: drop low-value events (token, metering)
                # before touching structural events needed for replay.
                _evicted = False
                for _i in range(len(self._history)):
                    if self._history[_i][0] not in self._STRUCTURAL_EVENTS:
                        self._history.pop(_i)
                        _evicted = True
                        break
                if not _evicted:
                    self._history.pop(0)
            self._history.append((event, data))
            if event in ('done', 'stream_end', 'error', 'cancel', 'apperror'):
                self._done = True
            for sq in list(self._subscribers):
                try:
                    sq.put_nowait((event, data))
                except _queue.Full:
                    pass  # slow consumer — drop the event rather than block

    def subscribe(self) -> 'queue.Queue':  # type: ignore[name-defined]
        """Register a new SSE consumer and return a dedicated queue."""
        import queue as _queue
        sq: _queue.Queue = _queue.Queue()
        with self._lock:
            self._subscribers.append(sq)
        return sq

    def unsubscribe(self, sq: 'queue.Queue') -> None:  # type: ignore[name-defined]
        """Remove a consumer queue (called from the SSE handler's finally block)."""
        with self._lock:
            try:
                self._subscribers.remove(sq)
            except ValueError:
                pass  # already removed

    def replay(self) -> list[tuple[str, dict]]:
        """Return a thread-safe snapshot of all events produced so far."""
        with self._lock:
            return list(self._history)

    def is_done(self) -> bool:
        """True once a terminal event (done/cancel/error/stream_end) was put."""
        with self._lock:
            return self._done

    def close(self, reason='closed'):
        """Force-close the stream, notifying all subscribers.

        Sends a sentinel (None) to each subscriber queue so SSE handlers
        break their read loop.  The 'done' event (sent by the agent thread)
        is the sole completion signal for the frontend; stream_close was
        removed to avoid spurious 'Unknown replay event' warnings.
        """
        import queue as _queue
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for sq in list(self._subscribers):
                try:
                    sq.put_nowait(None)  # sentinel — closes SSE connection
                except _queue.Full:
                    pass
            self._subscribers.clear()


STREAMS: dict = {}
STREAMS_LOCK = threading.Lock()
CANCEL_FLAGS: dict = {}
AGENT_INSTANCES: dict = {}  # stream_id -> AIAgent instance for interrupt propagation
SESSION_STREAMS: dict = {}  # session_id -> set[stream_id] for mode_changed broadcast
STREAM_PARTIAL_TEXT: dict = {}  # stream_id -> partial assistant text accumulated during streaming
SERVER_START_TIME = time.time()

# Agent cache: reuse AIAgent across messages in the same WebUI session so that
# _user_turn_count survives between turns.  This mirrors the gateway's
# _agent_cache pattern and is required for injectionFrequency: "first-turn".
SESSION_AGENT_CACHE: dict = {}   # session_id -> (AIAgent, config_sig)
SESSION_AGENT_CACHE_LOCK = threading.Lock()

# Per-session FIFO message queue for busy-input-mode="queue".
# Queue entries are dicts with keys: message, model, workspace, attachments.
# Messages are dequeued when the current stream ends (success/error/cancel).
# LOST ON RESTART by design — no persistence needed.
SESSION_MESSAGE_QUEUE: dict[str, list] = {}  # session_id -> [{payload}, ...]
SESSION_MESSAGE_QUEUE_LOCK = threading.Lock()
_MAX_QUEUE_LENGTH = 20

# ── WebSocket configuration ──────────────────────────────────────────────────
WS_HEARTBEAT_INTERVAL = 30      # Heartbeat interval in seconds
WS_MAX_REPLAY_SUBS = 10         # Max replay subscriptions per connection
WS_MESSAGE_MAX_SIZE = 65536     # Max message size in bytes (64KB)
WS_REPLAY_HISTORY_LIMIT = 1000  # Max events to keep in replay history
WS_QUEUE_TIMEOUT = 0.1          # Queue polling timeout in seconds (100ms)


def _evict_session_agent(session_id: str) -> None:
    """Remove a cached agent for a session (on delete, clear, or model switch)."""
    with SESSION_AGENT_CACHE_LOCK:
        SESSION_AGENT_CACHE.pop(session_id, None)

# ── Session env override context ────────────────────────────────────────────
# The ContextVar itself lives in app.compat.env_overrides (documented there):
# ContextVar, NOT threading.local, so overrides propagate to Hermes tool
# workers via propagate_context_to_thread(). The same ContextVar also feeds
# the in-process SDK readers through app.compat.browser_cdp (patched
# browser_tool._get_cdp_override_raw). Re-exported under the historical
# private name so existing references keep working.
from app.compat.env_overrides import env_override_ctx as _env_override_ctx
_original_popen = None          # saved reference to real subprocess.Popen
_popen_patched = False
_popen_lock = threading.Lock()


def _set_thread_env(**kwargs):
    """Register session env overrides and enable subprocess isolation.

    Overrides are stored in a ContextVar so they propagate to Hermes
    tool-worker threads (via ``contextvars.copy_context()``) and into any
    subprocess those workers spawn through the patched Popen. Contexts
    that never called ``_set_thread_env`` are unaffected — their
    subprocesses behave exactly as before.
    """
    global _popen_patched, _original_popen

    _env_override_ctx.set(dict(kwargs))

    # Lazily install SDK compat patches so in-process readers
    # (browser_tool._get_cdp_override_raw) honor this session's override.
    # Idempotent; also installed at server startup.
    try:
        from app.compat import install as _install_compat_patches
        _install_compat_patches()
    except Exception:
        logging.getLogger("app.api.config").debug(
            "compat patch install failed", exc_info=True
        )
    try:
        import logging as _logging
        _logging.getLogger("app.api.config").debug(
            "[env-override] set thread=%s session_key=%r keys=%s",
            threading.get_ident(),
            kwargs.get("HERMES_SESSION_KEY", "<missing>"),
            sorted(kwargs.keys()),
        )
    except Exception:
        pass

    with _popen_lock:
        if not _popen_patched:
            import subprocess as _sp

            _original_popen = _sp.Popen

            class _IsolatedPopen(_original_popen):  # type: ignore[valid-type]
                """Popen that merges session env overrides into the child env."""

                def __init__(self, *args, **kwargs):
                    overrides = _env_override_ctx.get() or {}
                    try:
                        import logging as _logging
                        _env_arg = kwargs.get('env', None)
                        _logging.getLogger("app.api.config").debug(
                            "[env-override] popen thread=%s has_overrides=%s "
                            "ov_keys=%s args0=%r",
                            threading.get_ident(),
                            1 if overrides else 0,
                            sorted(overrides.keys()) if overrides else None,
                            (args[0][:80] if args and isinstance(args[0], (str, list, tuple)) else None),
                        )
                    except Exception:
                        pass
                    if overrides:
                        env = kwargs.get('env', None)
                        env = dict(os.environ) if env is None else dict(env)
                        # An override value of None means "remove this key"
                        # (e.g. HTTP_PROXY for pinned sessions, where
                        # agent-browser would otherwise route the local CDP
                        # connection through the proxy).
                        for _k, _v in overrides.items():
                            if _v is None:
                                env.pop(_k, None)
                            else:
                                env[_k] = _v
                        kwargs['env'] = env
                    super().__init__(*args, **kwargs)

            _sp.Popen = _IsolatedPopen
            _popen_patched = True


def _clear_thread_env():
    _env_override_ctx.set({})


# ── Per-session agent locks ───────────────────────────────────────────────────
SESSION_AGENT_LOCKS: dict = {}
SESSION_AGENT_LOCKS_LOCK = threading.Lock()


def _get_session_agent_lock(session_id: str) -> threading.Lock:
    """Return the per-session Lock used to serialize all Session mutations.

    Lock lifecycle invariant:
      - A Lock is created lazily on first access and lives in SESSION_AGENT_LOCKS
        for the lifetime of the session.
      - The entry is pruned in /api/session/delete (under SESSION_AGENT_LOCKS_LOCK)
        so deleted sessions don't leak a Lock forever.
      - During context compression the agent may rotate session_id.  The
        streaming thread migrates the lock entry atomically under
        SESSION_AGENT_LOCKS_LOCK: it aliases the new session_id to the *same*
        Lock object and pops the old-id entry (see streaming.py compression
        block).  This ensures that subsequent callers using the new ID still
        acquire the same Lock, while the old-id entry is removed to prevent a
        leak.  The streaming thread already holds the Lock during this
        migration, so the reference stays alive even after the dict entry is
        removed.
      - Lock contract: hold for the in-memory mutation + s.sync_webui_metadata() only; never
        across network I/O (LLM calls, HTTP requests).
    """
    with SESSION_AGENT_LOCKS_LOCK:
        if session_id not in SESSION_AGENT_LOCKS:
            SESSION_AGENT_LOCKS[session_id] = threading.Lock()
        return SESSION_AGENT_LOCKS[session_id]


# ── Settings persistence ─────────────────────────────────────────────────────

_SETTINGS_DEFAULTS = {
    "send_key": "enter",  # 'enter' or 'ctrl+enter'
    "show_cli_sessions": True,  # show hermes CLI sessions from state.db in the sidebar
    "show_messaging_sessions": True,  # show IM/gateway sessions (weixin, telegram, discord, ...) in the sidebar
    "show_cron_sessions": False,  # show cron job sessions in the sidebar
    "check_for_updates": True,  # check if webui/agent repos are behind upstream
    "theme": "dark",  # light | dark | system
    "skin": "default",  # accent color skin: default | ares | mono | slate | poseidon | sisyphus | charizard
    "language": "en",  # UI locale code; must match a key in static/i18n.js LOCALES
    "sound_enabled": False,  # play notification sound when assistant finishes
    "notifications_enabled": False,  # browser notification when tab is in background
    "show_thinking": True,  # show/hide thinking/reasoning blocks in chat view
    "sidebar_density": "compact",  # compact | detailed
    "auto_title_refresh_every": "0",  # adaptive title refresh: 0=off, 5/10/20=every N exchanges
    "busy_input_mode": "interrupt",  # behavior when sending while agent is running: interrupt | queue | steer
    "session_poll_interval_sec": "10",  # frontend session polling interval in seconds, 0=disabled
    "font_size": "medium",  # UI font size / zoom level: small | medium | large
    "auto_compress_threshold": "0",  # Path 1 — auto-compress by message count (0=off, 30/50/100/200/500)
    "auto_compress_token_threshold": "0",  # Path 3 — auto-compress by token % of context (0=off, 0.50/0.60/0.70/0.85)
    "auto_compress_poor_ratio": "0.5",  # quality gate: remaining ratio > this → "poor" compression
    "auto_compress_disable_after": "3",  # quality gate: consecutive poor compressions before disabling auto-compress
    "max_compaction_snapshots": "5",  # max archived compaction snapshots to retain per session
    "last_seen_cron_ts_map": {},  # per-job {job_id: epoch_seconds} — drives +N unread badge
    "password_hash": None,  # PBKDF2-HMAC-SHA256 hash; None = auth disabled
    "default_mode": "manual",  # approval mode for new sessions: manual | smart | yolo
    "session_ttl": "86400",  # login cookie TTL in seconds (default 24h)
}
# Keys never served again: each belonged to a feature that has been removed.
# They are dropped on read and purged from settings.json.
_SETTINGS_LEGACY_DROP_KEYS = {"assistant_language", "bubble_layout", "default_model", "show_token_usage", "sync_to_insights", "default_workspace", "onboarding_completed"}
_SETTINGS_THEME_VALUES = {"light", "dark", "system"}
_SETTINGS_SKIN_VALUES = {
    "default",
    "ares",
    "mono",
    "slate",
    "poseidon",
    "sisyphus",
    "charizard",
}
_SETTINGS_LEGACY_THEME_MAP = {
    # Legacy full themes now map onto the closest supported theme + accent skin pair.
    "slate": ("dark", "slate"),
    "solarized": ("dark", "poseidon"),
    "monokai": ("dark", "sisyphus"),
    "nord": ("dark", "slate"),
    "oled": ("dark", "default"),
}


def _normalize_appearance(theme, skin) -> tuple[str, str]:
    """Normalize a (theme, skin) pair, migrating legacy theme names.

    Legacy migration table (from `_SETTINGS_LEGACY_THEME_MAP`):

        slate     → ("dark", "slate")
        solarized → ("dark", "poseidon")
        monokai   → ("dark", "sisyphus")
        nord      → ("dark", "slate")
        oled      → ("dark", "default")

    Unknown / custom theme names fall back to ("dark", "default").  This is a
    behavior change vs. the pre-PR-#627 state, where the `theme` field was
    open-ended ("no enum gate -- allows custom themes").  Users who set a
    custom CSS theme via `data-theme` will need to re-apply via skin or
    custom CSS — see CHANGELOG entry for details.

    The same mapping is mirrored in `static/boot.js` (`_LEGACY_THEME_MAP`)
    so client and server normalize identically; keep them in sync.
    """
    raw_theme = theme.strip().lower() if isinstance(theme, str) else ""
    raw_skin = skin.strip().lower() if isinstance(skin, str) else ""
    legacy = _SETTINGS_LEGACY_THEME_MAP.get(raw_theme)
    if legacy:
        next_theme, legacy_skin = legacy
    elif raw_theme in _SETTINGS_THEME_VALUES:
        next_theme, legacy_skin = raw_theme, "default"
    else:
        # Unknown themes used to exist; default to dark so upgrades stay visually stable.
        next_theme, legacy_skin = "dark", "default"
    next_skin = (
        raw_skin
        if raw_skin in _SETTINGS_SKIN_VALUES
        else legacy_skin
    )
    return next_theme, next_skin


_settings_cache: dict | None = None
_settings_cache_lock = threading.Lock()
_settings_mtime: float = 0.0


def _rewrite_settings_file(stored: dict) -> None:
    """Rewrite settings.json with ``stored`` (atomic, best-effort).

    Used to physically drop keys whose feature was deleted (issue #5 Part C)
    instead of carrying them on disk forever. Failures are logged and ignored
    — a read-only or full disk must never break settings loading.
    """
    tmp = SETTINGS_FILE.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(stored, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, SETTINGS_FILE)
    except Exception:
        logger.debug("Failed to rewrite %s after dropping legacy keys", SETTINGS_FILE, exc_info=True)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _read_settings_from_disk() -> tuple[dict, float]:
    """Read settings.json from disk and return (merged_dict, mtime)."""
    settings = dict(_SETTINGS_DEFAULTS)
    stored = None
    mt = 0.0
    try:
        settings_exists = SETTINGS_FILE.exists()
    except OSError:
        logger.debug("Cannot stat settings file %s (inaccessible?)", SETTINGS_FILE)
        settings_exists = False
    if settings_exists:
        try:
            stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                live = {
                    k: v
                    for k, v in stored.items()
                    if k not in _SETTINGS_LEGACY_DROP_KEYS
                }
                settings.update(live)
                if len(live) != len(stored):
                    # Issue #5 Part C: purge dead keys from disk too. Runs once
                    # per stale file — the rewrite removes them.
                    _rewrite_settings_file(live)
        except Exception:
            logger.debug("Failed to load settings from %s", SETTINGS_FILE)
        try:
            mt = SETTINGS_FILE.stat().st_mtime
        except OSError:
            mt = 0.0
    settings["theme"], settings["skin"] = _normalize_appearance(
        stored.get("theme") if isinstance(stored, dict) else settings.get("theme"),
        stored.get("skin") if isinstance(stored, dict) else settings.get("skin"),
    )
    # NOTE: do NOT set settings["default_model"] here. The authoritative source for
    # default_model is config.yaml (mirrored in _cfg_cache and refreshed by
    # reload_config()). Caching a snapshot of it in _settings_cache caused
    # /api/settings to keep returning the boot-time default_model after
    # POST /api/default-model until something else wrote settings.json. The
    # fresh value is injected by load_settings() on every call instead.
    return settings, mt


def load_settings() -> dict:
    """Load settings with mtime-based caching (mirrors config.yaml _cfg_cache pattern).

    Fast path: stat the file; if mtime unchanged, return cached copy.
    Slow path: re-read under lock with double-check.

    Note: `default_model` is intentionally NOT cached — it is derived from
    config.yaml via get_effective_default_model() and injected fresh on every
    call. See the comment in _read_settings_from_disk() for rationale.
    """
    global _settings_cache, _settings_mtime
    # Fast path — no lock
    try:
        mt = SETTINGS_FILE.stat().st_mtime if SETTINGS_FILE.exists() else 0.0
    except OSError:
        mt = 0.0
    if _settings_cache is not None and mt == _settings_mtime:
        cached = dict(_settings_cache)
        cached["default_model"] = get_effective_default_model()
        return cached

    # Slow path — acquire lock, double-check
    with _settings_cache_lock:
        try:
            mt = SETTINGS_FILE.stat().st_mtime if SETTINGS_FILE.exists() else 0.0
        except OSError:
            mt = 0.0
        if _settings_cache is not None and mt == _settings_mtime:
            cached = dict(_settings_cache)
            cached["default_model"] = get_effective_default_model()
            return cached

        settings, mt = _read_settings_from_disk()
        _settings_cache = settings
        _settings_mtime = mt
        result = dict(settings)
        result["default_model"] = get_effective_default_model()
        return result


# 默认 idle 时长：60分钟
DEFAULT_IDLE_TIMEOUT = 3600  # 秒


def get_idle_timeout():
    """获取 idle 时长配置。"""
    settings = load_settings()
    return settings.get('idle_timeout', DEFAULT_IDLE_TIMEOUT)


_SETTINGS_ALLOWED_KEYS = set(_SETTINGS_DEFAULTS.keys()) - {
    "password_hash",
    "default_model",
}
_SETTINGS_ENUM_VALUES = {
    "send_key": {"enter", "ctrl+enter"},
    "sidebar_density": {"compact", "detailed"},
    "auto_title_refresh_every": {"0", "5", "10", "20"},
    "busy_input_mode": {"queue", "interrupt", "steer"},
    "font_size": {"small", "medium", "large"},
    "auto_compress_threshold": {"0", "30", "50", "100", "200", "500"},
    "auto_compress_token_threshold": {"0", "0.05", "0.50", "0.60", "0.70", "0.85"},
    "auto_compress_poor_ratio": {"0.25", "0.5", "0.6", "0.7", "0.8"},
    "auto_compress_disable_after": {"1", "2", "3", "5", "10"},
    "max_compaction_snapshots": {"3", "5", "10", "20"},
    "default_mode": {"manual", "smart", "yolo"},
}
_SETTINGS_BOOL_KEYS = {
    "show_cli_sessions",
    "show_messaging_sessions",
    "show_cron_sessions",
    "check_for_updates",
    "sound_enabled",
    "notifications_enabled",
    "show_thinking",
}
# Language codes are validated as short alphanumeric BCP-47-like tags (e.g. 'en', 'zh', 'fr')
_SETTINGS_LANG_RE = __import__("re").compile(r"^[a-zA-Z]{2,10}(-[a-zA-Z0-9]{2,8})?$")


def save_settings(settings: dict) -> dict:
    """Save settings to disk. Returns the merged settings. Ignores unknown keys."""
    current = load_settings()
    pending_theme = current.get("theme")
    pending_skin = current.get("skin")
    theme_was_explicit = False
    skin_was_explicit = False
    # Handle _set_password: hash and store as password_hash
    raw_pw = settings.pop("_set_password", None)
    if raw_pw and isinstance(raw_pw, str) and raw_pw.strip():
        # Use PBKDF2 from auth module (600k iterations) -- never raw SHA-256
        from app.api.auth import _hash_password

        current["password_hash"] = _hash_password(raw_pw.strip())
    # Handle _clear_password: explicitly disable auth
    if settings.pop("_clear_password", False):
        current["password_hash"] = None
    for k, v in settings.items():
        if k in _SETTINGS_ALLOWED_KEYS:
            if k == "theme":
                if isinstance(v, str) and v.strip():
                    pending_theme = v
                    theme_was_explicit = True
                continue
            if k == "skin":
                if isinstance(v, str) and v.strip():
                    pending_skin = v
                    skin_was_explicit = True
                continue
            # Validate enum-constrained keys
            if k in _SETTINGS_ENUM_VALUES and v not in _SETTINGS_ENUM_VALUES[k]:
                continue
            # Validate language codes (BCP-47-like: 'en', 'zh', 'fr', 'zh-CN')
            if k == "language" and (
                not isinstance(v, str) or not _SETTINGS_LANG_RE.match(v)
            ):
                continue
            # Validate session_ttl: positive integer, 300 (5min) to 2592000 (30days)
            if k == "session_ttl":
                try:
                    ttl_int = int(v)
                    if ttl_int < 300 or ttl_int > 2592000:
                        continue
                    v = str(ttl_int)
                except (ValueError, TypeError):
                    continue
            # Coerce bool keys
            if k in _SETTINGS_BOOL_KEYS:
                v = bool(v)
            current[k] = v
    theme_value = pending_theme
    skin_value = pending_skin
    if theme_was_explicit and not skin_was_explicit:
        raw_theme = pending_theme.strip().lower() if isinstance(pending_theme, str) else ""
        if raw_theme not in _SETTINGS_THEME_VALUES:
            skin_value = None
    current["theme"], current["skin"] = _normalize_appearance(theme_value, skin_value)

    # default_workspace is no longer a persisted setting (issue #7): the
    # default workspace derives solely from HERMES_WEBUI_DEFAULT_WORKSPACE.
    # Any stale value sent by older clients is ignored (not an allowed key)
    # and dropped from disk on the next write via load_settings().
    current.pop("default_workspace", None)
    persisted = {k: v for k, v in current.items() if k != "default_model"}
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(
        json.dumps(persisted, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    current["default_model"] = get_effective_default_model()
    # Sync in-memory cache so next load_settings() returns fresh data immediately
    global _settings_cache, _settings_mtime
    _settings_cache = current
    try:
        _settings_mtime = SETTINGS_FILE.stat().st_mtime
    except OSError:
        _settings_mtime = 0.0
    return current


# NOTE (issue #7): DEFAULT_WORKSPACE is no longer overridden from settings.json
# at startup. It is resolved once from HERMES_WEBUI_DEFAULT_WORKSPACE at import
# time and is now a true constant.

# ── SESSIONS in-memory cache (LRU OrderedDict) ───────────────────────────────
SESSIONS: collections.OrderedDict = collections.OrderedDict()

# ── Profile state initialisation ────────────────────────────────────────────
# Must run after all imports are resolved to correctly patch module-level caches
try:
    from app.api.profiles import init_profile_state

    init_profile_state()
except ImportError:
    pass  # hermes_cli not available -- default profile only
