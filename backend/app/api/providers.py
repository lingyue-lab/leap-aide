"""Hermes Web UI -- provider management endpoints.

Closes #586 (allow provider key update) and part of #604 (model picker
multi-provider support).
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from app.api.config import (
    _PROVIDER_CARD_ALIASES,
    _PROVIDER_DISPLAY,
    _PROVIDER_MODELS,
    _get_config_path,
    _save_yaml_config_file,
    get_config,
    get_provider_card_models,
    invalidate_models_cache,
    reload_config,
)

logger = logging.getLogger(__name__)

# SECTION: Provider ↔ env var mapping

# Maps canonical provider slug → env var name for API key.
# Providers not listed here (OAuth/token-flow providers like copilot, nous,
# openai-codex) cannot have their keys managed from the WebUI.
_PROVIDER_ENV_VAR: dict[str, str] = {
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "copilot": "COPILOT_GITHUB_TOKEN",
    "alibaba": "DASHSCOPE_API_KEY",
    "google": "GOOGLE_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "huggingface": "HF_TOKEN",
    "zai": "GLM_API_KEY",
    "kimi-coding": "KIMI_API_KEY",
    "lmstudio": "LM_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "mistralai": "MISTRAL_API_KEY",
    "xai": "XAI_API_KEY",
    "opencode-zen": "OPENCODE_ZEN_API_KEY",
    "opencode-go": "OPENCODE_GO_API_KEY",
    "ollama": "OLLAMA_API_KEY",
    "ollama-cloud": "OLLAMA_API_KEY",
}
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
    env_var = _PROVIDER_ENV_VAR.get(provider_id)
    if env_var:
        env_path = _get_hermes_home() / ".env"
        env_values = _load_env_file(env_path)
        if env_values.get(env_var):
            return True
        if os.getenv(env_var):
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
    # Check custom_providers
    custom_providers = cfg.get("custom_providers", [])
    if isinstance(custom_providers, list):
        for cp in custom_providers:
            if isinstance(cp, dict):
                cp_name = (cp.get("name") or "").strip().lower().replace(" ", "-")
                if f"custom:{cp_name}" == provider_id or cp.get("name", "").strip().lower() == provider_id:
                    if str(cp.get("api_key") or "").strip():
                        return True
    return False


def _provider_is_oauth(provider_id: str) -> bool:
    """Check whether a provider uses OAuth/token flows (managed by CLI)."""
    return provider_id in _OAUTH_PROVIDERS


# SECTION: Connectivity probe (issue #8)


def _resolve_probe_endpoint(provider_id: str | None) -> tuple[str, str, str] | None:
    """Resolve ``(base_url, api_key, provider_id)`` for the connectivity probe.

    base_url chain: ``providers.<id>.base_url`` → ``model.base_url`` (only
    when this provider is the active one) → ``custom_providers[].base_url``
    → the shared OpenAI-compatible endpoint table in ``routes.py`` (lazily
    imported to avoid a circular import — routes imports this module at
    module level).  That table already covers well-known endpoints such as
    ``https://api.deepseek.com/v1``, the endpoint issue #8 stalled on, so no
    new hardcoded map is introduced here.

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
        for cp in cfg.get("custom_providers", []) or []:
            if not isinstance(cp, dict):
                continue
            cp_name = (cp.get("name") or "").strip().lower().replace(" ", "-")
            if f"custom:{cp_name}" == pid or cp.get("name", "").strip().lower() == pid:
                base_url = str(cp.get("base_url") or "").strip()
                break
    # 4. Well-known OpenAI-compatible endpoint table (lazily imported)
    if not base_url:
        try:
            from app.api.routes import _OPENAI_COMPAT_ENDPOINTS
            base_url = _OPENAI_COMPAT_ENDPOINTS.get(pid, "")
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
    env_var = _PROVIDER_ENV_VAR.get(provider_id)
    if env_var:
        env_values = _load_env_file(_get_hermes_home() / ".env")
        key = str(env_values.get(env_var) or "").strip()
        if key:
            return key
        key = str(os.getenv(env_var) or "").strip()
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
    custom_providers = cfg.get("custom_providers", [])
    if isinstance(custom_providers, list):
        for cp in custom_providers:
            if isinstance(cp, dict):
                cp_name = (cp.get("name") or "").strip().lower().replace(" ", "-")
                if f"custom:{cp_name}" == provider_id or cp.get("name", "").strip().lower() == provider_id:
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
            req.add_header("Authorization", f"Bearer {api_key}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp.read(1024)  # drain a little so the connection closes cleanly
            logger.info("Connectivity probe: %s reachable (%.2fs)",
                        pid, time.monotonic() - t0)
            return "ok"
        except urllib.error.HTTPError as exc:
            # Any HTTP status (even 404) proves network + TLS are fine; only
            # 401/403 *with* a configured key indicate a bad credential.
            if exc.code in (401, 403) and api_key:
                logger.warning(
                    "Connectivity probe: %s reachable but auth rejected (HTTP %d, "
                    "%.2fs) — check the API key", pid, exc.code, time.monotonic() - t0)
                return "auth"
            logger.info("Connectivity probe: %s reachable (%.2fs, HTTP %d)",
                        pid, time.monotonic() - t0, exc.code)
            return "ok"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning(
                "Connectivity probe: %s unreachable (%.2fs): %s — check outbound "
                "network/proxy (IPv4 preference is enabled by default, so a failure "
                "here is not an IPv6 issue)", pid, time.monotonic() - t0, exc)
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
    - ``models``: list of known model IDs for this provider
    """
    providers = []

    # Collect all known provider IDs from multiple sources, folding
    # OpenRouter-prefix cards (qwen, google, x-ai) into their canonical card.
    known_ids = {
        _PROVIDER_CARD_ALIASES.get(pid, pid)
        for pid in (_PROVIDER_DISPLAY.keys() | _PROVIDER_MODELS.keys())
    }

    # Also detect providers from config.yaml providers section
    cfg = get_config()
    providers_cfg = cfg.get("providers", {})
    if isinstance(providers_cfg, dict):
        known_ids.update(_PROVIDER_CARD_ALIASES.get(pid, pid) for pid in providers_cfg.keys())

    # Add OAuth providers even if not in _PROVIDER_DISPLAY
    known_ids.update(_OAUTH_PROVIDERS)

    for pid in sorted(known_ids):
        display_name = _PROVIDER_DISPLAY.get(pid, pid.replace("-", " ").title())
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
            env_var = _PROVIDER_ENV_VAR.get(pid)
            if env_var:
                env_path = _get_hermes_home() / ".env"
                env_values = _load_env_file(env_path)
                if env_values.get(env_var):
                    key_source = "env_file"
                elif os.getenv(env_var):
                    key_source = "env_var"
                else:
                    key_source = "config_yaml"
            else:
                key_source = "config_yaml"

        models = get_provider_card_models(pid)
        # Also include models from config.yaml providers section
        if isinstance(providers_cfg, dict):
            provider_cfg = providers_cfg.get(pid, {})
            if isinstance(provider_cfg, dict) and "models" in provider_cfg:
                cfg_models = provider_cfg["models"]
                if isinstance(cfg_models, dict):
                    models = models + [{"id": k, "label": k} for k in cfg_models.keys()]
                elif isinstance(cfg_models, list):
                    models = models + [{"id": k, "label": k} for k in cfg_models]

        providers.append({
            "id": pid,
            "display_name": display_name,
            "has_key": has_key,
            "configurable": not is_oauth and pid in _PROVIDER_ENV_VAR,
            "is_oauth": is_oauth,
            "key_source": key_source,
            "auth_error": auth_error,
            "models": models,
        })

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
            "error": f"'{_PROVIDER_DISPLAY.get(provider_id, provider_id)}' uses OAuth authentication. "
                     f"Use `hermes model` in the terminal to configure it.",
        }

    env_var = _PROVIDER_ENV_VAR.get(provider_id)
    if not env_var:
        return {
            "ok": False,
            "error": f"Cannot configure API key for '{_PROVIDER_DISPLAY.get(provider_id, provider_id)}'. "
                     f"This provider does not have a known env var mapping.",
        }

    # Validate API key format (basic sanity check)
    if api_key:
        api_key = api_key.strip()
        if "\n" in api_key or "\r" in api_key:
            return {"ok": False, "error": "API key must not contain newline characters."}
        if len(api_key) < 8:
            return {"ok": False, "error": "API key appears too short."}

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

    # Fire-and-forget connectivity probe so a bad key or an unreachable
    # endpoint shows up in the log now instead of stalling the first chat
    # for 30s (issue #8).  Daemon thread — the response contract is
    # unchanged and the caller never waits on the network.
    if api_key:
        import threading as _threading
        _threading.Thread(
            target=probe_provider_connectivity,
            args=(provider_id,),
            name=f"probe-{provider_id}",
            daemon=True,
        ).start()

    return {
        "ok": True,
        "provider": provider_id,
        "display_name": _PROVIDER_DISPLAY.get(provider_id, provider_id),
        "action": "updated" if api_key else "removed",
    }


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
                        cp_name = (cp.get("name") or "").strip().lower().replace(" ", "-")
                        if f"custom:{cp_name}" == provider_id or cp.get("name", "").strip().lower() == provider_id:
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
