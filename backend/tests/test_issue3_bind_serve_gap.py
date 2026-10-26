"""Regression tests for issue #3: first-boot ERR_TOO_MANY_RETRIES.

Before the fix, server.py bound and TLS-wrapped the socket (stage 7) but only
called serve_forever() after state.db validation, session display startup and
the synchronous prewarm imports. During that window the kernel completed TCP
connects into the accept backlog yet never answered the TLS handshake, so
Chrome exhausted its retry budget (net::ERR_TOO_MANY_RETRIES) and the index
CSS failed to load on first container boot; a refresh after serve_forever
started worked.

The fix starts serve_forever() on a thread immediately after the TLS wrap and
gates readiness instead: /health answers 503 {"status": "initializing"} and
non-public /api/* answers 503 + Retry-After until post-bind init finishes.

These tests boot the real server as a subprocess (same pattern as
test_tls_support.py) and assert:

1. ``Listening:`` is logged BEFORE stage 8 / prewarm — i.e. serving starts
   as soon as the socket is bound, not after the slow init.
2. With the LEAP_AIDE_TEST_BOOT_STALL hook widening the listen→ready window,
   /login is answered *during* init while /health reports 503, and /health
   flips to 200 once init completes.
"""
import http.client
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent  # backend/


def _find_free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port: int, extra_env: dict | None = None):
    """Start server.py as a subprocess; returns (proc, log_path)."""
    env = {k: v for k, v in os.environ.items()}
    env["HERMES_WEBUI_HOST"] = "127.0.0.1"
    env["HERMES_WEBUI_PORT"] = str(port)
    env["LOG_LEVEL"] = "INFO"  # capture Listening:/stage lines
    # Fresh state dir + no password: auth disabled, no cross-test leakage.
    env["HERMES_WEBUI_STATE_DIR"] = str(Path(tempfile.mkdtemp()))
    env.pop("HERMES_WEBUI_PASSWORD", None)
    env.pop("HERMES_WEBUI_TLS_CERT", None)
    env.pop("HERMES_WEBUI_TLS_KEY", None)
    env.pop("LEAP_AIDE_TEST_BOOT_STALL", None)
    for k, v in (extra_env or {}).items():
        env[k] = v
    log_path = Path(tempfile.mkdtemp()) / "server.log"
    log_file = open(log_path, "w+")
    proc = subprocess.Popen(
        [os.sys.executable, "-m", "app.server"],
        env=env, stdout=log_file, stderr=subprocess.STDOUT,
        text=True, cwd=str(ROOT),
    )
    proc._log_path = str(log_path)
    proc._log_file = log_file
    return proc, str(log_path)


def _get(port: int, path: str, timeout: float = 2.0) -> tuple[int, dict, bytes]:
    """One GET against 127.0.0.1:port; returns (status, headers, body)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, dict(resp.getheaders()), body
    finally:
        conn.close()


def _wait_health_ok(port: int, timeout: float = 45.0) -> bool:
    """Poll /health until 200 {"status":"ok"} or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, _headers, body = _get(port, "/health", timeout=2.0)
            if status == 200 and json.loads(body).get("status") == "ok":
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def _wait_any_response(port: int, path: str, timeout: float) -> tuple[float, int, dict, bytes] | None:
    """Poll *path* until the server answers; returns (elapsed, status, headers, body)."""
    t0 = time.monotonic()
    deadline = t0 + timeout
    while time.monotonic() < deadline:
        try:
            status, headers, body = _get(port, path, timeout=2.0)
            return (time.monotonic() - t0, status, headers, body)
        except Exception:
            time.sleep(0.1)
    return None


class _ServerTestCase(unittest.TestCase):
    """Shared teardown: terminate the server subprocess."""

    _proc = None
    _log_path = None

    def tearDown(self):
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        log_file = getattr(self._proc, "_log_file", None) if self._proc else None
        if log_file is not None:
            try:
                log_file.close()
            except Exception:
                pass
        self._proc = None


class TestListeningBeforeInit(_ServerTestCase):
    """Listening: must be logged before stage 8 / prewarm (fix ordering)."""

    def test_listening_logged_before_stage8_and_prewarm(self):
        port = _find_free_port()
        self._proc, self._log_path = _start_server(port)
        self.assertTrue(
            _wait_health_ok(port),
            f"Server never became healthy; see log: {self._log_path}",
        )
        # /health flips to ok right at mark_server_ready(); the Startup:
        # log line lands microseconds later on the main thread — poll briefly.
        log_text = ""
        deadline = time.time() + 5
        while time.time() < deadline:
            log_text = Path(self._log_path).read_text(errors="replace")
            if "Startup:" in log_text:
                break
            time.sleep(0.1)
        i_listen = log_text.find("Listening:")
        # Match on the stage name — the "[N/M]" numbers drift when stages
        # are added (_TOTAL_STAGES).
        i_stage8 = log_text.find("Validating state.db")
        i_prewarm = log_text.find("Pre-warm:")
        self.assertNotEqual(i_listen, -1, f"no Listening: line in log:\n{log_text[-2000:]}")
        self.assertNotEqual(i_stage8, -1, f"no stage-8 line in log:\n{log_text[-2000:]}")
        self.assertNotEqual(i_prewarm, -1, f"no Pre-warm: line in log:\n{log_text[-2000:]}")
        # The fix: serving starts right after the bind (stage 7), before the
        # slow post-bind init. Before the fix Listening: came after both.
        self.assertLess(i_listen, i_stage8,
                        "Listening: must precede stage 8 (issue #3)")
        self.assertLess(i_listen, i_prewarm,
                        "Listening: must precede prewarm (issue #3)")
        # Readiness is only marked after prewarm.
        i_startup = log_text.find("Startup:")
        self.assertNotEqual(i_startup, -1)
        self.assertLess(i_prewarm, i_startup)


class TestRequestsAnsweredDuringInit(_ServerTestCase):
    """The serve thread must answer public paths during the init window."""

    def test_login_served_and_health_503_during_stall(self):
        stall = 2.0
        port = _find_free_port()
        self._proc, self._log_path = _start_server(
            port, extra_env={"LEAP_AIDE_TEST_BOOT_STALL": str(stall)},
        )
        # /login is only answerable once the serve thread is accepting, i.e.
        # inside the stall window. First successful response must arrive fast
        # once the port is reachable — requests are NOT held until init ends.
        first = _wait_any_response(port, "/login", timeout=40.0)
        self.assertIsNotNone(
            first, f"/login never answered during init; see log: {self._log_path}",
        )
        # The very first response to /login is already 200: the serve thread
        # answers public paths the moment the socket is bound, while the
        # stall below proves init has NOT finished yet. (elapsed includes
        # pre-bind stages 1-6, so it is not asserted here.)
        _elapsed, status, _headers, _body = first
        self.assertEqual(status, 200)

        # Still in the init window: /health gates readiness...
        status, _headers, body = _get(port, "/health", timeout=2.0)
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body).get("status"), "initializing")

        # ...and non-public /api/* is gated too (auth disabled → request
        # would otherwise be handled). Static/HTML paths keep flowing.
        status, headers, body = _get(port, "/api/models", timeout=2.0)
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body).get("error"), "initializing")
        self.assertEqual(headers.get("Retry-After"), "1")
        status2, _h, _b = _get(port, "/login", timeout=2.0)
        self.assertEqual(status2, 200, "/login must stay served during init")

        # After init completes, /health flips to ok.
        self.assertTrue(
            _wait_health_ok(port, timeout=30.0),
            f"/health never reached 200; see log: {self._log_path}",
        )
        status, _h, _b = _get(port, "/api/models", timeout=2.0)
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
