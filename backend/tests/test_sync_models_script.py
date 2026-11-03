"""Offline tests for scripts/sync_models.py (issue #10, Step D).

The script drafts an OpenRouter model-list fragment for model_catalog.json;
it must never write the catalog itself.  All network access is mocked.
"""
import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import sync_models  # noqa: E402


_FAKE_PAYLOAD = {
    "data": [
        {"id": "openai/gpt-5.4", "name": "OpenAI: GPT-5.4"},
        {"id": "anthropic/claude-opus-4.6", "name": "Anthropic: Claude Opus 4.6"},
        {"id": "vendor/new-model", "name": "Vendor New Model"},
        {"id": "", "name": "no-id entries are dropped"},
        "not-a-dict",
    ]
}


def _mock_urlopen(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return MagicMock(return_value=resp)


def test_fetch_normalizes_payload():
    with patch("urllib.request.urlopen", _mock_urlopen(_FAKE_PAYLOAD)):
        models = sync_models.fetch_openrouter_models("https://example.test/models")
    assert models == [
        {"id": "openai/gpt-5.4", "label": "OpenAI: GPT-5.4"},
        {"id": "anthropic/claude-opus-4.6", "label": "Anthropic: Claude Opus 4.6"},
        {"id": "vendor/new-model", "label": "Vendor New Model"},
    ]


def test_fetch_rejects_unexpected_shape():
    with patch("urllib.request.urlopen", _mock_urlopen({"unexpected": True})):
        with pytest.raises(ValueError, match="unexpected payload shape"):
            sync_models.fetch_openrouter_models("https://example.test/models")


def test_build_draft_map_labels_keeps_curated_labels():
    existing = {"openai/gpt-5.4": "GPT-5.4"}
    models = [
        {"id": "openai/gpt-5.4", "label": "OpenAI: GPT-5.4"},
        {"id": "vendor/new-model", "label": "Vendor New Model"},
    ]
    draft = sync_models.build_draft(models, existing, map_labels=True)
    by_id = {m["id"]: m["label"] for m in draft["models"]}
    assert by_id["openai/gpt-5.4"] == "GPT-5.4", "curated label must be preserved"
    assert by_id["vendor/new-model"] == "Vendor New Model", "new id uses API name"


def test_build_draft_without_map_labels_uses_api_names():
    existing = {"openai/gpt-5.4": "GPT-5.4"}
    models = [{"id": "openai/gpt-5.4", "label": "OpenAI: GPT-5.4"}]
    draft = sync_models.build_draft(models, existing, map_labels=False)
    assert draft["models"][0]["label"] == "OpenAI: GPT-5.4"
    # No existing_entry → the safe mode=none placeholder (reviewer must not
    # paste a stale refresh over a live-wired catalog entry).
    assert draft["refresh"] == {"mode": "none"}


def test_build_draft_echoes_existing_entry_wiring():
    """existing_entry mirrors display_name/auth/endpoint/refresh verbatim."""
    existing_entry = {
        "display_name": "OpenRouter",
        "auth": {"type": "api_key", "env_var": "OPENROUTER_API_KEY"},
        "endpoint": {"base_url": "https://openrouter.ai/api/v1", "models_path": "/models"},
        "refresh": {"mode": "api", "membership": "curated", "timeout_s": 10},
    }
    draft = sync_models.build_draft(
        [{"id": "a/b", "label": "A B"}],
        {},
        map_labels=False,
        existing_entry=existing_entry,
    )
    assert draft["refresh"] == {"mode": "api", "membership": "curated", "timeout_s": 10}
    assert draft["endpoint"] == existing_entry["endpoint"]


def test_main_never_writes_catalog(tmp_path, capsys):
    """main() must only touch its -o target (or stdout), never the catalog."""
    catalog_before = sync_models.CATALOG_PATH.read_text(encoding="utf-8")
    out_file = tmp_path / "draft.json"
    with patch("urllib.request.urlopen", _mock_urlopen(_FAKE_PAYLOAD)):
        rc = sync_models.main(["-o", str(out_file), "--map-labels"])
    assert rc == 0
    assert sync_models.CATALOG_PATH.read_text(encoding="utf-8") == catalog_before, (
        "sync_models.py must never modify model_catalog.json"
    )
    draft = json.loads(out_file.read_text(encoding="utf-8"))
    assert draft["display_name"] == "OpenRouter"
    assert len(draft["models"]) == 3
    # The draft mirrors the catalog's real wiring (openrouter is a live
    # refresh.mode=api / membership=curated provider), never a stale
    # mode=none placeholder.
    catalog = json.loads(catalog_before)
    live_entry = catalog["providers"]["openrouter"]
    assert draft["refresh"] == live_entry["refresh"]
    assert draft["endpoint"] == live_entry["endpoint"]


def test_main_fetch_failure_exits_nonzero_without_output(tmp_path):
    err = MagicMock(side_effect=OSError("network down"))
    out_file = tmp_path / "draft.json"
    with patch("urllib.request.urlopen", err):
        rc = sync_models.main(["-o", str(out_file)])
    assert rc == 1
    assert not out_file.exists(), "no partial output on fetch failure"
