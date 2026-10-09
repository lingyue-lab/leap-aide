"""
Issue #569 (extracted): docker-entrypoint.sh must auto-detect host UID/GID.

Carved out of the original tests/test_issue569_579.py — that file mixed a
still-valid backend check (this file) with several tests that hit
the deleted legacy static JS bundles. The Docker init script must read
the host user's UID/GID via stat (probing a mounted volume) so files
written by leap-aide inside the container don't end up owned by root:root
on the host filesystem.

Eight static checks, no execution required.
"""
import pathlib
import re
import unittest

INIT_SCRIPT = (pathlib.Path(__file__).parent.parent.parent.parent / "backend" / "scripts" / "docker-entrypoint.sh").read_text(
    encoding="utf-8"
)


class TestHostUidAutoDetect(unittest.TestCase):
    """docker-entrypoint.sh: UID must be auto-detected from a mounted probe."""

    def test_stat_uid_used(self):
        self.assertRegex(
            INIT_SCRIPT, r"stat\s+-c\s+['\"]%u['\"]",
            "Host UID must be read with `stat -c '%u' <file>`",
        )

    def test_detected_uid_variable_used(self):
        # The detected value must be stored in a variable that downstream code consumes.
        self.assertRegex(
            INIT_SCRIPT, r"_detected_uid\s*=",
            "Detected UID must be captured in `_detected_uid`",
        )

    def test_auto_detect_comment_present(self):
        self.assertRegex(
            INIT_SCRIPT, r"#\s*Auto-detect",
            "Script must contain an `# Auto-detect` comment near UID detection",
        )


class TestHostGidAutoDetect(unittest.TestCase):
    """docker-entrypoint.sh: GID must be auto-detected from a mounted probe."""

    def test_stat_gid_used(self):
        self.assertRegex(
            INIT_SCRIPT, r"stat\s+-c\s+['\"]%g['\"]",
            "Host GID must be read with `stat -c '%g' <file>`",
        )

    def test_detected_gid_variable_used(self):
        self.assertRegex(
            INIT_SCRIPT, r"_detected_gid\s*=",
            "Detected GID must be captured in `_detected_gid`",
        )

    def test_auto_detect_gid_comment_present(self):
        self.assertRegex(
            INIT_SCRIPT,
            r"#\s*Auto-detect.*GID|GID.*Auto-detect",
            "Script must contain an `# Auto-detect ... GID` comment",
        )


class TestUidGidCoherence(unittest.TestCase):
    """UID and GID detection must both probe a mounted directory."""

    def test_both_uid_and_gid_targets_use_stat(self):
        # Both stat calls probe the same volume-mounted directory.
        uid_count = len(re.findall(r"stat\s+-c\s+['\"]%u['\"]", INIT_SCRIPT))
        gid_count = len(re.findall(r"stat\s+-c\s+['\"]%g['\"]", INIT_SCRIPT))
        self.assertGreaterEqual(uid_count, 1, "must probe UID at least once")
        self.assertGreaterEqual(gid_count, 1, "must probe GID at least once")

    def test_uid_assigned_before_consumption(self):
        # `_detected_uid` assignment must precede the volume-ownership warning.
        uid_match = re.search(r"_detected_uid\s*=", INIT_SCRIPT)
        # Find first consumer (echo of detected UID, or chown call).
        consumer_match = re.search(
            r"chown\s+[^\n]*_detected_uid|Auto-detected UID", INIT_SCRIPT,
        )
        self.assertIsNotNone(uid_match, "_detected_uid assignment must exist")
        self.assertIsNotNone(consumer_match, "_detected_uid must have a consumer")
        self.assertLess(
            uid_match.start(), consumer_match.start(),
            "_detected_uid must be assigned before being consumed",
        )


if __name__ == "__main__":
    unittest.main()