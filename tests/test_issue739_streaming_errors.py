"""
Issue #739 (extracted): streaming.py error path (PR #351).

Carved out of the original tests/test_issue739_652_653.py — that file
mixed backend source-level checks (this file) with three frontend DOM
tests that crash in the headless backend-test environment.

Eleven backend guarantees:

  - Quota classification matches "insufficient credit"/"quota exceeded"
  - Rate-limit classification matches "rate limit"/"429"
  - Quota classification is checked BEFORE rate-limit (order matters)
  - Auth classification matches "401"/"AuthenticationError"
  - "Not found" classification matches "404"/"model not found"
  - Provider HTML error responses are sanitised (tags stripped)
  - Terminal event set includes done/error/cancel/stream_end/apperror
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SRC = (REPO_ROOT / "api" / "streaming.py").read_text(encoding="utf-8")
CONFIG_PY = (REPO_ROOT / "api" / "config.py").read_text(encoding="utf-8")


def _block_after(text, marker):
    """Return text starting at `marker` (or empty if not found)."""
    idx = text.find(marker)
    return text[idx:] if idx >= 0 else ""


class TestQuotaClassification(unittest.TestCase):
    """_exc_is_quota must match OpenAI/Anthropic billing-failure strings."""

    def test_insufficient_credit_detected(self):
        self.assertIn(
            "'insufficient credit'", SRC,
            "_exc_is_quota must match 'insufficient credit' (MiniMax billing)",
        )

    def test_quota_exceeded_detected(self):
        self.assertIn(
            "'quota_exceeded'", SRC,
            "_exc_is_quota must match 'quota_exceeded' (OpenAI billing)",
        )

    def test_human_quota_exceeded_detected(self):
        self.assertIn(
            "'quota exceeded'", SRC,
            "_exc_is_quota must match human-readable 'quota exceeded'",
        )


class TestRateLimitClassification(unittest.TestCase):
    """_exc_is_rate_limit must match 429 / rate-limit strings."""

    def test_rate_limit_string_detected(self):
        self.assertIn(
            "'rate limit'", SRC,
            "_exc_is_rate_limit must match 'rate limit' in error string",
        )

    def test_429_status_detected(self):
        self.assertIn(
            "'429'", SRC,
            "_exc_is_rate_limit must match '429' status code",
        )

    def test_rate_limit_after_quota(self):
        # Quota must be classified FIRST; rate-limit depends on `not _exc_is_quota`.
        quota_idx = SRC.find("_exc_is_quota")
        rl_idx = SRC.find("_exc_is_rate_limit")
        self.assertGreater(quota_idx, 0, "_exc_is_quota must exist")
        self.assertGreater(rl_idx, quota_idx, "_exc_is_rate_limit must come after _exc_is_quota")
        # The rate-limit expression must depend on quota being False.
        # Look in the next 200 chars after the rate-limit declaration.
        rl_window = SRC[rl_idx:rl_idx + 200]
        self.assertRegex(
            rl_window, r"not\s+_exc_is_quota",
            "rate-limit branch must be guarded by `not _exc_is_quota`",
        )


class TestAuthClassification(unittest.TestCase):
    """_exc_is_auth must match 401 / AuthenticationError / unauthorized."""

    def test_401_string_detected(self):
        self.assertIn("'401'", SRC)

    def test_authentication_error_class_detected(self):
        self.assertIn("AuthenticationError", SRC)


class TestNotFoundClassification(unittest.TestCase):
    """Model-not-found errors must classify cleanly."""

    def test_model_not_found_detected(self):
        self.assertIn(
            "'model not found'", SRC,
            "_exc_is_not_found must match 'model not found'",
        )

    def test_404_status_detected(self):
        self.assertIn("'404'", SRC)


class TestProviderErrorSanitisation(unittest.TestCase):
    """Provider error responses may contain HTML; tags must be stripped."""

    def test_html_stripping_present(self):
        # The exact pattern: re.sub(r'<[^>]+>', ' ', err_str)
        self.assertIn(
            "re.sub(r'<[^>]+>',", SRC,
            "HTML tags must be stripped from provider error strings",
        )


class TestTerminalEventSet(unittest.TestCase):
    """api/config.py: terminal SSE event set must include done/stream_end/etc."""

    def test_terminal_set_includes_all_kinds(self):
        # Look for the tuple/list of terminal events in config.py.
        terminal_block = _block_after(CONFIG_PY, "stream_end")
        self.assertIn("'done'", terminal_block, "terminal set must include 'done'")
        self.assertIn("'error'", terminal_block, "terminal set must include 'error'")
        self.assertIn("'cancel'", terminal_block, "terminal set must include 'cancel'")
        self.assertIn("'apperror'", terminal_block, "terminal set must include 'apperror'")

    def test_terminal_check_helper_defined(self):
        # ReplayableStream.is_done() is the terminal-check helper (config.py:2113).
        self.assertRegex(
            CONFIG_PY,
            r"def\s+is_done\b",
            "is_done() helper must exist in config.py (ReplayableStream terminal check)",
        )


if __name__ == "__main__":
    unittest.main()