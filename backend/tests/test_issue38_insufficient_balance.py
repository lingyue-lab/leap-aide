"""
Issue #38 — zero-balance API key produces no prompt when sending a message.

Covers:
  1. Quota classification recognises zero-balance provider errors (legacy
     narrow strings plus the broader hermes-agent billing vocabulary).
  2. Both streaming error paths (silent failure + exception) route through
     the shared classifier, and the SDK billing verdict is trusted.
  3. Failed turns keep the user's message in the transcript so the frontend
     completion reload does not swallow the just-sent bubble.
"""
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).parent.parent
SRC = (REPO_ROOT / "app" / "api" / "streaming.py").read_text(encoding="utf-8")

import sys
sys.path.insert(0, str(REPO_ROOT))

from app.api.streaming import _ensure_failed_user_message, _is_quota_error


class TestQuotaClassification(unittest.TestCase):
    """_is_quota_error must flag exhausted-credit / zero-balance errors."""

    def test_legacy_strings_still_detected(self):
        for text in (
            "You exceeded your current quota",
            "insufficient credit",
            "credit balance is too low",
            "quota_exceeded",
            "quota exceeded",
        ):
            self.assertTrue(_is_quota_error(text), f"should classify: {text!r}")

    def test_zero_balance_strings_detected(self):
        # The issue #38 repro: a key whose account has no balance left.
        for text in (
            "Insufficient Balance",
            "Your account balance is insufficient",
            "402 Payment Required",
            "insufficient_quota",
            "account balance is too low",
            "out of funds",
            "余额不足",
        ):
            self.assertTrue(_is_quota_error(text), f"should classify: {text!r}")

    def test_non_billing_errors_not_flagged(self):
        for text in (
            "Rate limit exceeded, try again later",
            "401 Unauthorized",
            "model not found",
            "",
            None,
        ):
            self.assertFalse(_is_quota_error(text), f"must not classify: {text!r}")

    def test_both_error_paths_use_shared_classifier(self):
        # One definition + one call in the silent-failure path + one in the
        # exception path — the two paths must not drift apart again.
        self.assertGreaterEqual(
            SRC.count("_is_quota_error("), 3,
            "silent-failure and exception paths must both call _is_quota_error",
        )

    def test_sdk_billing_verdict_trusted_in_silent_path(self):
        # run_conversation returns failure_reason='billing' for classified
        # billing walls — the silent path must honour it even when the
        # provider wording misses the pattern list.
        self.assertIn(
            "failure_reason", SRC,
            "silent path must trust the SDK billing verdict (failure_reason)",
        )


class TestUserMessagePreservation(unittest.TestCase):
    """_ensure_failed_user_message keeps the failed turn visible (#38)."""

    def _run(self, session, msg_text):
        db = _FakeDB()
        with mock.patch("app.api.models._get_session_db", return_value=db):
            _ensure_failed_user_message(session, msg_text)
        return db

    def test_noop_when_user_message_present(self):
        s = _FakeSession([
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ])
        db = self._run(s, "hello")
        self.assertEqual(len(s.messages), 2)
        self.assertEqual(db.appended, [])
        self.assertEqual(s.sync_calls, 0)

    def test_noop_when_workspace_prefix_wraps_message(self):
        # run_conversation receives "[Workspace: ...]\n" + msg_text — the
        # in-memory user content may carry that prefix.
        s = _FakeSession([{"role": "user", "content": "[Workspace: /tmp]\nhello"}])
        db = self._run(s, "hello")
        self.assertEqual(len(s.messages), 1)
        self.assertEqual(db.appended, [])

    def test_appends_when_message_missing(self):
        s = _FakeSession([
            {"role": "user", "content": "older question"},
            {"role": "assistant", "content": "older answer"},
        ])
        db = self._run(s, "new question")
        self.assertEqual(len(s.messages), 3)
        last = s.messages[-1]
        self.assertEqual(last["role"], "user")
        self.assertEqual(last["content"], "new question")
        self.assertEqual(
            db.appended,
            [{"session_id": "s38", "role": "user", "content": "new question"}],
        )
        self.assertEqual(s.sync_calls, 1)

    def test_attaches_pending_original_content(self):
        s = _FakeSession([{"role": "assistant", "content": "hi"}])
        s._pending_original_message = "/skill foo"
        self._run(s, "run it")
        last = s.messages[-1]
        self.assertEqual(last["original_content__webui"], "/skill foo")
        self.assertIsNone(s._pending_original_message)

    def test_empty_message_is_noop(self):
        s = _FakeSession([{"role": "assistant", "content": "hi"}])
        db = self._run(s, "")
        self.assertEqual(len(s.messages), 1)
        self.assertEqual(db.appended, [])

    def test_both_error_paths_call_ensure(self):
        # definition line + silent-failure call + exception-path call
        self.assertGreaterEqual(
            SRC.count("_ensure_failed_user_message(s, msg_text)"), 3,
            "silent-failure and exception paths must both preserve the user message",
        )

    def test_exception_path_reloads_messages_from_state_db(self):
        self.assertIn(
            "_db.get_messages(s.session_id)", SRC,
            "exception path must reload the SDK-persisted transcript before "
            "clearing pending state",
        )


class _FakeDB:
    def __init__(self):
        self.appended = []

    def append_message(self, **kwargs):
        self.appended.append(kwargs)


class _FakeSession:
    def __init__(self, messages=None, session_id="s38"):
        self.session_id = session_id
        self.messages = list(messages or [])
        self._pending_original_message = None
        self.sync_calls = 0

    def sync_webui_metadata(self, **kwargs):
        self.sync_calls += 1


if __name__ == "__main__":
    unittest.main()
