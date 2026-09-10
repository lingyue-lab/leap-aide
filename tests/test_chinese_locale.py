"""Tests for the Chinese (zh-CN) locale.

The WebUI's i18n data was migrated from a single hand-written
``static/i18n.js`` (flat key/value object) to per-namespace JSON files
under ``frontend/src/i18n/locales/{locale}/{common,chat}.json``. The tests
here target that new layout.

Only ``en`` and ``zh-CN`` currently ship locale data, so this is the only
locale test file in the suite; other locales will gain test files once
their JSON resources are added.
"""
import json
from collections import Counter
from pathlib import Path
from typing import Iterable


REPO = Path(__file__).resolve().parent.parent
LOCALES_DIR = REPO / "frontend" / "src" / "i18n" / "locales"
I18N_CONFIG = REPO / "frontend" / "src" / "i18n" / "config.ts"


def _flatten(node: dict, prefix: str = "") -> dict[str, str]:
    """Flatten a nested JSON object to ``{dotted.key: value}``.

    Only leaf string values are kept; nested objects recurse. Lists (e.g.
    the ``empty.hints`` arrays) are kept as raw JSON so callers can match
    the original structure.
    """
    out: dict[str, str] = {}
    for k, v in node.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return out


def load_locale(locale: str) -> dict[str, str]:
    """Load every namespace JSON for ``locale`` and return a flat map.

    All namespace JSONs in ``LOCALES_DIR / locale`` are merged. Namespace
    ordering follows the file sort so the test is deterministic.
    """
    locale_dir = LOCALES_DIR / locale
    merged: dict[str, str] = {}
    for ns_file in sorted(locale_dir.glob("*.json")):
        merged.update(_flatten(json.loads(ns_file.read_text(encoding="utf-8"))))
    return merged


def _cross_file_duplicates(locale_dir: Path) -> list[tuple[str, str, str]]:
    """Return ``(key, file_a, file_b)`` triples for keys defined in >1 file."""
    occurrences: dict[str, list[str]] = {}
    for ns_file in sorted(locale_dir.glob("*.json")):
        for key in _flatten(json.loads(ns_file.read_text(encoding="utf-8"))):
            occurrences.setdefault(key, []).append(ns_file.name)
    return [
        (key, files[0], files[1])
        for key, files in occurrences.items()
        if len(set(files)) > 1
    ]


def test_chinese_locale_block_exists():
    """zh-CN locale dir + namespace JSONs exist and are registered in i18n config."""
    locale_dir = LOCALES_DIR / "zh-CN"
    assert locale_dir.is_dir(), f"zh-CN locale dir missing: {locale_dir}"
    json_files = sorted(locale_dir.glob("*.json"))
    assert json_files, f"zh-CN has no namespace JSON files in {locale_dir}"
    # i18next only loads locales imported in config.ts — verify zh-CN is wired up.
    config_src = I18N_CONFIG.read_text(encoding="utf-8")
    assert "'zh-CN'" in config_src or '"zh-CN"' in config_src, (
        "zh-CN must be registered in frontend/src/i18n/config.ts so i18next loads it"
    )


def test_chinese_locale_includes_representative_translations():
    """Representative translation strings survived the JSON migration."""
    zh = load_locale("zh-CN")
    expected = {
        "settings.title": "设置",
        "tabs.tasks": "任务",
        "tabs.skills": "技能",
        "sessions.today": "今天",
        "common.cancel": "取消",
    }
    mismatches = [(k, expected[k], zh.get(k)) for k in expected if zh.get(k) != expected[k]]
    assert not mismatches, f"zh-CN representative translations wrong/missing: {mismatches}"


def test_chinese_locale_covers_english_keys():
    """Every leaf English key must have a value in zh-CN."""
    en = load_locale("en")
    zh = load_locale("zh-CN")
    missing = sorted(set(en) - set(zh))
    assert not missing, f"zh-CN missing keys: {missing}"


def test_chinese_locale_has_no_duplicate_keys():
    """No flat key is defined in more than one zh-CN namespace JSON.

    JSON parsers reject within-file duplicates, so the real risk in the new
    layout is two namespaces redefining the same key (i18next would silently
    use the last-loaded value).
    """
    locale_dir = LOCALES_DIR / "zh-CN"
    duplicates = _cross_file_duplicates(locale_dir)
    assert not duplicates, f"zh-CN keys defined in multiple namespace files: {duplicates}"


def test_chinese_locale_namespace_files_are_valid_json():
    """Every zh-CN namespace file parses without error and has at least one key."""
    locale_dir = LOCALES_DIR / "zh-CN"
    for ns_file in sorted(locale_dir.glob("*.json")):
        data = json.loads(ns_file.read_text(encoding="utf-8"))
        flat = _flatten(data)
        assert flat, f"{ns_file.name} has no translatable leaf keys"


def test_chinese_locale_keys_use_standard_indentation():
    """Spot-check the JSON files are pretty-printed (indent=2) for diffability."""
    locale_dir = LOCALES_DIR / "zh-CN"
    for ns_file in sorted(locale_dir.glob("*.json")):
        text = ns_file.read_text(encoding="utf-8")
        # Pretty-printed JSON with indent=2 always has a "\n  " for the first nested level.
        assert "\n  " in text, f"{ns_file.name} is not pretty-printed with 2-space indent"
