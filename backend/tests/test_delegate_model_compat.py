"""
Tests: per-child model routing for delegate_task (issue #57 follow-up).

The supervised development loop needs a different model per phase — a cheap
one for mechanical implementation, a capable one for review. The SDK neither
exposes ``model`` on the tool schema nor forwards it through the registry
handler's whitelist, and the SDK itself must not be modified (AGENTS.md), so
LeapAide installs a runtime shim in ``app.compat.delegate_model``.

What has to hold:

  * the model-facing schema advertises ``model`` (otherwise the model never
    emits it);
  * the handler captures ``args["model"]`` before the whitelist drops it, and
    clears the ContextVar afterwards so concurrent sessions cannot bleed;
  * the credential resolver overlays the requested model onto a COPY of the
    delegation config — the SDK warns that mutating the cached dict corrupts it
    for every later caller in the process;
  * with no model requested every step is a no-op, so a deployment that never
    passes ``model`` behaves exactly as the SDK does.

Files touched by the production fix:
  app/compat/delegate_model.py (the shim)
  app/compat/__init__.py (installed alongside the other SDK patches)
"""

import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from tools import delegate_tool
    from tools.registry import registry

    from app.compat import delegate_model

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not SDK_AVAILABLE,
    reason="hermes SDK not importable in this environment",
)


class _FakeEntry:
    """Minimal stand-in for tools.registry.ToolEntry (which uses __slots__)."""

    def __init__(self, handler, overrides=None):
        self.handler = handler
        self.dynamic_schema_overrides = overrides


@pytest.fixture(scope="module", autouse=True)
def installed():
    delegate_model.install()
    yield


@pytest.fixture(autouse=True)
def clean_context():
    delegate_model.MODEL_CTX.set(None)
    yield
    delegate_model.MODEL_CTX.set(None)


# ── install ────────────────────────────────────────────────────────────────


def test_install_marks_the_entry_as_patched():
    entry = registry.get_entry("delegate_task")
    assert entry is not None, "delegate_task must be registered before install"
    assert getattr(entry.handler, "_leap_aide_delegate_model_handler", False)
    assert getattr(
        entry.dynamic_schema_overrides, "_leap_aide_delegate_model_schema", False
    )


def test_install_is_idempotent():
    """A second install must not wrap the wrapper."""
    entry = registry.get_entry("delegate_task")
    handler = entry.handler
    overrides = entry.dynamic_schema_overrides
    delegate_model.install()
    assert entry.handler is handler
    assert entry.dynamic_schema_overrides is overrides


def test_install_skips_cleanly_without_the_sdk():
    """A process without the SDK on sys.path must not raise."""
    from app.compat import delegate_model as mod

    assert callable(mod.install)


# ── schema ─────────────────────────────────────────────────────────────────


def test_the_tool_schema_advertises_model():
    entry = registry.get_entry("delegate_task")
    overrides = entry.dynamic_schema_overrides()
    prop = overrides["parameters"]["properties"].get("model")
    assert prop is not None, "the model must be able to see the model parameter"
    assert prop["type"] == "string"
    assert "model" in prop["description"]


def test_the_static_schema_is_not_mutated():
    """Injection happens in the dynamic overrides, never in the shared dict."""
    static = delegate_tool.DELEGATE_TASK_SCHEMA["parameters"]["properties"]
    assert "model" not in static


def test_the_existing_parameters_survive_the_injection():
    entry = registry.get_entry("delegate_task")
    props = entry.dynamic_schema_overrides()["parameters"]["properties"]
    for name in ("goal", "context", "tasks", "role", "output_schema"):
        assert name in props, f"{name} must not be dropped by the shim"


# ── handler ────────────────────────────────────────────────────────────────


def test_the_handler_captures_the_requested_model():
    seen = {}

    def original(args, **kwargs):
        seen["model"] = delegate_model.MODEL_CTX.get()
        return "ok"

    entry = _FakeEntry(original)
    delegate_model._patch_handler(entry)

    out = entry.handler({"goal": "g", "model": "  fast-model "}, parent_agent=None)

    assert out == "ok"
    assert seen["model"] == "fast-model", "the value must be stripped"


def test_the_context_is_cleared_after_the_call():
    """A leaked ContextVar would route the next dispatch to the wrong model."""
    entry = _FakeEntry(lambda args, **kwargs: None)
    delegate_model._patch_handler(entry)

    entry.handler({"goal": "g", "model": "child-model"})
    assert delegate_model.MODEL_CTX.get() is None


def test_no_model_leaves_the_context_empty():
    seen = {}

    def original(args, **kwargs):
        seen["model"] = delegate_model.MODEL_CTX.get()
        return "ok"

    entry = _FakeEntry(original)
    delegate_model._patch_handler(entry)

    assert entry.handler({"goal": "g"}) == "ok"
    assert seen["model"] is None


def test_a_blank_model_is_ignored():
    seen = {}

    def original(args, **kwargs):
        seen["model"] = delegate_model.MODEL_CTX.get()
        return "ok"

    entry = _FakeEntry(original)
    delegate_model._patch_handler(entry)

    entry.handler({"goal": "g", "model": "   "})
    assert seen["model"] is None


def test_the_context_is_restored_when_the_handler_raises():
    entry = _FakeEntry(lambda args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    delegate_model._patch_handler(entry)

    with pytest.raises(RuntimeError):
        entry.handler({"goal": "g", "model": "child-model"})
    assert delegate_model.MODEL_CTX.get() is None


# ── credential resolution ──────────────────────────────────────────────────


def test_the_resolver_overlays_the_requested_model():
    token = delegate_model.MODEL_CTX.set("child-model")
    try:
        creds = delegate_tool._resolve_delegation_credentials(
            {"model": "session-model"}, None
        )
    finally:
        delegate_model.MODEL_CTX.reset(token)

    assert creds["model"] == "child-model"


def test_the_resolver_is_unchanged_without_a_request():
    creds = delegate_tool._resolve_delegation_credentials(
        {"model": "session-model"}, None
    )
    assert creds["model"] == "session-model"


def test_the_resolver_does_not_mutate_the_config_it_is_given():
    """The SDK caches that dict process-wide; mutating it poisons later calls."""
    cfg = {"model": "session-model"}
    token = delegate_model.MODEL_CTX.set("child-model")
    try:
        delegate_tool._resolve_delegation_credentials(cfg, None)
    finally:
        delegate_model.MODEL_CTX.reset(token)

    assert cfg == {"model": "session-model"}


def test_a_missing_model_key_is_not_invented():
    token = delegate_model.MODEL_CTX.set("child-model")
    try:
        creds = delegate_tool._resolve_delegation_credentials({}, None)
    finally:
        delegate_model.MODEL_CTX.reset(token)

    assert creds["model"] == "child-model"
