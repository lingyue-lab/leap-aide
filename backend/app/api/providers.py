"""Hermes Web UI -- provider management endpoints.

Closes #586 (allow provider key update) and part of #604 (model picker
multi-provider support).
"""

from __future__ import annotations

import logging
import os
import threading
import time
import urllib.error
from pathlib import Path
from typing import Any

from app.api import model_catalog
from app.api.config import (
    _get_config_path,
    _save_yaml_config_file,
    custom_provider_slug,
    get_config,
    invalidate_models_cache,
    named_custom_provider,
    reload_config,
)

logger = logging.getLogger(__name__)

# SECTION: Provider env-var resolution

# Env var names come from the JSON catalog (auth.env_var / auth.env_vars), so
# catalog-only edits (adding a provider, fixing a var name) need no Python
# change — mirrors how config.py's eligibility scan consumes catalog_env_vars()
# (issue #10).  Providers with no mapping (OAuth/token-flow providers like
# nous, openai-codex) cannot have their keys managed from the WebUI.


def _provider_env_vars(provider_id: str) -> list[str]:
    """Resolve API-key env var names for a provider id.

    Accepts a Settings card id (``xai``, ``alibaba``, ``gemini``) or a catalog
    slug (``x-ai``, ``qwen``, ``google``) and returns the env var names
    declared by the catalog, in declaration order.  Unknown / custom providers
    return [].

    ``copilot`` is the one catalog exception: it authenticates via OAuth, so
    the catalog declares no env vars, but a manually set COPILOT_GITHUB_TOKEN
    in ``~/.hermes/.env`` is still a valid token-flow credential.
    """
    if not provider_id:
        return []
    slug = model_catalog.resolve_alias(provider_id)
    envs = model_catalog.catalog_env_vars().get(slug)
    if envs:
        return list(envs)
    if provider_id == "copilot":
        return ["COPILOT_GITHUB_TOKEN"]
    return []

# Settings card dedup: fold related catalog slugs into one provider card.
# Mirrors the retired config._PROVIDER_CARD_ALIASES so the Settings UI keeps
# showing one "Qwen" / "Gemini" / "xAI" card even though the catalog keeps
# separate records (e.g. google vs gemini, qwen vs alibaba).
_PROVIDER_CARD_FOLD = {
    "qwen": "alibaba",
    "google": "gemini",
    "x-ai": "xai",
}


def _provider_card_id(slug: str) -> str:
    """Fold a catalog slug into its Settings card id."""
    return _PROVIDER_CARD_FOLD.get(slug, slug)


def _provider_card_models(card_id: str) -> list[dict]:
    """Return the merged baseline model list for a Settings card.

    Folds every catalog slug whose card id matches *card_id* (e.g. both
    ``google`` and ``gemini`` baselines land on the ``gemini`` card),
    deduped by model id.
    """
    seen: set[str] = set()
    out: list[dict] = []
    for slug in model_catalog.catalog_providers():
        if _provider_card_id(slug) != card_id:
            continue
        for m in model_catalog.provider_baseline_models(slug):
            mid = m.get("id")
            if mid and mid not in seen:
                seen.add(mid)
                out.append(m)
    return out


# Providers that use OAuth or token flows — their credentials are managed
# through the Hermes CLI, not via API keys.  The WebUI cannot set these.
_OAUTH_PROVIDERS = frozenset({
    "openai-codex",
    "nous",
    "copilot",
})

# SECTION: Helper functions


def _get_hermes_home() -> Path:
    """Return the active Hermes home directory."""
    try:
        from app.api.profiles import get_active_hermes_home
        return get_active_hermes_home()
    except ImportError:
        return Path.home() / ".hermes"


def _load_env_file(env_path: Path) -> dict[str, str]:
    """Read key=value pairs from a .env file."""
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    except Exception:
        return {}
    return values


def _write_env_file(env_path: Path, updates: dict[str, str | None]) -> None:
    """Write key=value pairs to the .env file.

    Values of ``None`` cause the key to be removed.

    Preserves comments, blank lines, and original key order (#1164).
    New keys are appended at the end of the file with a blank-line separator.

    Holds ``_ENV_LOCK`` from ``api.streaming`` for the entire load → modify →
    write cycle to prevent TOCTOU races between concurrent POST /api/providers
    calls (each reading the same file baseline and overwriting the other's key).
    Also serialises os.environ mutations with streaming sessions.
    """
    from app.api.streaming import _ENV_LOCK
    import stat as _stat

    with _ENV_LOCK:
        # ── Read existing lines (preserving comments and blank lines) ──
        existing_lines: list[str] = []
        if env_path.exists():
            try:
                existing_lines = env_path.read_text(encoding="utf-8").splitlines()
            except Exception:
                existing_lines = []

        # Map each existing key to its line index so we can update in-place.
        existing_key_indices: dict[str, int] = {}
        for _i, _raw in enumerate(existing_lines):
            _stripped = _raw.strip()
            if _stripped and not _stripped.startswith("#") and "=" in _stripped:
                _existing_key_indices_key = _stripped.split("=", 1)[0].strip()
                existing_key_indices[_existing_key_indices_key] = _i

        output_lines = list(existing_lines)
        new_keys: list[str] = []

        for key, value in updates.items():
            if value is None:
                # Mark the line for removal (None sentinel) and clear env.
                os.environ.pop(key, None)
                if key in existing_key_indices:
                    output_lines[existing_key_indices[key]] = None  # type: ignore[assignment]
                continue
            clean = str(value).strip()
            if not clean:
                continue
            # Reject embedded newlines/carriage returns to prevent .env injection
            if "\n" in clean or "\r" in clean:
                raise ValueError("API key must not contain newline characters.")
            os.environ[key] = clean

            if key in existing_key_indices:
                output_lines[existing_key_indices[key]] = f"{key}={clean}"
            else:
                new_keys.append(f"{key}={clean}")

        # Remove deleted lines (None sentinels)
        output_lines = [l for l in output_lines if l is not None]

        # Append new keys after a blank-line separator
        if new_keys:
            if output_lines and output_lines[-1].strip() != "":
                output_lines.append("")
            output_lines.extend(new_keys)

        env_path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(output_lines)
        if content:
            content += "\n"
        # Atomic write via tempfile + os.replace so cross-process readers
        # (Telegram bot, CLI) never see a half-truncated file.  The shared
        # ``~/.hermes/.env`` is also written by ``hermes_cli.config.save_env_value``
        # using the same atomic pattern; matching it here closes the
        # cross-process leg of #1164 (within-process is covered by _ENV_LOCK).
        _mode = _stat.S_IRUSR | _stat.S_IWUSR  # 0o600
        import tempfile as _tempfile
        _tmp_fd, _tmp_path = _tempfile.mkstemp(
            dir=str(env_path.parent), prefix=".env_", suffix=".tmp"
        )
        try:
            with os.fdopen(_tmp_fd, "w", encoding="utf-8") as _f:
                _f.write(content)
                _f.flush()
                os.fsync(_f.fileno())
            os.chmod(_tmp_path, _mode)  # tighten before rename so readers see 0600
            os.replace(_tmp_path, env_path)
        except BaseException:
            try:
                os.unlink(_tmp_path)
            except OSError:
                pass
            raise
        try:
            env_path.chmod(_mode)
        except OSError:
            pass


def _provider_has_key(provider_id: str) -> bool:
    """Check whether a provider has a configured API key.

    Checks (in order):
    1. ``~/.hermes/.env`` for the known env var
    2. ``os.environ`` for the known env var
    3. ``config.yaml → model.api_key`` (only if provider is the active one)
    4. ``config.yaml → providers.<id>.api_key``
    5. ``config.yaml → custom_providers[].api_key`` (for custom providers)
    """
    env_vars = _provider_env_vars(provider_id)
    if env_vars:
        env_path = _get_hermes_home() / ".env"
        env_values = _load_env_file(env_path)
        if any(env_values.get(v) for v in env_vars):
            return True
        if any(os.getenv(v) for v in env_vars):
            return True

    cfg = get_config()
    # Check model.api_key — only match if this provider is the active one.
    # Previously this checked globally, causing all providers to show
    # "configured" when the active provider had a top-level api_key.
    model_cfg = cfg.get("model", {})
    if isinstance(model_cfg, dict) and str(model_cfg.get("api_key") or "").strip():
        active_provider = model_cfg.get("provider")
        if active_provider and str(active_provider).strip().lower() == provider_id.lower():
            return True
    # Check providers.<id>.api_key
    providers_cfg = cfg.get("providers", {})
    if isinstance(providers_cfg, dict):
        provider_cfg = providers_cfg.get(provider_id, {})
        if isinstance(provider_cfg, dict) and str(provider_cfg.get("api_key") or "").strip():
            return True
    # Check custom_providers (provider id is the normalized name slug)
    _cp = named_custom_provider(provider_id)
    if _cp is not None and str(_cp.get("api_key") or "").strip():
        return True
    return False


def _provider_is_oauth(provider_id: str) -> bool:
    """Check whether a provider uses OAuth/token flows (managed by CLI)."""
    return provider_id in _OAUTH_PROVIDERS


# SECTION: Connectivity probe (issue #8)

# Last verification result per provider (Settings card id -> status dict).
# Surfaced by GET /api/providers so Settings can show "API key rejected"
# on an already-saved key without waiting for a re-save (issue: a bad key
# saved before validation existed stayed invisible until chat time).
_PROBE_STATUS: dict[str, dict] = {}
_PROBE_STATUS_LOCK = threading.Lock()
_PROBE_STATUS_TTL_S = 900.0  # a cached result is stale after 15 minutes
_PROBE_REFRESH_MIN_INTERVAL_S = 60.0  # rate-limit GET-triggered probe kicks
_last_probe_kick = 0.0


def _record_probe_status(provider_id: str, status: str) -> None:
    """Store the latest verification result for *provider_id*."""
    if not provider_id or status not in ("ok", "auth", "network", "skipped"):
        return
    with _PROBE_STATUS_LOCK:
        _PROBE_STATUS[provider_id] = {"status": status, "checked_at": time.time()}


def _get_probe_status(provider_id: str) -> tuple[str | None, float | None]:
    """Return ``(status, checked_at)`` for *provider_id*, or ``(None, None)``."""
    with _PROBE_STATUS_LOCK:
        entry = _PROBE_STATUS.get(provider_id)
        if not isinstance(entry, dict):
            return None, None
        return entry.get("status"), entry.get("checked_at")


def _clear_probe_status(provider_id: str) -> None:
    """Drop any cached verification result for *provider_id*."""
    with _PROBE_STATUS_LOCK:
        _PROBE_STATUS.pop(provider_id, None)


# Kill switch for save-time key validation (tests default this off so the
# suite never touches the network; production default is enabled).
_VALIDATE_KEY_ENV = "HERMES_WEBUI_VALIDATE_KEY_ON_SAVE"


def _live_validate_api_key(provider_id: str, api_key: str) -> dict:
    """Fetch the provider's model list with the *candidate* key (pre-write).

    Returns ``{"status", "models_count", "detail"}`` where status is one of:

    - ``ok``      — endpoint returned a model list; the key works
    - ``auth``    — HTTP 401/403; the key is definitively rejected
    - ``network`` — unreachable / timeout; inconclusive (never blocks a save)
    - ``skipped`` — no live-query endpoint, keyless auth, or kill switch
    - ``unknown`` — unexpected error; inconclusive (never blocks a save)

    Never raises.  Uses the catalog's own query path so the probe hits the
    same URL and auth header style (Bearer vs x-api-key) that a real
    catalog refresh would.
    """
    if os.environ.get(_VALIDATE_KEY_ENV, "").strip().lower() in ("0", "false", "no"):
        return {"status": "skipped", "models_count": None, "detail": None}

    slug = model_catalog.resolve_alias(provider_id)
    rec = model_catalog.get_provider_record(slug)
    if rec is None or rec["refresh"]["mode"] != "api" or not rec.get("endpoint"):
        return {"status": "skipped", "models_count": None, "detail": None}
    if (rec.get("auth") or {}).get("type", "none") == "none":
        # Keyless provider — nothing to verify.
        return {"status": "skipped", "models_count": None, "detail": None}

    try:
        models = model_catalog.query_live_models(
            slug,
            api_key=api_key,
            timeout_s=model_catalog.provider_timeout_s(slug),
        )
        return {"status": "ok", "models_count": len(models), "detail": None}
    except urllib.error.HTTPError as exc:
        # HTTPError is an URLError subclass — must be caught first.
        if exc.code in (401, 403):
            return {"status": "auth", "models_count": None, "detail": f"HTTP {exc.code}"}
        return {"status": "unknown", "models_count": None, "detail": f"HTTP {exc.code}"}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"status": "network", "models_count": None, "detail": str(exc)}
    except Exception as exc:  # SSRF guard, JSON decode, ...
        return {"status": "unknown", "models_count": None, "detail": str(exc)}


def _maybe_kick_stale_probes(providers: list[dict]) -> None:
    """Refresh verification results for saved keys in the background.

    Fire-and-forget: GET /api/providers never waits on the network.  One
    kick per ``_PROBE_REFRESH_MIN_INTERVAL_S`` so opening Settings cannot
    stampede the providers.
    """
    global _last_probe_kick
    now = time.time()
    stale: list[str] = []
    for entry in providers:
        if not (entry.get("configurable") and entry.get("has_key")):
            continue
        status, checked_at = _get_probe_status(entry["id"])
        if status is None or checked_at is None or (now - checked_at) > _PROBE_STATUS_TTL_S:
            stale.append(entry["id"])
    if not stale:
        return
    with _PROBE_STATUS_LOCK:
        if now - _last_probe_kick < _PROBE_REFRESH_MIN_INTERVAL_S:
            return
        _last_probe_kick = now

    def _run() -> None:
        for pid in stale:
            probe_provider_connectivity(pid)

    threading.Thread(target=_run, name="probe-refresh", daemon=True).start()


def _resolve_probe_endpoint(provider_id: str | None) -> tuple[str, str, str] | None:
    """Resolve ``(base_url, api_key, provider_id)`` for the connectivity probe.

    base_url chain: ``providers.<id>.base_url`` → ``model.base_url`` (only
    when this provider is the active one) → ``custom_providers[].base_url``
    → the well-known OpenAI-compatible endpoint declared by the JSON model
    catalog (issue #10 — replaces the old routes.py hardcoded table and
    carries the corrected Z.ai endpoint).

    Returns ``None`` when no endpoint can be resolved — the probe is
    skipped rather than guessing a URL.
    """
    cfg = get_config()
    model_cfg = cfg.get("model", {})
    if not isinstance(model_cfg, dict):
        model_cfg = {}

    pid = (provider_id or "").strip().lower()
    if not pid:
        # No explicit provider: probe whatever is active.  config.yaml first,
        # then the auth store fallback — both local reads, no network.
        active = str(model_cfg.get("provider") or "").strip()
        if not active:
            try:
                from app.api.config import _config_only_catalog_fields
                _, active, _store = _config_only_catalog_fields()
                active = str(active or "")
            except Exception:
                active = ""
        pid = active.strip().lower()
    if not pid:
        return None

    base_url = ""
    # 1. providers.<id>.base_url
    providers_cfg = cfg.get("providers", {})
    if isinstance(providers_cfg, dict):
        prov = providers_cfg.get(pid, {})
        if isinstance(prov, dict):
            base_url = str(prov.get("base_url") or "").strip()
    # 2. model.base_url — only meaningful when this provider is the active one
    if not base_url:
        active = str(model_cfg.get("provider") or "").strip().lower()
        if active and active == pid:
            base_url = str(model_cfg.get("base_url") or "").strip()
    # 3. custom_providers[].base_url (same match logic as _provider_has_key)
    if not base_url:
        cp = named_custom_provider(pid)
        if cp is not None:
            base_url = str(cp.get("base_url") or "").strip()
    # 4. Well-known OpenAI-compatible endpoint from the JSON catalog.
    #    The probe appends "/models" itself, so use the raw base_url rather
    #    than model_catalog.provider_endpoint() (which joins models_path).
    #    provider_base_url() honors a user override (e.g. XIAOMI_BASE_URL
    #    for regional gateways) before falling back to the packaged default.
    if not base_url:
        try:
            from app.api import model_catalog as _model_catalog

            base_url = _model_catalog.provider_base_url(pid) or ""
        except Exception:
            base_url = ""
    if not base_url:
        return None

    api_key = _resolve_probe_key(cfg, pid, model_cfg)
    return base_url.rstrip("/"), api_key, pid


def _resolve_probe_key(cfg: dict, provider_id: str, model_cfg: dict) -> str:
    """Return the API key value for the connectivity probe.

    Mirrors ``_provider_has_key()``'s source order but yields the value:
    ``.env`` / process env var → ``model.api_key`` (active provider only) →
    ``providers.<id>.api_key`` → ``custom_providers[].api_key``.
    Returns ``""`` when nothing is configured.
    """
    env_vars = _provider_env_vars(provider_id)
    if env_vars:
        env_values = _load_env_file(_get_hermes_home() / ".env")
        for var in env_vars:
            key = str(env_values.get(var) or "").strip()
            if key:
                return key
        for var in env_vars:
            key = str(os.getenv(var) or "").strip()
            if key:
                return key
    active = str(model_cfg.get("provider") or "").strip().lower()
    if active and active == provider_id:
        key = str(model_cfg.get("api_key") or "").strip()
        if key:
            return key
    providers_cfg = cfg.get("providers", {})
    if isinstance(providers_cfg, dict):
        prov = providers_cfg.get(provider_id, {})
        if isinstance(prov, dict):
            key = str(prov.get("api_key") or "").strip()
            if key:
                return key
    cp = named_custom_provider(provider_id)
    if cp is not None:
        key = str(cp.get("api_key") or "").strip()
        if key:
            return key
    return ""


def probe_provider_connectivity(provider_id: str | None = None, *, timeout: float = 5.0) -> str:
    """Lightweight reachability/key probe: ``GET <base_url>/models`` (issue #8).

    Runs ONLY from background threads (boot prewarm, post key-save) so an
    unreachable provider or a bad key surfaces in the log at startup or at
    save time instead of as a silent 30s ConnectTimeout on the first chat.

    Returns one of:

    - ``'ok'``      endpoint reachable — network + TLS are fine
    - ``'auth'``    reachable but rejected the configured key (HTTP 401/403)
    - ``'network'`` connection failed (offline, DNS, proxy, blackholed IPv6)
    - ``'skipped'`` disabled via env, or no endpoint to probe

    Never raises; never called on a request path.
    """
    if os.environ.get("HERMES_WEBUI_CONNECTIVITY_PROBE", "").strip().lower() in ("0", "false", "no"):
        return "skipped"

    t0 = time.monotonic()
    try:
        resolved = _resolve_probe_endpoint(provider_id)
        if resolved is None:
            logger.debug("Connectivity probe skipped: no endpoint for provider %s",
                         provider_id or "(active)")
            return "skipped"
        base_url, api_key, pid = resolved

        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            base_url + "/models",
            headers={"Accept": "application/json"},
        )
        if api_key:
            # Match the catalog's declared header style — anthropic and
            # friends reject a Bearer token on /models (x-api-key).
            if model_catalog.provider_auth_header(pid).lower() == "x-api-key":
                req.add_header("x-api-key", api_key)
            else:
                req.add_header("Authorization", f"Bearer {api_key}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp.read(1024)  # drain a little so the connection closes cleanly
            logger.info("Connectivity probe: %s reachable (%.2fs)",
                        pid, time.monotonic() - t0)
            _record_probe_status(pid, "ok")
            return "ok"
        except urllib.error.HTTPError as exc:
            # Any HTTP status (even 404) proves network + TLS are fine; only
            # 401/403 *with* a configured key indicate a bad credential.
            if exc.code in (401, 403) and api_key:
                logger.warning(
                    "Connectivity probe: %s reachable but auth rejected (HTTP %d, "
                    "%.2fs) — check the API key", pid, exc.code, time.monotonic() - t0)
                _record_probe_status(pid, "auth")
                return "auth"
            logger.info("Connectivity probe: %s reachable (%.2fs, HTTP %d)",
                        pid, time.monotonic() - t0, exc.code)
            _record_probe_status(pid, "ok")
            return "ok"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning(
                "Connectivity probe: %s unreachable (%.2fs): %s — check outbound "
                "network/proxy (IPv4 preference is enabled by default, so a failure "
                "here is not an IPv6 issue)", pid, time.monotonic() - t0, exc)
            _record_probe_status(pid, "network")
            return "network"
    except Exception:
        logger.warning("Connectivity probe failed", exc_info=True)
        return "network"


# SECTION: Public API


def get_providers() -> dict[str, Any]:
    """Return a list of all known providers with their configuration status.

    Each entry contains:
    - ``id``: canonical provider slug
    - ``display_name``: human-readable name
    - ``has_key``: whether an API key is configured
    - ``configurable``: whether the key can be set from the WebUI
    - ``key_source``: where the key was found (``env_file``, ``env_var``,
      ``config_yaml``, ``oauth``, ``none``)
    - ``key_status``: last verification result for a saved key
      (``ok``/``auth``/``network``/``skipped``) or ``None`` when unknown
    - ``key_checked_at``: unix timestamp of that result, or ``None``
    - ``models``: list of known model IDs for this provider
    """
    providers = []

    # Collect all known provider IDs from multiple sources, folding
    # related catalog slugs (qwen, google, x-ai) into their canonical card.
    known_ids = {
        _provider_card_id(pid) for pid in model_catalog.catalog_providers()
    }

    # Also detect providers from config.yaml providers section
    cfg = get_config()
    providers_cfg = cfg.get("providers", {})
    if isinstance(providers_cfg, dict):
        known_ids.update(_provider_card_id(pid) for pid in providers_cfg.keys())

    # Add OAuth providers even if not in the catalog
    known_ids.update(_OAUTH_PROVIDERS)

    for pid in sorted(known_ids):
        display_name = model_catalog.provider_display(pid)
        is_oauth = _provider_is_oauth(pid)
        has_key = _provider_has_key(pid)

        # Determine key source
        key_source = "none"
        auth_error = None
        if is_oauth:
            key_source = "oauth"
            # Check if actually authenticated via hermes_cli.
            # IMPORTANT: do not unconditionally overwrite has_key from _provider_has_key().
            # A token in config.yaml is a valid credential even when get_auth_status()
            # returns logged_in=False (e.g. token not in the hermes credential pool,
            # or refresh token consumed by native Codex CLI / VS Code extension).
            try:
                from hermes_cli.auth import get_auth_status as _gas
                status = _gas(pid)
                if isinstance(status, dict) and status.get("logged_in"):
                    has_key = True
                    key_source = status.get("key_source", "oauth")
                elif has_key:
                    # _provider_has_key() found a token in config.yaml — respect it
                    # rather than hiding a working credential from the Settings UI.
                    key_source = "config_yaml"
                    auth_error = status.get("error") if isinstance(status, dict) else None
                else:
                    has_key = False
                    auth_error = status.get("error") if isinstance(status, dict) else None
            except Exception:
                # Import failed or auth check errored — don't override a known-good
                # key just because the hermes_cli auth module is unavailable.
                logger.debug("hermes_cli auth check failed for %s", pid, exc_info=True)
                # keep has_key from _provider_has_key()
        elif has_key:
            env_vars = _provider_env_vars(pid)
            if env_vars:
                env_path = _get_hermes_home() / ".env"
                env_values = _load_env_file(env_path)
                if any(env_values.get(v) for v in env_vars):
                    key_source = "env_file"
                elif any(os.getenv(v) for v in env_vars):
                    key_source = "env_var"
                else:
                    key_source = "config_yaml"
            else:
                key_source = "config_yaml"

        models = _provider_card_models(pid)
        # Also include models from config.yaml providers section
        if isinstance(providers_cfg, dict):
            provider_cfg = providers_cfg.get(pid, {})
            if isinstance(provider_cfg, dict) and "models" in provider_cfg:
                cfg_models = provider_cfg["models"]
                if isinstance(cfg_models, dict):
                    models = models + [{"id": k, "label": k} for k in cfg_models.keys()]
                elif isinstance(cfg_models, list):
                    models = models + [{"id": k, "label": k} for k in cfg_models]

        key_status, key_checked_at = _get_probe_status(pid)
        providers.append({
            "id": pid,
            "display_name": display_name,
            "has_key": has_key,
            "configurable": not is_oauth and bool(_provider_env_vars(pid)),
            "is_oauth": is_oauth,
            "key_source": key_source,
            "auth_error": auth_error,
            "key_status": key_status,
            "key_checked_at": key_checked_at,
            "models": models,
        })

    # Refresh stale verification results for saved keys in the background
    # (never blocks this GET — the badge may lag by one response).
    _maybe_kick_stale_probes(providers)

    # Determine active provider
    active_provider = None
    model_cfg = cfg.get("model", {})
    if isinstance(model_cfg, dict):
        active_provider = model_cfg.get("provider")

    return {
        "providers": providers,
        "active_provider": active_provider,
    }


def set_provider_key(provider_id: str, api_key: str | None) -> dict[str, Any]:
    """Set or update the API key for a provider.

    Writes the key to ``~/.hermes/.env`` using the standard env var name.
    If ``api_key`` is None or empty, the key is removed.

    Returns a status dict with the operation result.
    """
    provider_id = provider_id.strip().lower()

    if not provider_id:
        return {"ok": False, "error": "Provider ID is required."}

    if _provider_is_oauth(provider_id):
        return {
            "ok": False,
            "error": f"'{model_catalog.provider_display(provider_id)}' uses OAuth authentication. "
                     f"Use `hermes model` in the terminal to configure it.",
        }

    env_vars = _provider_env_vars(provider_id)
    if not env_vars:
        return {
            "ok": False,
            "error": f"Cannot configure API key for '{model_catalog.provider_display(provider_id)}'. "
                     f"This provider does not have a known env var mapping.",
        }
    # Multi-var providers (e.g. minimax: MINIMAX_API_KEY + MINIMAX_CN_API_KEY)
    # write to the first declared name; the remaining names are alternate
    # spellings scanned for key detection only.
    env_var = env_vars[0]

    # Validate API key format (basic sanity check)
    if api_key:
        api_key = api_key.strip()
        if "\n" in api_key or "\r" in api_key:
            return {"ok": False, "error": "API key must not contain newline characters."}
        if len(api_key) < 8:
            return {"ok": False, "error": "API key appears too short."}

    # Live-validate the candidate key BEFORE writing it: a rejected key
    # (HTTP 401/403) must not be saved and must surface as an error on the
    # Settings row.  Network / unexpected failures are inconclusive — the
    # key is still saved (with a warning) so a flaky endpoint or outage
    # never blocks a perfectly valid credential.
    validation = None
    if api_key:
        validation = _live_validate_api_key(provider_id, api_key)
        if validation["status"] == "auth":
            _record_probe_status(provider_id, "auth")
            return {
                "ok": False,
                "error": (
                    f"'{model_catalog.provider_display(provider_id)}' rejected the "
                    f"API key ({validation['detail']}). Check the key and try again."
                ),
            }

    env_path = _get_hermes_home() / ".env"
    try:
        _write_env_file(env_path, {env_var: api_key})
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        logger.exception("Failed to write env file for provider %s", provider_id)
        return {"ok": False, "error": f"Failed to save API key: {exc}"}

    # Invalidate the model cache so the dropdown refreshes on next request.
    # Using invalidate_models_cache() instead of reload_config() to avoid
    # disrupting active streaming sessions that may be reading config.cfg.
    invalidate_models_cache()

    # Record the verification result so GET /api/providers can badge the
    # row.  Anything else (skipped validation, inconclusive "unknown",
    # key removal) drops any stale result from a previous key.
    if validation is not None and validation["status"] in ("ok", "auth", "network"):
        _record_probe_status(provider_id, validation["status"])
    else:
        _clear_probe_status(provider_id)

    # Fire-and-forget connectivity probe so a bad key or an unreachable
    # endpoint shows up in the log now instead of stalling the first chat
    # for 30s (issue #8).  Daemon thread — the response contract is
    # unchanged and the caller never waits on the network.  Skipped only
    # when the sync validation already covered this save (it hit the same
    # endpoint) or this is a key removal.
    if api_key and (validation is None or validation["status"] == "skipped"):
        threading.Thread(
            target=probe_provider_connectivity,
            args=(provider_id,),
            name=f"probe-{provider_id}",
            daemon=True,
        ).start()

    result: dict[str, Any] = {
        "ok": True,
        "provider": provider_id,
        "display_name": model_catalog.provider_display(provider_id),
        "action": "updated" if api_key else "removed",
    }
    if validation is not None:
        result["validated"] = validation["status"] != "skipped"
        if validation["status"] == "ok":
            result["models_count"] = validation["models_count"]
        elif validation["status"] in ("network", "unknown"):
            result["warning"] = "Could not verify the key; it was saved anyway."
    return result


def remove_provider_key(provider_id: str) -> dict[str, Any]:
    """Remove the API key for a provider.

    Removes the key from ``~/.hermes/.env`` (via ``set_provider_key``)
    and also cleans up ``config.yaml`` if the key is stored there
    (``providers.<id>.api_key`` or top-level ``model.api_key`` when this
    provider is the active one).

    Returns a status dict with the operation result.
    """
    result = set_provider_key(provider_id, None)

    # Even if the .env removal succeeded, the key might also live in
    # config.yaml (e.g. providers.<id>.api_key or model.api_key).
    # Clean those up so _provider_has_key() returns False after removal.
    if result.get("ok"):
        _clean_provider_key_from_config(provider_id)

    return result


def _clean_provider_key_from_config(provider_id: str) -> None:
    """Remove provider API key entries from config.yaml.

    Handles three storage locations:
    1. ``providers.<id>.api_key`` — per-provider key
    2. ``model.api_key`` — top-level key (only if provider is active)
    3. ``custom_providers[].api_key`` — custom provider entries

    Writes back to config.yaml only if something was actually removed.
    Uses ``_cfg_lock`` to prevent TOCTOU races.
    """
    from app.api.config import _cfg_lock

    try:
        config_path = _get_config_path()
    except Exception:
        return

    if not config_path.exists():
        return

    try:
        import yaml as _yaml

        changed = False

        with _cfg_lock:
            raw = config_path.read_text(encoding="utf-8")
            cfg = _yaml.safe_load(raw)
            if not isinstance(cfg, dict):
                return

            # 1. Clean providers.<id>.api_key
            providers_cfg = cfg.get("providers", {})
            if isinstance(providers_cfg, dict):
                provider_cfg = providers_cfg.get(provider_id, {})
                if isinstance(provider_cfg, dict) and provider_cfg.get("api_key"):
                    del provider_cfg["api_key"]
                    changed = True

            # 2. Clean model.api_key — only if this provider is the active one
            model_cfg = cfg.get("model", {})
            if isinstance(model_cfg, dict) and model_cfg.get("api_key"):
                active_provider = model_cfg.get("provider")
                if active_provider and str(active_provider).strip().lower() == provider_id.lower():
                    del model_cfg["api_key"]
                    changed = True

            # 3. Clean custom_providers[].api_key
            custom_providers = cfg.get("custom_providers", [])
            if isinstance(custom_providers, list):
                for cp in custom_providers:
                    if isinstance(cp, dict):
                        cp_name = str(cp.get("name") or "").strip()
                        if not cp_name:
                            continue
                        cp_id = custom_provider_slug(cp_name)
                        if provider_id.lower() in {
                            cp_id,
                            f"custom:{cp_id}",
                        }:
                            if cp.get("api_key"):
                                del cp["api_key"]
                                changed = True

            if changed:
                _save_yaml_config_file(config_path, cfg)
        # Sync in-memory cache and bust model TTL cache
        # MUST be called outside _cfg_lock to avoid deadlock:
        # _cfg_lock is a threading.Lock (non-reentrant) and
        # reload_config() also acquires _cfg_lock internally.
        if changed:
            reload_config()
    except Exception:
        logger.exception("Failed to clean provider key from config.yaml for %s", provider_id)
