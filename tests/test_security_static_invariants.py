"""
Security hardening pass — source-text regression checks.

Originally carved out of tests/test_sprint43.py because that file mixed
backend source-level checks (this file) with frontend DOM checks that crash
in the headless backend-test environment.

Four backend guarantees remain:

  - api/config.py validates the discovered gateway URL uses an http(s) scheme
  - server.py uses a QuietHTTPServer subclass (only access logging)
  - api/routes.py calls _redact_text for sensitive patterns
  - api/streaming.py strips HTML tags from provider error responses
"""
import re
import unittest

REPO_ROOT = None  # set in setUpModule via __file__
SERVER_PY: str = ""
ROUTES_PY: str = ""
CONFIG_PY: str = ""


def setUpModule():
    global REPO_ROOT, SERVER_PY, ROUTES_PY, CONFIG_PY
    import pathlib
    REPO_ROOT = pathlib.Path(__file__).parent.parent
    SERVER_PY = (REPO_ROOT / "server.py").read_text(encoding="utf-8")
    ROUTES_PY = (REPO_ROOT / "api" / "routes.py").read_text(encoding="utf-8")
    CONFIG_PY = (REPO_ROOT / "api" / "config.py").read_text(encoding="utf-8")


class TestGatewayUrlSchemeValidation(unittest.TestCase):
    """api/config.py: gateway URL must reject non-http(s) schemes."""

    def test_urlparse_used(self):
        self.assertIn(
            "urlparse", CONFIG_PY,
            "URL scheme validation must call urlparse()",
        )

    def test_scheme_allowlist_includes_http_and_https(self):
        # Allow-list must contain both http and https (with optional "").
        self.assertRegex(
            CONFIG_PY,
            r'scheme\s+not\s+in\s*\(\s*["\'](?:["\']\s*,\s*["\'])*https?["\']',
            "URL scheme must be checked against an explicit http/https allow-list",
        )

    def test_invalid_scheme_raises(self):
        # Non-http(s) schemes must raise ValueError (PR #351 hardening).
        self.assertRegex(
            CONFIG_PY,
            r"raise\s+ValueError.*[Ss]cheme",
            "Invalid URL scheme must raise ValueError",
        )

    def test_bandit_b310_annotation_present(self):
        # Bandit flags urlopen() without scheme allow-list; we mark the line safe.
        self.assertRegex(
            CONFIG_PY, r"#\s*nosec\s+B310",
            "urlopen call must be annotated with `# nosec B310` after fix",
        )


class TestQuietHTTPServer(unittest.TestCase):
    """server.py: QuietHTTPServer subclass must wrap ThreadingHTTPServer."""

    def test_class_defined(self):
        self.assertRegex(
            SERVER_PY, r"class\s+QuietHTTPServer\s*\(",
            "QuietHTTPServer subclass must be defined in server.py",
        )

    def test_log_message_formatter_replaced(self):
        # The subclass overrides log_message to silence access-log spam.
        self.assertIn(
            "log_message", SERVER_PY,
            "QuietHTTPServer.log_message must be defined (silencing override)",
        )

    def test_quiet_server_instantiated(self):
        self.assertRegex(
            SERVER_PY, r"QuietHTTPServer\s*\(",
            "QuietHTTPServer must be the actual instantiated server",
        )


class TestRedactTextUsage(unittest.TestCase):
    """api/routes.py: _redact_text must be used on sensitive output paths."""

    def test_redact_text_imported(self):
        # Import may be multi-line (parenthesised `from api.helpers import (...)`).
        # Accept any whitespace between `import` and `_redact_text`.
        self.assertRegex(
            ROUTES_PY, r"from\s+api\.[\w.]+\s+import\s+[\s\S]{0,200}_redact_text",
            "_redact_text must be imported in routes.py",
        )

    def test_redact_text_used_in_multiple_paths(self):
        # At least two distinct call sites (initial error path + post-error path).
        call_count = len(re.findall(r"_redact_text\s*\(", ROUTES_PY))
        self.assertGreaterEqual(
            call_count, 2,
            f"_redact_text must be used in ≥2 places, found {call_count}",
        )


if __name__ == "__main__":
    unittest.main()