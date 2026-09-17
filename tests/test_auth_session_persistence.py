"""Regression tests: auth sessions persist across process restarts.

_sessions is an in-memory dict. Without persistence, any restart (launchd,
systemd, container) invalidates all active browser sessions and floods clients
with 401s until they clear cookies. The HMAC signing key already persists to
STATE_DIR; this PR persists the session table using the same pattern.
"""
import importlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Add repo root to sys.path so `import api.auth` resolves when this file
# is invoked directly via `python -m unittest tests.test_auth_session_persistence`.
_REPO_ROOT = Path(__file__).parent.parent.resolve()
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Import api.auth once at module level. Per-test state isolation is done in
# setUp by patching api.config.STATE_DIR / api.auth.STATE_DIR /
# api.auth._SESSIONS_FILE directly, NOT by mutating os.environ. Mutating
# os.environ['HERMES_WEBUI_STATE_DIR'] at import time clobbers the value
# that conftest.py:68 publishes for the test server subprocess and breaks
# sibling tests like test_smoke.test_state_dir_is_isolated_from_production
# under --batch-size>=2 (alphabetical collection imports this file first).
import api.auth as auth
import api.config as _config  # noqa: E402  -- module-level so tearDown can restore _config.STATE_DIR


class TestSessionPersistence(unittest.TestCase):
    """Sessions survive a simulated process restart (module reload)."""

    def setUp(self) -> None:
        # Per-test isolated state dir. Patch api.auth.STATE_DIR /
        # api.auth._SESSIONS_FILE / api.config.STATE_DIR directly so that
        # auth.create_session() (and every other write path) lands in
        # self._tmpdir both BEFORE and AFTER _simulate_restart() reloads
        # api.auth. Patching only _config.STATE_DIR around the reload (as
        # _simulate_restart does) would leave the pre-reload writes going
        # to the conftest state dir, which is the bug the original module-
        # level _TEST_STATE pattern hid by using a constant across tests.
        self._tmpdir = Path(tempfile.mkdtemp(prefix="webui-test-auth-persist-"))
        self._saved_config_state_dir = _config.STATE_DIR
        self._saved_auth_state_dir = auth.STATE_DIR
        self._saved_sessions_file = auth._SESSIONS_FILE
        _config.STATE_DIR = self._tmpdir
        auth.STATE_DIR = self._tmpdir
        auth._SESSIONS_FILE = self._tmpdir / ".sessions.json"
        auth._sessions.clear()
        sessions_file = self._tmpdir / '.sessions.json'
        if sessions_file.exists():
            sessions_file.unlink()

    def tearDown(self) -> None:
        # Restore the per-test baselines captured in setUp so this file's
        # tests can't leak their tmpdir pointers into sibling test files
        # running later in the same pytest process. shutil.rmtree runs in a
        # finally block -- even if the restores above raise, the tmpdir is
        # still reaped.
        try:
            auth.STATE_DIR    = self._saved_auth_state_dir
            auth._SESSIONS_FILE = self._saved_sessions_file
            _config.STATE_DIR  = self._saved_config_state_dir
        finally:
            shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _simulate_restart(self) -> None:
        """Reload auth module to simulate a fresh process start.

        api.auth does `from api.config import STATE_DIR` at module level, so
        `_SESSIONS_FILE` is computed from api.config.STATE_DIR at reload time.
        We temporarily override api.config.STATE_DIR with our per-test tmpdir
        so the reload uses the isolated test path without reloading api.config
        itself (which would invalidate imported references like
        STREAM_PARTIAL_TEXT in other tests).
        """
        _saved = _config.STATE_DIR
        _config.STATE_DIR = self._tmpdir
        try:
            importlib.reload(auth)
        finally:
            _config.STATE_DIR = _saved
        # importlib.reload rebinds auth's globals from the re-executed module
        # body, so auth.STATE_DIR / auth._SESSIONS_FILE now point at our
        # tmpdir. tearDown above restores them to the per-test baseline.

    def test_session_survives_restart(self) -> None:
        """A session created before restart should still verify after reload."""
        cookie = auth.create_session()
        self.assertTrue(auth.verify_session(cookie))
        self._simulate_restart()
        self.assertTrue(auth.verify_session(cookie),
                        "Session must survive process restart via persisted .sessions.json")

    def test_invalidated_session_does_not_survive_restart(self) -> None:
        """Invalidating a session must be reflected after reload."""
        cookie = auth.create_session()
        auth.invalidate_session(cookie)
        self._simulate_restart()
        self.assertFalse(auth.verify_session(cookie),
                         "Invalidated session must not be reinstated after restart")

    def test_expired_sessions_pruned_on_load(self) -> None:
        """Sessions that expire between restarts must not be loaded."""
        sessions_file = self._tmpdir / '.sessions.json'
        # Write a sessions file with one expired and one valid entry
        now = time.time()
        sessions_file.write_text(json.dumps({
            "expired_token": now - 10,
            "valid_token": now + 3600,
        }))
        self._simulate_restart()
        self.assertNotIn("expired_token", auth._sessions)
        self.assertIn("valid_token", auth._sessions)

    def test_sessions_file_permissions(self) -> None:
        """Sessions file must be owner-read-only (0600)."""
        auth.create_session()
        sessions_file = self._tmpdir / '.sessions.json'
        self.assertTrue(sessions_file.exists(), ".sessions.json was not created")
        mode = oct(sessions_file.stat().st_mode & 0o777)
        self.assertEqual(mode, oct(0o600),
                         f".sessions.json permissions {mode} — expected 0o600")

    def test_malformed_sessions_file_starts_fresh(self) -> None:
        """A corrupt sessions file must not crash auth — start with empty dict."""
        sessions_file = self._tmpdir / '.sessions.json'
        sessions_file.write_text("not valid json {{{{")
        self._simulate_restart()
        self.assertEqual(auth._sessions, {},
                         "Corrupt sessions file must result in empty session dict")

    def test_sessions_file_wrong_type_starts_fresh(self) -> None:
        """A sessions file containing a non-dict must be ignored."""
        sessions_file = self._tmpdir / '.sessions.json'
        sessions_file.write_text(json.dumps(["list", "not", "dict"]))
        self._simulate_restart()
        self.assertEqual(auth._sessions, {})


if __name__ == "__main__":
    unittest.main()