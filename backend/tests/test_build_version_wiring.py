"""
Structural tests for the image version badge build-arg.

The app's own version reaches the image through three files that must agree on
one ARG name: the Dockerfile declares ``ARG <NAME>`` and writes api/_version.py
from it, scripts/build.sh forwards the value, and the testing-image workflow
forwards it too (it calls buildah directly, without build.sh).

A disagreement fails NOTHING. The ARG arrives unset, the Dockerfile's ``unknown``
default wins, and the settings badge plus the HTTP Server header quietly report
``unknown`` on every install. These tests pin the name down as text so a rename
cannot half-land.

The arg is named LEAP_AIDE_VERSION rather than the historical HERMES_VERSION
because the value is *this app's* version (the git tag). Every HERMES_* name in
the Dockerfile describes the hermes-agent SDK, whose version is pinned separately
by HERMES_AGENT_COMMIT — the old name invited exactly that confusion.
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
VERSION_ARG = "LEAP_AIDE_VERSION"

DOCKERFILE = (REPO / "Dockerfile").read_text(encoding="utf-8")
BUILD_SH = (REPO / "scripts" / "build.sh").read_text(encoding="utf-8")
TESTING_IMAGE_YML = (
    REPO / ".github" / "workflows" / "run-tests-in-testing-image.yml"
).read_text(encoding="utf-8")

# scripts/install.sh has an unrelated `_HERMES_VERSION_SPECIFIED` flag (it tracks
# whether the hermes-agent installer was handed an explicit version), so the scan
# for the retired arg name must not match a `_`-prefixed identifier.
STALE_ARG = re.compile(r"(?<!_)HERMES_VERSION")


class TestVersionArgIsForwarded:

    def test_dockerfile_declares_it_and_bakes_it(self):
        assert f"ARG {VERSION_ARG}=" in DOCKERFILE, (
            f"the Dockerfile must declare ARG {VERSION_ARG} with a default, so a "
            f"bare `docker build` still produces a working image"
        )
        assert f"${{{VERSION_ARG}}}" in DOCKERFILE, (
            f"api/_version.py must be written from the {VERSION_ARG} ARG — "
            f"declaring it without interpolating it bakes 'unknown'"
        )

    def test_build_sh_forwards_it(self):
        assert f'--build-arg "{VERSION_ARG}=' in BUILD_SH, (
            f"scripts/build.sh must forward --build-arg {VERSION_ARG}; without it the "
            f"Dockerfile's 'unknown' default silently wins"
        )

    def test_testing_image_workflow_forwards_it(self):
        """This workflow invokes buildah itself, so build.sh cannot do it for it."""
        assert f'--build-arg "{VERSION_ARG}=' in TESTING_IMAGE_YML, (
            f"run-tests-in-testing-image.yml must forward --build-arg {VERSION_ARG}"
        )


class TestNoStaleArgName:

    def test_retired_name_is_gone(self):
        for name, text in (
            ("Dockerfile", DOCKERFILE),
            ("scripts/build.sh", BUILD_SH),
            ("run-tests-in-testing-image.yml", TESTING_IMAGE_YML),
        ):
            assert not STALE_ARG.search(text), (
                f"{name} still references the retired HERMES_VERSION build-arg — a "
                f"half-landed rename leaves _version.py at 'unknown' with no error"
            )
