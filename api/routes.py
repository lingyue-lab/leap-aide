"""
Hermes Web UI -- Route handlers for GET and POST endpoints.
Extracted from server.py (Sprint 11) so server.py is a thin shell.
"""

import hashlib
import html as _html
import json
import logging
import os
import queue
import re as _re
import sys
import threading
import time
from datetime import datetime
import uuid
from pathlib import Path
from urllib.parse import parse_qs

logger = logging.getLogger(__name__)

_PROVIDER_ALIASES = {
    "claude": "anthropic",
    "gpt": "openai",
    "gemini": "google",
    "openai-codex": "openai",
}

# OpenAI-compatible /v1/models endpoints for live model discovery.
# Used as fallback when hermes_cli.provider_model_ids() is unavailable or
# returns [] for a provider (#871).  Kept at module level so the dict is
# built once, not reconstructed per request.
_OPENAI_COMPAT_ENDPOINTS = {
    "zai": "https://api.z.ai/v1",
    "minimax": "https://api.minimax.chat/v1",
    "mistralai": "https://api.mistral.ai/v1",
    "xai": "https://api.x.ai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
}
# NOTE: "openai-codex" is excluded because it maps to the same endpoint as
# the base "openai" provider (api.openai.com/v1).  When both are configured
# the openai provider is already wired through provider_model_ids(); codex-
# specific model filtering happens downstream in hermes_cli.
#
# TODO: Add TTL-based caching (e.g. 60s) so repeated model-list requests
# don't hit provider APIs.  The frontend already caches via _liveModelCache
# but the backend re-fetches on every /api/models/live call.

from api.config import (
    STATE_DIR,
    SESSION_DIR,
    DEFAULT_WORKSPACE,
    DEFAULT_MODEL,
    SESSIONS,
    SESSIONS_MAX,
    LOCK,
    STREAMS,
    STREAMS_LOCK,
    CANCEL_FLAGS,
    SERVER_START_TIME,
    _resolve_cli_toolsets,
    _INDEX_HTML_PATH,
    get_available_models,
    load_settings,
    IMAGE_EXTS,
    MD_EXTS,
    MIME_MAP,
    MAX_FILE_BYTES,
    MAX_UPLOAD_BYTES,
    CHAT_LOCK,
    _get_session_agent_lock,
    SESSION_AGENT_LOCKS,
    SESSION_AGENT_LOCKS_LOCK,
    load_settings,
    save_settings,
    set_hermes_default_model,
    get_reasoning_status,
    set_reasoning_display,
    set_reasoning_effort,
    ReplayableStream,
    _set_thread_env,
    _clear_thread_env,
)
from api.helpers import (
    require,
    bad,
    safe_resolve,
    j,
    t,
    read_body,
    _security_headers,
    _sanitize_error,
    redact_session_data,
    _redact_text,
)
from api.stream_manager import stream_manager

# ── CSRF: validate Origin/Referer on POST ────────────────────────────────────


def _normalize_host_port(value: str) -> tuple[str, str | None]:
    """Split a host or host:port string into (hostname, port|None).
    Handles IPv6 bracket notation, e.g. [::1]:8080."""
    value = value.strip().lower()
    if not value:
        return '', None
    if value.startswith('['):
        end = value.find(']')
        if end != -1:
            host = value[1:end]
            rest = value[end + 1 :]
            if rest.startswith(':') and rest[1:].isdigit():
                return host, rest[1:]
            return host, None
    if value.count(':') == 1:
        host, port = value.rsplit(':', 1)
        if port.isdigit():
            return host, port
    return value, None


def _ports_match(origin_scheme: str, origin_port: str | None, allowed_port: str | None) -> bool:
    """Return True when two ports should be considered equivalent, scheme-aware.

    Treats an absent port as the scheme default: port 80 for http, port 443 for https.
    Port 80 is NOT treated as equivalent to 443 (different protocols = different origins).
    """
    if origin_port == allowed_port:
        return True
    # Determine the default port for the origin's scheme
    default = '443' if origin_scheme == 'https' else '80'
    if not origin_port and allowed_port == default:
        return True
    if not allowed_port and origin_port == default:
        return True
    return False


def _allowed_public_origins() -> set[str]:
    """Parse HERMES_WEBUI_ALLOWED_ORIGINS env var (comma-separated) into a set.

    Each entry must include the scheme, e.g. https://myapp.example.com:8000.
    Entries without a scheme are silently skipped and a warning is printed.
    """
    raw = os.getenv('HERMES_WEBUI_ALLOWED_ORIGINS', '')
    result = set()
    for value in raw.split(','):
        value = value.strip().rstrip('/').lower()
        if not value:
            continue
        if not (value.startswith('http://') or value.startswith('https://')):
            import sys
            print(
                f"[webui] WARNING: HERMES_WEBUI_ALLOWED_ORIGINS entry {value!r} is missing "
                f"the scheme (expected https://hostname or http://hostname). Entry ignored.",
                flush=True, file=sys.stderr,
            )
            continue
        result.add(value)
    return result


def _check_csrf(handler) -> bool:
    """Reject cross-origin POST requests. Returns True if OK."""
    origin = handler.headers.get("Origin", "")
    referer = handler.headers.get("Referer", "")
    host = handler.headers.get("Host", "")
    if not origin and not referer:
        return True  # non-browser clients (curl, agent) have no Origin
    target = origin or referer
    # Extract host:port from origin/referer
    m = _re.match(r"^https?://([^/]+)", target)
    if not m:
        return False
    origin_host = m.group(1)
    origin_scheme = m.group(0).split('://')[0].lower()  # 'http' or 'https'
    origin_name, origin_port = _normalize_host_port(origin_host)
    # Check against explicitly allowed public origins (env var)
    origin_value = m.group(0).rstrip('/').lower()
    if origin_value in _allowed_public_origins():
        return True
    # Allow same-origin: check Host, X-Forwarded-Host (reverse proxy), and
    # X-Real-Host against the origin. Reverse proxies (Caddy, nginx) set
    # X-Forwarded-Host to the client's original Host header.
    allowed_hosts = [
        h.strip()
        for h in [
            host,
            handler.headers.get("X-Forwarded-Host", ""),
            handler.headers.get("X-Real-Host", ""),
        ]
        if h.strip()
    ]
    for allowed in allowed_hosts:
        allowed_name, allowed_port = _normalize_host_port(allowed)
        if origin_name == allowed_name and _ports_match(origin_scheme, origin_port, allowed_port):
            return True
    return False


def _normalize_provider_id(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if raw in _PROVIDER_ALIASES:
        return _PROVIDER_ALIASES[raw]
    for prefix, normalized in (
        ("openai-codex", "openai"),
        ("openai", "openai"),
        ("anthropic", "anthropic"),
        ("claude", "anthropic"),
        ("google", "google"),
        ("gemini", "google"),
        ("openrouter", "openrouter"),
        ("custom", "custom"),
    ):
        if raw.startswith(prefix):
            return normalized
    # Unknown prefix — return empty so callers treat it as "no match" and pass
    # the model through unchanged rather than incorrectly stripping it.
    return "" 


def _catalog_provider_id_sets(catalog: dict) -> tuple[set[str], set[str]]:
    raw_provider_ids: set[str] = set()
    normalized_provider_ids: set[str] = set()
    for group in catalog.get("groups") or []:
        raw = str(group.get("provider_id") or "").strip().lower()
        if not raw:
            continue
        raw_provider_ids.add(raw)
        normalized = _normalize_provider_id(raw)
        if normalized:
            normalized_provider_ids.add(normalized)
    return raw_provider_ids, normalized_provider_ids


def _catalog_has_provider(
    provider_raw: str,
    provider_normalized: str,
    raw_provider_ids: set[str],
    normalized_provider_ids: set[str],
) -> bool:
    return (
        provider_raw in raw_provider_ids
        or (provider_normalized and provider_normalized in raw_provider_ids)
        or (provider_normalized and provider_normalized in normalized_provider_ids)
    )


def _model_matches_active_provider_family(
    model: str,
    active_provider: str,
) -> bool:
    model_lower = model.lower()
    for bare_prefix in ("gpt", "claude", "gemini"):
        if model_lower.startswith(bare_prefix):
            return _normalize_provider_id(bare_prefix) == active_provider
    return False


def _resolve_compatible_session_model(model_id: str | None) -> tuple[str, bool]:
    """Return (effective_model, was_normalized) for persisted session models.

    Sessions can outlive provider changes. When an older session still points at
    a different provider namespace (for example `gemini/...` after switching the
    agent to OpenAI Codex), reusing that stale model causes chat startup to hit
    the wrong backend and fail. Normalize only obvious cross-provider mismatches;
    preserve bare model IDs and OpenRouter/custom setups.
    """
    catalog = get_available_models()
    default_model = str(catalog.get("default_model") or DEFAULT_MODEL or "").strip()
    model = str(model_id or "").strip()
    if not model:
        return default_model, bool(default_model)

    active_provider = _normalize_provider_id(catalog.get("active_provider"))
    # Also keep the raw active_provider slug for cross-provider detection with
    # non-listed providers (ollama-cloud, deepseek, xai, etc.) that _normalize_provider_id
    # returns "" for. If the raw provider is set but normalization returned "", we still
    # want to detect that a session model from a known provider (e.g. openai/gpt-5.4-mini)
    # is stale relative to this unknown active provider. (#1023)
    raw_active_provider = str(catalog.get("active_provider") or "").strip().lower()
    if not active_provider and not raw_active_provider:
        return model, False

    if model.startswith("@") and ":" in model:
        provider_hint, bare_model = model[1:].split(":", 1)
        provider_raw = provider_hint.strip().lower()
        provider_normalized = _normalize_provider_id(provider_raw)
        bare_model = bare_model.strip()
        if not provider_raw or not bare_model:
            return model, False

        raw_provider_ids, normalized_provider_ids = _catalog_provider_id_sets(catalog)
        hint_matches_active = (
            provider_raw == raw_active_provider
            or provider_raw == active_provider
            or (provider_normalized and provider_normalized == active_provider)
        )
        if hint_matches_active:
            return bare_model, True

        if _catalog_has_provider(
            provider_raw,
            provider_normalized,
            raw_provider_ids,
            normalized_provider_ids,
        ):
            return model, False

        if _model_matches_active_provider_family(bare_model, active_provider):
            return bare_model, True
        if default_model:
            return default_model, True
        # If the @provider hint is not a known/normalized provider name it is
        # likely a user-configured custom gateway (e.g. @kat_v1_anthropic_gateway:…).
        # Preserve the original model ID so the displayed value matches the
        # persisted session data.  If the provider is genuinely stale the error
        # will surface at inference time.
        if not provider_normalized:
            return model, False
        return model, False

    slash = model.find("/")
    if slash < 0:
        model_lower = model.lower()
        for bare_prefix in ("gpt", "claude", "gemini"):
            if model_lower.startswith(bare_prefix):
                model_provider = _normalize_provider_id(bare_prefix)
                if model_provider and model_provider != active_provider and default_model:
                    return default_model, True
                return model, False
        return model, False

    model_provider = _normalize_provider_id(model[:slash])

    # For custom/openrouter active providers: only skip normalization when the
    # model's namespace prefix is actually routable by a group in the catalog.
    # A user who only has custom_providers configured (active_provider="custom")
    # with a stale session model like "openai/gpt-5.4-mini" would otherwise
    # never get cleaned up, causing "(unavailable)" to appear in the picker.
    if active_provider in {"custom", "openrouter"}:
        # These namespaces are always routable as-is — preserve them.
        if model_provider in {"", "custom", "openrouter"}:
            return model, False
        # Check if any catalog group can actually route this model's prefix.
        groups = catalog.get("groups") or []
        routable_provider_ids = {
            _normalize_provider_id(g.get("provider_id") or "") for g in groups
        }
        # openrouter group can route any provider/model namespace
        has_openrouter_group = any(
            (g.get("provider_id") or "") == "openrouter" for g in groups
        )
        if model_provider in routable_provider_ids or has_openrouter_group:
            return model, False
        # Model prefix is not routable — stale cross-provider reference, clear it.
        if default_model:
            return default_model, True
        return model, False

    # Skip normalization for models on custom/openrouter namespaces — these are
    # user-controlled and should never be silently replaced.
    # Also normalize when the model is from a known provider but the active provider
    # is an unlisted one (e.g. ollama-cloud) — active_provider is "" in that case
    # but raw_active_provider is set. If model_provider doesn't start with the raw
    # active provider name, the session model is stale. (#1023)
    _active_for_compare = active_provider or raw_active_provider
    if model_provider and model_provider not in {"", "custom", "openrouter"} and model_provider != _active_for_compare and default_model:
        return default_model, True
    return model, False


def _normalize_session_model_in_place(session) -> str:
    original_model = getattr(session, "model", None) or ""
    effective_model, changed = _resolve_compatible_session_model(original_model or None)
    # Only persist the correction if the session had an explicit model that needed changing.
    # Sessions with no model stored (empty/None) get the effective default returned without
    # a disk write — no need to rebuild the index for a fill-in-blank operation.
    if changed and effective_model and original_model and original_model != effective_model:
        session.model = effective_model
        session.sync_webui_metadata(touch_updated_at=False)
    return effective_model or original_model


def _resolve_effective_session_model_for_display(session) -> str:
    """Resolve the model a session should display without mutating persisted state.

    `GET /api/session` should stay side-effect free. If a stale persisted model
    needs normalization for the current provider configuration, return the
    effective model for the response payload only and leave disk state alone.
    """
    original_model = getattr(session, "model", None) or ""
    effective_model, _changed = _resolve_compatible_session_model(original_model or None)
    result = effective_model or original_model
    # Warn on bare model names — they cannot uniquely identify a model across
    # providers (e.g. "deepseek-v4-pro" exists on both DeepSeek and OpenRouter).
    # The persisted model should always carry an @provider: prefix.
    if result and not result.startswith("@") and "/" not in result:
        logger.warning(
            "Session %s has bare model name %r — should be @provider:model",
            getattr(session, "session_id", "?"),
            result,
        )
        # Normalize for display so the frontend model selector can match,
        # but don't persist (this is a read-only path).
        result = Session._normalize_model(result)
    return result


from api.models import (
    Session,
    get_session,
    new_session,
    all_sessions,
    _write_session_index,
    SESSION_INDEX_FILE,
    load_projects,
    save_projects,
    import_cli_session,
    get_cli_sessions,
    get_cli_session_messages,
    MESSAGING_SOURCE_TAGS,
)


def _sessions_file():
    """Return the Path to the sessions index file."""
    return SESSION_INDEX_FILE
from api.workspace import (
    load_workspaces,
    save_workspaces,
    get_last_workspace,
    set_last_workspace,
    list_dir,
    list_workspace_suggestions,
    read_file_content,
    safe_resolve_ws,
    resolve_trusted_workspace,
    validate_workspace_to_add,
    profile_default_workspace,
    _is_blocked_system_path,
    _workspace_blocked_roots,
)
from api.upload import handle_upload, handle_transcribe
from api.streaming import _sse, _run_agent_streaming, cancel_stream, _CANCEL_EVENTS
from api.providers import get_providers, set_provider_key, remove_provider_key
from api.onboarding import (
    apply_onboarding_setup,
    get_onboarding_status,
    complete_onboarding,
)

# Approval system (optional -- graceful fallback if agent not available)
# Split into two try blocks so a missing optional name (e.g. get_session_approved,
# which the current SDK does not export) does not silently turn the critical
# yolo/session helpers into no-op lambdas. When block B fails only the names
# it lists fall back; block A still wires up the real SDK functions.
try:
    from tools.approval import (
        submit_pending as _submit_pending_raw,
        approve_session,
        approve_permanent,
        save_permanent_allowlist,
        is_approved,
        _pending,
        _lock,
        _permanent_approved,
        resolve_gateway_approval,
        enable_session_yolo,
        disable_session_yolo,
        is_session_yolo_enabled,
    )
except ImportError:
    _submit_pending_raw = lambda *a, **k: None
    approve_session = lambda *a, **k: None
    approve_permanent = lambda *a, **k: None
    save_permanent_allowlist = lambda *a, **k: None
    is_approved = lambda *a, **k: True
    resolve_gateway_approval = lambda *a, **k: 0
    enable_session_yolo = lambda *a, **k: None
    disable_session_yolo = lambda *a, **k: None
    is_session_yolo_enabled = lambda *a, **k: False
    _pending = {}

# Optional approval helpers — newer SDK exposes get_session_approved /
# revoke_session_approval / restore_session_approvals. If they are absent
# (older hermes-agent) the routes that reference them fall back to no-ops
# rather than dragging the yolo helpers into the fallback path.
try:
    from tools.approval import (
        get_session_approved,
        revoke_session_approval,
        restore_session_approvals,
    )
except ImportError:
    get_session_approved = lambda *a, **k: set()
    revoke_session_approval = lambda *a, **k: False
    restore_session_approvals = lambda *a, **k: None
    _lock = threading.Lock()
    _permanent_approved = set()


def _is_smart_supported() -> bool:
    """Detect whether the agent kernel supports smart approval mode.

    Probes ``tools.approval`` for a ``supports_smart_mode`` marker or
    ``evaluate_risk`` function.  Returns False when the agent does not
    provide the capability, so the frontend can show smart as available
    but downgrade to manual at runtime if the agent isn't ready.
    """
    try:
        from tools.approval import evaluate_risk
        return True
    except ImportError:
        return False


def submit_pending(session_key: str, approval: dict) -> None:
    """Append a pending approval to the per-session queue.

    Wraps the agent's submit_pending to:
    - Add a stable approval_id (uuid4 hex) so the respond endpoint can target
      a specific entry even when multiple approvals are queued simultaneously.
    - Change the storage from a single overwriting dict value to a list, so
      parallel tool calls each get their own approval slot (fixes #527).
    """
    entry = dict(approval)
    entry.setdefault("approval_id", uuid.uuid4().hex)
    with _lock:
        queue = _pending.setdefault(session_key, [])
        # Replace a legacy non-list value if the agent version uses the old pattern.
        if not isinstance(queue, list):
            _pending[session_key] = [queue]
            queue = _pending[session_key]
        queue.append(entry)
    # NOTE: We do NOT call _submit_pending_raw here — that function overwrites
    # _pending[session_key] with a single dict, which would undo the list we just
    # built. The gateway blocking path uses _gateway_queues (a separate mechanism
    # managed by check_all_command_guards / register_gateway_notify), which is
    # unaffected by _pending. The _pending dict is only used for UI polling.

# Clarify prompts (optional -- graceful fallback if agent not available)
try:
    from api.clarify import (
        submit_pending as submit_clarify_pending,
        get_pending as get_clarify_pending,
        resolve_clarify,
    )
except ImportError:
    submit_clarify_pending = lambda *a, **k: None
    get_clarify_pending = lambda *a, **k: None
    resolve_clarify = lambda *a, **k: 0


# ── Login locale injection ──────────────────────────────────────────────────
# When serving the SPA shell we inject `language`, `lang_attr`, and a `login`
# translation dict into window.__HERMES_INJECTED__ so the React AuthContext can
# render the password form in the user's language before i18next mounts.
# `lang_attr` is the BCP-47 HTML attribute value (e.g. `zh-TW` for `zh-Hant`,
# `ru-RU` for `ru`) which differs from the internal language identifier.

_LOGIN_LANG_ATTR = {
    "en":      "en",
    "zh-CN":   "zh-CN",
    "zh-Hant": "zh-TW",
    "ru":      "ru-RU",
}

_LOGIN_STRINGS = {
    "en": {
        "password_label":       "Password",
        "password_placeholder": "Enter password to continue",
        "login_button":         "Login",
        "logging_in":           "Logging in...",
        "invalid_password":     "Invalid password",
    },
    "zh-CN": {
        "password_label":       "密码",
        "password_placeholder": "输入密码继续使用",
        "login_button":         "登录",
        "logging_in":           "登录中...",
        "invalid_password":     "密码错误",
    },
    "zh-Hant": {
        "password_label":       "密碼",
        "password_placeholder": "輸入密碼繼續使用",
        "login_button":         "登入",
        "logging_in":           "登入中...",
        "invalid_password":     "密碼錯誤",
    },
    "ru": {
        "password_label":       "Пароль",
        "password_placeholder": "Введите пароль, чтобы продолжить",
        "login_button":         "Войти",
        "logging_in":           "Вход...",
        "invalid_password":     "Неверный пароль",
    },
}


def _build_injected_script(settings: dict) -> str:
    """Build the <script>...</script> string that populates __HERMES_INJECTED__.

    Centralizes all values injected before React mounts so the `/login`
    handler and the SPA fallback stay in lockstep. The login translation
    strings are serialized with ensure_ascii=False so tests grepping for the
    raw CJK / Cyrillic substrings find them in the HTML response.
    """
    font_size = settings.get("font_size", "medium")
    theme = settings.get("theme", "dark")
    language = settings.get("language", "en") or "en"
    lang_attr = _LOGIN_LANG_ATTR.get(language, "en")
    login_strings = _LOGIN_STRINGS.get(language, _LOGIN_STRINGS["en"])

    payload = {
        "font_size": font_size,
        "theme": theme,
        "language": language,
        "lang_attr": lang_attr,
        "login": login_strings,
    }
    return (
        "<script>window.__HERMES_INJECTED__="
        + json.dumps(payload, ensure_ascii=False)
        + ";</script>\n</head>"
    )


def _serve_index_html(handler, parsed) -> bool:
    """Serve static/index.html with language/theme/font_size injected.

    Rewrites the hardcoded `<html lang="en">` in static/index.html to match
    the user's language setting so screen readers and CSS `:lang(...)` rules
    see the correct attribute on the very first byte.
    """
    html = _INDEX_HTML_PATH.read_text(encoding="utf-8")
    try:
        settings = load_settings()
    except Exception:
        settings = {}
    language = settings.get("language", "en") or "en"
    lang_attr = _LOGIN_LANG_ATTR.get(language, "en")
    html = html.replace('<html lang="en">', f'<html lang="{lang_attr}">', 1)
    injected = _build_injected_script(settings)
    html = html.replace("</head>", injected, 1)
    return t(handler, html, content_type="text/html; charset=utf-8")


# ── GET routes ────────────────────────────────────────────────────────────────


def handle_get(handler, parsed) -> bool:
    """Handle all GET routes. Returns True if handled, False for 404."""

    if parsed.path in ("/", "/index.html", "/login"):
        return _serve_index_html(handler, parsed)

    if parsed.path == "/api/auth/status":
        from api.auth import is_auth_enabled, parse_cookie, verify_session

        logged_in = False
        if is_auth_enabled():
            cv = parse_cookie(handler)
            logged_in = bool(cv and verify_session(cv))
        return j(handler, {"auth_enabled": is_auth_enabled(), "logged_in": logged_in})

    if parsed.path in ("/manifest.json", "/manifest.webmanifest"):
        static_root = Path(__file__).parent.parent / "static"
        manifest_path = (static_root / "manifest.json").resolve()
        if manifest_path.exists():
            data = manifest_path.read_bytes()
            handler.send_response(200)
            handler.send_header("Content-Type", "application/manifest+json; charset=utf-8")
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("Connection", "close")
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
            return True
        return j(handler, {"error": "not found"}, status=404)

    if parsed.path == "/sw.js":
        static_root = Path(__file__).parent.parent / "static"
        sw_path = (static_root / "sw.js").resolve()
        if sw_path.exists():
            # Inject the current git-derived version as the cache name so the
            # service worker cache busts automatically on every new deploy.
            from api.updates import WEBUI_VERSION
            text = sw_path.read_text(encoding="utf-8").replace(
                "__CACHE_VERSION__", WEBUI_VERSION
            )
            data = text.encode("utf-8")
            handler.send_response(200)
            handler.send_header("Content-Type", "application/javascript; charset=utf-8")
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("Service-Worker-Allowed", "/")
            handler.send_header("Connection", "close")
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
            return True
        return j(handler, {"error": "not found"}, status=404)

    if parsed.path == "/favicon.ico":
        static_root = Path(__file__).parent.parent / "static"
        ico_path = (static_root / "favicon.ico").resolve()
        if ico_path.exists() and ico_path.is_file():
            data = ico_path.read_bytes()
            handler.send_response(200)
            handler.send_header("Content-Type", "image/x-icon")
            handler.send_header("Content-Length", str(len(data)))
            handler.send_header("Cache-Control", "public, max-age=86400")
            handler.send_header("Connection", "close")
            handler.end_headers()
            handler.wfile.write(data)
        else:
            handler.send_response(204)
            handler.send_header("Connection", "close")
            handler.end_headers()
        return True

    if parsed.path == "/health":
        return j(
            handler,
            {
                "status": "ok",
                "sessions": len(SESSIONS),
                "active_streams": stream_manager.active_count(),
                "uptime_seconds": round(time.time() - SERVER_START_TIME, 1),
            },
        )

    if parsed.path == "/api/models":
        return j(handler, get_available_models())

    # ── Config file raw YAML (GET) ──
    if parsed.path == "/api/config/raw":
        from api.config import _get_config_path
        config_path = _get_config_path()
        try:
            raw_yaml = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
        except Exception as exc:
            return j(handler, {"error": str(exc)}, status=500)
        return t(handler, raw_yaml, content_type="text/plain; charset=utf-8")

    if parsed.path == "/api/models/live":
        return _handle_live_models(handler, parsed)

    # ── Providers (GET) ──
    if parsed.path == "/api/providers":
        return j(handler, get_providers())

    if parsed.path == "/api/settings":
        settings = load_settings()
        # Never expose the stored password hash to clients
        settings.pop("password_hash", None)
        # Inject the running version so the UI badge stays in sync with git tags
        # without any manual release step.
        try:
            from api.updates import WEBUI_VERSION
            settings["webui_version"] = WEBUI_VERSION
        except Exception:
            pass
        return j(handler, settings)

    if parsed.path == "/api/reasoning":
        # Current reasoning config (shared source of truth with the CLI —
        # reads display.show_reasoning and agent.reasoning_effort from
        # the active profile's config.yaml).
        return j(handler, get_reasoning_status())

    if parsed.path == "/api/onboarding/status":
        return j(handler, get_onboarding_status())

    if parsed.path.startswith("/static/"):
        return _serve_static(handler, parsed)

    if parsed.path.startswith("/assets/"):
        return _serve_assets(handler, parsed)

    if parsed.path == "/api/session":
        import time as _time
        _t0 = _time.monotonic()
        _debug_slow = os.environ.get("HERMES_DEBUG_SLOW", "")
        query = parse_qs(parsed.query)
        sid = query.get("session_id", [""])[0]
        if not sid:
            return j(handler, {"error": "session_id is required"}, status=400)
        # Handle archived (read-only compaction) session IDs: "parent_id/compaction_N"
        if '/' in sid and '_archived' not in sid:
            _parts = sid.split('/', 1)
            _parent_id, _child_stem = _parts[0], _parts[1]
            _archive_path = SESSION_DIR / f'{_parent_id}_archived' / f'{_child_stem}.json'
            if _archive_path.exists():
                try:
                    _raw = json.loads(_archive_path.read_text(encoding='utf-8'))
                    _raw['session_id'] = sid
                    _raw['_readonly'] = True
                    _raw['_parent_session_id'] = _parent_id
                    return j(handler, {'ok': True, 'session': _raw})
                except Exception:
                    return j(handler, {"error": "Failed to load archived session"}, status=500)
            return j(handler, {"error": "Archived session not found"}, status=404)
        # ?messages=0 skips the message payload for fast session switching.
        # The frontend uses this when switching conversations in the sidebar
        # (only needs metadata). The full message array is loaded lazily
        # via ?messages=1 when the message panel opens.
        load_messages = query.get("messages", ["1"])[0] != "0"
        resolve_model_default = "1" if load_messages else "0"
        resolve_model = query.get("resolve_model", [resolve_model_default])[0] != "0"
        # ?msg_limit=N returns only the last N messages (tail window).
        # Used by the frontend for fast session switching — avoids serialising
        # and sending hundreds of messages when the user only sees the most
        # recent exchange.  Older messages are loaded on-demand via scrolling.
        _msg_limit = query.get("msg_limit", [None])[0]
        try:
            msg_limit = max(1, int(_msg_limit)) if _msg_limit else None
        except (ValueError, TypeError):
            msg_limit = None
        # ?msg_before=N — 0-based index into the full message array.
        # Returns messages before this index (for scroll-to-top lazy loading).
        # Combined with msg_limit for paging.
        _msg_before = query.get("msg_before", [None])[0]
        try:
            msg_before = int(_msg_before) if _msg_before else None
        except (ValueError, TypeError):
            msg_before = None
        try:
            _t1 = _time.monotonic()
            s = get_session(sid, metadata_only=(not load_messages))
            _t2 = _time.monotonic()
            # When viewing session history, detect and fix stale last_seen_msg_count
            # (left over from a clear-history operation that didn't reset it).
            if load_messages:
                from api.models import _fix_stale_last_seen_msg_count
                _fix_stale_last_seen_msg_count(s)
            effective_model = (
                _resolve_effective_session_model_for_display(s)
                if resolve_model
                else None
            )
            _t3 = _time.monotonic()
            _all_msgs = s.messages if load_messages else []
            if load_messages:
                if msg_before is not None:
                    # Scroll-to-top paging: msg_before is a 0-based index into
                    # the full message list. Return the msg_limit messages that
                    # appear *before* this index (i.e. older messages).
                    # Using index instead of timestamp avoids issues with
                    # duplicate/missing timestamps.
                    _before_idx = max(0, min(int(msg_before), len(_all_msgs)))
                    _slice = _all_msgs[:_before_idx]
                    _truncated_msgs = _slice[-msg_limit:] if msg_limit else _slice
                elif msg_limit and len(_all_msgs) > msg_limit:
                    _truncated_msgs = _all_msgs[-msg_limit:]
                else:
                    _truncated_msgs = _all_msgs
            else:
                _truncated_msgs = _all_msgs
            raw = s.compact() | {
                "messages": _truncated_msgs,
                "tool_calls": getattr(s, "tool_calls", []) if load_messages else [],
                "active_stream_id": stream_manager.get_stream_id(sid),
                "pending_user_message": getattr(s, "pending_user_message", None),
                "pending_original_message": getattr(s, "_pending_original_message", None),
                "pending_user_message_ts": getattr(s, "_pending_user_message_ts", None),
                "pending_attachments": getattr(s, "pending_attachments", []) if load_messages else [],
                "pending_started_at": getattr(s, "pending_started_at", None),
            }
            # Signal to the frontend that older messages were omitted.
            # For msg_before paging, compare against the filtered set,
            # not the full list — otherwise we signal truncation even when
            # all older messages were returned.
            if msg_before is not None:
                _truncated = load_messages and msg_limit is not None and len(_slice) > msg_limit
            else:
                _truncated = load_messages and msg_limit is not None and len(_all_msgs) > msg_limit
            raw["_messages_truncated"] = _truncated
            # Index of the first returned message in the full message array.
            # Frontend uses this as cursor for scroll-to-top paging.
            if msg_before is not None:
                raw["_messages_offset"] = max(0, _before_idx - len(_truncated_msgs))
            else:
                raw["_messages_offset"] = max(0, len(_all_msgs) - len(_truncated_msgs))
            _t4 = _time.monotonic()
            if effective_model:
                raw["model"] = effective_model
            redact = redact_session_data(raw)
            _t5 = _time.monotonic()
            resp = j(handler, {"session": redact})
            _t6 = _time.monotonic()
            if _debug_slow:
                logger.warning(
                    "[SLOW] session_id=%s get_session=%.1fms model_resolve=%.1fms "
                    "compact=%.1fms redact=%.1fms json_write=%.1fms total=%.1fms",
                    sid,
                    (_t2-_t1)*1000, (_t3-_t2)*1000, (_t4-_t3)*1000,
                    (_t5-_t4)*1000, (_t6-_t5)*1000, (_t6-_t0)*1000,
                )
            return resp
        except KeyError:
            # Not a WebUI session -- try CLI store
            msgs = get_cli_session_messages(sid)
            if msgs:
                cli_meta = None
                for cs in get_cli_sessions():
                    if cs["session_id"] == sid:
                        cli_meta = cs
                        break
                sess = {
                    "session_id": sid,
                    "title": (cli_meta or {}).get("title", "CLI Session"),
                    "workspace": (cli_meta or {}).get("workspace", ""),
                    "model": (cli_meta or {}).get("model", "unknown"),
                    "message_count": len(msgs),
                    "created_at": (cli_meta or {}).get("created_at", 0),
                    "updated_at": (cli_meta or {}).get("updated_at", 0),
                    "last_activity_at": (cli_meta or {}).get("last_activity_at")
                    or (cli_meta or {}).get("updated_at", 0),
                    "pinned": False,
                    "archived": False,
                    "project_id": None,
                    "profile": (cli_meta or {}).get("profile"),
                    "is_cli_session": True,
                    "source_tag": (cli_meta or {}).get("source_tag"),
                    "user_id": (cli_meta or {}).get("user_id"),
                    "_readonly": True,
                    "messages": msgs,
                    "tool_calls": [],
                }
                return j(handler, {"session": redact_session_data(sess)})
            return bad(handler, "Session not found", 404)

    if parsed.path == "/api/session/status":
        sid = parse_qs(parsed.query).get("session_id", [""])[0]
        if not sid:
            return bad(handler, "Missing session_id")
        try:
            from api.session_ops import session_status
            return j(handler, session_status(sid))
        except KeyError:
            return bad(handler, "Session not found", 404)

    if parsed.path == "/api/session/mode":
        sid = parse_qs(parsed.query).get("session_id", [""])[0]
        if not sid:
            return bad(handler, "Missing session_id")
        try:
            s = get_session(sid, metadata_only=True)
            return j(handler, {
                "mode": getattr(s, 'mode', 'manual'),
                "smart_supported": _is_smart_supported(),
            })
        except KeyError:
            return bad(handler, "Session not found", 404)

    if parsed.path == "/api/session/yolo":
        sid = parse_qs(parsed.query).get("session_id", [""])[0]
        if not sid:
            return bad(handler, "Missing session_id")
        return j(handler, {"yolo_enabled": is_session_yolo_enabled(sid)})

    if parsed.path == "/api/session/usage":
        sid = parse_qs(parsed.query).get("session_id", [""])[0]
        if not sid:
            return bad(handler, "Missing session_id")
        try:
            from api.session_ops import session_usage
            return j(handler, session_usage(sid))
        except KeyError:
            return bad(handler, "Session not found", 404)

    if parsed.path == "/api/background/status":
        sid = parse_qs(parsed.query).get("session_id", [""])[0]
        if not sid:
            return bad(handler, "Missing session_id")
        from api.background import get_results
        return j(handler, {"results": get_results(sid)})

    if parsed.path == "/api/goal/status":
        sid = parse_qs(parsed.query).get("session_id", [""])[0]
        if not sid:
            return bad(handler, "Missing session_id")
        try:
            from hermes_cli.goals import GoalManager
            mgr = GoalManager(sid)
            st = mgr.state
            if st is None or st.status in ("cleared", "done"):
                return j(handler, {"goal": None})
            return j(handler, {
                "goal": st.goal,
                "status": st.status,
                "turns_used": st.turns_used,
                "max_turns": st.max_turns,
                "subgoals": len(st.subgoals) if st.subgoals else 0,
                "subgoal_texts": list(st.subgoals) if st.subgoals else [],
                "last_verdict": st.last_verdict,
                "paused_reason": st.paused_reason,
            })
        except Exception as e:
            logger.debug("Goal status check failed: %s", e)
            return j(handler, {"goal": None})

    if parsed.path == "/api/sessions":
        webui_sessions = all_sessions()
        settings = load_settings()
        cli = get_cli_sessions(
            show_cli=settings.get("show_cli_sessions", True),
            show_messaging=settings.get("show_messaging_sessions", True),
            show_cron=settings.get("show_cron_sessions", False),
        )
        webui_ids = {s["session_id"] for s in webui_sessions}
        deduped_cli = [s for s in cli
                       if s["session_id"] not in webui_ids]
        merged = webui_sessions + deduped_cli
        merged.sort(
            key=lambda s: s.get("last_activity_at") or s.get("updated_at", 0) or 0,
            reverse=True,
        )
        safe_merged = []
        for s in merged:
            item = dict(s)
            if isinstance(item.get("title"), str):
                item["title"] = _redact_text(item["title"])
            # Normalize bare model names for frontend display
            if item.get("model") and not item["model"].startswith("@") and "/" not in item["model"]:
                item["model"] = Session._normalize_model(item["model"])
            safe_merged.append(item)

        # Batch query delegate children for all sessions
        try:
            from api.models import _get_session_db, _get_all_delegate_children_map
            _db = _get_session_db()
            if _db is not None:
                _delegate_map = _get_all_delegate_children_map(_db)
                _active_ids = set(stream_manager.get_active_session_ids())
                for item in safe_merged:
                    _sid = item.get("session_id", "")
                    _children = _delegate_map.get(_sid, [])
                    item["_delegate_children_count"] = len(_children)
                    item["_has_delegate_children"] = len(_children) > 0
                    # Count running children (active stream + no ended_at)
                    _running = sum(
                        1 for c in _children
                        if not c.get('ended_at') and c.get('id') in _active_ids
                    )
                    item["_delegate_running_count"] = _running
        except Exception:
            pass  # non-fatal — delegate children info is additive

        return j(handler, {
            "sessions": safe_merged,
            "cli_count": len(deduped_cli),
            "server_time": time.time(),
            "server_tz": time.strftime("%z"),
        })

    if parsed.path == "/api/projects":
        return j(handler, {"projects": load_projects()})

    if parsed.path == "/api/session/export":
        return _handle_session_export(handler, parsed)

    if parsed.path == "/api/workspaces":
        user_workspaces = load_workspaces()
        user_paths = {w['path'] for w in user_workspaces}

        # The default workspace (from config.yaml terminal.cwd) is external
        try:
            _default_ws = profile_default_workspace()
        except Exception:
            _default_ws = ''
        for w in user_workspaces:
            if w['path'] == _default_ws:
                w['external'] = True

        # Mark the built-in default workspace
        try:
            from api.config import BUILTIN_DEFAULT_WORKSPACE
            for w in user_workspaces:
                if Path(w['path']).resolve() == BUILTIN_DEFAULT_WORKSPACE.resolve():
                    w['builtin'] = True
        except Exception:
            pass

        # Derive external workspaces from gateway/CLI sessions that have real cwd
        derived: list[dict] = []
        try:
            for s in get_cli_sessions():
                ws_path = s.get('workspace', '')
                if not ws_path or ws_path in user_paths:
                    continue
                user_paths.add(ws_path)
                # Use the last path segment as display name; fall back to full path
                _p = Path(ws_path)
                name = _p.name if _p.name else str(_p)
                derived.append({
                    'path': ws_path,
                    'name': name,
                    'external': True,
                })
        except Exception:
            pass  # state.db unavailable — degrade gracefully

        return j(
            handler, {
                "workspaces": user_workspaces + derived,
                "last": get_last_workspace(),
            }
        )

    if parsed.path == "/api/workspaces/suggest":
        qs = parse_qs(parsed.query)
        prefix = qs.get("prefix", [""])[0]
        return j(
            handler,
            {
                "suggestions": list_workspace_suggestions(prefix),
                "prefix": prefix,
            },
        )

    # /api/sessions/<id>/children — return delegate children for a session
    if parsed.path.startswith("/api/sessions/") and parsed.path.endswith("/children"):
        parts = parsed.path.split("/")
        if len(parts) == 5 and parts[1] == "api" and parts[2] == "sessions":
            session_id = parts[3]
            try:
                from api.models import _get_session_db, _get_all_delegate_children_map
                _db = _get_session_db()
                if _db is None:
                    return j(handler, {"children": []})
                _children_map = _get_all_delegate_children_map(_db)
                _children = _children_map.get(session_id, [])
                return j(handler, {"children": _children})
            except Exception:
                return j(handler, {"children": []})

    # /api/sessions/<id>/children/status — return delegate children status
    if parsed.path.startswith("/api/sessions/") and parsed.path.endswith("/children/status"):
        parts = parsed.path.split("/")
        if len(parts) == 6 and parts[1] == "api" and parts[2] == "sessions" and parts[5] == "status":
            session_id = parts[3]
            try:
                from api.models import _get_session_db, _get_all_delegate_children_map
                _db = _get_session_db()
                if _db is None:
                    return j(handler, {"children": [], "running_count": 0})
                _children_map = _get_all_delegate_children_map(_db)
                _children = _children_map.get(session_id, [])
                # Cross-reference ended_at with actual stream activity:
                # A child is "running" only if its stream is still active in
                # StreamManager.  This avoids false positives when ended_at
                # was never set by the SDK (timeout/crash).
                _active_ids = set(stream_manager.get_active_session_ids())
                running_count = 0
                for c in _children:
                    _cid = c.get('id', '')
                    _ended = c.get('ended_at')
                    if not _ended and _cid in _active_ids:
                        running_count += 1
                    c['streaming'] = _cid in _active_ids
                return j(handler, {
                    "children": _children,
                    "running_count": running_count,
                    "total_count": len(_children),
                })
            except Exception:
                return j(handler, {"children": [], "running_count": 0, "total_count": 0})

    if parsed.path == "/api/sessions/search":
        return _handle_sessions_search(handler, parsed)

    if parsed.path == "/api/list":
        return _handle_list_dir(handler, parsed)

    if parsed.path == "/api/workspace/list":
        return _handle_workspace_list(handler, parsed)

    if parsed.path == "/api/workspace/info":
        return _handle_workspace_info(handler, parsed)

    if parsed.path == "/api/workspace/file":
        return _handle_workspace_file_read(handler, parsed)

    if parsed.path == "/api/personalities":
        # Read personalities from config.yaml agent.personalities section
        # (matches hermes-agent CLI behavior, not filesystem SOUL.md approach)
        from api.config import reload_config as _reload_cfg

        _reload_cfg()  # pick up config.yaml changes without server restart
        from api.config import get_config as _get_cfg

        _cfg = _get_cfg()
        agent_cfg = _cfg.get("agent", {})
        raw_personalities = agent_cfg.get("personalities", {})
        personalities = []
        if isinstance(raw_personalities, dict):
            for name, value in raw_personalities.items():
                desc = ""
                if isinstance(value, dict):
                    desc = value.get("description", "")
                elif isinstance(value, str):
                    desc = value[:80] + ("..." if len(value) > 80 else "")
                personalities.append({"name": name, "description": desc})
        return j(handler, {"personalities": personalities})

    if parsed.path == "/api/git-info":
        qs = parse_qs(parsed.query)
        sid = qs.get("session_id", [""])[0]
        if not sid:
            return bad(handler, "session_id required")
        try:
            s = get_session(sid)
        except KeyError:
            return bad(handler, "Session not found", 404)
        from api.workspace import git_info_for_workspace

        info = git_info_for_workspace(Path(s.workspace))
        return j(handler, {"git": info})

    if parsed.path == "/api/commands":
        from api.commands import list_commands
        return j(handler, {"commands": list_commands()})

    if parsed.path == "/api/slash-commands":
        from api.slash_commands import get_unified_commands
        return j(handler, get_unified_commands())

    if parsed.path == "/api/updates/check":
        settings = load_settings()
        if not settings.get("check_for_updates", True):
            return j(handler, {"disabled": True})
        qs = parse_qs(parsed.query)
        force = qs.get("force", ["0"])[0] == "1"
        # ?simulate=1 returns fake behind counts for UI testing (localhost only)
        if (
            qs.get("simulate", ["0"])[0] == "1"
            and handler.client_address[0] == "127.0.0.1"
        ):
            return j(
                handler,
                {
                    "webui": {
                        "name": "webui",
                        "behind": 3,
                        "current_sha": "abc1234",
                        "latest_sha": "def5678",
                        "branch": "master",
                    },
                    "agent": {
                        "name": "agent",
                        "behind": 1,
                        "current_sha": "aaa0001",
                        "latest_sha": "bbb0002",
                        "branch": "master",
                    },
                    "checked_at": 0,
                },
            )
        from api.updates import check_for_updates

        return j(handler, check_for_updates(force=force))

    if parsed.path == "/api/chat/stream/status":
        stream_id = parse_qs(parsed.query).get("stream_id", [""])[0]
        return j(handler, {"active": stream_manager.get_stream_by_id(stream_id) is not None, "stream_id": stream_id})

    if parsed.path == "/api/chat/cancel":
        stream_id = parse_qs(parsed.query).get("stream_id", [""])[0]
        if not stream_id:
            return bad(handler, "stream_id required")
        cancelled = cancel_stream(stream_id)
        if not cancelled:
            # Idempotent no-op: cancelling an unknown or already-ended stream is
            # not an error from the caller's perspective. Returning cancelled=false
            # lets the frontend treat this as a settled state without surfacing a 400.
            return j(handler, {"ok": True, "cancelled": False, "stream_id": stream_id, "reason": "no_such_stream"})
        return j(handler, {"ok": True, "cancelled": cancelled, "stream_id": stream_id})

    if parsed.path == "/api/chat/queue":
        return _handle_queue(handler, parsed)

    if parsed.path == "/api/chat/session/stream/replay":
        return _handle_session_stream_replay(handler, parsed)

    if parsed.path == '/api/chat/session/stream/notify':
        return _handle_session_notify(handler)

    if parsed.path == "/api/media":
        return _handle_media(handler, parsed)

    if parsed.path == "/api/file/raw":
        return _handle_file_raw(handler, parsed)

    if parsed.path == "/api/file":
        return _handle_file_read(handler, parsed)

    if parsed.path == "/api/approval/pending":
        return _handle_approval_pending(handler, parsed)

    if parsed.path == "/api/approval/inject_test":
        # Loopback-only: used by automated tests; blocked from any remote client
        if handler.client_address[0] != "127.0.0.1":
            return j(handler, {"error": "not found"}, status=404)
        return _handle_approval_inject(handler, parsed)

    if parsed.path == "/api/approval/session-approved":
        return _handle_approval_session_list(handler, parsed)

    if parsed.path == "/api/clarify/pending":
        return _handle_clarify_pending(handler, parsed)

    if parsed.path == "/api/clarify/inject_test":
        # Loopback-only: used by automated tests; blocked from any remote client
        if handler.client_address[0] != "127.0.0.1":
            return j(handler, {"error": "not found"}, status=404)
        return _handle_clarify_inject(handler, parsed)

    # ── Cron API (GET) ──
    if parsed.path == "/api/crons":
        from cron.jobs import list_jobs

        return j(handler, {"jobs": list_jobs(include_disabled=True)})

    if parsed.path == "/api/crons/output":
        return _handle_cron_output(handler, parsed)

    if parsed.path == "/api/crons/recent":
        return _handle_cron_recent(handler, parsed)

    if parsed.path == "/api/toolsets":
        from model_tools import get_available_toolsets
        return j(handler, {"toolsets": get_available_toolsets()})

    # ── Skills API (GET) ──
    if parsed.path == "/api/skills":
        from tools.skills_tool import skills_list as _skills_list, SKILLS_DIR

        raw = _skills_list()
        data = json.loads(raw) if isinstance(raw, str) else raw

        # Build a frontmatter-name -> dir_name mapping so frontend can call skill_view correctly
        dir_name_map: dict = {}
        import re as _re_skill
        if SKILLS_DIR.exists():
            for _skill_md in SKILLS_DIR.rglob("SKILL.md"):
                try:
                    _text = _skill_md.read_text(encoding="utf-8", errors="ignore")[:1000]
                    _m = _re_skill.search(r'^name:\s*["\']?(.+?)["\']?\s*$', _text, _re_skill.MULTILINE)
                    _fm_name = _m.group(1).strip() if _m else _skill_md.parent.name
                    dir_name_map[_fm_name] = _skill_md.parent.name
                except Exception:
                    pass

        skills = data.get("skills", [])
        for skill in skills:
            skill["dir_name"] = dir_name_map.get(skill["name"], skill["name"])

        return j(handler, {"skills": skills})

    if parsed.path == "/api/skills/content":
        from tools.skills_tool import skill_view as _skill_view, SKILLS_DIR

        qs = parse_qs(parsed.query)
        name = qs.get("name", [""])[0]
        if not name:
            return j(handler, {"error": "name required"}, status=400)
        file_path = qs.get("file", [""])[0]
        if file_path:
            # Serve a linked file from the skill directory
            import re as _re

            if _re.search(r"[*?\[\]]", name):
                return bad(handler, "Invalid skill name", 400)
            skill_dir = None
            for p in SKILLS_DIR.rglob(name):
                if p.is_dir():
                    skill_dir = p
                    break
            if not skill_dir:
                return bad(handler, "Skill not found", 404)
            target = (skill_dir / file_path).resolve()
            try:
                target.relative_to(skill_dir.resolve())
            except ValueError:
                return bad(handler, "Invalid file path", 400)
            if not target.exists() or not target.is_file():
                return bad(handler, "File not found", 404)
            return j(
                handler,
                {"content": target.read_text(encoding="utf-8"), "path": file_path},
            )
        raw = _skill_view(name)
        data = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(data.get("linked_files"), dict):
            data["linked_files"] = {}
        return j(handler, data)

    if parsed.path == "/api/skills/files":
        from tools.skills_tool import SKILLS_DIR
        from pathlib import Path as _Path
        import re as _re_sf

        qs = parse_qs(parsed.query)
        name = qs.get("name", [""])[0]
        if not name or _re_sf.search(r"[*?\[\]]", name):
            return bad(handler, "name required or invalid", 400)
        skill_dir = None
        for p in SKILLS_DIR.rglob(name):
            if p.is_dir():
                skill_dir = p
                break
        if not skill_dir:
            return bad(handler, "Skill not found", 404)

        def _build_tree(base: _Path, rel: str = "") -> list:
            result = []
            try:
                items = sorted(base.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
            except PermissionError:
                return result
            for item in items:
                item_rel = f"{rel}/{item.name}" if rel else item.name
                if item.is_dir():
                    result.append({
                        "name": item.name,
                        "path": item_rel,
                        "is_dir": True,
                        "children": _build_tree(item, item_rel),
                    })
                else:
                    try:
                        size = item.stat().st_size
                    except OSError:
                        size = 0
                    result.append({
                        "name": item.name,
                        "path": item_rel,
                        "is_dir": False,
                        "size": size,
                    })
            return result

        return j(handler, {"tree": _build_tree(skill_dir), "skill_dir": str(skill_dir)})

    # ── Memory API (GET) ──
    if parsed.path == "/api/memory":
        return _handle_memory_read(handler)

    # ── Profile API (GET) ──
    if parsed.path == "/api/profiles":
        from api.profiles import list_profiles_api, get_active_profile_name

        return j(
            handler,
            {"profiles": list_profiles_api(), "active": get_active_profile_name()},
        )

    if parsed.path == "/api/profile/active":
        from api.profiles import get_active_profile_name, get_active_hermes_home

        return j(
            handler,
            {"name": get_active_profile_name(), "path": str(get_active_hermes_home())},
        )

    # ── Session Observer routes ──────────────────────────────────────────
    import re as _re_obs
    _obs_info = _re_obs.match(r'^/observations/([a-zA-Z0-9_-]+)/info$', parsed.path)
    _obs_root = _re_obs.match(r'^/observations/([a-zA-Z0-9_-]+)$', parsed.path)

    if _obs_info:
        from api.observer import handle_observer_info
        return handle_observer_info(handler, _obs_info.group(1))

    if _obs_root:
        from api.observer import handle_observer_page
        return handle_observer_page(handler, _obs_root.group(1))

    # ── SPA fallback: serve index.html for client-side routes ──────────────
    # Non-API, non-static paths are React Router routes (e.g. /session/:id).
    # Serve index.html so the SPA can handle them client-side.
    if not parsed.path.startswith("/api/") and not parsed.path.startswith("/static/"):
        try:
            return _serve_index_html(handler, parsed)
        except Exception:
            pass

    return False  # 404


# ── POST routes ───────────────────────────────────────────────────────────────


def handle_post(handler, parsed) -> bool:
    """Handle all POST routes. Returns True if handled, False for 404."""
    # CSRF: reject cross-origin browser requests
    if not _check_csrf(handler):
        return j(handler, {"error": "Cross-origin request rejected"}, status=403)

    if parsed.path == "/api/upload":
        return handle_upload(handler)

    if parsed.path == "/api/transcribe":
        return handle_transcribe(handler)

    if parsed.path == "/api/skills/import":
        return _handle_skill_import(handler)

    body = read_body(handler)

    # ── Config file raw YAML (POST) ──
    if parsed.path == "/api/config/raw":
        from api.config import _get_config_path, reload_config, invalidate_models_cache
        yaml_text = body.get("content", "")
        try:
            import yaml as _yaml
            _yaml.safe_load(yaml_text)  # validation only — raises on bad YAML
        except Exception as exc:
            return j(handler, {"ok": False, "error": f"Invalid YAML: {exc}"}, status=400)
        config_path = _get_config_path()
        try:
            config_path.write_text(yaml_text, encoding="utf-8")
        except Exception as exc:
            return j(handler, {"ok": False, "error": str(exc)}, status=500)
        try:
            reload_config()
            invalidate_models_cache()
        except Exception:
            pass
        return j(handler, {"ok": True})

    if parsed.path == "/api/session/new":
        try:
            workspace = str(resolve_trusted_workspace(body.get("workspace"))) if body.get("workspace") else None
        except ValueError as e:
            return bad(handler, str(e))
        # Use the profile sent by the client tab (if any) so that two tabs on
        # different profiles never clobber each other via the process-level global.
        s = new_session(workspace=workspace, model=body.get("model"), profile=body.get("profile") or None)
        return j(handler, {"session": s.compact() | {"messages": s.messages}})

    if parsed.path == "/api/default-model":
        try:
            return j(handler, set_hermes_default_model(body.get("model")))
        except ValueError as e:
            return bad(handler, str(e))
        except RuntimeError as e:
            return bad(handler, str(e), 500)

    # ── Providers (POST) ──
    if parsed.path == "/api/providers":
        provider_id = (body.get("provider") or "").strip().lower()
        api_key = body.get("api_key")
        if not provider_id:
            return bad(handler, "provider is required")
        if api_key is not None:
            api_key = str(api_key).strip() or None
        result = set_provider_key(provider_id, api_key)
        if not result.get("ok"):
            return bad(handler, result.get("error", "Unknown error"))
        return j(handler, result)

    if parsed.path == "/api/providers/delete":
        provider_id = (body.get("provider") or "").strip().lower()
        if not provider_id:
            return bad(handler, "provider is required")
        result = remove_provider_key(provider_id)
        if not result.get("ok"):
            return bad(handler, result.get("error", "Unknown error"))
        return j(handler, result)

    if parsed.path == "/api/reasoning":
        # CLI-parity /reasoning handler — writes to the same config.yaml keys
        # the CLI uses (display.show_reasoning, agent.reasoning_effort) so a
        # preference set via WebUI is honoured in the terminal REPL and vice
        # versa.  Body is one of:
        #   {"display": "show"|"hide"|"on"|"off"}   → display.show_reasoning
        #   {"effort":  "none"|"minimal"|"low"|"medium"|"high"|"xhigh"}
        #                                            → agent.reasoning_effort
        try:
            display = body.get("display")
            effort = body.get("effort")
            if display is not None:
                flag = str(display).strip().lower()
                if flag in ("show", "on", "true", "1"):
                    return j(handler, set_reasoning_display(True))
                if flag in ("hide", "off", "false", "0"):
                    return j(handler, set_reasoning_display(False))
                return bad(handler, f"display must be show|hide|on|off (got '{display}')")
            if effort is not None:
                return j(handler, set_reasoning_effort(effort))
            return bad(handler, "reasoning: must supply 'display' or 'effort'")
        except ValueError as e:
            return bad(handler, str(e))
        except RuntimeError as e:
            return bad(handler, str(e), 500)

    if parsed.path == "/api/admin/reload":
        # Hot-reload api.models module to pick up code changes without restart.
        import importlib
        from api import models as _models
        importlib.reload(_models)
        # Also re-expose get_session from the reloaded module so routes.py
        # continues to work (routes.py imported it at module level).
        import api.routes as _routes
        _routes.get_session = _models.get_session
        _routes.Session = _models.Session
        _routes.compact = _models.compact
        return j(handler, {"status": "ok", "reloaded": "api.models"})

    if parsed.path == "/api/session/rename":
        try:
            require(body, "session_id", "title")
        except ValueError as e:
            return bad(handler, str(e))
        try:
            s = get_session(body["session_id"])
        except KeyError:
            return bad(handler, "Session not found", 404)
        with _get_session_agent_lock(body["session_id"]):
            new_title = str(body["title"]).strip()[:80] or "Untitled"
            s.title = new_title
            s.sync_webui_metadata()
            # Persist title via SDK high-level API (user-source rank=2 —
            # blocks future auto-titling, matching CLI semantics). WebUI
            # must not write the title column directly; this SDK call
            # handles UNIQUE-constraint suffixes ('Untitled #2', etc.)
            # correctly.
            try:
                from api.models import _get_session_db
                db = _get_session_db()
                if db is not None:
                    db.set_session_title(body["session_id"], new_title)
            except Exception:
                logger.warning(
                    "Failed to persist renamed title for %s",
                    body["session_id"], exc_info=True,
                )
        return j(handler, {"session": s.compact()})

    if parsed.path == "/api/personality/set":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        if "name" not in body:
            return bad(handler, "Missing required field: name")
        sid = body["session_id"]
        name = body["name"].strip()
        try:
            s = get_session(sid)
        except KeyError:
            return bad(handler, "Session not found", 404)
        # Resolve personality from config.yaml agent.personalities section
        # (matches hermes-agent CLI behavior)
        prompt = ""
        if name:
            from api.config import reload_config as _reload_cfg2

            _reload_cfg2()  # pick up config changes without restart
            from api.config import get_config as _get_cfg2

            _cfg2 = _get_cfg2()
            agent_cfg = _cfg2.get("agent", {})
            raw_personalities = agent_cfg.get("personalities", {})
            if not isinstance(raw_personalities, dict) or name not in raw_personalities:
                return bad(
                    handler, f'Personality "{name}" not found in config.yaml', 404
                )
            from api.streaming import _resolve_personality_prompt
            prompt = _resolve_personality_prompt(_cfg2, name) or ""
        with _get_session_agent_lock(sid):
            s.personality = name if name else None
            s.sync_webui_metadata()
        return j(handler, {"ok": True, "personality": s.personality, "prompt": prompt})

    if parsed.path == "/api/session/update":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        try:
            s = get_session(body["session_id"])
        except KeyError:
            return bad(handler, "Session not found", 404)
        try:
            new_ws = str(resolve_trusted_workspace(body.get("workspace", s.workspace)))
        except ValueError as e:
            return bad(handler, str(e))
        with _get_session_agent_lock(body["session_id"]):
            s.workspace = new_ws
            s.model = body.get("model", s.model)
            # ── Persist model to state.db via SDK API ──
            # update_session_model unconditionally writes the model column
            # (no COALESCE) so the sidebar reflects the user's latest choice
            # even after a server restart.
            # Normalize bare model names to @provider:model so the stored
            # value can uniquely identify the model across providers.
            persist_model = Session._normalize_model(s.model)
            try:
                from api.models import _get_session_db
                db = _get_session_db()
                if db is not None:
                    db.update_session_model(s.session_id, persist_model)
            except Exception:
                logger.warning("Failed to persist model for session %s",
                               s.session_id, exc_info=True)
            # Per-session reasoning effort (None = use global default)
            if "reasoning_effort" in body:
                raw = str(body["reasoning_effort"] or "").strip().lower()
                if raw == "" or raw == "none":
                    s.reasoning_effort = raw if raw == "none" else None
                elif raw not in ("minimal", "low", "medium", "high", "xhigh"):
                    return bad(handler, f"Invalid reasoning_effort: {body['reasoning_effort']}")
                else:
                    s.reasoning_effort = raw
            s.sync_webui_metadata()
        return j(handler, {"session": s.compact() | {"messages": s.messages}})

    if parsed.path == "/api/session/seen":
        try:
            require(body, "session_id", "msg_count")
        except ValueError as e:
            return bad(handler, str(e))
        sid = body["session_id"]
        msg_count = int(body["msg_count"])
        try:
            s = get_session(sid)
        except KeyError:
            s = None
        if s is not None:
            with _get_session_agent_lock(sid):
                s.last_seen_msg_count = msg_count
                s.sync_webui_metadata(touch_updated_at=False)
        else:
            # Session not in WebUI store — could be a gateway/CLI session.
            # Write last_seen_msg_count directly to the agent's state.db.
            _write_gateway_last_seen(sid, msg_count)
        return j(handler, {"ok": True})

    if parsed.path == "/api/session/delete":
        sid = body.get("session_id", "")
        if not sid:
            return bad(handler, "session_id is required")
        if not all(c in '0123456789abcdefghijklmnopqrstuvwxyz_' for c in sid):
            return bad(handler, "Invalid session_id", 400)
        # Stop SessionDisplay if running
        try:
            from api.session_display import _DISPLAYS
            existing = _DISPLAYS.get(sid)
            if existing:
                existing.stop(remove_json=True)
        except Exception as e:
            logger.debug("SessionDisplay cleanup on delete failed for %s: %s", sid, e)
        # Stop per-session process loop if running
        with LOCK:
            s = SESSIONS.get(sid)
            if s and hasattr(s, '_process_loop_thread') and s._process_loop_thread:
                s._process_loop_stop.set()
                s._process_loop_thread.join(timeout=2)
        # Delete from WebUI session store
        with LOCK:
            _deleted_session = SESSIONS.pop(sid, None)
        if _deleted_session:
            from api.models import _notify_session_event
            _notify_session_event('delete_session', _deleted_session)
        # Evict cached agent so turn count doesn't leak into a recycled session
        from api.config import _evict_session_agent
        _evict_session_agent(sid)
        try:
            p = (SESSION_DIR / f"{sid}.json").resolve()
            p.relative_to(SESSION_DIR.resolve())
        except Exception:
            return bad(handler, "Invalid session_id", 400)
        try:
            p.unlink(missing_ok=True)
        except Exception:
            logger.debug("Failed to unlink session file %s", p)
        # Prune the per-session agent lock so deleted sessions don't leak
        # Lock entries in SESSION_AGENT_LOCKS forever.
        with SESSION_AGENT_LOCKS_LOCK:
            SESSION_AGENT_LOCKS.pop(sid, None)
        try:
            SESSION_INDEX_FILE.unlink(missing_ok=True)
        except Exception:
            logger.debug("Failed to unlink session index")
        # Also delete from state.db (primary store)
        try:
            from api.models import _get_session_db
            db = _get_session_db()
            if db is not None:
                db.delete_session(sid)
        except Exception:
            logger.debug("Failed to delete session %s from state.db", sid)
        # Also delete from CLI state.db (for CLI sessions shown in sidebar)
        try:
            from api.models import delete_cli_session

            delete_cli_session(sid)
        except Exception:
            logger.debug("Failed to delete CLI session %s", sid)
        return j(handler, {"ok": True})

    if parsed.path == "/api/session/clear-delegates":
        session_id = body.get("session_id", "")
        keep_count = int(body.get("keep_count", 0))
        if not session_id:
            return bad(handler, "session_id is required")
        try:
            from api.models import _get_session_db, _get_all_delegate_children_map, _notify_session_event
            _db = _get_session_db()
            if _db is None:
                return j(handler, {"ok": True, "deleted": 0})
            _children_map = _get_all_delegate_children_map(_db)
            _children = _children_map.get(session_id, [])
            # Sort by started_at descending (newest first), keep first N
            _children.sort(key=lambda c: c.get('started_at', 0), reverse=True)
            _to_delete = _children[keep_count:]
            deleted = 0
            for child in _to_delete:
                child_id = child.get('id')
                if not child_id:
                    continue
                try:
                    _db.delete_session(child_id)
                    # Also clean up from memory and JSON store
                    with LOCK:
                        SESSIONS.pop(child_id, None)
                    p = (SESSION_DIR / f"{child_id}.json").resolve()
                    try:
                        p.relative_to(SESSION_DIR.resolve())
                        p.unlink(missing_ok=True)
                    except Exception:
                        pass
                    deleted += 1
                except Exception:
                    logger.debug("Failed to delete delegate child %s", child_id)

            # Update parent session's delegate children count and notify frontend
            if deleted > 0:
                with LOCK:
                    parent = SESSIONS.get(session_id)
                if parent:
                    remaining = len(_children) - deleted
                    parent._delegate_children_count = remaining
                    parent._has_delegate_children = remaining > 0
                    parent._delegate_running_count = 0  # cleared all running children
                    _notify_session_event('upsert_session', parent)

            return j(handler, {"ok": True, "deleted": deleted})
        except Exception as e:
            logger.debug("Failed to clear delegates for %s: %s", session_id, e)
            return j(handler, {"ok": True, "deleted": 0})

    if parsed.path == "/api/session/truncate":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        if body.get("keep_count") is None:
            return bad(handler, "Missing required field(s): keep_count")
        try:
            s = get_session(body["session_id"])
        except KeyError:
            return bad(handler, "Session not found", 404)
        keep = int(body["keep_count"])
        with _get_session_agent_lock(body["session_id"]):
            _old_messages = s.messages
            s.messages = _old_messages[:keep]
            # ── Soft-delete truncated messages in state.db ──
            if keep < len(_old_messages):
                _target = _old_messages[keep]
                _target_id = _target.get('id') if isinstance(_target, dict) else None
                if _target_id is not None:
                    try:
                        from api.models import _get_session_db
                        db = _get_session_db()
                        if db is not None:
                            db.rewind_to_message(body["session_id"], _target_id)
                    except Exception:
                        logger.debug("rewind_to_message failed for %s", body["session_id"])
            s.sync_webui_metadata()
        return j(
            handler, {"ok": True, "session": s.compact() | {"messages": s.messages}}
        )

    if parsed.path == "/api/session/compress":
        return _handle_session_compress(handler, body)

    if parsed.path == "/api/session/clear":
        sid = body.get("session_id", "")
        if not sid:
            return bad(handler, "session_id is required")
        s = get_session(sid)
        if s is None:
            return bad(handler, "Session not found", 404)
        # keep_last: number of most-recent messages to retain (default 0 = delete all)
        keep_last = int(body.get("keep_last", 0) or 0)
        lock = _get_session_agent_lock(sid)
        with lock:
            if keep_last > 0:
                s.messages = s.messages[-keep_last:]
            else:
                s.messages = []
            s.tool_calls = []
            # ── Reset context-window state for the cleared session ──
            # last_prompt_tokens: reset to 0 — no active prompt exists after clear.
            # It will be repopulated on the next turn by the context compressor.
            s.last_prompt_tokens = 0
            # input_tokens / output_tokens / estimated_cost are intentionally NOT
            # reset — they are cumulative API usage across the session's lifetime.
            # Clearing message history does not undo the tokens that were already
            # consumed and billed.
            # Reset _metadata_message_count so compact() falls back to the
            # authoritative len(self.messages) instead of a stale DB value.
            s._metadata_message_count = None
            # Reset last_seen_msg_count to match the new message count so the unread
            # badge (+N) computes correctly. Without this, a session that had 351
            # messages (last_seen=351) then gets cleared to 0 would compute
            # unread = max(0, new_count - 351) = 0 even when new messages arrive.
            s.last_seen_msg_count = len(s.messages)
            # Reset auto-compress throttle on clear
            s.auto_compress_disabled = False
            s.auto_compress_fail_count = 0
            s.pending_user_message = None
            s.pending_attachments = []
            s.pending_started_at = None
            # ── Persist message truncation to state.db ──
            # /clear truncates from the head (keeps last N), which
            # rewind_to_message (tail-truncate) cannot express.
            # Use replace_messages for correctness.
            try:
                from api.models import _get_session_db
                _db = _get_session_db()
                if _db is not None:
                    _db.replace_messages(sid, s.messages)
            except Exception:
                logger.warning("replace_messages failed for clear: %s", sid, exc_info=True)
            try:
                s.sync_webui_metadata()
            except Exception:
                logger.warning("sync_webui_metadata failed for clear: %s", sid, exc_info=True)
        # Evict cached agent so next turn starts fresh
        from api.config import _evict_session_agent
        _evict_session_agent(sid)
        # Invalidate session index cache
        try:
            SESSION_INDEX_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        # Notify frontend about the updated session (message_count changed)
        from api.models import _notify_session_event
        _notify_session_event('upsert_session', s)
        return j(handler, {"ok": True, "session_id": sid, "kept": len(s.messages)})

    if parsed.path == "/api/session/retry":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        try:
            from api.session_ops import retry_last
            result = retry_last(body["session_id"])
            return j(handler, {"ok": True, **result})
        except KeyError:
            return bad(handler, "Session not found", 404)
        except ValueError as e:
            return j(handler, {"error": str(e)})

    if parsed.path == "/api/session/undo":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        try:
            from api.session_ops import undo_last
            result = undo_last(body["session_id"])
            return j(handler, {"ok": True, **result})
        except KeyError:
            return bad(handler, "Session not found", 404)
        except ValueError as e:
            return j(handler, {"error": str(e)})

    # ── Unified mode toggle (POST) ──
    # Stores mode on the Session object (persisted to JSON), replacing the
    # legacy in-memory YOLO toggle.  Valid modes: manual, smart, yolo.
    if parsed.path == "/api/session/mode":
        try:
            require(body, "session_id", "mode")
        except ValueError as e:
            return bad(handler, str(e))
        sid = body["session_id"]
        mode = body["mode"]
        if mode not in ("manual", "smart", "yolo"):
            return bad(handler, "Invalid mode: must be manual, smart, or yolo")
        try:
            s = get_session(sid)
            s.mode = mode
            s.sync_webui_metadata()
            # If switching to yolo, resolve pending approvals
            if mode == "yolo":
                enable_session_yolo(sid)
                try:
                    from tools.approval import _pending as _p, _lock as _l
                    with _l:
                        _p.pop(sid, None)
                except Exception:
                    pass
                resolve_gateway_approval(sid, "once", resolve_all=True)
            else:
                disable_session_yolo(sid)
            result = {"ok": True, "mode": mode, "smart_supported": _is_smart_supported()}
            # Broadcast mode_changed to all active SSE streams so frontend
            # tabs can sync mode state in real time.  Each stream's
            # EventSource listener filters by session_id.
            mode_event = json.dumps({"mode": mode, "session_id": sid}, ensure_ascii=False)
            stream_manager.broadcast("mode_changed", mode_event)
            return j(handler, result)
        except KeyError:
            return bad(handler, "Session not found", 404)

    # ── YOLO mode toggle (POST, backward-compat) ──
    # Delegates to the unified mode store so both APIs stay in sync.
    # Fixes #467
    if parsed.path == "/api/session/yolo":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        sid = body["session_id"]
        enabled = bool(body.get("enabled", True))
        if enabled:
            enable_session_yolo(sid)
            try:
                s = get_session(sid)
                s.mode = "yolo"
                s.sync_webui_metadata()
            except Exception:
                pass
            try:
                from tools.approval import _pending as _p, _lock as _l
                with _l:
                    _p.pop(sid, None)
            except Exception:
                pass
            resolve_gateway_approval(sid, "once", resolve_all=True)
        else:
            disable_session_yolo(sid)
            try:
                s = get_session(sid)
                s.mode = "manual"
                s.sync_webui_metadata()
            except Exception:
                pass
        return j(handler, {"ok": True, "yolo_enabled": enabled})

    if parsed.path == "/api/btw":
        return _handle_btw(handler, body)

    if parsed.path == "/api/background":
        return _handle_background(handler, body)

    if parsed.path == "/api/chat/dispatch":
        return _handle_chat_dispatch(handler, body)

    if parsed.path == "/api/chat/start":
        return _handle_chat_start(handler, body)

    if parsed.path == "/api/chat":
        return _handle_chat_sync(handler, body)

    if parsed.path == "/api/chat/steer":
        from api.streaming import _handle_chat_steer
        return _handle_chat_steer(handler, body)

    # ── Cron API (POST) ──
    if parsed.path == "/api/crons/create":
        return _handle_cron_create(handler, body)

    if parsed.path == "/api/crons/update":
        return _handle_cron_update(handler, body)

    if parsed.path == "/api/crons/delete":
        return _handle_cron_delete(handler, body)

    if parsed.path == "/api/crons/run":
        return _handle_cron_run(handler, body)

    if parsed.path == "/api/crons/pause":
        return _handle_cron_pause(handler, body)

    if parsed.path == "/api/crons/resume":
        return _handle_cron_resume(handler, body)

    # ── File ops (POST) ──
    if parsed.path == "/api/workspace/file":
        return _handle_workspace_file_write(handler, body)

    if parsed.path == "/api/workspace/delete":
        return _handle_workspace_file_delete(handler, body)

    if parsed.path == "/api/workspace/new":
        return _handle_workspace_new(handler, body)

    if parsed.path == "/api/workspace/move":
        return _handle_workspace_move(handler, body)

    if parsed.path == "/api/file/delete":
        return _handle_file_delete(handler, body)

    if parsed.path == "/api/file/save":
        return _handle_file_save(handler, body)

    if parsed.path == "/api/file/create":
        return _handle_file_create(handler, body)

    if parsed.path == "/api/file/rename":
        return _handle_file_rename(handler, body)

    if parsed.path == "/api/file/create-dir":
        return _handle_create_dir(handler, body)

    # ── Workspace management (POST) ──
    if parsed.path == "/api/workspaces/add":
        return _handle_workspace_add(handler, body)

    if parsed.path == "/api/workspaces/remove":
        return _handle_workspace_remove(handler, body)

    if parsed.path == "/api/workspaces/rename":
        return _handle_workspace_rename(handler, body)

    if parsed.path == "/api/workspaces/select":
        path_str = body.get("path", "").strip()
        if not path_str:
            return bad(handler, "path is required")
        set_last_workspace(path_str)
        return j(handler, {"ok": True, "last_workspace": path_str})

    # ── Approval (POST) ──
    if parsed.path == "/api/approval/respond":
        return _handle_approval_respond(handler, body)

    if parsed.path == "/api/approval/revoke":
        return _handle_approval_revoke(handler, body)

    # ── Clarify (POST) ──
    if parsed.path == "/api/clarify/respond":
        return _handle_clarify_respond(handler, body)

    # ── Skills (POST) ──
    if parsed.path == "/api/skills/save":
        return _handle_skill_save(handler, body)

    if parsed.path == "/api/skills/delete":
        return _handle_skill_delete(handler, body)

    if parsed.path == "/api/skills/content":
        return _handle_skill_file_save(handler, body)

    if parsed.path == "/api/skills/file/delete":
        return _handle_skill_file_delete(handler, body)

    if parsed.path == "/api/skills/file/create":
        return _handle_skill_file_create(handler, body)

    if parsed.path == "/api/skills/file/rename":
        return _handle_skill_file_rename(handler, body)

    # ── Memory (POST) ──
    if parsed.path == "/api/memory/write":
        return _handle_memory_write(handler, body)

    # ── Profile API (POST) ──
    if parsed.path == "/api/profile/switch":
        name = body.get("name", "").strip()
        if not name:
            return bad(handler, "name is required")
        try:
            from api.profiles import switch_profile, _validate_profile_name
            from api.helpers import build_profile_cookie
            if name != 'default':
                _validate_profile_name(name)
            # process_wide=False: don't mutate the process-global _active_profile.
            # Per-client profile is managed via cookie + thread-local (#798).
            result = switch_profile(name, process_wide=False)
            # Invalidate the models cache so the very next /api/models request
            # rebuilds from the new profile's config.yaml rather than returning
            # the old profile's cached model list (#1200 — profile-switch model bug).
            from api.config import invalidate_models_cache
            invalidate_models_cache()
            return j(handler, result, extra_headers={
                'Set-Cookie': build_profile_cookie(name),
            })
        except (ValueError, FileNotFoundError) as e:
            return bad(handler, _sanitize_error(e), 404)
        except RuntimeError as e:
            return bad(handler, str(e), 409)

    if parsed.path == "/api/profile/create":
        name = body.get("name", "").strip()
        if not name:
            return bad(handler, "name is required")
        import re as _re

        if not _re.match(r"^[a-z0-9][a-z0-9_-]{0,63}$", name):
            return bad(
                handler,
                "Invalid profile name: lowercase letters, numbers, hyphens, underscores only",
            )
        clone_from = body.get("clone_from")
        if clone_from is not None:
            clone_from = str(clone_from).strip()
            if not _re.match(r"^[a-z0-9][a-z0-9_-]{0,63}$", clone_from):
                return bad(handler, "Invalid clone_from name")
        base_url = body.get("base_url", "").strip() if body.get("base_url") else None
        api_key = body.get("api_key", "").strip() if body.get("api_key") else None
        if base_url and not base_url.startswith(("http://", "https://")):
            return bad(handler, "base_url must start with http:// or https://")
        try:
            from api.profiles import create_profile_api

            result = create_profile_api(
                name,
                clone_from=clone_from,
                clone_config=bool(body.get("clone_config", False)),
                base_url=base_url,
                api_key=api_key,
            )
            return j(handler, {"ok": True, "profile": result})
        except (ValueError, FileExistsError, RuntimeError) as e:
            return bad(handler, str(e))

    if parsed.path == "/api/profile/delete":
        name = body.get("name", "").strip()
        if not name:
            return bad(handler, "name is required")
        try:
            from api.profiles import delete_profile_api, _validate_profile_name

            _validate_profile_name(name)
            result = delete_profile_api(name)
            return j(handler, result)
        except (ValueError, FileNotFoundError) as e:
            return bad(handler, _sanitize_error(e))
        except RuntimeError as e:
            return bad(handler, str(e), 409)

    # ── Settings (POST) ──
    if parsed.path == "/api/settings":
        from api.auth import (
            create_session,
            is_auth_enabled,
            parse_cookie,
            set_auth_cookie,
            verify_session,
        )

        auth_enabled_before = is_auth_enabled()
        current_cookie = parse_cookie(handler)
        logged_in_before = bool(current_cookie and verify_session(current_cookie))
        requested_password = bool(
            isinstance(body.get("_set_password"), str)
            and body.get("_set_password", "").strip()
        )

        saved = save_settings(body)
        saved.pop("password_hash", None)  # never expose hash to client

        # When auto_compress_token_threshold changes, sync to agent config.yaml
        # so path-3 (in-agent) compression picks up the new threshold on the
        # next agent construction.
        _token_thresh = body.get("auto_compress_token_threshold")
        if _token_thresh is not None:
            try:
                from hermes_cli.config import load_config as _load_cfg, save_config as _save_cfg

                _thresh_val = float(_token_thresh)
                _cfg = _load_cfg()
                _comp = _cfg.setdefault("compression", {})
                if _thresh_val <= 0:
                    _comp["enabled"] = False
                else:
                    # WebUI handles token-threshold compression itself after each
                    # turn (see streaming.py done processing).  Disabling the
                    # agent's built-in compression keeps the session_id stable,
                    # avoids session-id rotation, and archives pre-compression
                    # state to {sid}_archived/ — consistent with path 1 behaviour.
                    _comp["enabled"] = False
                    _comp["threshold"] = _thresh_val
                _save_cfg(_cfg)
            except Exception:
                logger.debug("Failed to sync compression settings to config.yaml", exc_info=True)

        auth_enabled_after = is_auth_enabled()
        auth_just_enabled = bool(
            requested_password and auth_enabled_after and not auth_enabled_before
        )
        logged_in_after = logged_in_before
        new_cookie = None

        if auth_just_enabled and not logged_in_before:
            new_cookie = create_session()
            logged_in_after = True

        saved["auth_enabled"] = auth_enabled_after
        saved["logged_in"] = logged_in_after
        saved["auth_just_enabled"] = auth_just_enabled

        if not new_cookie:
            return j(handler, saved)

        response_body = json.dumps(saved, ensure_ascii=False, indent=2).encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(response_body)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Connection", "close")
        set_auth_cookie(handler, new_cookie)
        _security_headers(handler)
        handler.end_headers()
        handler.wfile.write(response_body)
        return True

    if parsed.path == "/api/onboarding/setup":
        # Writing API keys to disk - restrict to local/private networks unless auth is active.
        # In Docker, requests arrive from the bridge network (172.x.x.x), not 127.0.0.1,
        # even when the user accesses via localhost:8787 on the host.
        # Behind a reverse proxy (nginx/Caddy/Traefik) or SSH tunnel, X-Forwarded-For
        # carries the real origin IP — read it first before falling back to the raw socket addr.
        # HERMES_WEBUI_ONBOARDING_OPEN=1 lets operators on remote servers explicitly bypass
        # the check when they control network access themselves (e.g. firewall + VPN).
        from api.auth import is_auth_enabled
        import os as _os
        if not is_auth_enabled() and not _os.getenv("HERMES_WEBUI_ONBOARDING_OPEN"):
            import ipaddress
            try:
                # Prefer forwarded headers set by reverse proxies
                _xff = handler.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                _xri = handler.headers.get("X-Real-IP", "").strip()
                _raw = handler.client_address[0]
                _ip_str = _xff or _xri or _raw
                addr = ipaddress.ip_address(_ip_str)
                is_local = addr.is_loopback or addr.is_private
            except ValueError:
                is_local = False
            if not is_local:
                return bad(handler, "Onboarding setup is only available from local networks when auth is not enabled. To bypass this on a remote server, set HERMES_WEBUI_ONBOARDING_OPEN=1.", 403)
        try:
            return j(handler, apply_onboarding_setup(body))
        except ValueError as e:
            return bad(handler, str(e))
        except RuntimeError as e:
            return bad(handler, str(e), 500)

    if parsed.path == "/api/onboarding/complete":
        return j(handler, complete_onboarding())

    # ── Session pin (POST) ──
    if parsed.path == "/api/session/pin":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        try:
            s = get_session(body["session_id"])
        except KeyError:
            return bad(handler, "Session not found", 404)
        with _get_session_agent_lock(body["session_id"]):
            s.pinned = bool(body.get("pinned", True))
            s.sync_webui_metadata()

        # Pin → start SessionDisplay; Unpin → stop SessionDisplay
        try:
            from api.session_display import SessionDisplay, _DISPLAYS
            existing = _DISPLAYS.get(body["session_id"])
            if s.pinned:
                if existing and existing.is_running():
                    pass  # already running, nothing to do
                else:
                    sd = SessionDisplay(body["session_id"])
                    sd.start()
            else:
                if existing:
                    existing.stop(remove_json=True)
        except Exception as e:
            logger.warning("SessionDisplay pin hook failed for %s: %s",
                           body["session_id"], e)

        return j(handler, {"ok": True, "session": s.compact()})

    # ── Session assign workspace (POST) ──
    if parsed.path == "/api/session/workspace":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        try:
            s = get_session(body["session_id"])
        except KeyError:
            return bad(handler, "Session not found", 404)
        raw_ws = body.get("workspace", "")
        try:
            new_ws = str(resolve_trusted_workspace(raw_ws)) if raw_ws else ""
        except ValueError as e:
            return bad(handler, str(e))
        with _get_session_agent_lock(body["session_id"]):
            s.workspace = new_ws
            s.sync_webui_metadata()
            # ── Persist cwd to state.db ──────────────────────────────
            # cwd is NOT a __webui column — it's managed by the hermes-agent
            # SDK (written on session creation, and by TUI gateway for CLI
            # sessions).  WebUI normally only writes __webui columns.
            #
            # However, for webui sessions the SDK never touches cwd after the
            # initial INSERT, so there is no write conflict.  Adding a
            # cwd__webui column would create reconciliation ambiguity (which
            # wins when both are set?).  Using the single source-of-truth cwd
            # column is simpler and correct.
            try:
                from api.models import _get_session_db
                db = _get_session_db()
                if db is not None:
                    db._conn.execute(
                        "UPDATE sessions SET cwd = ? WHERE id = ?",
                        (new_ws or None, body["session_id"]),
                    )
                    db._conn.commit()
            except Exception:
                logger.warning(
                    "Failed to persist cwd for session %s",
                    body["session_id"],
                    exc_info=True,
                )
        set_last_workspace(new_ws)
        return j(handler, {"ok": True, "session": s.compact()})

    # ── Session skills binding (POST) ──
    if parsed.path == "/api/session/skills":
        try:
            require(body, "session_id", "skills")
        except ValueError as e:
            return bad(handler, str(e))
        skills = body["skills"]
        if not isinstance(skills, list):
            return bad(handler, "skills must be a list")
        import re as _re_sk
        for _sk in skills:
            if not isinstance(_sk, str) or not _sk or _re_sk.search(r'[/\\.]', _sk):
                return bad(handler, f"Invalid skill name: {_sk!r}")
        try:
            s = get_session(body["session_id"])
        except KeyError:
            return bad(handler, "Session not found", 404)
        with _get_session_agent_lock(body["session_id"]):
            s.skills = skills
            s.sync_webui_metadata()
        # Clear agent cache so next message rebuilds with new skill list
        from api.config import SESSION_AGENT_CACHE, SESSION_AGENT_CACHE_LOCK
        with SESSION_AGENT_CACHE_LOCK:
            SESSION_AGENT_CACHE.pop(body["session_id"], None)
        return j(handler, {"ok": True, "skills": skills})

    # ── Session archive (POST) ──
    if parsed.path == "/api/session/archive":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        try:
            s = get_session(body["session_id"])
        except KeyError:
            return bad(handler, "Session not found", 404)
        with _get_session_agent_lock(body["session_id"]):
            s.archived = bool(body.get("archived", True))
            s.sync_webui_metadata()
        return j(handler, {"ok": True, "session": s.compact()})

    # ── Session move to project (POST) ──
    if parsed.path == "/api/session/move":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        try:
            s = get_session(body["session_id"])
        except KeyError:
            return bad(handler, "Session not found", 404)
        with _get_session_agent_lock(body["session_id"]):
            s.project_id = body.get("project_id") or None
            s.sync_webui_metadata()
        return j(handler, {"ok": True, "session": s.compact()})

    # ── Project CRUD (POST) ──
    if parsed.path == "/api/projects/create":
        try:
            require(body, "name")
        except ValueError as e:
            return bad(handler, str(e))
        import re as _re

        name = body["name"].strip()[:128]
        if not name:
            return bad(handler, "name required")
        color = body.get("color")
        if color and not _re.match(r"^#[0-9a-fA-F]{3,8}$", color):
            return bad(handler, "Invalid color format")
        projects = load_projects()
        proj = {
            "project_id": uuid.uuid4().hex[:12],
            "name": name,
            "color": color,
            "created_at": time.time(),
        }
        projects.append(proj)
        save_projects(projects)
        return j(handler, {"ok": True, "project": proj})

    if parsed.path == "/api/projects/rename":
        try:
            require(body, "project_id", "name")
        except ValueError as e:
            return bad(handler, str(e))
        import re as _re

        projects = load_projects()
        proj = next(
            (p for p in projects if p["project_id"] == body["project_id"]), None
        )
        if not proj:
            return bad(handler, "Project not found", 404)
        proj["name"] = body["name"].strip()[:128]
        if "color" in body:
            color = body["color"]
            if color and not _re.match(r"^#[0-9a-fA-F]{3,8}$", color):
                return bad(handler, "Invalid color format")
            proj["color"] = color
        save_projects(projects)
        return j(handler, {"ok": True, "project": proj})

    if parsed.path == "/api/projects/delete":
        try:
            require(body, "project_id")
        except ValueError as e:
            return bad(handler, str(e))
        projects = load_projects()
        proj = next(
            (p for p in projects if p["project_id"] == body["project_id"]), None
        )
        if not proj:
            return bad(handler, "Project not found", 404)
        projects = [p for p in projects if p["project_id"] != body["project_id"]]
        save_projects(projects)
        # Unassign all sessions that belonged to this project
        if SESSION_INDEX_FILE.exists():
            try:
                index = json.loads(SESSION_INDEX_FILE.read_text(encoding="utf-8"))
                for entry in index:
                    if entry.get("project_id") == body["project_id"]:
                        try:
                            s = get_session(entry["session_id"])
                            s.project_id = None
                            s.sync_webui_metadata()
                        except Exception:
                            logger.debug("Failed to update session %s", entry.get("session_id"))
            except Exception:
                logger.debug("Failed to load session index for project unlink")
        return j(handler, {"ok": True})

    # ── Session import from JSON (POST) ──
    if parsed.path == "/api/session/import":
        return _handle_session_import(handler, body)

    # ── Self-update (POST) ──
    if parsed.path == "/api/updates/apply":
        target = body.get("target", "")
        if target not in ("webui", "agent"):
            return bad(handler, 'target must be "webui" or "agent"')
        from api.updates import apply_update

        return j(handler, apply_update(target))

    if parsed.path == "/api/updates/force":
        target = body.get("target", "")
        if target not in ("webui", "agent"):
            return bad(handler, 'target must be "webui" or "agent"')
        from api.updates import apply_force_update

        return j(handler, apply_force_update(target))

    # ── CLI session import (POST) ──
    if parsed.path == "/api/session/import_cli":
        return _handle_session_import_cli(handler, body)

    # ── Auth endpoints (POST) ──
    if parsed.path == "/api/auth/login":
        from api.auth import (
            verify_password,
            create_session,
            set_auth_cookie,
            is_auth_enabled,
        )
        from api.auth import _check_login_rate, _record_login_attempt

        if not is_auth_enabled():
            return j(handler, {"ok": True, "message": "Auth not enabled"})
        client_ip = handler.client_address[0]
        if not _check_login_rate(client_ip):
            return j(
                handler,
                {"error": "Too many attempts. Try again in a minute."},
                status=429,
            )
        password = body.get("password", "")
        if not verify_password(password):
            _record_login_attempt(client_ip)
            return bad(handler, "Invalid password", 401)
        cookie_val = create_session()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Connection", "close")
        _security_headers(handler)
        set_auth_cookie(handler, cookie_val)
        handler.end_headers()
        handler.wfile.write(json.dumps({"ok": True}).encode())
        return True

    if parsed.path == "/api/auth/logout":
        from api.auth import clear_auth_cookie, invalidate_session, parse_cookie

        cookie_val = parse_cookie(handler)
        if cookie_val:
            invalidate_session(cookie_val)
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Connection", "close")
        _security_headers(handler)
        clear_auth_cookie(handler)
        handler.end_headers()
        handler.wfile.write(json.dumps({"ok": True}).encode())
        return True

    # ── Session cleanup (POST) ──
    if parsed.path in ("/api/sessions/cleanup", "/api/sessions/gateway/cleanup"):
        return _handle_session_cleanup(handler, body)

    # ── Test-only seed helpers (POST) ──
    # Gated by HERMES_WEBUI_TEST_SEED=1 in the server's environment; the helper
    # module also raises if the flag is missing. Production servers never set
    # the flag, so these endpoints 404 in prod. See api/_test_helpers.py.
    if parsed.path == "/api/_test/seed_session":
        if os.environ.get("HERMES_WEBUI_TEST_SEED") != "1":
            return j(handler, {"error": "test seed disabled"}, status=404)
        try:
            from api._test_helpers import seed_test_session
            result = seed_test_session(
                session_id=body["session_id"],
                source=body["source"],
                title=body.get("title"),
                model=body.get("model"),
                started_at=body.get("started_at"),
                parent_session_id=body.get("parent_session_id"),
                ended_at=body.get("ended_at"),
                end_reason=body.get("end_reason"),
                messages=body.get("messages"),
            )
            return j(handler, result)
        except KeyError as e:
            return j(handler, {"error": f"missing field: {e}"}, status=400)
        except Exception as e:
            logger.exception("test seed failed")
            return j(handler, {"error": str(e)}, status=500)

    if parsed.path == "/api/_test/cleanup_sessions":
        if os.environ.get("HERMES_WEBUI_TEST_SEED") != "1":
            return j(handler, {"error": "test seed disabled"}, status=404)
        try:
            from api._test_helpers import cleanup_test_sessions
            result = cleanup_test_sessions(body.get("session_ids") or [])
            return j(handler, result)
        except Exception as e:
            logger.exception("test seed cleanup failed")
            return j(handler, {"error": str(e)}, status=500)

    if parsed.path == "/api/_test/nuke_state_db":
        if os.environ.get("HERMES_WEBUI_TEST_SEED") != "1":
            return j(handler, {"error": "test seed disabled"}, status=404)
        try:
            from api._test_helpers import nuke_test_state_db
            nuke_test_state_db()
            return j(handler, {"ok": True})
        except Exception as e:
            logger.exception("test nuke state.db failed")
            return j(handler, {"error": str(e)}, status=500)

    return False  # 404

# ── GET route helpers ─────────────────────────────────────────────────────────

# MIME types for static file serving. Hoisted to module scope to avoid
# rebuilding the dict on every request.
_STATIC_MIME = {
    "css": "text/css",
    "js": "application/javascript",
    "html": "text/html",
    "svg": "image/svg+xml",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "ico": "image/x-icon",
    "gif": "image/gif",
    "webp": "image/webp",
    "woff": "font/woff",
    "woff2": "font/woff2",
}
# MIME types that are text-based and should carry charset=utf-8
_TEXT_MIME_TYPES = {"text/css", "application/javascript", "text/html", "image/svg+xml", "text/plain"}
# Text MIME types eligible for pre-compressed (.gz) serving
_COMPRESSIBLE_MIME = {"text/css", "application/javascript", "text/html", "image/svg+xml"}

# ── ETag helpers ──────────────────────────────────────────────────────────────
# Cache ETag values keyed by (filepath, size, mtime_ns) so repeated requests
# skip the hashlib computation.  The dict is small (bounded by the number of
# static files) and never needs explicit eviction.
_etag_cache: dict[str, tuple[str, str]] = {}


def _get_etag(filepath: Path) -> str:
    """Return an ETag derived from file size and mtime (fast, no content read)."""
    try:
        stat = filepath.stat()
    except OSError:
        return ""
    cache_key = f"{stat.st_size}-{stat.st_mtime_ns}"
    str_path = str(filepath)
    cached = _etag_cache.get(str_path)
    if cached and cached[0] == cache_key:
        return cached[1]
    etag = hashlib.md5(cache_key.encode()).hexdigest()[:16]
    _etag_cache[str_path] = (cache_key, etag)
    return etag


# Regex for Vite content-hash filenames (e.g. index-7g0fwQV9.js)
_IMMUTABLE_RE = _re.compile(r'-[a-zA-Z0-9]{8,}\.')


def _is_immutable_asset(filename: str) -> bool:
    """Return True if the filename looks like a Vite content-hashed asset."""
    return bool(_IMMUTABLE_RE.search(filename))


def _send_static_response(handler, filepath: Path, ct_header: str, is_text: bool) -> None:
    """Shared logic for serving a static file with ETag, caching, and gzip."""
    etag = _get_etag(filepath)
    client_etag = handler.headers.get("If-None-Match", "")
    if etag and client_etag == etag:
        handler.send_response(304)
        handler.send_header("ETag", etag)
        handler.end_headers()
        return

    # Determine cache policy
    if _is_immutable_asset(filepath.name):
        cache_control = "public, max-age=31536000, immutable"
    else:
        cache_control = "no-store"

    # Check for pre-compressed .gz variant (text assets only)
    raw = None
    using_gzip = False
    if is_text:
        gz_path = filepath.with_suffix(filepath.suffix + ".gz")
        accept_enc = handler.headers.get("Accept-Encoding", "")
        if gz_path.exists() and "gzip" in accept_enc:
            try:
                raw = gz_path.read_bytes()
                using_gzip = True
            except OSError:
                raw = None  # fallback to uncompressed

    if raw is None:
        raw = filepath.read_bytes()

    handler.send_response(200)
    handler.send_header("Content-Type", ct_header)
    handler.send_header("Cache-Control", cache_control)
    if etag:
        handler.send_header("ETag", etag)
    if using_gzip:
        handler.send_header("Content-Encoding", "gzip")
        handler.send_header("Vary", "Accept-Encoding")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _serve_static(handler, parsed):
    static_root = (Path(__file__).parent.parent / "static").resolve()
    # Strip the leading '/static/' prefix, then resolve and sandbox
    rel = parsed.path[len("/static/"):]
    static_file = (static_root / rel).resolve()
    try:
        static_file.relative_to(static_root)
    except ValueError:
        return j(handler, {"error": "not found"}, status=404)
    if not static_file.exists() or not static_file.is_file():
        return j(handler, {"error": "not found"}, status=404)
    ext = static_file.suffix.lower()
    ct = _STATIC_MIME.get(ext.lstrip("."), "text/plain")
    ct_header = f"{ct}; charset=utf-8" if ct in _TEXT_MIME_TYPES else ct
    is_text = ct in _COMPRESSIBLE_MIME
    try:
        _send_static_response(handler, static_file, ct_header, is_text)
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        pass
    return True


def _serve_assets(handler, parsed):
    """Serve Vite-built frontend assets from static/assets/ directory.

    The Vite build outputs index.html with /assets/... references, but the
    Python server's static handler only serves under the /static/ prefix.
    This function maps /assets/* → static/assets/* so the built frontend
    works correctly when served by the Python server directly.
    """
    static_root = (Path(__file__).parent.parent / "static").resolve()
    # Strip the leading '/assets/' prefix, then prepend 'assets/' so the
    # resolved path is static/assets/<rest> rather than static/<rest>.
    rel = "assets/" + parsed.path[len("/assets/"):]
    asset_file = (static_root / rel).resolve()
    try:
        asset_file.relative_to(static_root)
    except ValueError:
        return j(handler, {"error": "not found"}, status=404)
    if not asset_file.exists() or not asset_file.is_file():
        return j(handler, {"error": "not found"}, status=404)
    ext = asset_file.suffix.lower()
    ct = _STATIC_MIME.get(ext.lstrip("."), "text/plain")
    ct_header = f"{ct}; charset=utf-8" if ct in _TEXT_MIME_TYPES else ct
    is_text = ct in _COMPRESSIBLE_MIME
    try:
        _send_static_response(handler, asset_file, ct_header, is_text)
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        pass
    except Exception as e:
        import ssl
        import socket
        if isinstance(e, (ssl.SSLError, socket.error)):
            pass
        else:
            logger.warning('[ASSET] Error serving %s: %s', parsed.path, e)
    return True


def _handle_session_export(handler, parsed):
    sid = parse_qs(parsed.query).get("session_id", [""])[0]
    if not sid:
        return bad(handler, "session_id is required")
    try:
        s = get_session(sid)
    except KeyError:
        return bad(handler, "Session not found", 404)
    safe = redact_session_data(s.__dict__)
    payload = json.dumps(safe, ensure_ascii=False, indent=2)
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header(
        "Content-Disposition", f'attachment; filename="hermes-{sid}.json"'
    )
    handler.send_header("Content-Length", str(len(payload.encode("utf-8"))))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(payload.encode("utf-8"))
    return True


def _handle_sessions_search(handler, parsed):
    qs = parse_qs(parsed.query)
    q = qs.get("q", [""])[0].strip()
    content_search = qs.get("content", ["1"])[0] == "1"
    depth = int(qs.get("depth", ["0"])[0])  # 0 = all messages (FTS5 preferred)

    # Empty query → return all sessions (compact format)
    if not q:
        safe_sessions = []
        for s in all_sessions():
            item = dict(s)
            if isinstance(item.get("title"), str):
                item["title"] = _redact_text(item["title"])
            safe_sessions.append(item)
        return j(handler, {"sessions": safe_sessions, "query": "", "count": len(safe_sessions)})

    # Try FTS5 search via SessionDB first
    try:
        from hermes_state import SessionDB, get_last_init_error
        if get_last_init_error() is not None:
            raise RuntimeError("SessionDB unavailable")

        # Resolve hermes home
        import os
        from pathlib import Path
        try:
            from api.profiles import get_active_hermes_home
            hermes_home = Path(get_active_hermes_home()).expanduser().resolve()
        except Exception:
            hermes_home = Path(os.getenv('HERMES_HOME', str(Path.home() / '.hermes')))

        db_path = hermes_home / 'state.db'
        if not db_path.exists():
            raise FileNotFoundError("state.db not found")

        db = SessionDB(db_path, read_only=True)
        try:
            # Sanitize query for FTS5
            sanitized = SessionDB._sanitize_fts5_query(q)
            if not sanitized:
                return j(handler, {"sessions": [], "query": q, "count": 0})

            # Build the allowed-source list from the three sidebar toggles so
            # search results match exactly what the sidebar shows.
            _settings = load_settings()
            source_filter = ['webui']
            if _settings.get("show_cli_sessions", True):
                source_filter.append('cli')
            if _settings.get("show_cron_sessions", False):
                source_filter.append('cron')
            if _settings.get("show_messaging_sessions", True):
                source_filter.extend(sorted(MESSAGING_SOURCE_TAGS))
            matches = db.search_messages(
                query=sanitized,
                source_filter=source_filter,
                exclude_sources=None,
                limit=200,
            )

            # Build results with session metadata + match_type
            seen = set()
            results = []
            # First check title matches against all_sessions (fast path)
            all_sessions_list = all_sessions()
            session_map = {s['session_id']: s for s in all_sessions_list}

            # Check title matches first
            q_lower = q.lower()
            for s in all_sessions_list:
                sid = s['session_id']
                if sid in seen:
                    continue
                if q_lower in (s.get('title') or '').lower():
                    item = dict(s)
                    if isinstance(item.get("title"), str):
                        item["title"] = _redact_text(item["title"])
                    item["match_type"] = "title"
                    results.append(item)
                    seen.add(sid)

            # Add content matches from FTS5
            if content_search:
                for match in matches:
                    sid = match.get('session_id')
                    if sid in seen:
                        # Already matched by title or earlier content match;
                        # keep the first match timestamp for scroll-to.
                        continue
                    seen.add(sid)
                    # Build compact session dict
                    s = session_map.get(sid)
                    if s:
                        item = dict(s)
                    else:
                        # Non-webui session (gateway/CLI/cron) — absent from
                        # all_sessions(), so fetch its metadata from state.db.
                        # search_messages() only returns session_started/model/
                        # source; title/cwd/message_count live on the row.
                        row = None
                        try:
                            row = db.get_session(sid)
                        except Exception:
                            row = None
                        row = row or {}
                        started_at = row.get('started_at') or match.get('session_started') or 0
                        item = {
                            'session_id': sid,
                            'title': row.get('title') or 'Untitled',
                            'workspace': row.get('cwd') or '',
                            'model': row.get('model') or match.get('model'),
                            'message_count': row.get('message_count') or match.get('message_count', 0),
                            'created_at': started_at,
                            'updated_at': row.get('last_activity_at') or started_at,
                            'pinned': bool(row.get('pinned__webui')),
                        }
                    if isinstance(item.get("title"), str):
                        item["title"] = _redact_text(item["title"])
                    item["match_type"] = "content"
                    item["match_message_id"] = match.get('id')
                    item["match_snippet"] = match.get('snippet', '')
                    results.append(item)
        finally:
            db.close()

        return j(handler, {"sessions": results, "query": q, "count": len(results)})

    except Exception:
        pass  # Fallback to legacy Python linear scan

    # Legacy fallback: Python linear scan (when SessionDB unavailable)
    q_lower = q.lower()
    results = []
    for s in all_sessions():
        title_match = q_lower in (s.get("title") or "").lower()
        if title_match:
            item = dict(s, match_type="title")
            if isinstance(item.get("title"), str):
                item["title"] = _redact_text(item["title"])
            results.append(item)
            continue
        if content_search:
            try:
                sess = get_session(s["session_id"])
                msgs = sess.messages[:depth] if depth else sess.messages
                for m in msgs:
                    c = m.get("content") or ""
                    if isinstance(c, list):
                        c = " ".join(
                            p.get("text", "")
                            for p in c
                            if isinstance(p, dict) and p.get("type") == "text"
                        )
                    if q_lower in str(c).lower():
                        item = dict(s, match_type="content")
                        if isinstance(item.get("title"), str):
                            item["title"] = _redact_text(item["title"])
                        results.append(item)
                        break
            except (KeyError, Exception):
                pass
    return j(handler, {"sessions": results, "query": q, "count": len(results)})


def _handle_list_dir(handler, parsed):
    qs = parse_qs(parsed.query)
    sid = qs.get("session_id", [""])[0]
    if not sid:
        return bad(handler, "session_id is required")
    try:
        s = get_session(sid)
        workspace = s.workspace
    except KeyError:
        # Fallback for CLI sessions not loaded in WebUI memory
        try:
            cli_meta = None
            for cs in get_cli_sessions():
                if cs["session_id"] == sid:
                    cli_meta = cs
                    break
            if not cli_meta:
                return bad(handler, "Session not found", 404)
            workspace = cli_meta.get("workspace", "")
        except Exception:
            return bad(handler, "Session not found", 404)
    try:
        return j(
            handler,
            {
                "entries": list_dir(Path(workspace), qs.get("path", ["."])[0]),
                "path": qs.get("path", ["."])[0],
            },
        )
    except (FileNotFoundError, ValueError) as e:
        return bad(handler, _sanitize_error(e), 404)


def _handle_session_stream_replay(handler, parsed):
    """SSE endpoint keyed by session_id (supports both main and subagent sessions)."""
    session_id = parse_qs(parsed.query).get("session_id", [""])[0]
    if not session_id:
        return j(handler, {"error": "session_id required"}, status=400)

    # Wait for stream creation (up to 30 seconds)
    stream = stream_manager.get_stream(session_id)
    if stream is None:
        for _ in range(300):
            time.sleep(0.1)
            stream = stream_manager.get_stream(session_id)
            if stream:
                break
    if stream is None:
        return j(handler, {"error": "no active stream for session"}, status=404)

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("X-Accel-Buffering", "no")
    handler.send_header("Connection", "keep-alive")
    handler.end_headers()

    sq = stream.subscribe()
    try:
        # Replay history
        history = stream.replay()
        if history:
            _sse(handler, 'replay_start', {'count': len(history)})
            for event, data in history:
                _sse(handler, event, data)
            _sse(handler, 'replay_end', {})

        # Live events
        while True:
            try:
                event, data = sq.get(timeout=30)
            except queue.Empty:
                handler.wfile.write(b": heartbeat\n\n")
                handler.wfile.flush()
                continue
            if event is None:  # sentinel from close()
                break
            _sse(handler, event, data)
            if event in ('stream_end', 'error', 'cancel', 'apperror'):
                break
    except (BrokenPipeError, ConnectionResetError):
        pass
    except Exception as e:
        # Handle SSL errors from abrupt client disconnects
        import ssl
        if not isinstance(e, ssl.SSLError):
            logger.debug('Chat stream SSE error: %s', e)
    finally:
        stream.unsubscribe(sq)
    return True


def _handle_session_notify(handler):
    """SSE endpoint for global session notifications (non-replayable).

    First event is subscribe_success with a timestamp. Frontend MUST call
    loadSessions after receiving subscribe_success to establish baseline
    state. The notify queue (size 1000) caches events during the gap,
    ensuring no events are lost.
    """
    from api.notify_stream import notify_stream

    handler.send_response(200)
    handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
    handler.send_header('Cache-Control', 'no-cache')
    handler.send_header('X-Accel-Buffering', 'no')
    handler.send_header('Connection', 'keep-alive')
    handler.end_headers()

    q = notify_stream.subscribe()
    try:
        _sse(handler, 'subscribe_success', {'ts': time.time()})
        while True:
            try:
                event, data = q.get(timeout=30)
            except queue.Empty:
                handler.wfile.write(b': heartbeat\n\n')
                handler.wfile.flush()
                continue
            if event is None:
                break
            _sse(handler, event, data)
    except (BrokenPipeError, ConnectionResetError):
        pass
    except Exception as e:
        # Handle SSL errors from abrupt client disconnects
        import ssl
        if not isinstance(e, ssl.SSLError):
            logger.debug('Notify SSE error: %s', e)
    finally:
        notify_stream.unsubscribe(q)
    return True


def _write_gateway_last_seen(sid: str, msg_count: int) -> None:
    """Write last_seen_msg_count to the agent's state.db for a gateway/CLI session.

    Gateway sessions live in the agent's state.db, not the WebUI session store.
    This is the only way to persist read-position metadata for those sessions.
    """
    import os as _os
    from pathlib import Path as _Path
    import sqlite3 as _sqlite3

    try:
        from api.profiles import get_active_hermes_home
        hermes_home = _Path(get_active_hermes_home()).expanduser().resolve()
    except Exception:
        hermes_home = _Path(_os.getenv("HERMES_HOME", str(_Path.home() / ".hermes"))).expanduser().resolve()
    db_path = hermes_home / "state.db"
    if not db_path.exists():
        return
    try:
        conn = _sqlite3.connect(str(db_path))
        # Only write if the column exists (older hermes-agent may not have it)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "last_seen_msg_count__webui" in cols:
            conn.execute(
                "UPDATE sessions SET last_seen_msg_count__webui = ? WHERE id = ?",
                (msg_count, sid),
            )
            conn.commit()
        conn.close()
    except Exception:
        pass  # best-effort — don't fail the HTTP response


def _content_disposition_value(disposition: str, filename: str) -> str:
    """Build a latin-1-safe Content-Disposition value with RFC 5987 filename*."""
    import urllib.parse as _up

    safe_name = Path(filename).name.replace("\r", "").replace("\n", "")
    ascii_fallback = "".join(
        ch if 32 <= ord(ch) < 127 and ch not in {'"', '\\'} else "_"
        for ch in safe_name
    ).strip(" .")
    if not ascii_fallback:
        suffix = Path(safe_name).suffix
        ascii_suffix = "".join(
            ch if 32 <= ord(ch) < 127 and ch not in {'"', '\\'} else "_"
            for ch in suffix
        )
        ascii_fallback = f"download{ascii_suffix}" if ascii_suffix else "download"
    quoted_name = _up.quote(safe_name, safe="")
    return (
        f'{disposition}; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quoted_name}"
    )


def _handle_media(handler, parsed):
    """Serve a local file by absolute path for inline display in the chat.

    Security:
    - Path must resolve to an allowed root (hermes home, /tmp, common dirs)
    - Auth-gated when auth is enabled
    - Only image MIME types are served inline; all others force download
    - SVG always served as attachment (XSS risk)
    - No path traversal: resolved path must stay within an allowed root
    """
    import os as _os
    from api.auth import is_auth_enabled, parse_cookie, verify_session
    _HOME = Path(_os.path.expanduser("~"))
    _HERMES_HOME = Path(_os.getenv("HERMES_HOME", str(_HOME / ".hermes"))).expanduser()

    # Auth check
    if is_auth_enabled():
        cv = parse_cookie(handler)
        if not (cv and verify_session(cv)):
            handler.send_response(401)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Connection", "close")
            handler.end_headers()
            handler.wfile.write(b'{"error":"Authentication required"}')
            return

    qs = parse_qs(parsed.query)
    raw_path = qs.get("path", [""])[0].strip()
    if not raw_path:
        return bad(handler, "path parameter required", 400)

    # Resolve the path and check it is within an allowed root
    try:
        target = Path(raw_path).resolve()
    except Exception:
        return bad(handler, "Invalid path", 400)

    # Allowed roots: hermes home, /tmp, and active workspace.
    # Intentionally NOT the entire home dir — that would expose ~/.ssh,
    # ~/.aws, browser profiles, etc. to any authenticated user.
    allowed_roots = [
        _HERMES_HOME.resolve(),
        Path("/tmp").resolve(),
        (_HOME / ".hermes").resolve(),
    ]
    # Also allow the active workspace directory (where screenshots land)
    try:
        from api.workspace import get_last_workspace
        ws = Path(get_last_workspace()).resolve()
        if ws.is_dir():
            allowed_roots.append(ws)
    except Exception:
        pass
    within_allowed = any(
        _os.path.commonpath([str(target), str(root)]) == str(root)
        for root in allowed_roots
        if root.exists()
    )
    if not within_allowed:
        return bad(handler, "Path not in allowed location", 403)

    if not target.exists() or not target.is_file():
        return j(handler, {"error": "not found"}, status=404)

    # Determine MIME type
    ext = target.suffix.lower()
    mime = MIME_MAP.get(ext, "application/octet-stream")

    # Only serve image types inline; everything else is a download
    _INLINE_IMAGE_TYPES = {
        "image/png", "image/jpeg", "image/gif", "image/webp",
        "image/x-icon", "image/bmp",
    }
    _DOWNLOAD_TYPES = {"image/svg+xml"}  # SVG: XSS risk, force download

    try:
        raw_bytes = target.read_bytes()
    except PermissionError:
        return bad(handler, "Permission denied", 403)
    except Exception:
        return bad(handler, "Could not read file", 500)

    handler.send_response(200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(len(raw_bytes)))
    handler.send_header("Cache-Control", "private, max-age=3600")
    handler.send_header("Connection", "close")
    _security_headers(handler)

    if mime in _DOWNLOAD_TYPES or mime not in _INLINE_IMAGE_TYPES:
        handler.send_header(
            "Content-Disposition",
            _content_disposition_value("attachment", target.name),
        )
    else:
        handler.send_header(
            "Content-Disposition",
            _content_disposition_value("inline", target.name),
        )

    handler.end_headers()
    handler.wfile.write(raw_bytes)


def _handle_file_raw(handler, parsed):
    qs = parse_qs(parsed.query)
    sid = qs.get("session_id", [""])[0]
    if not sid:
        return bad(handler, "session_id is required")
    try:
        s = get_session(sid)
    except KeyError:
        return bad(handler, "Session not found", 404)
    rel = qs.get("path", [""])[0]
    force_download = qs.get("download", [""])[0] == "1"
    target = safe_resolve(Path(s.workspace), rel)
    if not target.exists() or not target.is_file():
        return j(handler, {"error": "not found"}, status=404)
    ext = target.suffix.lower()
    mime = MIME_MAP.get(ext, "application/octet-stream")
    raw_bytes = target.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(len(raw_bytes)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Connection", "close")
    # Security: force download for dangerous MIME types to prevent XSS.
    # Exception: ?inline=1 permits text/html to be served inline for the
    # sandboxed workspace HTML preview iframe (sandbox="allow-scripts" with no
    # allow-same-origin, so the iframe cannot access parent cookies/storage).
    inline_preview = qs.get("inline", [""])[0] == "1"
    dangerous_types = {"text/html", "application/xhtml+xml", "image/svg+xml"}
    html_inline_ok = inline_preview and mime == "text/html"
    if force_download or (mime in dangerous_types and not html_inline_ok):
        handler.send_header(
            "Content-Disposition",
            _content_disposition_value("attachment", target.name),
        )
    else:
        handler.send_header(
            "Content-Disposition",
            _content_disposition_value("inline", target.name),
        )
    # Defense-in-depth for ?inline=1 HTML: even though the workspace.js iframe
    # sets sandbox="allow-scripts", a user could be tricked into opening the
    # ?inline=1 URL directly in a top-level tab (e.g. via a chat link), which
    # would render the HTML in the WebUI's origin without iframe sandbox. The
    # CSP sandbox directive applies the same isolation server-side: without
    # allow-same-origin, the document is treated as a unique opaque origin and
    # cannot read WebUI cookies, localStorage, or postMessage to the parent.
    if html_inline_ok:
        # Match the iframe sandbox="allow-scripts" exactly: scripts allowed,
        # but no allow-same-origin → unique opaque origin (no cookie/storage
        # access even when accessed via direct URL outside the iframe).
        handler.send_header("Content-Security-Policy", "sandbox allow-scripts")
    handler.end_headers()
    handler.wfile.write(raw_bytes)
    return True


def _handle_file_read(handler, parsed):
    qs = parse_qs(parsed.query)
    sid = qs.get("session_id", [""])[0]
    if not sid:
        return bad(handler, "session_id is required")
    try:
        s = get_session(sid)
    except KeyError:
        return bad(handler, "Session not found", 404)
    rel = qs.get("path", [""])[0]
    if not rel:
        return bad(handler, "path is required")
    try:
        return j(handler, read_file_content(Path(s.workspace), rel))
    except (FileNotFoundError, ValueError) as e:
        return bad(handler, _sanitize_error(e), 404)


def _handle_approval_pending(handler, parsed):
    sid = parse_qs(parsed.query).get("session_id", [""])[0]
    with _lock:
        queue = _pending.get(sid)
        # Support both the new list format and a legacy single-dict value.
        if isinstance(queue, list):
            p = queue[0] if queue else None
            total = len(queue)
        elif queue:
            p = queue
            total = 1
        else:
            p = None
            total = 0
    if p:
        return j(handler, {"pending": dict(p), "pending_count": total})
    return j(handler, {"pending": None, "pending_count": 0})


def _handle_approval_inject(handler, parsed):
    """Inject a fake pending approval -- loopback-only, used by automated tests."""
    qs = parse_qs(parsed.query)
    sid = qs.get("session_id", [""])[0]
    key = qs.get("pattern_key", ["test_pattern"])[0]
    cmd = qs.get("command", ["rm -rf /tmp/test"])[0]
    if sid:
        submit_pending(
            sid,
            {
                "command": cmd,
                "pattern_key": key,
                "pattern_keys": [key],
                "description": "test pattern",
            },
        )
        return j(handler, {"ok": True, "session_id": sid})
    return j(handler, {"error": "session_id required"}, status=400)


def _handle_clarify_pending(handler, parsed):
    sid = parse_qs(parsed.query).get("session_id", [""])[0]
    pending = get_clarify_pending(sid)
    if pending:
        return j(handler, {"pending": pending})
    return j(handler, {"pending": None})


def _handle_clarify_inject(handler, parsed):
    """Inject a fake pending clarify prompt -- loopback-only, used by automated tests."""
    qs = parse_qs(parsed.query)
    sid = qs.get("session_id", [""])[0]
    question = qs.get("question", ["Which option?"])[0]
    choices = qs.get("choices", [])
    if sid:
        submit_clarify_pending(
            sid,
            {
                "question": question,
                "choices_offered": choices,
                "session_id": sid,
                "kind": "clarify",
            },
        )
        return j(handler, {"ok": True, "session_id": sid})
    return j(handler, {"error": "session_id required"}, status=400)


def _handle_live_models(handler, parsed):
    """Return the live model list for a provider.

    Delegates to the agent's provider_model_ids() which handles:
    - OpenRouter: live fetch from /api/v1/models
    - Anthropic: live fetch from /v1/models (API key or OAuth token)
    - Copilot: live fetch from api.githubcopilot.com/models with correct headers
    - openai-codex: Codex OAuth endpoint + local ~/.codex/ cache fallback
    - Nous: live fetch from inference-api.nousresearch.com/v1/models
    - DeepSeek, kimi-coding, opencode-zen/go, custom: generic OpenAI-compat /v1/models
    - ZAI, MiniMax, Google/Gemini: fall back to static list (non-standard endpoints)
    - All others: static _PROVIDER_MODELS fallback

    The agent already maintains all provider-specific auth and endpoint logic
    in one place; the WebUI inherits it rather than duplicating it.

    Query params:
        provider  (optional) — provider ID; defaults to active profile provider
    """
    qs = parse_qs(parsed.query)
    provider = (qs.get("provider", [""])[0] or "").lower().strip()

    try:
        from api.config import get_config as _gc
        cfg = _gc()
        if not provider:
            provider = cfg.get("model", {}).get("provider") or ""
        if not provider:
            return j(handler, {"error": "no_provider", "models": []})

        # Normalize provider alias so 'z.ai' -> 'zai', 'x.ai' -> 'xai', etc.
        # The browser sends whatever active_provider the static endpoint returned;
        # without normalization, provider_model_ids() misses the alias and returns [].
        # Uses the WebUI-owned table (api/config._resolve_provider_alias) which
        # works even when hermes_cli is not on sys.path.
        from api.config import _resolve_provider_alias
        provider = _resolve_provider_alias(provider)

        # Delegate to the agent's live-fetch + fallback resolver.
        # provider_model_ids() tries live endpoints first and falls back to
        # the static _PROVIDER_MODELS list — it never raises.
        try:
            import sys as _sys
            import os as _os
            _agent_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                                       "..", "..", ".hermes", "hermes-agent")
            _agent_dir = _os.path.normpath(_agent_dir)
            if _agent_dir not in _sys.path:
                _sys.path.insert(0, _agent_dir)
            from hermes_cli.models import provider_model_ids as _pmi
            ids = _pmi(provider)
        except Exception as _import_err:
            logger.debug("provider_model_ids import failed for %s: %s", provider, _import_err)
            ids = []

        if not ids:
            # For 'custom' provider, provider_model_ids() returns [] because
            # 'custom' isn't a real endpoint.  Fall back to the custom_providers
            # entries from config.yaml so the live-model enrichment step can
            # add any models that weren't already in the static list.
            if provider == "custom":
                try:
                    _cp_entries = cfg.get("custom_providers", [])
                    if isinstance(_cp_entries, list):
                        ids = [
                            _cp.get("model", "")
                            for _cp in _cp_entries
                            if isinstance(_cp, dict) and _cp.get("model", "")
                        ]
                except Exception:
                    pass

        # ── OpenAI-compat live fetch fallback ──────────────────────────────────
        # When provider_model_ids() is unavailable or returns [] for a provider
        # that exposes a standard /v1/models endpoint, fetch directly.  This
        # eliminates the need to keep _PROVIDER_MODELS in sync for providers
        # that have a discoverable API (#871).
        #
        # WARNING: This uses synchronous urllib.request which blocks the worker
        # thread for up to 8 seconds on timeout. This is acceptable because:
        #  (a) the server uses threading (not async), so other requests continue;
        #  (b) the frontend shows the static list immediately and enriches in
        #      the background via _fetchLiveModels(), so the user never waits.
        if not ids:
            _ep = _OPENAI_COMPAT_ENDPOINTS.get(provider)
            if _ep:
                try:
                    import urllib.request
                    _providers_cfg = cfg.get("providers", {})
                    _prov = _providers_cfg.get(provider, {}) if isinstance(_providers_cfg, dict) else {}
                    # Only use provider-scoped key — never fall back to a top-level
                    # api_key which may belong to a different provider.
                    _key = _prov.get("api_key") if isinstance(_prov, dict) else None
                    if not _key:
                        _key = cfg.get("model", {}).get("api_key")
                    if _key:
                        _req = urllib.request.Request(
                            f"{_ep}/models",
                            headers={"Authorization": f"Bearer {_key}"},
                        )
                        with urllib.request.urlopen(_req, timeout=8) as _resp:
                            _body = json.loads(_resp.read())
                        ids = [m.get("id", "") for m in _body.get("data", []) if m.get("id")]
                        logger.debug("Live-fetched %d models from %s /v1/models", len(ids), provider)
                except Exception as _fetch_err:
                    logger.debug("Live fetch from %s failed: %s", provider, _fetch_err)
                    # Fall through to static list below

        # Static fallback — only reached when live fetch also failed.
        if not ids:
            from api.config import get_provider_card_models as _gcm
            ids = [m["id"] for m in _gcm(provider)]
        if not ids:
            return j(handler, {"provider": provider, "models": [], "count": 0})

        # Normalise to {id, label} — provider_model_ids() returns plain string IDs.
        # For ollama-cloud use the shared Ollama formatter (handles `:variant` suffix).
        # For all other providers use a simpler hyphen-split capitaliser.
        from api.config import _format_ollama_label as _fmt_ollama

        def _make_label(mid):
            """Best-effort human label from a model ID string."""
            if provider in ("ollama", "ollama-cloud"):
                return _fmt_ollama(mid)
            # Preserve slashes for router IDs like "anthropic/claude-sonnet-4.6"
            display = mid.split("/")[-1] if "/" in mid else mid
            parts = display.split("-")
            result = []
            for p in parts:
                pl = p.lower()
                if pl == "gpt":
                    result.append("GPT")
                elif pl in ("claude", "gemini", "gemma", "llama", "mistral",
                            "qwen", "deepseek", "grok", "kimi", "glm"):
                    result.append(p.capitalize())
                elif p[:1].isdigit():
                    result.append(p)  # version numbers: 5.4, 3.5, 4.6 — unchanged
                else:
                    result.append(p.capitalize())
            label = " ".join(result)
            # Restore well-known uppercase tokens that title-casing breaks
            for orig in ("GPT", "GLM", "API", "AI", "XL", "MoE"):
                label = label.replace(orig.title(), orig)
            return label

        models_out = [{"id": mid, "label": _make_label(mid)} for mid in ids if mid]
        return j(handler, {"provider": provider, "models": models_out,
                           "count": len(models_out)})

    except Exception as _e:
        logger.debug("_handle_live_models failed for %s: %s", provider, _e)
        return j(handler, {"error": str(_e), "models": []})


_CRON_OUTPUT_TS_PATTERNS = (
    # New format: 2026-07-28_11-20-38.md
    _re.compile(r"(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})"),
    # Legacy format: 20260728_112038.md
    _re.compile(r"(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})"),
    # Unix timestamp prefix: 1690500000.md or 1690500000123.md
    _re.compile(r"^(\d{10,13})"),
)


def _cron_output_timestamp(filename: str):
    """Best-effort parse of a cron output filename into a (dt, filename_sort_key)
    pair used for latest-first ordering. Returns None when no pattern matches.
    """
    for pat in _CRON_OUTPUT_TS_PATTERNS[:2]:
        m = pat.search(filename)
        if m:
            try:
                y, mo, d, h, mi, s = (int(g) for g in m.groups())
                return datetime(y, mo, d, h, mi, s)
            except ValueError:
                continue
    m = _CRON_OUTPUT_TS_PATTERNS[2].match(filename)
    if m:
        ts = int(m.group(1))
        return datetime.fromtimestamp(ts / 1000 if ts > 1e12 else ts)
    return None


def _handle_cron_output(handler, parsed):
    from cron.jobs import OUTPUT_DIR as CRON_OUT

    qs = parse_qs(parsed.query)
    job_id = qs.get("job_id", [""])[0]
    limit = int(qs.get("limit", ["5"])[0])
    if not job_id:
        return j(handler, {"error": "job_id required"}, status=400)
    out_dir = CRON_OUT / job_id
    outputs = []
    if out_dir.exists():
        # Sort by parsed timestamp (latest first) so mixed legacy + new format
        # filenames interleave correctly. Fall back to filename string order
        # when neither pattern matches.
        files = sorted(
            out_dir.glob("*.md"),
            key=lambda f: (_cron_output_timestamp(f.name), f.name),
            reverse=True,
        )[:limit]
        for f in files:
            try:
                txt = f.read_text(encoding="utf-8", errors="replace")
                outputs.append({"filename": f.name, "content": txt[:8000]})
            except Exception:
                logger.debug("Failed to read cron output file %s", f)
    return j(handler, {"job_id": job_id, "outputs": outputs})


def _handle_cron_recent(handler, parsed):
    """Return recent terminal cron executions newer than `since` (Unix seconds).

    `since` is a Unix timestamp (float, seconds). `claimed_at` is stored as
    ISO-8601 in the SDK; we parse each row to a datetime and compare on
    `.timestamp()` so the comparison is timezone-safe regardless of the
    offset embedded in the stored string.
    """
    from cron.executions import list_executions
    from datetime import datetime

    qs = parse_qs(parsed.query)
    since_raw = qs.get("since", ["0"])[0] or "0"
    limit_raw = qs.get("limit", ["100"])[0] or "100"
    try:
        since = float(since_raw)
    except ValueError:
        return bad(handler, "since must be a numeric Unix timestamp")
    try:
        limit = int(limit_raw)
    except ValueError:
        return bad(handler, "limit must be an integer")
    # SDK clamps at 500 internally; mirror that ceiling here.
    if limit < 1 or limit > 500:
        return bad(handler, "limit must be between 1 and 500")

    # ISO timestamps can carry a +HH:MM offset that breaks lexicographic
    # compare across DST/profile boundaries; parse per row for safety.
    terminal = ("completed", "failed", "unknown")

    def _claimed_ts(rec):
        return datetime.fromisoformat(rec["claimed_at"]).timestamp()

    completions = [
        r
        for r in list_executions(limit=limit)
        if r.get("status") in terminal and _claimed_ts(r) >= since
    ]
    return j(handler, {"completions": completions, "since": since})


def _handle_memory_read(handler):
    try:
        from api.profiles import get_active_hermes_home

        mem_dir = get_active_hermes_home() / "memories"
    except ImportError:
        mem_dir = Path.home() / ".hermes" / "memories"
    mem_file = mem_dir / "MEMORY.md"
    user_file = mem_dir / "USER.md"
    memory = (
        mem_file.read_text(encoding="utf-8", errors="replace")
        if mem_file.exists()
        else ""
    )
    user = (
        user_file.read_text(encoding="utf-8", errors="replace")
        if user_file.exists()
        else ""
    )
    return j(
        handler,
        {
            "memory": _redact_text(memory),
            "user": _redact_text(user),
            "memory_path": str(mem_file),
            "user_path": str(user_file),
            "memory_mtime": mem_file.stat().st_mtime if mem_file.exists() else None,
            "user_mtime": user_file.stat().st_mtime if user_file.exists() else None,
        },
    )


# ── POST route helpers ────────────────────────────────────────────────────────


def _handle_session_cleanup(handler, body):
    """POST /api/sessions/cleanup — delete ended sessions older than threshold.

    Supports:
    - source="webui": clean WebUI session JSON files from SESSION_DIR (skips pinned)
    - source=<gateway>: clean gateway sessions from state.db by source tag
    """
    source = str(body.get("source", "")).strip()
    if not source:
        return bad(handler, "source is required", 400)

    older_than_days = int(body.get("older_than_days", 30))
    if older_than_days < 1:
        return bad(handler, "older_than_days must be >= 1", 400)

    cutoff = time.time() - (older_than_days * 86400)
    deleted_count = 0

    if source == "webui":
        # ── WebUI sessions: iterate JSON files in SESSION_DIR ──
        for p in SESSION_DIR.glob("*.json"):
            if p.name.startswith("_"):
                continue
            try:
                s = Session.load(p.stem)
                if s is None:
                    continue
                # Skip pinned sessions — they are never cleaned
                if s.pinned:
                    continue
                # Only delete if the session hasn't been updated in <older_than_days>
                if s.updated_at < cutoff:
                    with LOCK:
                        SESSIONS.pop(p.stem, None)
                    p.unlink(missing_ok=True)
                    deleted_count += 1
            except Exception:
                logger.debug("Failed to clean up session file %s", p)
        if SESSION_INDEX_FILE.exists():
            SESSION_INDEX_FILE.unlink(missing_ok=True)
        return j(handler, {"deleted_count": deleted_count, "source": source, "older_than_days": older_than_days})

    # ── Gateway sessions: delete from state.db ──
    import sqlite3
    try:
        from api.profiles import get_active_hermes_home
        hermes_home = Path(get_active_hermes_home()).expanduser().resolve()
    except Exception:
        hermes_home = Path(os.environ.get('HERMES_HOME', str(HOME / '.hermes'))).expanduser().resolve()

    db_path = hermes_home / 'state.db'
    if not db_path.exists():
        return j(handler, {"deleted_count": 0, "source": source, "older_than_days": older_than_days})

    try:
        with sqlite3.connect(str(db_path)) as conn:
            cur = conn.cursor()

            if source == "all":
                cur.execute(
                    "SELECT id FROM sessions WHERE ended_at IS NOT NULL AND ended_at < ?",
                    (cutoff,),
                )
            elif source == "cron":
                cur.execute(
                    "SELECT id FROM sessions WHERE source = 'cron' AND ended_at IS NOT NULL AND ended_at < ?",
                    (cutoff,),
                )
            elif source == "message":
                # Clean up all messaging platform sessions
                from api.models import MESSAGING_SOURCE_TAGS
                placeholders = ", ".join("?" for _ in MESSAGING_SOURCE_TAGS)
                cur.execute(
                    f"SELECT id FROM sessions WHERE source IN ({placeholders}) AND ended_at IS NOT NULL AND ended_at < ?",
                    list(MESSAGING_SOURCE_TAGS) + [cutoff],
                )
            else:
                cur.execute(
                    "SELECT id FROM sessions WHERE source = ? AND ended_at IS NOT NULL AND ended_at < ?",
                    (source, cutoff),
                )

            to_delete = [row[0] for row in cur.fetchall()]
            for sid in to_delete:
                cur.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
            if source == "all":
                cur.execute(
                    "DELETE FROM sessions WHERE ended_at IS NOT NULL AND ended_at < ?",
                    (cutoff,),
                )
            elif source == "cron":
                cur.execute(
                    "DELETE FROM sessions WHERE source = 'cron' AND ended_at IS NOT NULL AND ended_at < ?",
                    (cutoff,),
                )
            elif source == "message":
                from api.models import MESSAGING_SOURCE_TAGS
                placeholders = ", ".join("?" for _ in MESSAGING_SOURCE_TAGS)
                cur.execute(
                    f"DELETE FROM sessions WHERE source IN ({placeholders}) AND ended_at IS NOT NULL AND ended_at < ?",
                    list(MESSAGING_SOURCE_TAGS) + [cutoff],
                )
            else:
                cur.execute(
                    "DELETE FROM sessions WHERE source = ? AND ended_at IS NOT NULL AND ended_at < ?",
                    (source, cutoff),
                )
            conn.commit()
            deleted_count = len(to_delete)
    except Exception as e:
        return j(handler, {"error": _sanitize_error(e)}, status=500)

    return j(handler, {"deleted_count": deleted_count, "source": source, "older_than_days": older_than_days})


def _handle_btw(handler, body):
    """POST /api/btw — ephemeral side question using session context.

    Creates a temporary hidden session, streams the answer via SSE, then
    discards the session. The parent session is not modified.
    """
    try:
        require(body, "session_id")
        require(body, "question")
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session(body["session_id"])
    except KeyError:
        return bad(handler, "Session not found", 404)
    question = str(body["question"]).strip()
    if not question:
        return bad(handler, "question is required")
    # Duplicate-stream guard (same pattern as chat/start)
    current_stream_id = stream_manager.get_stream_id(sid)
    if current_stream_id:
        if stream_manager.get_stream_by_id(current_stream_id) is not None:
            return j(handler, {"error": "session already has an active stream"}, status=409)
    # Create ephemeral hidden session inheriting context
    from api.models import new_session as _new_session
    ephemeral = _new_session(workspace=s.workspace, model=s.model, profile=getattr(s, 'profile', None))
    # Copy conversation history for context (agent reads from messages)
    ephemeral.messages = list(s.messages or [])
    ephemeral.title = f"btw: {question[:60]}"
    ephemeral.sync_webui_metadata()
    stream_id = stream_manager.begin_stream(ephemeral)
    if not stream_id:
        return j(handler, {"error": "session already has active stream"}, status=409)
    ephemeral.sync_webui_metadata()
    from api.background import track_btw
    track_btw(body["session_id"], ephemeral.session_id, stream_id, question)
    thr = threading.Thread(
        target=_run_agent_streaming,
        args=(ephemeral.session_id, question, s.model, s.workspace, stream_id, None),
        kwargs={"ephemeral": True},
        daemon=True,
    )
    thr.start()
    return j(handler, {"stream_id": stream_id, "session_id": ephemeral.session_id, "parent_session_id": body["session_id"]})


def _start_background_task(parent_session, prompt, keep_session=True):
    """Create and start a background agent session.

    If *keep_session* is True (WebUI), the session file persists on disk so
    it appears in the sidebar.  If False, it is deleted after completion
    (existing direct-API behaviour).
    """
    from api.models import new_session as _new_session

    bg = _new_session(
        workspace=parent_session.workspace,
        model=parent_session.model,
        profile=getattr(parent_session, 'profile', None),
    )
    bg.title = f"bg: {prompt[:60]}"
    bg.pending_user_message = prompt
    bg._pending_user_message_ts = time.time()
    bg.sync_webui_metadata()
    stream_id = stream_manager.begin_stream(bg)
    if not stream_id:
        return None
    bg.sync_webui_metadata()
    task_id = uuid.uuid4().hex[:8]
    parent_sid = parent_session.session_id
    bg_sid = bg.session_id
    from api.background import track_background, complete_background
    track_background(parent_sid, bg_sid, stream_id, task_id, prompt)

    def _run_bg_and_notify():
        """Run the background agent, then mark the tracked task ``done`` with the
        last assistant reply so ``/api/background/status`` can surface it."""
        try:
            _run_agent_streaming(bg_sid, prompt, parent_session.model,
                                parent_session.workspace, stream_id, None)
            # Reload the bg session from disk and extract the final assistant reply.
            try:
                from api.models import Session as _Session
                reloaded = _Session.load(bg_sid)
                _answer = ""
                for _m in reversed((reloaded.messages if reloaded else None) or []):
                    if not isinstance(_m, dict) or _m.get("role") != "assistant":
                        continue
                    if _m.get("_error"):
                        continue
                    _content = str(_m.get("content") or "").strip()
                    if _content:
                        _answer = _content
                        break
                complete_background(parent_sid, task_id, _answer or "(no answer produced)")
            except Exception:
                complete_background(parent_sid, task_id, "(background task failed)")
            if not keep_session:
                try:
                    (SESSION_DIR / f"{bg_sid}.json").unlink(missing_ok=True)
                except Exception:
                    pass
        except Exception:
            try:
                complete_background(parent_sid, task_id, "(background task failed)")
            except Exception:
                pass

    thr = threading.Thread(target=_run_bg_and_notify, daemon=True)
    thr.start()
    return {
        "task_id": task_id,
        "stream_id": stream_id,
        "session_id": bg_sid,
        "title": bg.title,
        "created_at": getattr(bg, "created_at", None) or time.time(),
        "workspace": bg.workspace,
    }


def _handle_background(handler, body):
    """POST /api/background — run prompt in parallel background agent.

    Creates a hidden session, starts streaming in a daemon thread.
    Frontend polls /api/background/status for completed results.
    The session file is deleted after completion (keep_session=False).
    """
    try:
        require(body, "session_id")
        require(body, "prompt")
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session(body["session_id"])
    except KeyError:
        return bad(handler, "Session not found", 404)
    prompt = str(body["prompt"]).strip()
    if not prompt:
        return bad(handler, "prompt is required")
    result = _start_background_task(s, prompt, keep_session=False)
    return j(handler, result)


def _handle_queue(handler, parsed):
    """Handle GET/DELETE for session message queue.

    GET  /api/chat/queue?session_id=...          — return queue status.
    GET  /api/chat/queue?session_id=...&action=clear — clear the queue.
    """
    from api.config import SESSION_MESSAGE_QUEUE, SESSION_MESSAGE_QUEUE_LOCK
    qs = parse_qs(parsed.query)
    session_id = qs.get("session_id", [""])[0]
    if not session_id:
        return bad(handler, "session_id required")

    if qs.get("action", [""])[0] == "clear":
        with SESSION_MESSAGE_QUEUE_LOCK:
            q = SESSION_MESSAGE_QUEUE.pop(session_id, None)
        removed = len(q) if q else 0
        return j(handler, {"ok": True, "removed": removed})

    # Default: return queue status
    with SESSION_MESSAGE_QUEUE_LOCK:
        q = SESSION_MESSAGE_QUEUE.get(session_id)
        items = []
        if q:
            for i, payload in enumerate(q):
                items.append({
                    "position": i + 1,
                    "message": payload.get("message", ""),
                    "attachments": payload.get("attachments", []),
                })
    return j(handler, {"queue_length": len(items), "items": items})


def _resolve_busy_handling(s, mode, body):
    """Resolve what to do when a session may already be busy.

    Returns (result, stream_id_or_none) where result is one of:
      - "proceed"     — no active stream, send normally
      - "reject"      — active stream, reject (mode=reject)
      - "interrupted" — cancelled, proceed to send
      - "queued"      — message enqueued for later
      - "steered"     — steer injected into running agent
    """
    current_stream_id = stream_manager.get_stream_id(s.session_id)
    if current_stream_id:
        # Check if a cancel is in progress for this session.  If so,
        # spin-wait for it to complete instead of returning a 409.
        _cancel_done = _CANCEL_EVENTS.get(s.session_id)
        if _cancel_done is not None:
            if _cancel_done.wait(timeout=3.0):
                # Cancel completed — reload session to pick up cleaned state
                s = get_session(s.session_id)
                return ("proceed", None)
            # Timeout — fall through to normal busy handling below

        current_active = stream_manager.get_stream_by_id(current_stream_id) is not None
        if not current_active:
            # Stale stream id — proceed
            return ("proceed", None)
    else:
        # No active stream
        return ("proceed", None)

    # ── Active stream exists ──
    if mode == "reject":
        return ("reject", current_stream_id)

    if mode == "interrupt":
        from api.streaming import cancel_stream
        cancel_stream(current_stream_id)
        return ("interrupted", None)

    if mode == "queue":
        from api.config import SESSION_MESSAGE_QUEUE, SESSION_MESSAGE_QUEUE_LOCK, _MAX_QUEUE_LENGTH
        payload = {
            "message": str(body.get("message", "")).strip(),
            "model": body.get("model") or s.model,
            "workspace": str(body.get("workspace") or s.workspace),
            "attachments": [str(a) for a in (body.get("attachments") or [])][:20],
        }
        with SESSION_MESSAGE_QUEUE_LOCK:
            q = SESSION_MESSAGE_QUEUE.get(s.session_id)
            if q is None:
                q = []
                SESSION_MESSAGE_QUEUE[s.session_id] = q
            if len(q) >= _MAX_QUEUE_LENGTH:
                return ("reject", None)  # caller returns 429
            q.append(payload)
            queue_position = len(q)   # 1-based position
            queue_length = len(q)
        return ("queued", {"queue_position": queue_position, "queue_length": queue_length})

    if mode == "steer":
        steer_text = str(body.get("message", "")).strip()
        if not steer_text:
            return ("reject", None)  # caller returns 400
        if len(steer_text) > 4000:
            return ("reject", None)  # caller returns 400
        from api.streaming import _try_steer
        steer_result = _try_steer(s.session_id, steer_text)
        if steer_result.get("accepted"):
            return ("steered", steer_result.get("stream_id"))
        # Steer failed — fall back to queue
        s._last_steer_fallback = steer_result.get("fallback", "unknown")
        from api.config import SESSION_MESSAGE_QUEUE, SESSION_MESSAGE_QUEUE_LOCK, _MAX_QUEUE_LENGTH
        payload = {
            "message": str(body.get("message", "")).strip(),
            "model": body.get("model") or s.model,
            "workspace": str(body.get("workspace") or s.workspace),
            "attachments": [str(a) for a in (body.get("attachments") or [])][:20],
        }
        with SESSION_MESSAGE_QUEUE_LOCK:
            q = SESSION_MESSAGE_QUEUE.get(s.session_id)
            if q is None:
                q = []
                SESSION_MESSAGE_QUEUE[s.session_id] = q
            if len(q) >= _MAX_QUEUE_LENGTH:
                return ("reject", None)
            q.append(payload)
            queue_position = len(q)
            queue_length = len(q)
        return ("queued", {"queue_position": queue_position, "queue_length": queue_length})

    return ("proceed", None)  # fallback


def _handle_chat_dispatch(handler, body):
    """Dispatch a slash command. Returns direct response or proceed instruction.

    This endpoint determines the command type without starting a stream.
    For direct commands (e.g. /help, /model, /clear), the response is returned
    immediately.  For streaming commands (agent, force_mode), a proceed
    instruction with the transformed message is returned so the frontend can
    add the user message to the store before calling /api/chat/start.
    """
    try:
        require(body, "session_id")
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session(body["session_id"])
    except KeyError:
        return bad(handler, "Session not found", 404)
    msg = str(body.get("message", "")).strip()
    if not msg:
        return bad(handler, "message is required")

    if not msg.startswith("/"):
        return j(handler, {"action": "proceed", "message": msg})

    from api.slash_commands import dispatch_command
    try:
        result = dispatch_command(msg, s)
    except Exception as _dc_err:
        logger.debug("dispatch_command failed: %s", _dc_err, exc_info=True)
        # dispatch failed — fall through to start with raw message
        return j(handler, {"action": "proceed", "message": msg})

    if result is None:
        return j(handler, {"action": "proceed", "message": msg})

    if result.get("type") == "direct":
        resp = result["response"]
        return j(handler, {
            "action": "direct",
            "content": resp.get("content", ""),
            "session_id": s.session_id,
            "session_update": resp.get("session_update", {}),
        })

    if result.get("type") == "agent":
        return j(handler, {
            "action": "proceed",
            "message": result["transformed_msg"],
            "original_msg": msg,
        })

    if result.get("type") == "force_mode":
        return j(handler, {
            "action": "proceed",
            "message": result["prompt"],
            "mode": result["mode"],
            "original_msg": msg,
        })

    return j(handler, {"action": "proceed", "message": msg})


def _handle_chat_start(handler, body):
    try:
        require(body, "session_id")
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session(body["session_id"])
    except KeyError:
        # Welcome-state first send: client uses a local placeholder session_id.
        # Backend mints its own session_id here so state.db and SESSIONS stay
        # consistent (no in-memory rename after the row is written).
        # defer_notify=True — skip the upsert_session notify here because
        # begin_stream() below fires its own closely-related notify. Saves
        # one notify IPC round-trip on the welcome-state critical path.
        s = new_session(
            workspace=(str(body.get("workspace")) if body.get("workspace") else None),
            model=body.get("model"),
            profile=body.get("profile") or None,
            defer_notify=True,
        )
    msg = str(body.get("message", "")).strip()
    if not msg:
        return bad(handler, "message is required")

    # Accept original_msg from frontend (set by dispatch for slash commands)
    _original_msg = body.get("original_msg")
    if _original_msg:
        s._pending_original_message = _original_msg

    # NOTE: Slash-command dispatch is now handled by /api/chat/dispatch.
    # By the time we reach here, the message is already the transformed
    # prompt (for agent/force_mode commands) or a plain user message.

    attachments = [str(a) for a in (body.get("attachments") or [])][:20]
    try:
        workspace = str(resolve_trusted_workspace(body.get("workspace") or s.workspace))
    except ValueError as e:
        return bad(handler, str(e))
    requested_model = body.get("model") or s.model
    model, normalized_model = _resolve_compatible_session_model(requested_model)
    # Normalize bare model names to @provider:model for unique identification
    model = Session._normalize_model(model)
    # Persist normalized model so drain/replay paths resolve the provider
    # correctly. The full sync at the end of this handler (line ~4175)
    # persists model + pending state together, so no extra sync here.
    if model and s.model != model:
        s.model = model

    # ── Busy input mode ──
    _valid_modes = frozenset({"interrupt", "queue", "steer", "reject"})
    mode = str(body.get("mode") or "").strip()
    if not mode:
        mode = str(load_settings().get("busy_input_mode", "interrupt")).strip()
    if mode not in _valid_modes:
        return bad(handler, f"invalid mode: {mode}. Must be one of: {', '.join(sorted(_valid_modes))}")
    result, result_stream_id = _resolve_busy_handling(s, mode, body)
    if result == "reject":
        if result_stream_id:
            return j(
                handler,
                {"error": "session already has an active stream", "active_stream_id": result_stream_id},
                status=409,
            )
        # steer validation errors (empty text, >4000 chars) or queue overflow
        return bad(handler, "message invalid or queue full", status=400)
    if result == "queued":
        resp = {"queued": True, "session_id": s.session_id}
        if isinstance(result_stream_id, dict):
            resp.update(result_stream_id)
        # If steer fell back to queue, tell the frontend why
        _steer_fb = getattr(s, "_last_steer_fallback", None)
        if _steer_fb:
            resp["steer_fallback"] = _steer_fb
            s._last_steer_fallback = None
        return j(handler, resp)
    if result == "steered":
        resp = {"steered": True, "stream_id": result_stream_id, "session_id": s.session_id}
        _steer_fb = getattr(s, "_last_steer_fallback", None)
        if _steer_fb:
            resp["steer_fallback"] = _steer_fb
            s._last_steer_fallback = None
        return j(handler, resp)
    # Auto-compress is now handled in _run_agent_streaming (pre-flight,
    # after SSE connection is established) so the frontend receives a
    # `compressing` event and can show a toast instead of a silent pause.

    with _get_session_agent_lock(s.session_id):
        s.workspace = workspace
        s.model = model
        s.pending_user_message = msg
        s._pending_user_message_ts = time.time()
        s.pending_attachments = attachments
        s.pending_started_at = time.time()
        s.sync_webui_metadata()
    stream_id = stream_manager.begin_stream(s)
    if not stream_id:
        return j(handler, {"error": "session already has active stream"}, status=409)
    thr = threading.Thread(
        target=_run_agent_streaming,
        args=(s.session_id, msg, model, workspace, stream_id, attachments),
        daemon=True,
    )
    thr.start()
    response = {"stream_id": stream_id, "session_id": s.session_id}
    if normalized_model:
        response["effective_model"] = model
    # B.1 — Embed the new (or existing) session in the response so the
    # frontend can promote its placeholder in a single round-trip instead
    # of GETting /api/session after this POST.  Matches the payload shape
    # of GET /api/session so the frontend reuses promotePendingToReal.
    _raw_session = s.compact() | {
        "messages": s.messages or [],
        "tool_calls": getattr(s, "tool_calls", []) or [],
        "active_stream_id": stream_id,
        "pending_user_message": msg,
        "pending_original_message": getattr(s, "_pending_original_message", None),
        "pending_attachments": attachments,
        "pending_started_at": s.pending_started_at,
    }
    response["session"] = redact_session_data(_raw_session)
    return j(handler, response)


def _handle_chat_sync(handler, body):
    """Fallback synchronous chat endpoint (POST /api/chat). Not used by frontend."""
    s = get_session(body["session_id"])
    msg = str(body.get("message", "")).strip()
    if not msg:
        return j(handler, {"error": "empty message"}, status=400)
    try:
        workspace = str(resolve_trusted_workspace(body.get("workspace") or s.workspace))
    except ValueError as e:
        return bad(handler, str(e))
    with _get_session_agent_lock(s.session_id):
        s.workspace = workspace
        s.model = Session._normalize_model(body.get("model") or s.model)
    from api.streaming import _ENV_LOCK

    # Register thread-local env overrides so subprocesses spawned by the agent
    # inherit the correct session key / cwd (see _IsolatedPopen in api/config.py).
    _set_thread_env(
        TERMINAL_CWD=str(workspace),
        HERMES_EXEC_ASK='1',
        HERMES_SESSION_KEY=s.session_id,
    )

    with _ENV_LOCK:
        old_cwd = os.environ.get("TERMINAL_CWD")
        os.environ["TERMINAL_CWD"] = str(workspace)
        old_exec_ask = os.environ.get("HERMES_EXEC_ASK")
        old_session_key = os.environ.get("HERMES_SESSION_KEY")
        os.environ["HERMES_EXEC_ASK"] = "1"
        os.environ["HERMES_SESSION_KEY"] = s.session_id
    try:
        from run_agent import AIAgent

        with CHAT_LOCK:
            from api.config import resolve_model_provider

            _model, _provider, _base_url = resolve_model_provider(s.model)
            # Resolve API key via Hermes runtime provider (matches gateway behaviour)
            _api_key = None
            try:
                from hermes_cli.runtime_provider import resolve_runtime_provider

                _rt = resolve_runtime_provider(requested=_provider)
                _api_key = _rt.get("api_key")
                # Also use runtime provider/base_url if the webui config didn't resolve them
                if not _provider:
                    _provider = _rt.get("provider")
                if not _base_url:
                    _base_url = _rt.get("base_url")
            except Exception as _e:
                print(
                    f"[webui] WARNING: resolve_runtime_provider failed: {_e}",
                    flush=True,
                )
            agent = AIAgent(
                model=_model,
                provider=_provider,
                base_url=_base_url,
                api_key=_api_key,
                # Identify browser-originated sessions as WebUI so Hermes Agent
                # does not inject CLI-specific terminal/output guidance.
                platform="webui",
                quiet_mode=True,
                enabled_toolsets=_resolve_cli_toolsets(),
                session_id=s.session_id,
            )
            workspace_ctx = f"[Workspace: {s.workspace}]\n"
            workspace_system_msg = (
                f"Active workspace at session start: {s.workspace}\n"
                "Every user message is prefixed with [Workspace: /absolute/path] indicating the "
                "workspace the user has selected in the web UI at the time they sent that message. "
                "This tag is the single authoritative source of the active workspace and updates "
                "with every message. It overrides any prior workspace mentioned in this system "
                "prompt, memory, or conversation history. Always use the value from the most recent "
                "[Workspace: ...] tag as your default working directory for ALL file operations: "
                "write_file, read_file, search_files, terminal workdir, and patch. "
                "Never fall back to a hardcoded path when this tag is present."
            )
            from api.streaming import _sanitize_messages_for_api, _restore_reasoning_metadata

            _previous_messages = list(s.messages or [])

            result = agent.run_conversation(
                user_message=workspace_ctx + msg,
                system_message=workspace_system_msg,
                conversation_history=_sanitize_messages_for_api(s.messages),
                task_id=s.session_id,
                persist_user_message=msg,
            )
    finally:
        with _ENV_LOCK:
            if old_cwd is None:
                os.environ.pop("TERMINAL_CWD", None)
            else:
                os.environ["TERMINAL_CWD"] = old_cwd
            if old_exec_ask is None:
                os.environ.pop("HERMES_EXEC_ASK", None)
            else:
                os.environ["HERMES_EXEC_ASK"] = old_exec_ask
            if old_session_key is None:
                os.environ.pop("HERMES_SESSION_KEY", None)
            else:
                os.environ["HERMES_SESSION_KEY"] = old_session_key
        _clear_thread_env()
    with _get_session_agent_lock(s.session_id):
        s.messages = _restore_reasoning_metadata(
            _previous_messages,
            result.get("messages") or s.messages,
        )
        # Title column is owned by the hermes-agent SDK — webui must not
        # write it (CLAUDE.md). The SDK auto-titles via maybe_auto_title()
        # in agent/turn_context.py on every turn completion.
        s.sync_webui_metadata()
    return j(
        handler,
        {
            "answer": result.get("final_response") or "",
            "status": "done" if result.get("completed", True) else "partial",
            "session": s.compact() | {"messages": s.messages},
            "result": {k: v for k, v in result.items() if k != "messages"},
        },
    )


def _handle_cron_create(handler, body):
    try:
        require(body, "prompt", "schedule")
    except ValueError as e:
        return bad(handler, str(e))
    try:
        from cron.jobs import create_job

        job = create_job(
            prompt=body["prompt"],
            schedule=body["schedule"],
            name=body.get("name") or None,
            deliver=body.get("deliver") or "local",
            skills=body.get("skills") or [],
            model=body.get("model") or None,
            workdir=body.get("workdir") or None,
            script=body.get("script") or None,
            no_agent=bool(body.get("no_agent")),
            context_from=body.get("context_from") or None,
            enabled_toolsets=body.get("enabled_toolsets") or None,
            repeat=body.get("repeat"),
        )
        return j(handler, {"ok": True, "job": job})
    except Exception as e:
        return j(handler, {"error": str(e)}, status=400)


def _handle_cron_update(handler, body):
    try:
        require(body, "job_id")
    except ValueError as e:
        return bad(handler, str(e))
    from cron.jobs import update_job

    updates = {k: v for k, v in body.items() if k != "job_id" and v is not None}
    job = update_job(body["job_id"], updates)
    if not job:
        return bad(handler, "Job not found", 404)
    return j(handler, {"ok": True, "job": job})


def _handle_cron_delete(handler, body):
    try:
        require(body, "job_id")
    except ValueError as e:
        return bad(handler, str(e))
    from cron.jobs import remove_job

    ok = remove_job(body["job_id"])
    if not ok:
        return bad(handler, "Job not found", 404)
    return j(handler, {"ok": True, "job_id": body["job_id"]})


def _handle_cron_run(handler, body):
    job_id = body.get("job_id", "")
    if not job_id:
        return bad(handler, "job_id required")
    from cron.jobs import get_job
    from cron.scheduler import run_job

    job = get_job(job_id)
    if not job:
        return bad(handler, "Job not found", 404)

    def _run_and_save(job):
        try:
            from cron.jobs import save_job_output, mark_job_run
            success, output, final_response, error = run_job(job)
            save_job_output(job["id"], output)
            mark_job_run(job["id"], success, error)
        except Exception as e:
            logger.warning("Manual run_job failed for %s: %s", job.get("id"), e)

    threading.Thread(target=_run_and_save, args=(job,), daemon=True).start()
    return j(handler, {"ok": True, "job_id": job_id, "status": "triggered"})


def _handle_cron_pause(handler, body):
    job_id = body.get("job_id", "")
    if not job_id:
        return bad(handler, "job_id required")
    from cron.jobs import pause_job

    result = pause_job(job_id, reason=body.get("reason"))
    if result:
        return j(handler, {"ok": True, "job": result})
    return bad(handler, "Job not found", 404)


def _handle_cron_resume(handler, body):
    job_id = body.get("job_id", "")
    if not job_id:
        return bad(handler, "job_id required")
    from cron.jobs import resume_job

    result = resume_job(job_id)
    if result:
        return j(handler, {"ok": True, "job": result})
    return bad(handler, "Job not found", 404)


def _handle_file_delete(handler, body):
    try:
        require(body, "session_id", "path")
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session(body["session_id"])
    except KeyError:
        return bad(handler, "Session not found", 404)
    try:
        target = safe_resolve(Path(s.workspace), body["path"])
        if not target.exists():
            return bad(handler, "File not found", 404)
        if target.is_dir():
            return bad(handler, "Cannot delete directories via this endpoint")
        target.unlink()
        return j(handler, {"ok": True, "path": body["path"]})
    except (ValueError, PermissionError) as e:
        return bad(handler, _sanitize_error(e))


def _handle_file_save(handler, body):
    try:
        require(body, "session_id", "path")
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session(body["session_id"])
    except KeyError:
        return bad(handler, "Session not found", 404)
    try:
        target = safe_resolve(Path(s.workspace), body["path"])
        if not target.exists():
            return bad(handler, "File not found", 404)
        if target.is_dir():
            return bad(handler, "Cannot save: path is a directory")
        target.write_text(body.get("content", ""), encoding="utf-8")
        return j(
            handler, {"ok": True, "path": body["path"], "size": target.stat().st_size}
        )
    except (ValueError, PermissionError) as e:
        return bad(handler, _sanitize_error(e))


def _handle_file_create(handler, body):
    try:
        require(body, "session_id", "path")
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session(body["session_id"])
    except KeyError:
        return bad(handler, "Session not found", 404)
    try:
        target = safe_resolve(Path(s.workspace), body["path"])
        if target.exists():
            return bad(handler, "File already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body.get("content", ""), encoding="utf-8")
        return j(
            handler, {"ok": True, "path": str(target.relative_to(Path(s.workspace)))}
        )
    except (ValueError, PermissionError) as e:
        return bad(handler, _sanitize_error(e))


def _handle_file_rename(handler, body):
    try:
        require(body, "session_id", "path", "new_name")
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session(body["session_id"])
    except KeyError:
        return bad(handler, "Session not found", 404)
    try:
        source = safe_resolve(Path(s.workspace), body["path"])
        if not source.exists():
            return bad(handler, "File not found", 404)
        new_name = body["new_name"].strip()
        if not new_name or "/" in new_name or ".." in new_name:
            return bad(handler, "Invalid file name")
        dest = source.parent / new_name
        if dest.exists():
            return bad(handler, f'A file named "{new_name}" already exists')
        source.rename(dest)
        new_rel = str(dest.relative_to(Path(s.workspace)))
        return j(handler, {"ok": True, "old_path": body["path"], "new_path": new_rel})
    except (ValueError, PermissionError, OSError) as e:
        return bad(handler, _sanitize_error(e))


def _is_external_workspace(ws_path: str) -> bool:
    """Return True if the workspace path is external (default workspace or gateway/CLI derived)."""
    # Default workspace from config.yaml is always external
    try:
        if ws_path == profile_default_workspace():
            return True
    except Exception:
        pass
    # Gateway/CLI derived workspaces are external
    try:
        for s in get_cli_sessions():
            if s.get('workspace', '') == ws_path:
                return True
    except Exception:
        pass
    return False


def _is_builtin_default_workspace(ws_path: str) -> bool:
    """Return True if the workspace path is the built-in default workspace."""
    try:
        from api.config import BUILTIN_DEFAULT_WORKSPACE
        return Path(ws_path).resolve() == BUILTIN_DEFAULT_WORKSPACE.resolve()
    except Exception:
        return False


def _handle_create_dir(handler, body):
    try:
        require(body, "session_id", "path")
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session(body["session_id"])
    except KeyError:
        return bad(handler, "Session not found", 404)
    try:
        target = safe_resolve(Path(s.workspace), body["path"])
        if target.exists():
            return bad(handler, "Path already exists")
        target.mkdir(parents=True)
        return j(
            handler, {"ok": True, "path": str(target.relative_to(Path(s.workspace)))}
        )
    except (ValueError, PermissionError, OSError) as e:
        return bad(handler, _sanitize_error(e))


def _handle_workspace_add(handler, body):
    path_str = body.get("path", "").strip()
    name = (body.get("display_name") or body.get("name") or "").strip()
    auto_create = body.get("create", False)
    if not path_str:
        return bad(handler, "path is required")
    # Validate the path is NOT a blocked system root BEFORE any filesystem mutation.
    # This prevents creating orphan directories on rejected paths (#782 review).
    # _is_blocked_system_path honours user-tmp carve-outs (e.g. /var/folders on
    # macOS) so pytest's tmp_path_factory paths and other legit user-tmp dirs
    # still register cleanly.
    candidate = Path(path_str).expanduser().resolve()
    if _is_blocked_system_path(candidate):
        return bad(handler, f"Path points to a system directory: {candidate}")
    # Now safe to create the directory if requested
    if auto_create:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            return bad(handler, f"Could not create directory: {_sanitize_error(e)}")
    # Full validation (exists, is_dir) — should pass now that dir exists
    try:
        p = validate_workspace_to_add(path_str)
    except ValueError as e:
        return bad(handler, str(e))
    wss = load_workspaces()
    if any(w["path"] == str(p) for w in wss):
        return bad(handler, "Workspace already in list")
    final_name = name or p.name
    if any(w["name"] == final_name for w in wss):
        return bad(handler, f'Workspace name "{final_name}" already exists')
    wss.append({"path": str(p), "name": final_name})
    save_workspaces(wss)
    return j(handler, {"ok": True, "workspaces": wss})


def _handle_workspace_remove(handler, body):
    path_str = body.get("path", "").strip()
    if not path_str:
        return bad(handler, "path is required")
    # Block removal of built-in default workspace
    if _is_builtin_default_workspace(path_str):
        return bad(handler, "Cannot remove the default workspace", 403)
    # Block removal of external (gateway/CLI derived) workspaces
    if _is_external_workspace(path_str):
        return bad(handler, "Cannot remove an external workspace derived from gateway/CLI sessions", 403)
    wss = load_workspaces()
    wss = [w for w in wss if w["path"] != path_str]
    save_workspaces(wss)
    return j(handler, {"ok": True, "workspaces": wss})


def _handle_workspace_rename(handler, body):
    path_str = body.get("path", "").strip()
    name = body.get("name", "").strip()
    if not path_str or not name:
        return bad(handler, "path and name are required")
    # Block rename of built-in default workspace
    if _is_builtin_default_workspace(path_str):
        return bad(handler, "Cannot rename the default workspace", 403)
    # Block rename of external (gateway/CLI derived) workspaces
    if _is_external_workspace(path_str):
        return bad(handler, "Cannot rename an external workspace derived from gateway/CLI sessions", 403)
    wss = load_workspaces()
    # Check name uniqueness (exclude the workspace being renamed)
    if any(w["name"] == name and w["path"] != path_str for w in wss):
        return bad(handler, f'Workspace name "{name}" already exists')
    for w in wss:
        if w["path"] == path_str:
            w["name"] = name
            break
    else:
        return bad(handler, "Workspace not found", 404)
    save_workspaces(wss)
    return j(handler, {"ok": True, "workspaces": wss})


def _handle_workspace_list(handler, parsed):
    """GET /api/workspace/list?path=<absolute_path>
    List directory entries for a workspace path directly.
    """
    from urllib.parse import parse_qs as _pqs
    qs = _pqs(parsed.query)
    path_str = qs.get("path", [""])[0].strip()
    if not path_str:
        return bad(handler, "path is required")
    target = Path(path_str).expanduser().resolve()
    if _is_blocked_system_path(target):
        return bad(handler, "Path not allowed", 403)
    try:
        entries = list_dir(target, ".")
        # list_dir returns type='dir'|'file', prefix paths with absolute workspace path
        abs_prefix = str(target)
        for e in entries:
            e["is_dir"] = (e.get("type") == "dir")
            e["path"] = abs_prefix.rstrip("/") + "/" + e["path"]
        git = None
        try:
            from api.workspace import git_info_for_workspace
            git = git_info_for_workspace(target)
        except Exception:
            pass
        return j(handler, {"entries": entries, "path": path_str, "git": git})
    except (FileNotFoundError, ValueError) as exc:
        return bad(handler, _sanitize_error(exc), 404)


def _handle_workspace_info(handler, parsed):
    """GET /api/workspace/info?path=<absolute_path>
    Return metadata overview for a workspace directory.
    """
    from urllib.parse import parse_qs as _pqs
    qs = _pqs(parsed.query)
    path_str = qs.get("path", [""])[0].strip()
    if not path_str:
        return bad(handler, "path is required")
    target = Path(path_str).expanduser().resolve()
    if _is_blocked_system_path(target):
        return bad(handler, "Path not allowed", 403)
    if not target.is_dir():
        return bad(handler, "Not a directory", 404)
    try:
        from api.workspace import workspace_info
        info = workspace_info(target)
        return j(handler, info)
    except Exception as exc:
        return bad(handler, _sanitize_error(exc), 500)


def _handle_workspace_file_read(handler, parsed):
    """GET /api/workspace/file?path=<absolute_path>
    Read a file by absolute path.
    """
    from urllib.parse import parse_qs as _pqs
    qs = _pqs(parsed.query)
    path_str = qs.get("path", [""])[0].strip()
    if not path_str:
        return bad(handler, "path is required")
    target = Path(path_str).expanduser().resolve()
    if _is_blocked_system_path(target):
        return bad(handler, "Path not allowed", 403)
    if not target.is_file():
        return bad(handler, "Not a file", 404)
    try:
        from api.workspace import MAX_FILE_BYTES
        size = target.stat().st_size
        if size > MAX_FILE_BYTES:
            return bad(handler, f"File too large ({size} bytes)", 413)
        content = target.read_text(encoding="utf-8", errors="replace")
        handler.send_response(200)
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(content.encode("utf-8"))
    except OSError as exc:
        return bad(handler, _sanitize_error(exc), 500)


def _handle_workspace_file_write(handler, body):
    """POST /api/workspace/file {path, content}
    Write a file by absolute path.
    """
    path_str = (body.get("path") or "").strip()
    content = body.get("content", "")
    if not path_str:
        return bad(handler, "path is required")
    target = Path(path_str).expanduser().resolve()
    if _is_blocked_system_path(target):
        return bad(handler, "Path not allowed", 403)
    try:
        target.write_text(content, encoding="utf-8")
        return j(handler, {"ok": True})
    except OSError as exc:
        return bad(handler, _sanitize_error(exc), 500)


def _handle_workspace_file_delete(handler, body):
    """POST /api/workspace/delete {path}
    Delete a file or directory by absolute path.
    """
    import shutil

    path_str = (body.get("path") or "").strip()
    if not path_str:
        return bad(handler, "path is required")
    target = Path(path_str).expanduser().resolve()
    if _is_blocked_system_path(target):
        return bad(handler, "Path not allowed", 403)
    if not target.exists():
        return bad(handler, "Not found", 404)
    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return j(handler, {"ok": True, "path": path_str})
    except OSError as exc:
        return bad(handler, _sanitize_error(exc), 500)


def _handle_workspace_new(handler, body):
    """POST /api/workspace/new {path, is_dir}
    Create a new file or directory at the given absolute path.
    """
    path_str = (body.get("path") or "").strip()
    if not path_str:
        return bad(handler, "path is required")
    is_dir = body.get("is_dir", False)
    target = Path(path_str).expanduser().resolve()
    if _is_blocked_system_path(target):
        return bad(handler, "Path not allowed", 403)
    if target.exists():
        return bad(handler, "Already exists", 409)
    try:
        if is_dir:
            target.mkdir(parents=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("", encoding="utf-8")
        return j(handler, {"ok": True, "path": path_str, "is_dir": is_dir})
    except OSError as exc:
        return bad(handler, _sanitize_error(exc), 500)


def _handle_workspace_move(handler, body):
    """POST /api/workspace/move {src, dst}
    Rename/move a file or directory.
    """
    src_str = (body.get("src") or "").strip()
    dst_str = (body.get("dst") or "").strip()
    if not src_str or not dst_str:
        return bad(handler, "src and dst are required")
    if src_str == dst_str:
        return bad(handler, "src and dst are the same")
    src = Path(src_str).expanduser().resolve()
    dst = Path(dst_str).expanduser().resolve()
    if _is_blocked_system_path(src) or _is_blocked_system_path(dst):
        return bad(handler, "Path not allowed", 403)
    if not src.exists():
        return bad(handler, "Source not found", 404)
    if dst.exists():
        return bad(handler, "Target already exists", 409)
    try:
        src.rename(dst)
        return j(handler, {"ok": True, "old_path": src_str, "new_path": dst_str})
    except OSError as exc:
        return bad(handler, _sanitize_error(exc), 500)


def _handle_approval_respond(handler, body):
    sid = body.get("session_id", "")
    if not sid:
        return bad(handler, "session_id is required")
    choice = body.get("choice", "deny")
    if choice not in ("once", "session", "always", "deny"):
        return bad(handler, f"Invalid choice: {choice}")
    approval_id = body.get("approval_id", "")

    # Pop the targeted entry from the pending queue by approval_id.
    # Falls back to popping the first entry for backward-compat with old clients.
    pending = None
    with _lock:
        queue = _pending.get(sid)
        if isinstance(queue, list):
            if approval_id:
                # Find and remove the specific entry by approval_id.
                for i, entry in enumerate(queue):
                    if entry.get("approval_id") == approval_id:
                        pending = queue.pop(i)
                        break
                else:
                    # approval_id not found -- fall back to oldest entry.
                    pending = queue.pop(0) if queue else None
            else:
                pending = queue.pop(0) if queue else None
            if not queue:
                _pending.pop(sid, None)
        elif queue:
            # Legacy single-dict value.
            pending = _pending.pop(sid, None)

    if pending:
        keys = pending.get("pattern_keys") or [pending.get("pattern_key", "")]
        if choice == "session":
            for k in keys:
                approve_session(sid, k)
        elif choice == "always":
            for k in keys:
                approve_session(sid, k)
                approve_permanent(k)
            save_permanent_allowlist(_permanent_approved)
        elif choice == "once":
            logger.info(
                "approval once: session=%s keys=%s command=%s",
                sid, keys, pending.get("command", "")[:120]
            )
    # Unblock the agent thread waiting in the gateway approval queue.
    # This is the primary signal when streaming is active — the agent
    # thread is parked in entry.event.wait() and needs to be woken up.
    # The gateway thread will call approve_session() with the correct
    # keys after being unblocked.
    resolve_gateway_approval(sid, choice, resolve_all=False)
    # Persist session-approved patterns to the session JSON so they survive
    # restarts.  Must run AFTER resolve_gateway_approval() so the gateway
    # thread has a chance to call approve_session() before we read the set.
    # A tiny yield ensures the gateway thread gets the GIL.
    # Note: "once" is intentionally excluded — it's a one-time approval,
    # not persisted to session state.
    if choice in ("session", "always"):
        try:
            import time as _time
            _time.sleep(0.02)  # yield to gateway thread
            s = get_session(sid)
            approved = list(get_session_approved(sid))
            if approved:
                s.session_approved = approved
                s.sync_webui_metadata(touch_updated_at=False)
        except (KeyError, Exception):
            pass
    # Write approval_resolved marker to the active stream so replay can skip it
    try:
        s = get_session(sid)
        stream_id = stream_manager.get_stream_id(sid)
        if stream_id and approval_id:
            stream = stream_manager.get_stream_by_id(stream_id)
            if stream is not None:
                stream.put("approval_resolved", {"approval_id": approval_id})
    except Exception:
        pass  # best-effort; don't fail the response if stream lookup fails
    return j(handler, {"ok": True, "choice": choice})


def _handle_approval_session_list(handler, parsed):
    """GET /api/approval/session-approved — list approved patterns for a session."""
    sid = parse_qs(parsed.query).get("session_id", [""])[0]
    if not sid:
        return bad(handler, "session_id is required")
    # Touch the session to trigger restore_session_approvals() from the
    # persisted session_approved field (needed after server restart).
    try:
        get_session(sid)
    except KeyError:
        pass
    session_patterns = list(get_session_approved(sid))
    permanent_patterns = list(_permanent_approved)
    return j(handler, {
        "session_approved": session_patterns,
        "permanent_approved": permanent_patterns,
    })


def _handle_approval_revoke(handler, body):
    """POST /api/approval/revoke — revoke a session-scoped approval."""
    sid = body.get("session_id", "")
    if not sid:
        return bad(handler, "session_id is required")
    pattern_key = body.get("pattern_key", "")
    if not pattern_key:
        return bad(handler, "pattern_key is required")
    removed = revoke_session_approval(sid, pattern_key)
    if removed:
        # Persist the updated list to the session JSON
        try:
            s = get_session(sid)
            s.session_approved = list(get_session_approved(sid))
            s.sync_webui_metadata(touch_updated_at=False)
        except (KeyError, Exception):
            pass
    return j(handler, {"ok": removed, "revoked": pattern_key})


def _handle_clarify_respond(handler, body):
    sid = body.get("session_id", "")
    if not sid:
        return bad(handler, "session_id is required")
    response = body.get("response")
    if response is None:
        response = body.get("answer")
    if response is None:
        response = body.get("choice")
    response = str(response or "").strip()
    if not response:
        return bad(handler, "response is required")
    clarify_id = body.get("clarify_id", "")
    resolve_clarify(sid, response, resolve_all=False)
    # Write clarify_resolved marker to the active stream so replay can skip it
    try:
        s = get_session(sid)
        stream_id = stream_manager.get_stream_id(sid)
        if stream_id:
            stream = stream_manager.get_stream_by_id(stream_id)
            if stream is not None:
                stream.put("clarify_resolved", {"clarify_id": clarify_id})
    except Exception:
        pass  # best-effort; don't fail the response if stream lookup fails
    return j(handler, {"ok": True, "response": response})


def _fallback_estimate_messages_tokens_rough(msgs):
    """Fallback heuristic token estimate when runtime metadata helpers are absent."""
    total = 0
    for m in msgs or []:
        if not isinstance(m, dict):
            continue
        content = m.get("content", "")
        if isinstance(content, list):
            content_text = "\n".join(
                str(p.get("text") or p.get("content") or "")
                for p in content
                if isinstance(p, dict)
            )
        else:
            content_text = str(content or "")
        total += len(content_text.split())
    return max(1, total)


def _estimate_messages_tokens_rough(msgs):
    """Estimate token count for a list of messages.

    Uses the agent's token estimator when available, falling back to a
    word-count heuristic.
    """
    try:
        from agent.model_metadata import estimate_messages_tokens_rough as _agent_est
        return _agent_est(msgs)
    except Exception:
        return _fallback_estimate_messages_tokens_rough(msgs)


def _archive_session_before_compress(s):
    """Save a read-only snapshot of the current session before compression.

    Saved to ``{SESSION_DIR}/{session_id}_archived/compaction_{N}.json``
    with ``_readonly: true`` and ``_parent_session_id`` metadata.

    After saving, prunes the oldest snapshots if the count exceeds
    ``max_compaction_snapshots`` (configured in settings).

    Returns the archive path on success or None (never blocks compression).
    """
    try:
        import json as _json
        archive_dir = SESSION_DIR / f'{s.session_id}_archived'
        archive_dir.mkdir(parents=True, exist_ok=True)
        # Count existing archived snapshots
        existing = sorted(
            int(p.stem.split('_')[1])
            for p in archive_dir.glob('compaction_*.json')
            if p.stem.startswith('compaction_') and p.stem.split('_')[1].isdigit()
        )
        next_n = existing[-1] + 1 if existing else 1
        archived_id = f'compaction_{next_n}'
        # Build archive from in-memory session (not stale JSON on disk)
        data = s.compact() | {
            'session_id': archived_id,
            '_parent_session_id': s.session_id,
            '_readonly': True,
            '_compaction_n': next_n,
            'messages': s.messages,
            'tool_calls': s.tool_calls,
        }
        data['_compaction_n'] = next_n
        # Keep the original title for display
        dst = archive_dir / f'{archived_id}.json'
        dst.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

        # Mark session as having archived snapshots (fast sidebar lookup)
        s.archived = True

        # Prune oldest snapshots if count exceeds max_compaction_snapshots
        try:
            from api.config import load_settings
            max_str = str(load_settings().get("max_compaction_snapshots", "5")).strip()
            max_snap = int(max_str) if max_str.isdigit() else 5
        except Exception:
            max_snap = 5
        all_snapshots = sorted(
            archive_dir.glob('compaction_*.json'),
            key=lambda p: int(p.stem.split('_')[1]) if p.stem.startswith('compaction_') and p.stem.split('_')[1].isdigit() else 9999,
        )
        while len(all_snapshots) > max_snap:
            oldest = all_snapshots.pop(0)
            try:
                oldest.unlink()
            except OSError:
                pass

        # If pruning removed all snapshots, clear the flag
        remaining = list(archive_dir.glob('compaction_*.json'))
        if not remaining:
            s.archived = False

        return str(dst)
    except Exception:
        logger.debug("Failed to archive session %s before compression", s.session_id, exc_info=True)
        return None


def _persist_compressed_messages(s) -> None:
    """将压缩后的 s.messages 持久化到 state.db。

    带简单重试（3 次，200ms/400ms/800ms 退避），写库失败时记录 warning。
    所有四条压缩路径统一调用此函数：
      - _archive_session_before_compress (manual)
      - _auto_compress_session (chat-start auto)
      - streaming.py agent 内压缩 (SSE)
      - streaming.py token 阈值压缩 (SSE)
    """
    import time as _time
    from api.models import _get_session_db
    _db = _get_session_db()
    if _db is None:
        return
    for attempt in range(3):
        try:
            _db.replace_messages(s.session_id, s.messages)
            return
        except Exception:
            if attempt < 2:
                _time.sleep(0.2 * (2 ** attempt))
    logger.warning(
        "replace_messages exhausted retries for compress session=%s",
        s.session_id, exc_info=True,
    )


def _reanchor_last_seen_after_compress(s) -> None:
    """Re-anchor last_seen_msg_count after compression replaces the message list.

    Compression swaps ``s.messages`` for a shorter summary, so the user's
    "last seen" watermark no longer maps to a valid index.  Clamp it to the
    new message count (= "seen all current messages") so the sidebar unread
    badge (``message_count - last_seen_msg_count``) stays correct for any
    post-compression turns instead of going stale and suppressing the badge.

    Also reset the cached metadata message count so ``compact()`` re-derives
    ``message_count`` from ``len(self.messages)``.

    Must be called only AFTER ``s.messages`` has been reassigned to the
    compressed list.
    """
    s._metadata_message_count = None
    s.last_seen_msg_count = min(s.last_seen_msg_count or 0, len(s.messages))


# _discover_archived_sessions moved to api/models.py to avoid circular import


def _record_auto_compress_quality(s, before: int, after: int) -> None:
    """Update auto-compress quality tracking (in-memory; caller syncs to DB).

    "poor" = the compressed list still retains more than poor_ratio of the
    original message count (i.e. reduction is insufficient).  After
    ``disable_after`` consecutive poor attempts, auto-compress is disabled for
    the session until the user clears history or a later effective /compress.
    Both thresholds read from settings so they are user-configurable.
    """
    from api.config import load_settings as _ls

    _st = _ls()
    try:
        _poor_ratio = float(str(_st.get("auto_compress_poor_ratio", "0.5") or "0.5"))
    except (TypeError, ValueError):
        _poor_ratio = 0.5
    try:
        _max_poor = int(str(_st.get("auto_compress_disable_after", "3") or "3"))
    except (TypeError, ValueError):
        _max_poor = 3

    _ratio = after / max(before, 1)
    _poor = _ratio > _poor_ratio
    _fail_count = getattr(s, 'auto_compress_fail_count', 0)
    if _poor:
        _fail_count += 1
        s.auto_compress_fail_count = _fail_count
        if _fail_count >= _max_poor:
            s.auto_compress_disabled = True
            print(f"[auto-compress] DISABLED for {s.session_id}: "
                  f"{before} → {after} msgs (ratio={_ratio:.1%}) "
                  f"after {_fail_count} poor attempts", flush=True)
    else:
        s.auto_compress_fail_count = 0
        s.auto_compress_disabled = False


def _auto_compress_session(s) -> None:
    """Internal (no HTTP handler) session compressor for auto-compress on chat start.

    Runs the same compression logic as _handle_session_compress but returns nothing
    (raises on failure so the caller can swallow the error gracefully).
    """
    from api.streaming import _sanitize_messages_for_api
    import api.config as _cfg
    import hermes_cli.runtime_provider as _runtime_provider
    import run_agent as _run_agent

    messages = _sanitize_messages_for_api(s.messages)
    if len(messages) < 4:
        return  # nothing to compress

    resolved_model, resolved_provider, resolved_base_url = _cfg.resolve_model_provider(s.model)
    _rt = _runtime_provider.resolve_runtime_provider(requested=resolved_provider)
    resolved_api_key = _rt.get("api_key")
    if not resolved_api_key:
        raise RuntimeError("No API key configured for compression")

    agent = _run_agent.AIAgent(
        model=resolved_model,
        provider=resolved_provider,
        base_url=resolved_base_url,
        api_key=resolved_api_key,
        platform="webui",
        quiet_mode=True,
        enabled_toolsets=_resolve_cli_toolsets(),
        session_id=s.session_id,
    )
    approx_tokens = _estimate_messages_tokens_rough(messages)
    compressed = agent.context_compressor.compress(messages, current_tokens=approx_tokens)

    with _cfg._get_session_agent_lock(s.session_id):
        # Abort if history changed concurrently (shouldn't happen during chat start
        # since there's no active stream, but be safe).
        if _sanitize_messages_for_api(s.messages) != messages:
            raise RuntimeError("Session modified concurrently; skipping auto-compress")
        _archive_session_before_compress(s)
        s.messages = compressed
        s.tool_calls = []
        s.last_compression_time = time.time()

        # ── Update context-window fields from compressor ──
        new_tokens = _estimate_messages_tokens_rough(compressed)
        s.last_prompt_tokens = new_tokens
        _cc = getattr(agent, 'context_compressor', None)
        if _cc:
            s.context_length = getattr(_cc, 'context_length', 0) or 0
            s.threshold_tokens = getattr(_cc, 'threshold_tokens', 0) or 0
        # ── Keep in-memory metadata consistent ──
        _reanchor_last_seen_after_compress(s)

        # ── Compression quality tracking ──────────────────────────────
        _before = len(messages)
        _after = len(compressed)
        _record_auto_compress_quality(s, _before, _after)

        print(f"[auto-compress] saving session {s.session_id}: "
              f"{_before} → {_after} messages", flush=True)
        # ── Persist compressed message list to state.db ──
        _persist_compressed_messages(s)
        s.sync_webui_metadata()
        print(f"[auto-compress] save OK for {s.session_id}", flush=True)


def _handle_session_compress(handler, body):
    def _visible_messages_for_anchor(messages):
        out = []
        for m in messages or []:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            if not role or role == "tool":
                continue
            content = m.get("content", "")
            has_attachments = bool(m.get("attachments"))
            if role == "assistant":
                tool_calls = m.get("tool_calls")
                has_tool_calls = isinstance(tool_calls, list) and len(tool_calls) > 0
                has_tool_use = False
                has_reasoning = bool(m.get("reasoning"))
                if isinstance(content, list):
                    for p in content:
                        if not isinstance(p, dict):
                            continue
                        if p.get("type") == "tool_use":
                            has_tool_use = True
                        if p.get("type") in {"thinking", "reasoning"}:
                            has_reasoning = True
                    text = "\n".join(
                        str(p.get("text") or p.get("content") or "")
                        for p in content
                        if isinstance(p, dict) and p.get("type") == "text"
                    ).strip()
                else:
                    text = str(content or "").strip()
                if text or has_attachments or has_tool_calls or has_tool_use or has_reasoning:
                    out.append(m)
                continue
            if isinstance(content, list):
                text = "\n".join(
                    str(p.get("text") or p.get("content") or "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                ).strip()
            else:
                text = str(content or "").strip()
            if text or has_attachments:
                out.append(m)
        return out

    def _anchor_message_key(m):
        if not isinstance(m, dict):
            return None
        role = str(m.get("role") or "")
        if not role or role == "tool":
            return None
        content = m.get("content", "")
        if isinstance(content, list):
            text = "\n".join(
                str(p.get("text") or p.get("content") or "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        else:
            text = str(content or "")
        norm = " ".join(text.split()).strip()[:160]
        ts = m.get("_ts") or m.get("timestamp")
        attachments = m.get("attachments")
        attach_count = len(attachments) if isinstance(attachments, list) else 0
        if not norm and not attach_count and not ts:
            return None
        return {"role": role, "ts": ts, "text": norm, "attachments": attach_count}

    try:
        require(body, "session_id")
    except ValueError as e:
        return bad(handler, str(e))

    sid = str(body.get("session_id") or "").strip()
    if not sid:
        return bad(handler, "session_id is required")

    # Cap focus_topic to 500 chars — matches the defensive input-size pattern
    # used elsewhere (session title :80, first-exchange snippets :500) and
    # prevents a user from forwarding an unbounded string into the compressor
    # prompt path. No privilege boundary here (user prompting themself), just
    # cheap bound-checking.
    focus_topic = str(body.get("focus_topic") or body.get("topic") or "").strip()[:500] or None

    try:
        s = get_session(sid)
    except KeyError:
        return bad(handler, "Session not found", 404)

    if stream_manager.get_stream_id(sid):
        return bad(handler, "Session is still streaming; wait for the current turn to finish.", 409)

    try:
        from api.streaming import _sanitize_messages_for_api

        messages = _sanitize_messages_for_api(s.messages)
        if len(messages) < 4:
            return bad(handler, "Not enough conversation to compress (need at least 4 messages).")

        def _fallback_summarize_manual_compression(original_messages, compressed_messages, before_tokens, after_tokens, focus_topic=None):
            """Lightweight fallback summary to keep /session/compress usable in tests/runtime."""
            after_tokens = after_tokens if after_tokens is not None else _fallback_estimate_messages_tokens_rough(compressed_messages)
            headline = f"Compressed: {len(original_messages)} \u2192 {len(compressed_messages)} messages"
            summary = {
                "headline": headline,
                "token_line": f"Rough transcript estimate: ~{before_tokens} \u2192 ~{after_tokens} tokens",
                "note": f"Focus: {focus_topic}" if focus_topic else None,
            }
            summary["reference_message"] = (
                f"[CONTEXT COMPACTION \u2014 REFERENCE ONLY] {headline}\n"
                f"{summary['token_line']}\n"
                + (summary["note"] + "\n" if summary.get("note") else "")
                + "Compression completed."
            )
            return summary

        def _summarize_manual_compression(
            original_messages,
            compressed_messages,
            before_tokens,
            after_tokens,
            focus_topic=None,
        ):
            try:
                from agent.manual_compression_feedback import summarize_manual_compression

                return summarize_manual_compression(
                    original_messages,
                    compressed_messages,
                    before_tokens,
                    after_tokens,
                )
            except Exception:
                return _fallback_summarize_manual_compression(
                    original_messages,
                    compressed_messages,
                    before_tokens,
                    after_tokens,
                    focus_topic,
                )

        import api.config as _cfg
        import hermes_cli.runtime_provider as _runtime_provider
        import run_agent as _run_agent

        resolved_model, resolved_provider, resolved_base_url = _cfg.resolve_model_provider(s.model)

        resolved_api_key = None
        try:
            _rt = _runtime_provider.resolve_runtime_provider(requested=resolved_provider)
            resolved_api_key = _rt.get("api_key")
            if not resolved_provider:
                resolved_provider = _rt.get("provider")
            if not resolved_base_url:
                resolved_base_url = _rt.get("base_url")
        except Exception as _e:
            logger.warning("resolve_runtime_provider failed for compression: %s", _e)

        if not resolved_api_key:
            return bad(handler, "No provider configured -- cannot compress.")

        # Compute compression *outside* the lock — the LLM round-trip can take
        # many seconds and we must not block cancel_stream or other writers.
        # Lock contract: hold for the in-memory mutation only, never across
        # network I/O.
        original_messages = list(messages)
        approx_tokens = _estimate_messages_tokens_rough(original_messages)

        agent = _run_agent.AIAgent(
            model=resolved_model,
            provider=resolved_provider,
            base_url=resolved_base_url,
            api_key=resolved_api_key,
            # Identify browser-originated sessions as WebUI so Hermes Agent
            # does not inject CLI-specific terminal/output guidance.
            platform="webui",
            quiet_mode=True,
            enabled_toolsets=_resolve_cli_toolsets(),
            session_id=sid,
        )
        compressed = agent.context_compressor.compress(
            original_messages,
            current_tokens=approx_tokens,
            focus_topic=focus_topic,
        )
        new_tokens = _estimate_messages_tokens_rough(compressed)
        summary = _summarize_manual_compression(
            original_messages,
            compressed,
            approx_tokens,
            new_tokens,
            focus_topic=focus_topic,
        )

        with _cfg._get_session_agent_lock(sid):
            # Re-read messages to detect concurrent edits during the LLM call.
            # If the history changed, the compression result is stale — abort.
            if _sanitize_messages_for_api(s.messages) != original_messages:
                return bad(handler, "Session was modified during compression; please retry.", 409)

            _archive_session_before_compress(s)
            s.messages = compressed
            s.tool_calls = []
            s.last_compression_time = time.time()
            s.pending_user_message = None
            s.pending_attachments = []
            s.pending_started_at = None
            # ── Update context-window fields from compressor ──
            # new_tokens was estimated from the compressed messages above —
            # use it so ContextUsageIndicator reflects the post-compress state.
            s.last_prompt_tokens = new_tokens
            _cc = getattr(agent, 'context_compressor', None)
            if _cc:
                s.context_length = getattr(_cc, 'context_length', 0) or 0
                s.threshold_tokens = getattr(_cc, 'threshold_tokens', 0) or 0
            # ── Keep in-memory metadata consistent with the new message list ──
            _reanchor_last_seen_after_compress(s)
            # ── Track compression lineage BEFORE sync_webui_metadata so the
            #     __webui columns are persisted to state.db ──
            _prev_count = getattr(s, 'compression_segment_count', 0) or 0
            s.compression_segment_count = _prev_count + 1
            s.parent_session_id = sid
            if not getattr(s, 'lineage_root_id', None):
                s.lineage_root_id = sid
            s.lineage_tip_id = sid
            # ── Compression anchor (for scroll-to-first-visible) ──
            visible_after = _visible_messages_for_anchor(compressed)
            s.compression_anchor_visible_idx = max(0, len(visible_after) - 1) if visible_after else None
            s.compression_anchor_message_key = _anchor_message_key(visible_after[-1]) if visible_after else None
            # ── Persist to state.db ──
            _persist_compressed_messages(s)
            s.sync_webui_metadata()

        session_payload = redact_session_data(
            s.compact() | {
                "messages": s.messages,
                "tool_calls": s.tool_calls,
                "active_stream_id": stream_manager.get_stream_id(s.session_id),
                "pending_user_message": s.pending_user_message,
                "pending_original_message": getattr(s, "_pending_original_message", None),
                "pending_user_message_ts": getattr(s, "_pending_user_message_ts", None),
                "pending_attachments": s.pending_attachments,
                "pending_started_at": s.pending_started_at,
                "compression_anchor_visible_idx": getattr(s, "compression_anchor_visible_idx", None),
                "compression_anchor_message_key": getattr(s, "compression_anchor_message_key", None),
            }
        )
        return j(
            handler,
            {
                "ok": True,
                "session": session_payload,
                "summary": summary,
                "focus_topic": focus_topic,
                "stats": {
                    "saved_messages": len(original_messages) - len(compressed),
                    "approx_tokens_before": approx_tokens,
                    "approx_tokens_after": new_tokens,
                    "messages_before": len(original_messages),
                    "messages_after": len(compressed),
                },
            },
        )
    except Exception as e:
        logger.warning("Manual session compression failed: %s", e)
        return bad(handler, f"Compression failed: {_sanitize_error(e)}")


def _handle_skill_save(handler, body):
    try:
        require(body, "name", "content")
    except ValueError as e:
        return bad(handler, str(e))
    skill_name = body["name"].strip().lower().replace(" ", "-")
    if not skill_name or "/" in skill_name or ".." in skill_name:
        return bad(handler, "Invalid skill name")
    category = (body.get("category") or "").strip()
    if category and ("/" in category or ".." in category):
        return bad(handler, "Invalid category")
    from tools.skills_tool import SKILLS_DIR

    if category:
        skill_dir = SKILLS_DIR / category / skill_name
    else:
        skill_dir = SKILLS_DIR / skill_name
    # Validate resolved path stays within SKILLS_DIR
    try:
        skill_dir.resolve().relative_to(SKILLS_DIR.resolve())
    except ValueError:
        return bad(handler, "Invalid skill path")
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(body["content"], encoding="utf-8")
    return j(handler, {"ok": True, "name": skill_name, "path": str(skill_file)})


def _handle_skill_delete(handler, body):
    try:
        require(body, "name")
    except ValueError as e:
        return bad(handler, str(e))
    from tools.skills_tool import SKILLS_DIR
    import shutil

    deleted_name = body["name"]
    matches = list(SKILLS_DIR.rglob(f"{deleted_name}/SKILL.md"))
    if not matches:
        return bad(handler, "Skill not found", 404)
    skill_dir = matches[0].parent
    shutil.rmtree(str(skill_dir))

    # 清理所有引用了此 skill 的 session
    try:
        from api.models import all_sessions
        from api.config import SESSION_AGENT_CACHE, SESSION_AGENT_CACHE_LOCK
        for s in all_sessions():
            if s.skills and deleted_name in s.skills:
                s.skills = [sk for sk in s.skills if sk != deleted_name]
                s.sync_webui_metadata(touch_updated_at=False)
                with SESSION_AGENT_CACHE_LOCK:
                    SESSION_AGENT_CACHE.pop(s.session_id, None)
    except Exception:
        pass  # 清理失败不应阻止删除操作本身

    return j(handler, {"ok": True, "name": deleted_name})


def _handle_skill_file_save(handler, body):
    """Save an arbitrary file within a skill directory (used by SkillFilesDrawer)."""
    try:
        require(body, "name", "file", "content")
    except ValueError as e:
        return bad(handler, str(e))
    from tools.skills_tool import SKILLS_DIR
    import re as _re_sfs

    name = body["name"].strip()
    file_rel = body["file"].strip()
    if not name or _re_sfs.search(r"[*?\[\]]", name):
        return bad(handler, "Invalid skill name", 400)
    # Reject obvious path traversal attempts early
    if ".." in file_rel or file_rel.startswith("/"):
        return bad(handler, "Invalid file path", 400)
    skill_dir = None
    for p in SKILLS_DIR.rglob(name):
        if p.is_dir():
            skill_dir = p
            break
    if not skill_dir:
        return bad(handler, "Skill not found", 404)
    target = (skill_dir / file_rel).resolve()
    try:
        target.relative_to(skill_dir.resolve())
    except ValueError:
        return bad(handler, "Invalid file path", 400)
    if not target.exists() or not target.is_file():
        return bad(handler, "File not found", 404)
    target.write_text(body["content"], encoding="utf-8")
    return j(handler, {"ok": True, "path": file_rel})


def _handle_skill_file_delete(handler, body):
    """POST /api/skills/file/delete {name, file}
    Delete a file or directory from a skill directory.
    """
    import shutil

    try:
        require(body, "name", "file")
    except ValueError as e:
        return bad(handler, str(e))
    from tools.skills_tool import SKILLS_DIR
    import re as _re_sfd

    name = body["name"].strip()
    file_rel = body["file"].strip()
    if not name or _re_sfd.search(r"[*?\[\]]", name):
        return bad(handler, "Invalid skill name", 400)
    if ".." in file_rel or file_rel.startswith("/"):
        return bad(handler, "Invalid file path", 400)
    if file_rel in ("SKILL.md", "skill.md"):
        return bad(handler, "Cannot delete SKILL.md directly", 400)

    skill_dir = None
    for p in SKILLS_DIR.rglob(name):
        if p.is_dir():
            skill_dir = p
            break
    if not skill_dir:
        return bad(handler, "Skill not found", 404)
    target = (skill_dir / file_rel).resolve()
    try:
        target.relative_to(skill_dir.resolve())
    except ValueError:
        return bad(handler, "Invalid file path", 400)
    if not target.exists():
        return bad(handler, "File not found", 404)
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return j(handler, {"ok": True, "path": file_rel})


def _handle_skill_file_create(handler, body):
    """POST /api/skills/file/create {name, file, content?, is_dir?}
    Create a new file or directory within a skill directory.
    """
    try:
        require(body, "name", "file")
    except ValueError as e:
        return bad(handler, str(e))
    from tools.skills_tool import SKILLS_DIR
    import re as _re_sfc

    name = body["name"].strip()
    file_rel = body["file"].strip()
    is_dir = body.get("is_dir", False)
    content = body.get("content", "")

    if not name or _re_sfc.search(r"[*?\[\]]", name):
        return bad(handler, "Invalid skill name", 400)
    if ".." in file_rel or file_rel.startswith("/"):
        return bad(handler, "Invalid file path", 400)

    skill_dir = None
    for p in SKILLS_DIR.rglob(name):
        if p.is_dir():
            skill_dir = p
            break
    if not skill_dir:
        return bad(handler, "Skill not found", 404)

    target = (skill_dir / file_rel).resolve()
    try:
        target.relative_to(skill_dir.resolve())
    except ValueError:
        return bad(handler, "Invalid file path", 400)
    if target.exists():
        return bad(handler, "File already exists", 409)

    if is_dir:
        target.mkdir(parents=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return j(handler, {"ok": True, "path": file_rel, "is_dir": is_dir})


def _handle_skill_file_rename(handler, body):
    """POST /api/skills/file/rename {name, old_file, new_file}
    Rename a file or directory within a skill directory.
    """
    try:
        require(body, "name", "old_file", "new_file")
    except ValueError as e:
        return bad(handler, str(e))
    from tools.skills_tool import SKILLS_DIR
    import re as _re_sfr

    name = body["name"].strip()
    old_rel = body["old_file"].strip()
    new_rel = body["new_file"].strip()

    if not name or _re_sfr.search(r"[*?\[\]]", name):
        return bad(handler, "Invalid skill name", 400)
    if ".." in old_rel or old_rel.startswith("/"):
        return bad(handler, "Invalid old path", 400)
    if ".." in new_rel or new_rel.startswith("/"):
        return bad(handler, "Invalid new path", 400)
    if old_rel == new_rel:
        return bad(handler, "Old and new paths are the same", 400)

    skill_dir = None
    for p in SKILLS_DIR.rglob(name):
        if p.is_dir():
            skill_dir = p
            break
    if not skill_dir:
        return bad(handler, "Skill not found", 404)

    src = (skill_dir / old_rel).resolve()
    dst = (skill_dir / new_rel).resolve()
    try:
        src.relative_to(skill_dir.resolve())
        dst.relative_to(skill_dir.resolve())
    except ValueError:
        return bad(handler, "Invalid file path", 400)
    if not src.exists():
        return bad(handler, "Source not found", 404)
    if dst.exists():
        return bad(handler, "Target already exists", 409)

    src.rename(dst)
    return j(handler, {"ok": True, "old_path": old_rel, "new_path": new_rel})


def _handle_skill_import(handler):
    """Import a skill directory uploaded as multipart/form-data.

    Form fields:
      skill_name  – target skill name (will be slugified)
      category    – optional category subdirectory
      overwrite   – "true" to overwrite an existing skill directory
      file_0 … file_N  – binary file content
      path_0 … path_N  – corresponding relative path within the skill directory
    """
    import shutil as _shutil
    from api.upload import parse_multipart
    from tools.skills_tool import SKILLS_DIR

    content_type = handler.headers.get('Content-Type', '')
    content_length = int(handler.headers.get('Content-Length', 0) or 0)

    # No hard cap for skill imports (binary assets can be large); use 500 MB guard.
    _MAX_IMPORT = 500 * 1024 * 1024
    if content_length > _MAX_IMPORT:
        return bad(handler, f'Upload too large (max 500 MB)', 413)

    try:
        fields, files = parse_multipart(handler.rfile, content_type, content_length)
    except Exception as exc:
        return bad(handler, f'Failed to parse upload: {exc}', 400)

    # ── Validate metadata fields ──
    skill_name = fields.get('skill_name', '').strip().lower().replace(' ', '-')
    if not skill_name or '/' in skill_name or '..' in skill_name:
        return bad(handler, 'Invalid skill name', 400)

    category = fields.get('category', '').strip()
    if category and ('/' in category or '..' in category):
        return bad(handler, 'Invalid category', 400)

    overwrite = fields.get('overwrite', 'false').lower() == 'true'

    # ── Resolve target directory ──
    if category:
        skill_dir = SKILLS_DIR / category / skill_name
    else:
        skill_dir = SKILLS_DIR / skill_name

    try:
        skill_dir.resolve().relative_to(SKILLS_DIR.resolve())
    except ValueError:
        return bad(handler, 'Invalid skill path', 400)

    # ── Conflict detection ──
    if skill_dir.exists() and not overwrite:
        return j(handler, {'conflict': True, 'name': skill_name}, status=409)

    # ── Wipe existing directory when overwriting ──
    if skill_dir.exists() and overwrite:
        _shutil.rmtree(str(skill_dir))

    # ── Write files ──
    file_count = 0
    i = 0
    while f'file_{i}' in files:
        _filename, file_bytes = files[f'file_{i}']
        rel_path = fields.get(f'path_{i}', '').strip()
        i += 1

        if not rel_path:
            continue

        # Security: reject path traversal
        parts = rel_path.replace('\\', '/').split('/')
        if not all(p and p != '..' for p in parts) or rel_path.startswith('/'):
            continue

        target = (skill_dir / rel_path).resolve()
        try:
            target.relative_to(skill_dir.resolve())
        except ValueError:
            continue  # path escaped skill_dir — skip silently

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(file_bytes)
        file_count += 1

    return j(handler, {'ok': True, 'name': skill_name, 'file_count': file_count})


def _handle_memory_write(handler, body):
    try:
        require(body, "section", "content")
    except ValueError as e:
        return bad(handler, str(e))
    try:
        from api.profiles import get_active_hermes_home

        mem_dir = get_active_hermes_home() / "memories"
    except ImportError:
        mem_dir = Path.home() / ".hermes" / "memories"
    mem_dir.mkdir(parents=True, exist_ok=True)
    section = body["section"]
    if section == "memory":
        target = mem_dir / "MEMORY.md"
    elif section == "user":
        target = mem_dir / "USER.md"
    else:
        return bad(handler, 'section must be "memory" or "user"')
    target.write_text(body["content"], encoding="utf-8")
    return j(handler, {"ok": True, "section": section, "path": str(target)})


def _handle_session_import_cli(handler, body):
    """Import a single CLI session into the WebUI store."""
    try:
        require(body, "session_id")
    except ValueError as e:
        return bad(handler, str(e))

    sid = str(body["session_id"])

    # Check if already imported — refresh messages from CLI store if new ones arrived
    existing = Session.load(sid)
    if existing:
        fresh_msgs = get_cli_session_messages(sid)
        if fresh_msgs and len(fresh_msgs) > len(existing.messages):
            # Prefix-equality guard: only extend if existing messages are a prefix of
            # the fresh CLI messages. Prevents silently dropping WebUI-added messages
            # on hybrid sessions (user sent messages via WebUI while CLI continued).
            if existing.messages == fresh_msgs[:len(existing.messages)]:
                existing.messages = fresh_msgs
                existing.sync_webui_metadata(touch_updated_at=False)
        return j(
            handler,
            {
                "session": existing.compact()
                | {
                    "messages": existing.messages,
                    "is_cli_session": True,
                },
                "imported": False,
            },
        )

    # Fetch messages from CLI store
    msgs = get_cli_session_messages(sid)
    if not msgs:
        return bad(handler, "Session not found in CLI store", 404)

    # Get profile, model, timestamps, and title from CLI session metadata
    profile = None
    created_at = None
    updated_at = None
    cli_title = None
    for cs in get_cli_sessions():
        if cs["session_id"] == sid:
            profile = cs.get("profile")
            model = cs.get("model", "unknown")
            created_at = cs.get("created_at")
            updated_at = cs.get("updated_at")
            cli_title = cs.get("title")
            break

    # Use the CLI session title if available (e.g., cron job name). Title
    # is owned by the hermes-agent SDK — webui must not derive its own.
    title = cli_title or ""

    s = import_cli_session(
        sid,
        title,
        msgs,
        model,
        profile=profile,
        created_at=created_at,
        updated_at=updated_at,
    )
    s.is_cli_session = True
    s._cli_origin = sid
    s.sync_webui_metadata(touch_updated_at=False)
    return j(
        handler,
        {
            "session": s.compact()
            | {
                "messages": msgs,
                "is_cli_session": True,
            },
            "imported": True,
        },
    )


def _handle_session_import(handler, body):
    """Import a session from a JSON export. Creates a new session with a new ID."""
    if not body or not isinstance(body, dict):
        return bad(handler, "Request body must be a JSON object")
    messages = body.get("messages")
    if not isinstance(messages, list):
        return bad(handler, 'JSON must contain a "messages" array')
    title = body.get("title", "Imported session")
    workspace = body.get("workspace", str(DEFAULT_WORKSPACE))
    model = body.get("model", DEFAULT_MODEL)
    s = Session(
        title=title,
        workspace=workspace,
        model=model,
        messages=messages,
        tool_calls=body.get("tool_calls", []),
    )
    s.pinned = body.get("pinned", False)
    with LOCK:
        SESSIONS[s.session_id] = s
        SESSIONS.move_to_end(s.session_id)
        while len(SESSIONS) > SESSIONS_MAX:
            SESSIONS.popitem(last=False)
    from api.models import _notify_session_event
    _notify_session_event('upsert_session', s)
    # ── Persist imported messages to state.db ──
    try:
        _db = _get_session_db()
        if _db is not None:
            _db.replace_messages(s.session_id, s.messages)
    except Exception:
        logger.debug("replace_messages failed for import: %s", s.session_id)
    s.sync_webui_metadata()
    return j(handler, {"ok": True, "session": s.compact() | {"messages": s.messages}})
