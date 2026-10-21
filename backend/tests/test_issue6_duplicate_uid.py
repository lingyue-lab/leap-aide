"""Issue #6: no duplicate UID may shadow leap-aide in the terminal prompt.

Static checks only — no container required. Mirrors the style of
test_issue569_docker_uid.py.

Run: python3 backend/tests/test_issue6_duplicate_uid.py -v
"""
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DOCKERFILE = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
ENTRYPOINT = (REPO_ROOT / "backend" / "scripts" / "docker-entrypoint.sh").read_text(encoding="utf-8")


def _code(text):
    """Drop comment-only lines so prose about `usermod -o` cannot match."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


ENTRYPOINT_CODE = _code(ENTRYPOINT)


class TestDistroDefaultAccountRemoved(unittest.TestCase):
    """ubuntu:24.04 ships an 'ubuntu' account at UID/GID 1000; free it."""

    def test_ubuntu_account_is_deleted(self):
        self.assertIn(
            "userdel ubuntu", DOCKERFILE,
            "the distro default 'ubuntu' account (UID/GID 1000) must be removed in the base stage",
        )

    def test_removal_precedes_leap_aide_creation(self):
        self.assertIn("userdel ubuntu", DOCKERFILE)
        self.assertLess(
            DOCKERFILE.index("userdel ubuntu"),
            DOCKERFILE.index("useradd -u 1024"),
            "UID 1000 must be freed before the leap-aide users are created",
        )


class TestNoDuplicateIdRemap(unittest.TestCase):
    """`usermod -o` silently creates a second holder of the wanted UID."""

    def test_usermod_disallows_duplicates(self):
        self.assertNotIn("usermod -o", ENTRYPOINT_CODE)

    def test_groupmod_disallows_duplicates(self):
        self.assertNotIn("groupmod -o", ENTRYPOINT_CODE)

    def test_collision_guard_present(self):
        self.assertIn("Releasing UID", ENTRYPOINT_CODE)
        self.assertIn("Releasing GID", ENTRYPOINT_CODE)

    def test_guard_runs_before_remap(self):
        self.assertIn("Releasing UID", ENTRYPOINT_CODE)
        self.assertLess(
            ENTRYPOINT_CODE.index("Releasing UID"),
            ENTRYPOINT_CODE.index("usermod -u ${WANTED_UID} leap-aide"),
            "the conflict check must precede the usermod call",
        )

    def test_identity_is_logged(self):
        self.assertIn("-- Running as $(id -un)", ENTRYPOINT_CODE)


class TestRootModeCarveOut(unittest.TestCase):
    """WANTED_UID=0 is a deliberate root-mode escape hatch, not a collision."""

    def test_root_mode_checked_before_collision_guard(self):
        self.assertIn('"${WANTED_UID}" = "0"', ENTRYPOINT_CODE)
        self.assertLess(
            ENTRYPOINT_CODE.index('"${WANTED_UID}" = "0"'),
            ENTRYPOINT_CODE.index("_uid_owner="),
            "root mode must be decided before the collision guard deletes anything",
        )

    def test_root_mode_is_all_or_nothing(self):
        self.assertIn("WANTED_UID=0 requires WANTED_GID=0", ENTRYPOINT_CODE)

    def test_root_mode_skips_usermod(self):
        root_mode = ENTRYPOINT_CODE.index('"${WANTED_UID}" = "0"')
        usermod = ENTRYPOINT_CODE.index("usermod -u ${WANTED_UID} leap-aide")
        self.assertIn("ok_exit", ENTRYPOINT_CODE[root_mode:usermod],
                      "root mode must exit before the remap")

    def test_root_mode_warns(self):
        self.assertIn("run as root inside the container", ENTRYPOINT_CODE)


if __name__ == "__main__":
    unittest.main()
