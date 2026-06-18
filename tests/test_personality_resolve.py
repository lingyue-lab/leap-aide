"""Unit tests for _resolve_personality_prompt() — personality resolution from config.

Pure function tests — no server needed.
Run:  python tests/test_personality_resolve.py
Or:   .venv/bin/python -c "exec(open('tests/test_personality_resolve.py').read())"
"""

import sys
import pathlib

REPO_ROOT = pathlib.Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO_ROOT))

from api.streaming import _resolve_personality_prompt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(personalities=None):
    """Build a minimal config dict with an agent.personalities section."""
    return {"agent": {"personalities": personalities or {}}}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_none_name_returns_none():
    """personality_name=None always returns None."""
    assert _resolve_personality_prompt(_cfg({"foo": "bar"}), None) is None


def test_empty_name_returns_none():
    """personality_name='' returns None (falsy)."""
    assert _resolve_personality_prompt(_cfg({"foo": "bar"}), "") is None


def test_name_not_found_returns_none():
    """Name present in config but not in personalities dict → None."""
    assert _resolve_personality_prompt(_cfg({"pirate": "Arr!"}), "ninja") is None


def test_string_personality():
    """String-valued personality is returned as-is."""
    prompt = "You are a pirate. Arr!"
    result = _resolve_personality_prompt(_cfg({"pirate": prompt}), "pirate")
    assert result == prompt


def test_dict_personality_with_system_prompt():
    """Dict personality with 'system_prompt' key."""
    result = _resolve_personality_prompt(
        _cfg({"helper": {"system_prompt": "Be helpful."}}), "helper"
    )
    assert result == "Be helpful."


def test_dict_personality_with_prompt_key_fallback():
    """Dict personality with 'prompt' key (fallback when system_prompt missing)."""
    result = _resolve_personality_prompt(
        _cfg({"helper": {"prompt": "Be helpful."}}), "helper"
    )
    assert result == "Be helpful."


def test_dict_personality_system_prompt_takes_priority():
    """'system_prompt' wins over 'prompt' when both present."""
    result = _resolve_personality_prompt(
        _cfg({"helper": {"system_prompt": "Sys prompt", "prompt": "Fallback"}}),
        "helper",
    )
    assert result == "Sys prompt"


def test_dict_personality_with_tone():
    """Dict personality with tone appends 'Tone: ...' line."""
    result = _resolve_personality_prompt(
        _cfg({"helper": {"system_prompt": "Be helpful.", "tone": "friendly"}}),
        "helper",
    )
    assert result == "Be helpful.\nTone: friendly"


def test_dict_personality_with_style():
    """Dict personality with style appends 'Style: ...' line."""
    result = _resolve_personality_prompt(
        _cfg({"helper": {"system_prompt": "Be helpful.", "style": "concise"}}),
        "helper",
    )
    assert result == "Be helpful.\nStyle: concise"


def test_dict_personality_with_tone_and_style():
    """Both tone and style appended in order."""
    result = _resolve_personality_prompt(
        _cfg({
            "helper": {
                "system_prompt": "Be helpful.",
                "tone": "friendly",
                "style": "concise",
            }
        }),
        "helper",
    )
    assert result == "Be helpful.\nTone: friendly\nStyle: concise"


def test_dict_personality_only_tone_no_prompt():
    """Only tone, no system_prompt/prompt → 'Tone: ...'."""
    result = _resolve_personality_prompt(
        _cfg({"helper": {"tone": "friendly"}}), "helper"
    )
    assert result == "Tone: friendly"


def test_dict_personality_empty_all_fields():
    """Dict with no system_prompt, prompt, tone, or style → None."""
    result = _resolve_personality_prompt(
        _cfg({"helper": {"description": "just a description"}}), "helper"
    )
    assert result is None


def test_config_missing_agent_key():
    """Config dict without 'agent' key → None."""
    result = _resolve_personality_prompt({}, "foo")
    assert result is None


def test_config_without_personalities_key():
    """Config with 'agent' but no 'personalities' → None."""
    result = _resolve_personality_prompt({"agent": {}}, "foo")
    assert result is None


def test_personalities_not_a_dict():
    """'personalities' is not a dict (e.g. list) → None."""
    result = _resolve_personality_prompt(
        {"agent": {"personalities": ["pirate", "ninja"]}}, "pirate"
    )
    assert result is None


def test_config_not_a_dict():
    """Entire config is not a dict → None."""
    result = _resolve_personality_prompt([], "foo")
    assert result is None


def test_config_is_none():
    """Config is None → None."""
    result = _resolve_personality_prompt(None, "foo")
    assert result is None


def test_clarify_personality():
    """End-to-end: the real clarify personality resolves correctly."""
    # This uses the string-format personality — prompt should be returned as-is
    prompt = "When you need to ask the user a question, always use the clarify tool."
    result = _resolve_personality_prompt(
        _cfg({"clarify": prompt}), "clarify"
    )
    assert result == prompt


def test_multiple_personalities_pick_correct_one():
    """Only the named personality is returned."""
    result = _resolve_personality_prompt(
        _cfg({"pirate": "Arr!", "ninja": "Silent.", "wizard": "Abracadabra!"}),
        "ninja",
    )
    assert result == "Silent."


if __name__ == '__main__':
    import traceback
    all_tests = [
        (name, obj) for name, obj in list(globals().items())
        if name.startswith('test_') and callable(obj)
    ]
    passed = failed = 0
    for name, fn in all_tests:
        try:
            fn()
            print(f'  PASS {name}')
            passed += 1
        except Exception:
            print(f'  FAIL {name}')
            traceback.print_exc()
            failed += 1
    print(f'\nResults: {passed} passed, {failed} failed out of {passed+failed}')
    sys.exit(0 if failed == 0 else 1)
