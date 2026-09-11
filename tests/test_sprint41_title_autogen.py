"""
Sprint 41 (extracted): Title auto-generation guard (PR #333).

Carved out of the original tests/test_sprint41.py — that file also read
the deleted legacy static/style.css + messages.js and crashed pytest
collection. The four assertions below verify that api/streaming.py still
recognises the three default-title cases (Untitled / New Chat / empty)
as triggers for auto-title generation. The structural regex from the
original test was rewritten as direct token lookups because the
streaming.py title block was refactored between PR #333 and the current
code (the literal `if s.title == 'Untitled':` form no longer exists;
the new code uses a tuple check).
"""
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).parent.parent
STREAMING_PY = (REPO_ROOT / "api" / "streaming.py").read_text(encoding="utf-8")


class TestTitleAutoGenerationDefaults(unittest.TestCase):
    """api/streaming.py: default-title guard must still recognise Untitled/New Chat/empty."""

    def test_untitled_literal_present(self):
        self.assertIn(
            "'Untitled'", STREAMING_PY,
            "'Untitled' guard must be present in streaming.py title block",
        )

    def test_new_chat_literal_present(self):
        self.assertIn(
            "'New Chat'", STREAMING_PY,
            "'New Chat' guard must be present (PR #333 default-title set)",
        )

    def test_empty_title_in_default_set(self):
        # Empty string check must be part of the trigger set.
        self.assertIn(
            "'Untitled', 'New Chat', ''", STREAMING_PY,
            "Empty/falsy title must be part of the default-title guard tuple (PR #333)",
        )

    def test_title_generation_invoked_for_defaults(self):
        # The trigger set must actually be wired into the auto-title branch.
        # We accept any reasonable structural form: a tuple check, an `in`
        # clause, or a function call wrapping the defaults.
        trigger_sites = [
            "current in ('Untitled', 'New Chat', '')",
            "current in ('Untitled','New Chat','')",
            "s.title in ('Untitled', 'New Chat', '')",
        ]
        self.assertTrue(
            any(site in STREAMING_PY for site in trigger_sites),
            "Default-title tuple must appear in an actual title-trigger check",
        )


if __name__ == "__main__":
    unittest.main()