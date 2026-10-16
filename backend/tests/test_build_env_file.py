"""
Structural tests for the repo-root `.build.env` build defaults file.

`.build.env` (formerly `backend/scripts/hermes-agent.env`) is read at build and
install time by scripts/install.sh, scripts/npm_warmup.sh and scripts/build.sh,
plus one CI workflow that greps it directly. Three workflows depend on it:
run-tests-in-testing-image.yml greps it and feeds the value to --build-arg,
while build-runtime-image.yml and release-runtime-image.yml reach it through
scripts/build.sh, the consumer that resolves the pin on their behalf. Every one
of those consumers degrades SILENTLY when the file is missing or renamed:
install.sh stops pinning the hermes-agent SDK, the npm warmup falls back to
hardcoded Playwright revisions, and the image builds unpinned. These tests pin
the wiring down as text so a rename cannot half-land.
"""
import os
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
BUILD_ENV = REPO / ".build.env"
BUILD_ENV_NAME = ".build.env"
LEGACY_PATH = "backend/scripts/" + "hermes-agent.env"  # split: this file must not self-match

_read_cache = {}


def _read(relpath):
    """Read a repo-root-relative file (cached).

    A missing file must fail only the calling test with a clear hint instead of
    aborting pytest collection for the whole batch: inside the testing image
    /app is filtered by .dockerignore, so a dropped path would otherwise take
    the batch down with FileNotFoundError.
    """
    if relpath not in _read_cache:
        path = REPO / relpath
        assert path.is_file(), (
            f"{relpath} is missing under {REPO} — inside the testing image this "
            f"usually means the repo-root .dockerignore excludes it; whitelist it "
            f"with a `!` entry AFTER the `**/.*` rule"
        )
        _read_cache[relpath] = path.read_text(encoding="utf-8")
    return _read_cache[relpath]

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
        dockerignore = _read(".dockerignore")
        assert "!.env" in dockerignore, "sanity: the .env whitelist entry is expected to exist"
        hidden_rule = dockerignore.index("**/.*")
        whitelist = dockerignore.index("!" + BUILD_ENV_NAME)
        assert whitelist > hidden_rule, (
            f"the !{BUILD_ENV_NAME} line must come AFTER the **/.* rule or it is ignored"
        )

    def test_hard_constraints_comment_names_the_new_path(self):
        assert BUILD_ENV_NAME in _read(".dockerignore").split("Rules of thumb")[0], (
            f"the HARD CONSTRAINTS block must list {BUILD_ENV_NAME}"
        )


class TestConsumersPointAtIt:

    def test_install_sh_loads_it(self):
        install_sh = _read("scripts/install.sh")
        assert BUILD_ENV_NAME in install_sh, "install.sh must load .build.env"
        assert LEGACY_PATH not in install_sh

    def test_install_sh_has_no_hardcoded_pin_fallback(self):
        """An unset pin must mean "no --commit", not "some other SHA"."""
        assert "f43eabee5f36e11448086ee8ee17c499958e81bf" not in _read("scripts/install.sh"), (
            "install.sh must not carry a hardcoded fallback pin — it drifted from "
            ".build.env once already and pinned installs that never saw this repo"
        )

    def test_build_sh_reads_it(self):
        build_sh = _read("scripts/build.sh")
        assert "$PROJECT_ROOT/" + BUILD_ENV_NAME in build_sh
        assert LEGACY_PATH not in build_sh

    def test_npm_warmup_reads_it_in_the_image(self):
        npm_warmup_sh = _read("scripts/npm_warmup.sh")
        assert "/app/" + BUILD_ENV_NAME in npm_warmup_sh, (
            "npm_warmup.sh runs inside the image — it needs the /app/.build.env candidate"
        )
        assert LEGACY_PATH not in npm_warmup_sh

    def test_ci_workflows_grep_it(self):
        """Workflows that call buildah's build verb themselves must grep the pin
        and feed it to --build-arg — there is no scripts/build.sh on that path to
        do it for them."""
        for name, text in (
            ("run-tests-in-testing-image.yml",
             _read(".github/workflows/run-tests-in-testing-image.yml")),
        ):
            assert BUILD_ENV_NAME in text, f"{name} must read the pin from {BUILD_ENV_NAME}"
            assert LEGACY_PATH not in text

    def test_build_sh_workflows_delegate_the_pin(self):
        """build-runtime-image.yml and release-runtime-image.yml build through
        scripts/build.sh, which is what resolves HERMES_AGENT_COMMIT from
        .build.env for them — one level deeper than the old release.yml, but
        still one grep, in one place.

        Two ways to silently unpin these images: stop calling build.sh, or
        re-add the pin here as a build-arg. The second is exactly how the old
        release.yml failed — `with:` values are NOT shell-expanded by GitHub
        Actions, so an inline `$(grep ...)` was passed to the builder as a
        literal string, and then to install.sh as `--hermes-commit`.
        """
        for name, text in (
            ("build-runtime-image.yml",
             _read(".github/workflows/build-runtime-image.yml")),
            ("release-runtime-image.yml",
             _read(".github/workflows/release-runtime-image.yml")),
        ):
            assert "./scripts/build.sh --docker-image" in text, (
                f"{name} must build via ./scripts/build.sh --docker-image — that is "
                f"the only path by which the {BUILD_ENV_NAME} pin reaches the image"
            )
            assert "HERMES_AGENT_COMMIT" not in text, (
                f"{name} must not resolve the pin itself; scripts/build.sh already "
                f"does, from {BUILD_ENV_NAME} (an inline $(grep ...) in a `with:` "
                f"block reaches the builder as a literal string)"
            )
            assert LEGACY_PATH not in text

    def test_the_old_release_workflow_is_gone(self):
        """A leftover release.yml would fire on the same tag push as
        release-runtime-image.yml and publish a second, differently-versioned
        image."""
        assert not (REPO / ".github" / "workflows" / "release.yml").exists()

    def test_release_and_daily_workflows_share_the_build_roster(self):
        """release-runtime-image.yml is build-runtime-image.yml with a different
        trigger and version. A BUILD_* variable added to one and not the other
        means the released image is not the image the daily build exercises."""
        def roster(text):
            return sorted(re.findall(r"^\s+(BUILD_[A-Z_]+):", text, re.MULTILINE))

        assert roster(_read(".github/workflows/release-runtime-image.yml")) == \
            roster(_read(".github/workflows/build-runtime-image.yml"))
