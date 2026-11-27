"""Issue #91: clarify_timeout_sec is the single source of truth for the backend clarify wait."""
import json

import pytest

from app.api import config
from tests._test_helpers import get, post


def test_settings_roundtrip_clarify_timeout_endpoint():
    """POST /api/settings persists clarify_timeout_sec and GET reads it back."""
    original = get("/api/settings")[0].get("clarify_timeout_sec", "600")
    try:
        d, status = post("/api/settings", {"clarify_timeout_sec": "30"})
        assert status == 200
        assert d.get("clarify_timeout_sec") == "30"
        d2, status2 = get("/api/settings")
        assert status2 == 200
        assert d2.get("clarify_timeout_sec") == "30"
    finally:
        post("/api/settings", {"clarify_timeout_sec": original})


@pytest.mark.parametrize("bad", ["abc", "0", "999999"])
def test_settings_reject_invalid_clarify_timeout(bad):
    """Invalid values are dropped by save_settings; the previous value survives."""
    original = get("/api/settings")[0].get("clarify_timeout_sec", "600")
    try:
        post("/api/settings", {"clarify_timeout_sec": original})
        post("/api/settings", {"clarify_timeout_sec": bad})
        assert get("/api/settings")[0].get("clarify_timeout_sec") == original
    finally:
        post("/api/settings", {"clarify_timeout_sec": original})


def test_get_clarify_timeout_reads_setting(monkeypatch, tmp_path):
    """A persisted value of 30 is returned as int 30."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"clarify_timeout_sec": "30"}), encoding="utf-8")
    monkeypatch.setattr(config, "SETTINGS_FILE", path)
    assert config.get_clarify_timeout() == 30


@pytest.mark.parametrize(
    "raw,expected",
    [("abc", 600), ("0", 600), ("-5", 600), ("999999", 86400)],
)
def test_get_clarify_timeout_falls_back_or_clamps(monkeypatch, tmp_path, raw, expected):
    """Invalid/absent values fall back to 600; over-max values clamp to 86400."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"clarify_timeout_sec": raw}), encoding="utf-8")
    monkeypatch.setattr(config, "SETTINGS_FILE", path)
    assert config.get_clarify_timeout() == expected


def test_get_clarify_timeout_default_when_key_missing(monkeypatch, tmp_path):
    """No key in settings.json means the 600s default."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(config, "SETTINGS_FILE", path)
    assert config.get_clarify_timeout() == 600
