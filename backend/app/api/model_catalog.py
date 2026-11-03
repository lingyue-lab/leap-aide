"""JSON-backed model catalog (issue #10).

Single source of truth for provider display names, aliases, baseline model
lists, live-query endpoints, auth hints, and refresh policy.  The catalog
file ships with the app at ``backend/app/api/data/model_catalog.json`` and
is meant to be edited by humans via PR — adding a model or fixing an
endpoint never requires a Python change.

Design boundaries:
  * The catalog decides WHAT an eligible provider shows, never WHETHER it
    appears.  Key detection / eligibility stays in config.py + providers.py.
  * Live queries run inside the existing catalog build (TTL / invalidation /
    mtime / prewarm driven).  This module never spawns background threads.
  * Merge rules are single and deterministic: on success show
    "JSON baseline ∪ live results" (baseline entries and labels win; new
    live models are appended; live-only models that disappear upstream are
    dropped automatically); on failure keep the last successful result and
    fall back to the baseline — a group is never cleared by a failed query.

Pure stdlib only (urllib / json / socket / threading) — no pyyaml, no
imports from other app.api modules at module level (callers pass in any
config-derived values such as trusted hostnames).
"""

from __future__ import annotations

import copy
import ipaddress
import json
import logging
import os
import socket
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Packaged catalog location — ships with the app, human-edited via PR.
CATALOG_PATH = Path(__file__).resolve().parent / "data" / "model_catalog.json"

SUPPORTED_SCHEMA_VERSION = 1

# Per-provider live-query timeout (seconds).  Override globally via env.
_DEFAULT_QUERY_TIMEOUT_S = float(os.getenv("HERMES_WEBUI_CATALOG_QUERY_TIMEOUT_S", "3.0"))

# DNS resolution timeout for the SSRF guard (seconds).  socket.getaddrinfo
# honors no timeout of its own; an unresponsive resolver used to hang the
# whole catalog build (the worker waits on as_completed with no bound), so
# resolve on a helper thread and join with this deadline.
_DEFAULT_DNS_TIMEOUT_S = float(os.getenv("HERMES_WEBUI_CATALOG_DNS_TIMEOUT_S", "3.0"))

# Hostname substrings that are allowed to resolve to private/loopback IPs
# (local model services declared by the user).
_KNOWN_LOCAL_HOST_MARKERS = ("ollama", "localhost", "127.0.0.1", "lmstudio", "lm-studio")

_VALID_AUTH_TYPES = ("api_key", "oauth", "none")
_VALID_REFRESH_MODES = ("api", "none")
_VALID_MEMBERSHIPS = ("replace", "curated")

_empty_catalog: dict = {
    "schema_version": SUPPORTED_SCHEMA_VERSION,
    "aliases": {},
    "providers": {},
}

_catalog: dict = _empty_catalog
_catalog_mtime: float = 0.0
_alias_map: dict[str, str] = {}  # alias (lowercased) -> canonical slug
_env_vars_map: dict[str, list[str]] = {}  # slug -> env vars to scan for keys
_catalog_lock = threading.RLock()

# Last successfully resolved model list per provider.  Survives failed
# refreshes within a process; the disk cache envelope additionally carries
# provider_models so last-success survives restarts.
_LAST_GOOD_MODELS: dict[str, list[dict]] = {}
_last_good_lock = threading.RLock()


# ── Loading & validation ────────────────────────────────────────────────────


def catalog_path() -> Path:
    """Return the filesystem path of the catalog JSON file."""
    return CATALOG_PATH


def catalog_mtime() -> float:
    """Return the catalog file mtime, or 0.0 when the file is missing."""
    try:
        return CATALOG_PATH.stat().st_mtime
    except OSError:
        return 0.0


def _build_indexes(catalog: dict) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Build the alias reverse-map and env-var map from a validated catalog."""
    aliases: dict[str, str] = {}
    env_map: dict[str, list[str]] = {}
    providers = catalog.get("providers", {})
    for slug, rec in providers.items():
        auth = rec.get("auth") or {}
        if auth.get("type") == "api_key":
            envs = auth.get("env_vars") or []
            if not envs and auth.get("env_var"):
                envs = [auth["env_var"]]
            if envs:
                env_map[slug] = [str(e) for e in envs if e]
        for alias in rec.get("aliases") or []:
            aliases[str(alias).strip().lower()] = slug
    # Top-level aliases map wins over per-provider arrays (lets the catalog
    # express cross-key quirks such as "aws" -> "bedrock" with no provider
    # record for the target).
    for alias, target in (catalog.get("aliases") or {}).items():
        aliases[str(alias).strip().lower()] = str(target)
    return aliases, env_map


def _validate_provider(slug: str, rec: object) -> dict | None:
    """Validate one provider record; return a normalized copy or None."""
    if not isinstance(rec, dict):
        logger.warning("model_catalog: provider %r is not an object; skipped", slug)
        return None
    display_name = rec.get("display_name")
    if not isinstance(display_name, str) or not display_name.strip():
        logger.warning("model_catalog: provider %r missing display_name; skipped", slug)
        return None

    auth = rec.get("auth") or {}
    if not isinstance(auth, dict):
        logger.warning("model_catalog: provider %r auth must be an object; skipped", slug)
        return None
    auth_type = auth.get("type", "none")
    if auth_type not in _VALID_AUTH_TYPES:
        logger.warning(
            "model_catalog: provider %r has invalid auth.type %r; skipped", slug, auth_type
        )
        return None

    endpoint = rec.get("endpoint")
    if endpoint is not None:
        if not isinstance(endpoint, dict) or not str(endpoint.get("base_url") or "").strip():
            logger.warning("model_catalog: provider %r has invalid endpoint; skipped", slug)
            return None

    refresh = rec.get("refresh") or {}
    if not isinstance(refresh, dict):
        logger.warning("model_catalog: provider %r refresh must be an object; skipped", slug)
        return None
    mode = refresh.get("mode", "none")
    if mode not in _VALID_REFRESH_MODES:
        logger.warning("model_catalog: provider %r has invalid refresh.mode %r; skipped", slug, mode)
        return None
    membership = refresh.get("membership", "replace")
    if membership not in _VALID_MEMBERSHIPS:
        logger.warning(
            "model_catalog: provider %r has invalid refresh.membership %r; skipped",
            slug,
            membership,
        )
        return None

    # Gateway variant: this catalog entry is a second gateway of an
    # SDK-known provider (e.g. xiaomi-token-plan → xiaomi), each with its
    # own base_url and env var so both gateways can be configured at once.
    sdk_provider = rec.get("sdk_provider")
    if sdk_provider is not None:
        if not isinstance(sdk_provider, str) or not sdk_provider.strip():
            logger.warning(
                "model_catalog: provider %r has invalid sdk_provider; skipped", slug
            )
            return None
        sdk_provider = sdk_provider.strip()
        if sdk_provider == slug:
            logger.warning(
                "model_catalog: provider %r sdk_provider points at itself; ignored",
                slug,
            )
            sdk_provider = None

    raw_models = rec.get("models") or []
    if not isinstance(raw_models, list):
        logger.warning("model_catalog: provider %r models must be a list; skipped", slug)
        return None
    models: list[dict] = []
    for m in raw_models:
        if not isinstance(m, dict) or not str(m.get("id") or "").strip():
            logger.warning("model_catalog: provider %r has invalid model entry; skipped", slug)
            return None
        entry: dict = {"id": str(m["id"]), "label": str(m.get("label") or m["id"])}
        if "supports_extended_thinking" in m:
            entry["supports_extended_thinking"] = bool(m["supports_extended_thinking"])
        models.append(entry)

    out = {
        "display_name": display_name.strip(),
        "aliases": [str(a) for a in (rec.get("aliases") or []) if str(a).strip()],
        "auth": {
            "type": auth_type,
            "env_var": str(auth.get("env_var") or "") or None,
            "env_vars": [str(e) for e in (auth.get("env_vars") or []) if e] or None,
            "header": str(auth.get("header") or "Bearer"),
        },
        "endpoint": None,
        "refresh": {
            "mode": mode,
            "membership": membership,
            "timeout_s": float(refresh.get("timeout_s") or _DEFAULT_QUERY_TIMEOUT_S),
        },
        "default_model": str(rec.get("default_model") or "") or None,
        "sdk_provider": sdk_provider,
        "models": models,
    }
    if isinstance(endpoint, dict):
        out["endpoint"] = {
            "base_url": str(endpoint.get("base_url") or "").strip().rstrip("/"),
            # Optional SDK-style base-URL override var (e.g. XIAOMI_BASE_URL
            # for regional gateways like token-plan-cn.xiaomimimo.com).
            "base_url_env": str(endpoint.get("base_url_env") or "").strip() or None,
            "models_path": str(endpoint.get("models_path") or "/models"),
            "trust_local": bool(endpoint.get("trust_local", False)),
        }
    return out


def load_catalog(*, force: bool = False) -> dict:
    """Parse and validate the catalog JSON, cached by file mtime.

    File-level problems (missing file, bad JSON, wrong schema_version)
    yield an empty catalog plus a log line — callers degrade to "no
    catalog records" rather than crashing.  Provider-level problems drop
    only the offending record.
    """
    global _catalog, _catalog_mtime, _alias_map, _env_vars_map
    with _catalog_lock:
        mtime = catalog_mtime()
        if not force and _catalog is not _empty_catalog and mtime == _catalog_mtime:
            return _catalog
        if mtime == 0.0:
            if not force and _catalog is not _empty_catalog:
                return _catalog
            logger.warning("model_catalog: %s missing; using empty catalog", CATALOG_PATH)
            _catalog = _empty_catalog
            _alias_map, _env_vars_map = {}, {}
            _catalog_mtime = 0.0
            return _catalog
        try:
            with open(CATALOG_PATH, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            logger.warning("model_catalog: failed to parse %s; using empty catalog", CATALOG_PATH, exc_info=True)
            _catalog = _empty_catalog
            _alias_map, _env_vars_map = {}, {}
            _catalog_mtime = mtime
            return _catalog
        if not isinstance(raw, dict) or raw.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
            logger.warning(
                "model_catalog: unsupported schema_version in %s; using empty catalog",
                CATALOG_PATH,
            )
            _catalog = _empty_catalog
            _alias_map, _env_vars_map = {}, {}
            _catalog_mtime = mtime
            return _catalog
        providers_raw = raw.get("providers")
        if not isinstance(providers_raw, dict):
            logger.warning("model_catalog: providers must be an object; using empty catalog")
            _catalog = _empty_catalog
            _alias_map, _env_vars_map = {}, {}
            _catalog_mtime = mtime
            return _catalog
        providers: dict[str, dict] = {}
        for slug, rec in providers_raw.items():
            validated = _validate_provider(str(slug), rec)
            if validated is not None:
                providers[str(slug)] = validated
            else:
                logger.warning("model_catalog: dropped invalid provider %r", slug)
        top_aliases = raw.get("aliases")
        if not isinstance(top_aliases, dict):
            top_aliases = {}
        _catalog = {
            "schema_version": SUPPORTED_SCHEMA_VERSION,
            "aliases": top_aliases,
            "providers": providers,
        }
        _alias_map, _env_vars_map = _build_indexes(_catalog)
        _catalog_mtime = mtime
        return _catalog


def catalog_providers() -> list[str]:
    """Return the list of provider slugs present in the (loaded) catalog."""
    return sorted(load_catalog().get("providers", {}).keys())


# ── Lookup helpers ──────────────────────────────────────────────────────────


def resolve_alias(name: str) -> str:
    """Return the canonical provider slug for *name*.

    Lookup is case-insensitive and whitespace-trimmed; unknown names pass
    through unchanged (same semantics as the legacy _PROVIDER_ALIASES).
    """
    if not name:
        return name
    load_catalog()
    raw = str(name).strip().lower()
    return _alias_map.get(raw, name)


def get_provider_record(slug: str) -> dict | None:
    """Return the validated catalog record for *slug*, or None.

    Exact provider-key matches win; otherwise the name is alias-resolved
    first (so ``dashscope`` finds the ``qwen`` record).
    """
    if not slug:
        return None
    catalog = load_catalog()
    providers = catalog.get("providers", {})
    rec = providers.get(slug)
    if rec is not None:
        return rec
    # Alias-resolve first so "dashscope" finds the qwen record, etc.
    return providers.get(resolve_alias(slug))


def provider_display(slug: str) -> str:
    """Return the display name for *slug* (falls back to title-cased slug)."""
    rec = get_provider_record(slug)
    if rec is not None:
        return rec["display_name"]
    return str(slug).title()


def provider_baseline_models(slug: str) -> list[dict]:
    """Return a deep copy of the hand-curated baseline model list for *slug*."""
    rec = get_provider_record(slug)
    if rec is None:
        return []
    return copy.deepcopy(rec["models"])


def provider_default_model(slug: str) -> str | None:
    """Return the catalog-level default model for *slug*, if declared."""
    rec = get_provider_record(slug)
    if rec is None:
        return None
    return rec.get("default_model")


def provider_sdk_provider(slug: str) -> str | None:
    """Return the SDK provider slug for a gateway-variant catalog entry.

    Entries that declare ``sdk_provider`` are WebUI-only aliases for a
    second gateway of an SDK-known provider (e.g. ``xiaomi-token-plan``
    routes through the SDK's ``xiaomi`` provider with an explicit
    base_url and its own key).  Regular entries return None.
    """
    rec = get_provider_record(slug)
    if rec is None:
        return None
    return rec.get("sdk_provider")


def find_gateway_variant(sdk_provider: str, base_url: str) -> str | None:
    """Return the gateway-variant slug whose endpoint matches *base_url*.

    config.yaml persists a gateway variant as its SDK provider plus an
    explicit ``base_url`` (the hermes CLI only understands SDK slugs), so
    the WebUI needs the reverse mapping to recover the variant — and with
    it the variant's private env var — from that persisted shape.
    Returns None when no variant of *sdk_provider* serves *base_url*.
    """
    if not sdk_provider or not base_url:
        return None
    want = str(base_url).strip().rstrip("/")
    if not want:
        return None
    for slug in catalog_providers():
        rec = get_provider_record(slug)
        if rec is None or rec.get("sdk_provider") != sdk_provider:
            continue
        if (provider_base_url(slug) or "").rstrip("/") == want:
            return slug
    return None


def provider_api_key(slug: str) -> str:
    """Return the configured API key value for *slug*, or ``""``.

    Reads the env vars declared by the catalog auth block, preferring the
    hermes ``.env`` file over the process environment (keys are edited
    there via Settings; the backend process env may lag).  Order mirrors
    ``providers._resolve_probe_key``'s env-var branch.
    """
    rec = get_provider_record(slug)
    if rec is None:
        return ""
    auth = rec.get("auth") or {}
    env_vars = list(auth.get("env_vars") or [])
    if auth.get("env_var"):
        env_vars = [auth["env_var"]] + env_vars
    if not env_vars:
        return ""
    env_values: dict = {}
    try:
        # Keys live in the hermes .env file; the backend process environment
        # does not always carry them.  Lazy import: providers.py imports this
        # module at top level.
        from app.api.providers import _get_hermes_home, _load_env_file

        env_values = _load_env_file(_get_hermes_home() / ".env")
    except Exception:
        env_values = {}
    for var in env_vars:
        val = str(env_values.get(var) or "").strip()
        if val:
            return val
    for var in env_vars:
        val = str(os.environ.get(var) or "").strip()
        if val:
            return val
    return ""


def provider_base_url(slug: str) -> str | None:
    """Return the effective base URL for *slug*, honoring a user override.

    When the catalog endpoint declares ``base_url_env`` (SDK-style override
    var, e.g. ``XIAOMI_BASE_URL`` for regional gateways such as
    token-plan-cn.xiaomimimo.com), a non-empty value from the process
    environment or the hermes ``.env`` file wins over the packaged
    ``base_url``.  Otherwise the catalog default applies.
    """
    rec = get_provider_record(slug)
    if rec is None or rec.get("endpoint") is None:
        return None
    ep = rec["endpoint"]
    base = ep["base_url"]
    env_var = ep.get("base_url_env")
    if env_var:
        override = str(os.environ.get(env_var) or "").strip()
        if not override:
            # Keys and gateway overrides live in the hermes .env file; the
            # backend process environment does not always carry them.
            # Lazy import: providers.py imports this module at top level.
            try:
                from app.api.providers import _get_hermes_home, _load_env_file

                override = str(
                    _load_env_file(_get_hermes_home() / ".env").get(env_var) or ""
                ).strip()
            except Exception:
                override = ""
        if override:
            base = override.rstrip("/")
    return base or None


def provider_endpoint(slug: str) -> str | None:
    """Return the full models-endpoint URL (base_url + models_path) or None."""
    base = provider_base_url(slug)
    if base is None:
        return None
    rec = get_provider_record(slug)
    path = rec["endpoint"]["models_path"] if rec else "/models"
    if not path:
        return base
    return base + "/" + path.lstrip("/")


def provider_auth_header(slug: str) -> str:
    """Return the auth header style for *slug*: ``Bearer`` (default) or ``x-api-key``."""
    rec = get_provider_record(slug)
    if rec is None:
        return "Bearer"
    return (rec.get("auth") or {}).get("header") or "Bearer"


def provider_timeout_s(slug: str) -> float:
    """Return the per-provider live-query timeout for *slug*."""
    rec = get_provider_record(slug)
    if rec is None:
        return _DEFAULT_QUERY_TIMEOUT_S
    return float(rec["refresh"].get("timeout_s") or _DEFAULT_QUERY_TIMEOUT_S)


def catalog_env_vars() -> dict[str, list[str]]:
    """Return {slug: [env vars...]} for api_key-authenticated providers."""
    load_catalog()
    return {slug: list(envs) for slug, envs in _env_vars_map.items()}


def should_query_live(slug: str, *, has_key: bool) -> bool:
    """Whether *slug* should be live-queried during a catalog rebuild.

    Requires a catalog record with refresh.mode=api and an endpoint, plus
    eligibility: keyless-local providers (auth.type=none) always qualify;
    api_key providers require a configured key.  This function does NOT
    decide provider *eligibility* — the caller only invokes it for
    providers already detected by key/config.
    """
    rec = get_provider_record(slug)
    if rec is None:
        return False
    if rec["refresh"]["mode"] != "api" or not rec.get("endpoint"):
        return False
    auth_type = (rec.get("auth") or {}).get("type", "none")
    if auth_type == "none":
        return True
    return bool(has_key)


# ── HTTP query + SSRF safety ────────────────────────────────────────────────


class ProviderAuthError(Exception):
    """Live models query rejected the credential (HTTP 401/403).

    Raised by ``resolve_provider_models`` instead of falling back to the
    baseline/last-success lists: a rejected key must hide the provider's
    models (the catalog decides WHAT shows), while transient failures keep
    the fallback behavior.  Callers (the catalog build) catch this to omit
    the provider's group entirely.
    """

    def __init__(self, slug: str, status: int):
        super().__init__(f"provider {slug!r} rejected the API key (HTTP {status})")
        self.slug = slug
        self.status = status


def _is_auth_http_error(exc: BaseException) -> bool:
    """True when *exc* is an HTTP 401/403 — a definite credential rejection."""
    return isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403)


def _getaddrinfo_limited(
    hostname: str, timeout_s: float = _DEFAULT_DNS_TIMEOUT_S
) -> list | None:
    """``socket.getaddrinfo`` bounded by *timeout_s*.

    Returns the address list, ``None`` when resolution fails, or ``None``
    after the deadline (the helper thread is daemonized and abandoned —
    its result is discarded).  getaddrinfo itself has no timeout knob, so
    run it off-thread and join with a deadline.
    """
    result: dict[str, list] = {}

    def _resolve() -> None:
        try:
            result["ips"] = socket.getaddrinfo(hostname, None)
        except OSError:
            pass

    worker = threading.Thread(target=_resolve, daemon=True, name="catalog-dns")
    worker.start()
    worker.join(timeout_s)
    return result.get("ips")


def _check_url_safe(url: str, trust_local_hosts: set[str] | frozenset[str] = frozenset()) -> None:
    """Raise ValueError when *url* is unsafe to fetch (SSRF guard).

    Scheme must be http(s); hostnames resolving to private/loopback/
    link-local IPs are rejected unless they match a known-local marker
    (ollama / localhost / 127.0.0.1 / lmstudio) or are explicitly trusted
    (user-declared custom_providers, catalog endpoint.trust_local).
    """
    parsed = urlparse(url if "://" in url else f"http://{url}")
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Invalid URL scheme: {parsed.scheme}")
    if not parsed.hostname:
        return
    resolved_ips = _getaddrinfo_limited(parsed.hostname)
    if resolved_ips is None:
        # Unresolvable or DNS timeout: let urlopen fail on its own terms
        # (its socket timeout is bounded); nothing to SSRF-check here.
        return
    host_lower = (parsed.hostname or "").lower()
    for _, _, _, _, addr in resolved_ips:
        try:
            addr_obj = ipaddress.ip_address(addr[0])
        except ValueError:
            continue
        if addr_obj.is_private or addr_obj.is_loopback or addr_obj.is_link_local:
            is_known_local = any(k in host_lower for k in _KNOWN_LOCAL_HOST_MARKERS) or (
                host_lower in trust_local_hosts
            )
            if not is_known_local:
                raise ValueError(f"SSRF: resolved hostname to private IP {addr[0]}")


def _auth_headers(slug: str, api_key: str) -> dict[str, str]:
    """Build auth headers for a live query against *slug*'s endpoint."""
    if not api_key:
        return {}
    style = provider_auth_header(slug)
    if style.lower() == "x-api-key":
        return {"x-api-key": api_key}
    return {"Authorization": f"Bearer {api_key}"}


def _extract_model_entries(payload: object, label_fn=None) -> list[dict]:
    """Normalize an OpenAI-style (or ``{"models": [...]}``) payload to entries."""
    models_list: list = []
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            models_list = payload["data"]
        elif isinstance(payload.get("models"), list):
            models_list = payload["models"]
    elif isinstance(payload, list):
        models_list = payload
    out: list[dict] = []
    for model in models_list:
        if not isinstance(model, dict):
            continue
        model_id = str(model.get("id", "") or model.get("name", "") or model.get("model", ""))
        model_name = str(model.get("name", "") or model.get("model", "") or model_id)
        if not model_id:
            continue
        if label_fn is not None:
            label = label_fn(model_id)
        else:
            label = model_name or model_id
        out.append({"id": model_id, "label": label})
    return out


def query_openai_compat(
    url: str,
    *,
    api_key: str = "",
    timeout_s: float | None = None,
    auth_header: str = "Bearer",
    trust_local_hosts: set[str] | frozenset[str] = frozenset(),
    label_fn=None,
) -> list[dict]:
    """Fetch an OpenAI-compatible ``/models`` endpoint.

    Shared by the custom ``model.base_url`` fetch path and catalog-driven
    live queries.  Returns ``[{id, label}, ...]``; raises on any failure
    (network, SSRF, bad payload) — callers treat exceptions as "query
    failed, keep prior/baseline".
    """
    _check_url_safe(url, trust_local_hosts)
    timeout = _DEFAULT_QUERY_TIMEOUT_S if timeout_s is None else float(timeout_s)
    req = urllib.request.Request(url, method="GET")  # nosec B310
    req.add_header("User-Agent", "OpenAI/Python 1.0")
    if api_key:
        if auth_header.lower() == "x-api-key":
            req.add_header("x-api-key", api_key)
        else:
            req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=timeout) as response:  # nosec B310
        payload = json.loads(response.read().decode("utf-8"))
    return _extract_model_entries(payload, label_fn=label_fn)


def query_live_models(
    slug: str,
    *,
    api_key: str = "",
    timeout_s: float | None = None,
    trust_local_hosts: set[str] | frozenset[str] = frozenset(),
    label_fn=None,
) -> list[dict]:
    """Live-query *slug*'s catalog endpoint; return ``[{id, label}, ...]``.

    Raises on failure.  ``endpoint.trust_local`` providers (ollama /
    lmstudio localhost endpoints) add their own hostname to the SSRF
    allowlist automatically.
    """
    rec = get_provider_record(slug)
    if rec is None or not rec.get("endpoint"):
        raise ValueError(f"model_catalog: no endpoint configured for provider {slug!r}")
    url = provider_endpoint(slug)
    if not url:
        raise ValueError(f"model_catalog: no endpoint configured for provider {slug!r}")
    ep = rec["endpoint"]
    trusted = set(trust_local_hosts)
    if ep.get("trust_local"):
        host = urlparse(url).hostname
        if host:
            trusted.add(host.lower())
    return query_openai_compat(
        url,
        api_key=api_key,
        timeout_s=timeout_s if timeout_s is not None else provider_timeout_s(slug),
        auth_header=(rec.get("auth") or {}).get("header") or "Bearer",
        trust_local_hosts=trusted,
        label_fn=label_fn,
    )


# ── Merge + resolve ─────────────────────────────────────────────────────────


def _norm_id(model_id: str, slug: str | None = None) -> str:
    """Normalize a model id for cross-shape comparison.

    Strips a leading ``@anything:`` routing prefix so baseline ids like
    ``@zai:glm-5.1`` compare equal to live API ids like ``glm-5.1``.
    """
    mid = str(model_id or "")
    if mid.startswith("@") and ":" in mid:
        mid = mid.split(":", 1)[1]
    return mid


def merge_models(
    slug: str,
    baseline: list[dict],
    live_entries: list[dict],
) -> list[dict]:
    """Derive the provider's model list from a successful live query.

    replace (default): the live API result is the single source of
    truth — membership and order come from the response.  The JSON
    baseline contributes display metadata only: a curated label (and
    ``supports_extended_thinking`` flag, when set) for ids the API still
    lists, matched after ``@provider:``-prefix normalization.  Models the
    API omits vanish on the next rebuild, and API-only models appear
    immediately — no baseline residue.

    curated: intersection only — baseline entries the API still lists, in
    baseline order.  Aggregator sources use this to avoid bloating the
    dropdown with hundreds of unpromoted models.
    """
    rec = get_provider_record(slug)
    membership = "replace"
    if rec is not None:
        membership = rec["refresh"].get("membership", "replace")

    if membership == "curated":
        live_keys = {_norm_id(e.get("id", "")) for e in live_entries}
        return [
            copy.deepcopy(m)
            for m in baseline
            if _norm_id(m.get("id", "")) in live_keys
            or str(m.get("id", "")) in live_keys
        ]

    # replace: API list wins; baseline donates labels for surviving ids.
    baseline_by_norm: dict[str, dict] = {}
    for m in baseline:
        mid = str(m.get("id", ""))
        if not mid:
            continue
        baseline_by_norm[_norm_id(mid)] = m
        baseline_by_norm.setdefault(mid, m)

    out: list[dict] = []
    seen: set[str] = set()
    for entry in live_entries:
        live_id = str(entry.get("id", ""))
        if not live_id:
            continue
        norm = _norm_id(live_id)
        if norm in seen or live_id in seen:
            continue
        seen.add(norm)
        seen.add(live_id)
        donor = baseline_by_norm.get(live_id) or baseline_by_norm.get(norm)
        label = str(entry.get("label") or live_id)
        if donor is not None and donor.get("label"):
            label = str(donor["label"])
        new_entry: dict = {"id": live_id, "label": label}
        et = entry.get("supports_extended_thinking")
        if et is None and donor is not None:
            et = donor.get("supports_extended_thinking")
        if et is not None:
            new_entry["supports_extended_thinking"] = bool(et)
        out.append(new_entry)
    return out


def remember_good_models(slug: str, models: list[dict]) -> None:
    """Record the last successfully resolved model list for *slug*."""
    with _last_good_lock:
        _LAST_GOOD_MODELS[slug] = copy.deepcopy(models)


def last_good_models(slug: str) -> list[dict] | None:
    """Return the last successful model list for *slug*, or None."""
    with _last_good_lock:
        models = _LAST_GOOD_MODELS.get(slug)
        return copy.deepcopy(models) if models is not None else None


def forget_last_good_models(slug: str | None = None) -> None:
    """Clear last-success records: all of them, or just *slug*'s.

    Per-slug clearing is used on auth failure so models fetched with a
    since-revoked key cannot resurface through the last-success fallback.
    """
    with _last_good_lock:
        if slug is None:
            _LAST_GOOD_MODELS.clear()
        else:
            _LAST_GOOD_MODELS.pop(slug, None)


def _normalize_cfg_override(cfg_models_override: object) -> list[dict] | None:
    """Normalize a config.yaml providers.<id>.models override to [{id,label}].

    Matches the legacy builder semantics: dict keys or list items become
    both id and label.  Returns None when the override is absent.
    """
    if cfg_models_override is None:
        return None
    if isinstance(cfg_models_override, dict):
        return [{"id": str(k), "label": str(k)} for k in cfg_models_override.keys()]
    if isinstance(cfg_models_override, list):
        return [{"id": str(k), "label": str(k)} for k in cfg_models_override]
    return None


def resolve_provider_models(
    slug: str,
    *,
    cfg_models_override: object = None,
    api_key: str = "",
    prior: list[dict] | None = None,
    timeout_s: float | None = None,
    label_fn=None,
    trust_local_hosts: set[str] | frozenset[str] = frozenset(),
) -> list[dict]:
    """Resolve the final model list for an eligible provider.

    Priority: config.yaml override > live API result on success (the
    API is the single source of truth; the baseline only donates
    display labels for ids it still lists) > last-success on failure >
    baseline.  A failed live query NEVER empties a previously
    non-empty list — except an auth failure (HTTP 401/403), which
    raises ``ProviderAuthError`` so the caller can hide the group.
    """
    # 1. Explicit user config always wins; no live query.
    override = _normalize_cfg_override(cfg_models_override)
    if override is not None:
        return override

    rec = get_provider_record(slug)
    baseline = provider_baseline_models(slug)

    # 2. Unknown provider: keep last success only.
    if rec is None:
        if prior:
            return copy.deepcopy(prior)
        return last_good_models(slug) or []

    # 3. refresh.mode=none or no endpoint: baseline, with last-success
    #    preferred when the baseline is empty (e.g. ollama-cloud).
    if rec["refresh"]["mode"] != "api" or not rec.get("endpoint"):
        if baseline:
            remember_good_models(slug, baseline)
            return baseline
        return copy.deepcopy(prior) if prior else (last_good_models(slug) or [])

    # 4. Live query (caller gates eligibility; re-check here for safety).
    auth_type = (rec.get("auth") or {}).get("type", "none")
    if auth_type != "none" and not api_key:
        # Not eligible to query — fall through to failure path.
        if prior:
            return copy.deepcopy(prior)
        return baseline or last_good_models(slug) or []
    try:
        live_entries = query_live_models(
            slug,
            api_key=api_key,
            timeout_s=timeout_s,
            trust_local_hosts=trust_local_hosts,
            label_fn=label_fn,
        )
    except Exception as exc:
        if _is_auth_http_error(exc):
            # A rejected credential is not transient: hide the provider's
            # models instead of falling back to baseline/last-success, and
            # drop the stale last-success entry so a revoked key's models
            # cannot resurface later.
            forget_last_good_models(slug)
            raise ProviderAuthError(slug, exc.code) from exc
        logger.debug("model_catalog: live query failed for %r; keeping prior/baseline", slug, exc_info=True)
        if prior:
            return copy.deepcopy(prior)
        remembered = last_good_models(slug)
        if remembered:
            return remembered
        return baseline

    # 5. Success: the live list is authoritative (baseline only donates
    #    labels).  An empty 200 payload is treated as inconclusive — a
    #    gateway bug must not blank a previously non-empty list.
    if not live_entries:
        logger.debug("model_catalog: live query returned no models for %r; keeping prior/baseline", slug)
        if prior:
            return copy.deepcopy(prior)
        remembered = last_good_models(slug)
        if remembered:
            return remembered
        return baseline
    out = merge_models(slug, baseline, live_entries)
    remember_good_models(slug, out)
    return out
