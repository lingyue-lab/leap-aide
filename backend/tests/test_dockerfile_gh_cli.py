"""Guard: the image must ship the GitHub CLI (`gh`) and keep it usable.

The Hermes Agent SDK shells out to `gh` on its own: cli.py runs
`gh pr list` to detect merged pull requests, and tools/skills_hub.py runs
`gh auth token` to authenticate against the skills hub. When the binary is
missing from the image both calls fail with "gh: command not found", so
every GitHub-facing agent task (PR/issue triage, repo queries, skills hub
auth) is dead on arrival inside the container.

The invariants that make `gh` usable rather than merely present are
pinned here:

  * the base stage installs the `gh` package — Ubuntu's `universe` component
    already ships it, so it needs no extra apt repo, keyring or pinned
    tarball and it inherits BUILD_APT_MIRROR like every other tool.
  * the base stage bakes in the `gh token` extension (Link-/gh-token) for
    every identity the container may run as. It is installed by hand, not
    through `gh extension install`: gh's root command runs an auth check
    that exits 4 for every built-in command when no token is present, and
    the image build has no GitHub credentials. gh stores extensions per
    invoking user, so one install per identity is the minimum.
  * the runtime stage points GH_CONFIG_DIR inside the persistent
    ~/.hermes volume, so `gh auth login` survives a container recreation
    and the SDK's `gh auth token` probe reads the same credentials.
  * install.sh installs `gh` on both the container and the host path, so
    a deployment provisioned by the installer alone is not left without it.
"""
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_read_cache = {}


def _dockerfile() -> str:
    """Read the Dockerfile (cached).

    Read lazily so a missing file fails only the calling test with a clear
    hint instead of aborting pytest collection for the whole batch.
    """
    if "dockerfile" not in _read_cache:
        path = REPO_ROOT / "Dockerfile"
        assert path.is_file(), (
            f"Dockerfile is missing under {REPO_ROOT} — inside the testing "
            "image this usually means the repo-root .dockerignore excludes it"
        )
        _read_cache["dockerfile"] = path.read_text(encoding="utf-8")
    return _read_cache["dockerfile"]


def _install_sh() -> str:
    """Read scripts/install.sh (cached, lazy — see _dockerfile)."""
    if "install.sh" not in _read_cache:
        path = REPO_ROOT / "scripts" / "install.sh"
        assert path.is_file(), (
            f"scripts/install.sh is missing under {REPO_ROOT} — inside the "
            "testing image this usually means the repo-root .dockerignore "
            "excludes it"
        )
        _read_cache["install.sh"] = path.read_text(encoding="utf-8")
    return _read_cache["install.sh"]


def _stage_body(name: str) -> str:
    """Return the body of the named build stage (``FROM <base> AS <name>``)."""
    dockerfile = _dockerfile()
    head = re.search(
        rf"^FROM\s+\S+\s+AS\s+{re.escape(name)}\s*$", dockerfile, re.MULTILINE
    )
    assert head, f"Dockerfile has no '{name}' build stage"
    rest = dockerfile[head.end():]
    nxt = re.search(r"^FROM\s+", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def _apt_install_args(text: str) -> str:
    """Flatten every ``apt-get install`` continuation block into one string."""
    blocks: list[str] = []
    for logical in _logical_lines(text):
        if "apt-get install" in logical:
            blocks.append(logical)
    return " ".join(blocks)


def _logical_lines(text: str) -> list[str]:
    """Join shell continuation lines into logical lines.

    A ``RUN`` that spans lines writes its package list on a *continuation*
    line (``RUN apt-get purge -y --auto-remove \\`` /
    ``        build-essential python3.12-dev xz-utils libatomic1 \\``), so a
    scan of physical lines never sees a package name next to the verb.
    Joining first is what lets the purge guard below actually fail.
    """
    merged: list[str] = []
    buf = ""
    for line in text.splitlines():
        stripped = line.strip()
        buf = f"{buf} {stripped}" if buf else stripped
        if buf.endswith("\\"):
            buf = buf[:-1].rstrip()
            continue
        merged.append(buf)
        buf = ""
    if buf:
        merged.append(buf)
    return merged


def _apt_removed_packages(text: str) -> set[str]:
    """Package names passed to ``apt-get purge``/``remove``/``autoremove``."""
    removed: set[str] = set()
    for logical in _logical_lines(text):
        for segment in logical.split("&&"):
            match = re.search(r"apt-get\s+(?:purge|remove|autoremove)\b(.*)", segment)
            if not match:
                continue
            for token in match.group(1).split():
                if token.startswith("-"):
                    continue
                removed.add(token)
    return removed


def test_base_stage_installs_gh_package():
    """The base stage must install `gh` — every later stage inherits it."""
    args = _apt_install_args(_stage_body("base"))
    assert re.search(r"(?:^|\s)gh(?:\s|$)", args), (
        "the base stage must install the `gh` package — without it the Hermes "
        "SDK's `gh pr list` / `gh auth token` calls fail with "
        "'gh: command not found' and every GitHub agent task breaks"
    )


def test_runtime_stage_keeps_gh_through_the_apt_trim():
    """The runtime trim must not purge `gh` along with the build chain.

    Scans the *flattened* apt commands, not physical lines: the package
    list lives on a continuation line, so a per-line scan would never see
    `gh` next to the verb and could never fail.
    """
    removed = _apt_removed_packages(_stage_body("runtime"))
    assert "gh" not in removed, (
        "the runtime stage must not remove `gh` — it is a runtime "
        "dependency of the Hermes Agent SDK, not a build tool; "
        f"the trim removes: {sorted(removed)}"
    )


def test_runtime_stage_points_gh_config_dir_at_persistent_volume():
    """GH_CONFIG_DIR must live inside the mounted ~/.hermes volume."""
    runtime = _stage_body("runtime")
    assert re.search(
        r"^ENV\s+GH_CONFIG_DIR=/home/leap-aide/\.hermes/gh\s*$", runtime, re.MULTILINE
    ), (
        "the runtime stage must set GH_CONFIG_DIR=/home/leap-aide/.hermes/gh — "
        "the default ~/.config/gh is outside the compose volume, so "
        "`gh auth login` would be lost on every container recreation"
    )


def test_install_sh_installs_gh_for_container_provisioning():
    """install.sh's container dep list must install gh as well.

    The Dockerfile already installs gh, so this only fires for containers
    provisioned by install.sh alone — but the two lists are supposed to
    stay in sync, and a container without gh breaks the same SDK calls.
    """
    install_sh = _install_sh()
    assert re.search(
        r"if ! command -v gh[^\n]*; then\s*\n\s*"
        r"_sys_deps_to_install=\"\$\{_sys_deps_to_install\} gh\"",
        install_sh,
    ), "install.sh's IN_DOCKER dependency list must install the `gh` package"


def test_install_sh_installs_gh_on_the_host_path():
    """The host path must install gh too.

    The SDK's `gh pr list` / `gh auth token` calls run in host mode as
    well, so the shared install path (next to git/curl) has to cover gh —
    not just the Docker-only dependency list.
    """
    install_sh = _install_sh()
    assert "install_package gh" in install_sh, (
        "install.sh's shared dependency path must install the `gh` package — "
        "the Hermes SDK shells out to `gh` in host mode as well"
    )


def test_base_stage_installs_gh_token_extension():
    """The `gh token` extension must be baked into the image.

    gh keeps extensions in the *invoking user's* data dir, so the files
    have to land in the tree of every identity the container may run as:
    a root install sits in /root/.local/share/gh/extensions and the agent
    — which runs as leap-aide — never sees it.
    """
    base = _stage_body("base")
    assert "/Link-/gh-token/releases/download/" in base, (
        "the base stage must install the Link-/gh-token extension — it "
        "provides `gh token generate|revoke|installations` for GitHub App "
        "installation access tokens"
    )
    assert ".local/share/gh/extensions/gh-token" in base, (
        "the extension must land in a gh data dir — gh stores extensions per "
        "user, so an install for the wrong identity is invisible at runtime"
    )
    assert "gh token --version" in base, (
        "the install must be smoke-tested with `gh token --version` — the "
        "extension is only useful if gh can dispatch it"
    )


def test_gh_token_extension_covers_every_runtime_identity():
    """Every identity the container may run as must find the extension.

    The server runs as `leap-aide`, the entrypoint's first phase runs as
    `leap-aide-init`, and root mode (WANTED_UID=0) runs everything as
    root. gh resolves its extension dir from the invoking user's HOME, so
    an extension installed for only one of them is invisible to the others.
    """
    base = _stage_body("base")
    match = re.search(r"for\s+_home\s+in\s+([^;]+);", base)
    assert match, "the extension install must loop over the runtime identities"
    homes = match.group(1)
    for home in ("/home/leap-aide", "/home/leap-aide-init", "/root"):
        assert home in homes, (
            f"the gh-token extension must be installed for {home} too — the "
            "container may run the server under that identity"
        )
        # Each identity must also be smoke-tested, or the build can go green
        # with an install that only one of them can actually dispatch.
        # Each of these homes is named after its user (leap-aide, leap-aide-init,
        # root), so the basename is the account to `su` into.
        user = home.rsplit("/", 1)[-1]
        assert f"su -s /bin/bash -l {user} -c 'gh token --version'" in base, (
            f"the build must smoke-test `gh token --version` as {user} — "
            "installing for an identity without proving dispatch works there "
            "defeats the per-identity install"
        )


def test_gh_token_extension_download_is_mirrorable():
    """The release download must be redirectable, like every other download.

    github.com is often unreachable (not merely slow) from CN networks, and
    --retry cannot rescue an unreachable host; a build arg is the only way
    out, and every other external fetch in this Dockerfile has one.
    """
    base = _stage_body("base")
    assert re.search(r"^ARG\s+BUILD_GH_TOKEN_DOWNLOAD_HOST=", base, re.MULTILINE), (
        "the gh-token download must expose a BUILD_GH_TOKEN_DOWNLOAD_HOST "
        "build arg, the same way BUILD_NPM_REGISTRY / "
        "BUILD_PLAYWRIGHT_DOWNLOAD_HOST cover their downloads"
    )
    assert "${BUILD_GH_TOKEN_DOWNLOAD_HOST:-https://github.com}" in base, (
        "the build arg must default to github.com so ordinary builds need no "
        "configuration"
    )
    assert '"${_gh_token_host}/Link-/gh-token/releases/download/' in base, (
        "the download URL must be built from the resolved host variable — a "
        "hard-coded URL would make the build arg decorative"
    )


def test_gh_token_extension_version_is_pinned():
    """The extension version must be pinned, like the hermes-agent commit."""
    base = _stage_body("base")
    assert re.search(
        r"^ARG GH_TOKEN_EXTENSION_VERSION=v\d+\.\d+\.\d+", base, re.MULTILINE
    ), "the gh-token extension version must be pinned to an explicit release tag"
    assert "releases/download/${GH_TOKEN_EXTENSION_VERSION}/" in base, (
        "the release URL must use the pinned version, not 'latest'"
    )
