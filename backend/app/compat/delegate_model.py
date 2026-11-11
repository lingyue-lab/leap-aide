"""Per-child model routing for ``delegate_task``.

The Hermes Agent SDK's ``delegate_task`` schema has no ``model`` property, and
the registry handler whitelists the arguments it forwards, so a top-level
``model`` is dropped before selection ever sees it — model selection always
resolves to the process-wide ``delegation.model`` block. The supervised
development loop (issue #57) needs a different model per phase: a cheap one for
mechanical work, a capable one for review.

The SDK must not be modified (AGENTS.md), so this installs a runtime shim —
the same pattern as :mod:`app.compat.browser_cdp` and
:mod:`app.compat.desktop_guard`:

1. **schema**  — inject a documented ``model`` property into the tool's dynamic
   overrides, so the model sees the parameter and emits it;
2. **handler** — capture ``args["model"]`` into a ContextVar before the
   whitelist drops it. The handler and ``_resolve_delegation_credentials``
   run on the same thread inside one ``delegate_task`` call, so the value is
   visible at selection time;
3. **select**  — overlay the ContextVar onto a *copy* of the delegation config.
   The SDK documents that mutating the dict returned by ``load_config_readonly``
   corrupts the in-process cache for every later caller, hence the copy.

Semantics: one model per ``delegate_task`` call, applied to every child in it.
When no model is requested the original code path runs untouched, so a
deployment that never passes ``model`` behaves exactly as the SDK does.
"""

from __future__ import annotations

import functools
import logging
import threading
from contextvars import ContextVar
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Set by the patched handler, read by the patched credential resolver. Both run
# on the same thread within one delegate_task call, so no locking is needed and
# concurrent sessions cannot bleed into each other.
MODEL_CTX: ContextVar[Optional[str]] = ContextVar(
    "leap_aide_delegate_model", default=None
)

_install_lock = threading.Lock()
_installed = False

# Injected into the model-facing schema. Kept deliberately conservative: it
# promises only what this shim delivers, so a deployment without the shim never
# advertises a parameter it would ignore.
_MODEL_PROPERTY: dict[str, Any] = {
    "type": "string",
    "description": (
        "Optional model id for the child agents of THIS call, applied to every "
        "task in it. Omit to inherit the session's model. Ignored when the "
        "deployment has not enabled per-child model routing."
    ),
}


def _patch_schema(entry: Any) -> None:
    """Expose ``model`` on the tool schema the model actually reads."""
    original = entry.dynamic_schema_overrides

    if original is None:
        def build_overrides() -> dict[str, Any]:
            return {"parameters": {"properties": {"model": dict(_MODEL_PROPERTY)}}}
    else:
        @functools.wraps(original)
        def build_overrides() -> dict[str, Any]:
            overrides = original()
            if not isinstance(overrides, dict):
                return overrides
            # Rebuild rather than mutate: _build_dynamic_schema_overrides hands
            # back a fresh dict, but a future SDK version may not.
            parameters = dict(overrides.get("parameters") or {})
            properties = dict(parameters.get("properties") or {})
            properties.setdefault("model", dict(_MODEL_PROPERTY))
            parameters["properties"] = properties
            return {**overrides, "parameters": parameters}

    build_overrides._leap_aide_delegate_model_schema = True
    entry.dynamic_schema_overrides = build_overrides


def _patch_handler(entry: Any) -> None:
    """Capture ``args["model"]`` before the registry's whitelist drops it."""
    original = entry.handler

    @functools.wraps(original)
    def handler(args: Any, **kwargs: Any) -> Any:
        requested = args.get("model") if isinstance(args, dict) else None
        if not isinstance(requested, str) or not requested.strip():
            return original(args, **kwargs)
        token = MODEL_CTX.set(requested.strip())
        try:
            return original(args, **kwargs)
        finally:
            MODEL_CTX.reset(token)

    handler._leap_aide_delegate_model_handler = True
    entry.handler = handler


def _patch_resolver(module: Any) -> None:
    """Overlay the requested model onto the delegation config."""
    original = module._resolve_delegation_credentials

    if getattr(original, "_leap_aide_delegate_model_resolver", False):
        return

    @functools.wraps(original)
    def resolve(cfg: dict, parent_agent: Any) -> dict:
        requested = MODEL_CTX.get()
        if requested:
            # Copy: the SDK warns that mutating the cached config dict
            # corrupts it for every subsequent caller in this process.
            cfg = {**cfg, "model": requested}
        return original(cfg, parent_agent)

    resolve._leap_aide_delegate_model_resolver = True
    module._resolve_delegation_credentials = resolve


def install() -> None:
    """Install the per-child model shim (idempotent, thread-safe)."""
    global _installed
    with _install_lock:
        if _installed:
            return
        try:
            # Importing registers the tool; get_entry() returns None before it.
            from tools import delegate_tool as _dt
            from tools.registry import registry
        except Exception:
            # SDK not importable in this process (e.g. unit tests without the
            # hermes checkout on sys.path) — nothing to patch.
            logger.debug(
                "compat.delegate_model: SDK delegation modules not importable; skip",
                exc_info=True,
            )
            return

        entry = registry.get_entry("delegate_task")
        if entry is None:
            logger.debug(
                "compat.delegate_model: delegate_task not registered; skip"
            )
            return

        try:
            if not getattr(
                entry.dynamic_schema_overrides,
                "_leap_aide_delegate_model_schema",
                False,
            ):
                _patch_schema(entry)
            if not getattr(entry.handler, "_leap_aide_delegate_model_handler", False):
                _patch_handler(entry)
            _patch_resolver(_dt)
        except Exception:
            # A half-installed shim must not take the server down; the loop
            # simply falls back to the session's own model.
            logger.warning(
                "compat.delegate_model: patch failed; per-child model routing "
                "unavailable",
                exc_info=True,
            )
            return

        _installed = True
        logger.info(
            "compat.delegate_model: per-child model routing patch installed"
        )
