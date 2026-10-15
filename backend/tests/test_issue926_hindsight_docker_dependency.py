"""Regression tests for #926 Hindsight dependency in Docker WebUI venv.

The original fix installed hindsight-client from `scripts/docker-init.sh` at
container start, *after* the `.deps_installed` fast-restart guard, so existing
Docker venvs self-healed on the next boot. That script no longer exists: the
dependency is now installed at image build time in the Dockerfile's `app`
stage (`uv pip install --system`), which makes the missing-dependency state
unreachable rather than self-healing.

These tests pin the surviving invariant — hindsight-client is a *Docker-only*
extra, installed from the Dockerfile with a version floor — so neither half can
be dropped silently: removing the Dockerfile install breaks hindsight inside
the image, and moving it into requirements.txt leaks an optional memory client
into the local non-Docker bootstrap.
"""
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCKERFILE = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
REQUIREMENTS_TXT = (REPO_ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8")


def test_926_dockerfile_installs_hindsight_distribution():
    """The image build must install the PyPI distribution named hindsight-client."""
    # `>=0.4.22` is the floor that fixed #926, not an arbitrary pin: lowering it
    # reinstates the bug, so the literal is deliberately asserted.
    assert '"hindsight-client>=0.4.22"' in DOCKERFILE
    assert re.search(
        r'uv pip install --system\s+\\\n\s+"hindsight-client>=', DOCKERFILE
    ), "hindsight-client must be installed system-wide by the image build"


def test_926_hindsight_dependency_stays_docker_specific():
    """Local non-Docker bootstrap should not install optional memory clients."""
    assert "hindsight-client" not in REQUIREMENTS_TXT
