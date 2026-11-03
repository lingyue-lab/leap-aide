#!/usr/bin/env python3
"""Draft an OpenRouter model-list update for model_catalog.json (issue #10).

WHAT THIS DOES
--------------
Fetches the public OpenRouter model list (https://openrouter.ai/api/v1/models,
no API key required) and prints a *draft* JSON fragment for the catalog's
``openrouter`` provider entry.  The fragment goes to stdout or ``-o FILE``.

WHAT THIS NEVER DOES
--------------------
It NEVER writes backend/app/api/data/model_catalog.json directly.  The catalog
is human-curated: new models need review (label quality, whether the model
belongs in the baseline at all, whether an existing entry's label should be
kept).  Apply the draft by hand after review:

  1. python3 scripts/sync_models.py --map-labels > /tmp/openrouter_draft.json
  2. Review the diff — drop models you don't want, fix labels.
  3. Paste the "models" array into the openrouter entry of model_catalog.json.

This script is a manual maintenance tool.  It is NOT wired into CI and has no
scheduled job — run it whenever the OpenRouter baseline looks stale.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = REPO_ROOT / "backend" / "app" / "api" / "data" / "model_catalog.json"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def _load_existing_entry(catalog_path: Path) -> dict:
    """Return the catalog's current openrouter provider entry (or ``{}``)."""
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return (catalog.get("providers") or {}).get("openrouter") or {}


def _load_existing_labels(catalog_path: Path) -> dict[str, str]:
    """Return {model_id: label} for the catalog's current openrouter baseline."""
    labels: dict[str, str] = {}
    for m in _load_existing_entry(catalog_path).get("models") or []:
        mid = m.get("id")
        label = m.get("label")
        if mid and label:
            labels[mid] = label
    return labels


def fetch_openrouter_models(url: str = OPENROUTER_MODELS_URL, timeout_s: float = 15.0) -> list[dict]:
    """Fetch and normalize the OpenRouter /models payload to [{id, label}]."""
    req = urllib.request.Request(url, method="GET")  # nosec B310 — fixed https endpoint
    req.add_header("User-Agent", "leap-aide-sync-models/1.0")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # nosec B310
        payload = json.loads(resp.read().decode("utf-8"))
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ValueError(f"unexpected payload shape from {url!r}: keys={list(payload)[:8]!r}")
    out: list[dict] = []
    for m in data:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "").strip()
        if not mid:
            continue
        out.append({"id": mid, "label": str(m.get("name") or mid)})
    return out


def build_draft(
    models: list[dict],
    existing_labels: dict[str, str],
    *,
    map_labels: bool,
    existing_entry: dict | None = None,
) -> dict:
    """Build the draft openrouter entry fragment.

    With *map_labels*, ids already present in the catalog keep their curated
    label; only genuinely new ids get the API-provided name.  *existing_entry*
    (when given) supplies the display_name / auth / endpoint / refresh fields
    so the draft mirrors the catalog's current wiring instead of inventing a
    stale ``mode=none`` refresh block.
    """
    models_out: list[dict] = []
    for m in models:
        mid = m["id"]
        if map_labels and mid in existing_labels:
            models_out.append({"id": mid, "label": existing_labels[mid]})
        else:
            models_out.append({"id": mid, "label": m["label"]})
    entry = existing_entry or {}
    return {
        "display_name": entry.get("display_name") or "OpenRouter",
        "auth": entry.get("auth") or {"type": "api_key", "env_var": "OPENROUTER_API_KEY"},
        **({"endpoint": entry["endpoint"]} if entry.get("endpoint") else {}),
        "refresh": entry.get("refresh") or {"mode": "none"},
        "models": models_out,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Draft an OpenRouter model-list update for model_catalog.json "
                    "(prints a fragment; never writes the catalog).",
    )
    parser.add_argument(
        "-o", "--output", metavar="FILE",
        help="write the draft JSON to FILE instead of stdout",
    )
    parser.add_argument(
        "--map-labels", action="store_true",
        help="keep curated labels for model ids already in the catalog; "
             "only new ids use the API-provided name",
    )
    parser.add_argument(
        "--url", default=OPENROUTER_MODELS_URL,
        help="models endpoint (default: OpenRouter public API; override for tests)",
    )
    args = parser.parse_args(argv)

    try:
        models = fetch_openrouter_models(args.url)
    except Exception as exc:  # network / payload errors — exit non-zero, no partial write
        print(f"error: fetch failed: {exc}", file=sys.stderr)
        return 1

    existing = _load_existing_labels(CATALOG_PATH)
    fragment = build_draft(
        models,
        existing,
        map_labels=args.map_labels,
        existing_entry=_load_existing_entry(CATALOG_PATH),
    )

    new_ids = [m["id"] for m in fragment["models"] if m["id"] not in existing]
    text = json.dumps(fragment, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        dest = args.output
    else:
        sys.stdout.write(text)
        dest = "stdout"
    print(
        f"# draft: {len(fragment['models'])} models "
        f"({len(new_ids)} new vs catalog), written to {dest}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
