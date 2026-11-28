"""Unit tests for the test-side /api/models warm-up helper (issue #93)."""
import json
import urllib.error
import warnings

import pytest

from tests.conftest import _warm_models_cache


class _FakeResponse:
    """Minimal context-manager stand-in for urllib's response object."""

    def __init__(self, payload: dict, status: int = 200):
        self._body = json.dumps(payload).encode()
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_warm_softfails_on_unreachable_server():
    """No network: warn-only, return False, never raise (spec section 6-4)."""
    with pytest.warns(RuntimeWarning, match="warm-up did not complete"):
        assert _warm_models_cache(
            "http://127.0.0.1:9", budget_s=0.4, poll_s=0.1
        ) is False


def test_warm_softfails_on_http_error(monkeypatch):
    """HTTP error during warm (e.g. 503 initializing) is soft-fail (section 6-4)."""
    def _raise(*_a, **_k):
        raise urllib.error.HTTPError(
            "http://127.0.0.1/x", 503, "initializing", {}, None
        )

    monkeypatch.setattr("urllib.request.urlopen", _raise)
    with pytest.warns(RuntimeWarning, match="warm-up did not complete"):
        assert _warm_models_cache(
            "http://127.0.0.1:9", budget_s=0.4, poll_s=0.1
        ) is False


def test_warm_polls_until_catalog_is_built(monkeypatch):
    """building:true payloads are not warm: keep polling, then succeed."""
    responses = iter([
        _FakeResponse({"groups": [], "building": True}),
        _FakeResponse({"groups": [{"provider": "p", "models": []}]}),
    ])
    calls = {"n": 0}

    def _fake(*_a, **_k):
        calls["n"] += 1
        return next(responses)

    monkeypatch.setattr("urllib.request.urlopen", _fake)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ok = _warm_models_cache("http://unused", budget_s=5.0, poll_s=0.0)
    assert ok is True
    assert calls["n"] == 2
    assert not [w for w in caught if issubclass(w.category, RuntimeWarning)]
