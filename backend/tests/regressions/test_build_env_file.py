"""
Structural tests for the repo-root `.build.env` build defaults file.

`.build.env` (formerly `backend/scripts/hermes-agent.env`) is read at build and
install time by scripts/install.sh, scripts/npm_warmup.sh, scripts/build.sh and
two CI workflows. Every one of those consumers degrades SILENTLY when the file
is missing or renamed: install.sh stops pinning the hermes-agent SDK, the npm
warmup falls back to hardcoded Playwright revisions, and the image builds
unpinned. These tests pin the wiring down as text so a rename cannot half-land.
"""
import os
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[3]
BUILD_ENV = REPO / ".build.env"
BUILD_ENV_NAME = ".build.env"
LEGACY_PATH = "backend/scripts/" + "hermes-agent.env"  # split: this file must not self-match

INSTALL_SH = (REPO / "scripts" / "install.sh").read_text(encoding="utf-8")
BUILD_SH = (REPO / "scripts" / "build.sh").read_text(encoding="utf-8")
NPM_WARMUP_SH = (REPO / "scripts" / "npm_warmup.sh").read_text(encoding="utf-8")
DOCKERIGNORE = (REPO / ".dockerignore").read_text(encoding="utf-8")
TESTS_YML = (REPO / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
RELEASE_YML = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

# Directories that are either huge or not part of the source tree. buildah keeps
# image layers under the repo root on some setups (hundreds of GB), so the scan
# must PRUNE these while walking — an rglob that filters afterwards still
# descends into them and hangs.
SCAN_SKIP_DIRS = {
    ".git", "node_modules", "static", ".venv", ".pytest_cache",
    ".buildah-storage-root", ".buildah-storage-runroot", ".buldah-storage-runroot",
    "__pycache__", "frontend",
}
SCAN_SUFFIXES = {".sh", ".yml", ".yaml", ".py", ".md", ".env", ".txt"}
SCAN_NAMES = {"Dockerfile", ".dockerignore", ".gitignore", BUILD_ENV_NAME}


def _iter_scannable_files():
    """Yield source-ish files, pruning the heavy build directories."""
    self_name = pathlib.Path(__file__).name  # this file documents the old path on purpose
    for root, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames if d not in SCAN_SKIP_DIRS]
        for name in filenames:
            if name == self_name:
                continue
            if name not in SCAN_NAMES and pathlib.Path(name).suffix not in SCAN_SUFFIXES:
                continue
            yield pathlib.Path(root) / name


class TestBuildEnvFileExists:

    def test_file_is_at_repo_root(self):
        assert BUILD_ENV.is_file(), f"{BUILD_ENV_NAME} must exist at the repo root"

    def test_no_references_to_the_old_path(self):
        """A stale path is the main failure mode: it silently unpins the build."""
        offenders = []
        for path in _iter_scannable_files():
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if LEGACY_PATH in text:
                offenders.append(str(path.relative_to(REPO)))
        assert not offenders, (
            f"stale reference to {LEGACY_PATH} in: {offenders} — the file moved to "
            f"the repo root as {BUILD_ENV_NAME}"
        )

    def test_parses_as_plain_assignments(self):
        """The file is sourced under `set -e`.

        Every non-comment line must be a plain assignment: a trailing
        conditional (`[ -n ... ] && VAR=...`) would make the source return
        non-zero and abort install.sh before it installs anything.
        """
        assignment = re.compile(r"^\s*(export\s+)?[A-Za-z_][A-Za-z0-9_]*=")
        for lineno, line in enumerate(BUILD_ENV.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            assert assignment.match(line), (
                f"{BUILD_ENV_NAME}:{lineno} is not a plain KEY=value assignment: {line!r}"
            )

    def test_pin_assignment_is_never_empty(self):
        """`HERMES_AGENT_COMMIT=""` must not come back.

        npm_warmup.sh reads an already-exported HERMES_AGENT_COMMIT and THEN
        sources this file, so an empty assignment would erase the value passed
        in through the build-arg. Unpin by commenting the line out instead.
        """
        match = re.search(r"^HERMES_AGENT_COMMIT=(.*)$", BUILD_ENV.read_text(encoding="utf-8"), re.MULTILINE)
        if match is not None:
            assert match.group(1).strip().strip('"').strip("'"), (
                "HERMES_AGENT_COMMIT must be non-empty or commented out — an empty "
                "assignment silently erases an exported pin (see npm_warmup.sh)"
            )


class TestDockerignoreWhitelist:

    def test_build_env_is_whitelisted_after_the_hidden_file_rule(self):
        """`**/.*` is fail-closed; a `!` line only overrides earlier rules."""
        assert "!.env" in DOCKERIGNORE, "sanity: the .env whitelist entry is expected to exist"
        hidden_rule = DOCKERIGNORE.index("**/.*")
        whitelist = DOCKERIGNORE.index("!" + BUILD_ENV_NAME)
        assert whitelist > hidden_rule, (
            f"the !{BUILD_ENV_NAME} line must come AFTER the **/.* rule or it is ignored"
        )

    def test_hard_constraints_comment_names_the_new_path(self):
        assert BUILD_ENV_NAME in DOCKERIGNORE.split("Rules of thumb")[0], (
            f"the HARD CONSTRAINTS block must list {BUILD_ENV_NAME}"
        )


class TestConsumersPointAtIt:

    def test_install_sh_loads_it(self):
        assert BUILD_ENV_NAME in INSTALL_SH, "install.sh must load .build.env"
        assert LEGACY_PATH not in INSTALL_SH

    def test_install_sh_has_no_hardcoded_pin_fallback(self):
        """An unset pin must mean "no --commit", not "some other SHA"."""
        assert "f43eabee5f36e11448086ee8ee17c499958e81bf" not in INSTALL_SH, (
            "install.sh must not carry a hardcoded fallback pin — it drifted from "
            ".build.env once already and pinned installs that never saw this repo"
        )

    def test_build_sh_reads_it(self):
        assert "$PROJECT_ROOT/" + BUILD_ENV_NAME in BUILD_SH
        assert LEGACY_PATH not in BUILD_SH

    def test_npm_warmup_reads_it_in_the_image(self):
        assert "/app/" + BUILD_ENV_NAME in NPM_WARMUP_SH, (
            "npm_warmup.sh runs inside the image — it needs the /app/.build.env candidate"
        )
        assert LEGACY_PATH not in NPM_WARMUP_SH

    def test_ci_workflows_grep_it(self):
        for name, text in (("tests.yml", TESTS_YML), ("release.yml", RELEASE_YML)):
            assert BUILD_ENV_NAME in text, f"{name} must read the pin from {BUILD_ENV_NAME}"
            assert LEGACY_PATH not in text

    def test_release_workflow_resolves_the_pin_in_a_shell_step(self):
        """`with:` values are not shell-expanded by GitHub Actions.

        An inline `$(grep ...)` in `build-args:` reaches the builder as a
        literal string, which is then handed to install.sh as `--hermes-commit`.
        """
        assert "steps.pin.outputs.commit" in RELEASE_YML
        assert not re.search(r"^\s*HERMES_AGENT_COMMIT=\$\(", RELEASE_YML, re.MULTILINE), (
            "release.yml must resolve the pin in a `run:` step, not inline in build-args"
        )
