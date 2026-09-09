"""
Backend-side guarantees for the session metadata fast path (Issue #765 follow-up).

The companion assertions that checked the legacy frontend JS source
(``static/sessions.js``, ``static/ui.js``, ``static/boot.js``) were
retired with the Vite migration; the equivalent logic is now in
``frontend/src/`` and has its own vitest coverage. Only the server-side
assertion that ``/api/session`` skips resolving the model catalog when
the caller only wants metadata remains.
"""
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_messages_zero_skips_effective_model_resolution():
    src = (ROOT / "api" / "routes.py").read_text(encoding="utf-8")

    assert re.search(
        r"effective_model\s*=\s*\(\s*"
        r"_resolve_effective_session_model_for_display\(s\)\s*"
        r"if resolve_model\s*else None\s*\)",
        src,
    ), "messages=0 metadata requests must not resolve the model catalog"
    assert 'resolve_model_default = "1" if load_messages else "0"' in src
