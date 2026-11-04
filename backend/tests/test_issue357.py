"""
Tests for GitHub issue #357: Docker container fails to start without internet access.

Structural tests — verify Dockerfile and docker-entrypoint.sh contain the expected
patterns for pre-installed uv and workspace permission fixes.

Two problems fixed:
1. uv was downloaded at container startup; fails in air-gapped / firewalled environments.
   Fix: pre-install uv in the Docker image at build time (system-wide in /usr/local/bin).
2. workspace directory created with plain mkdir (as root); bind-mount dirs created by
   Docker as root are unwritable by the leap-aide user.
   Fix: sudo mkdir + sudo chown for workspace directory.
"""
import pathlib
import re

# Repo-root-relative paths are resolved from __file__ rather than the process
# cwd: `run-test.sh` chdirs to the repo root, but a bare `pytest backend/tests`
# run from anywhere else must still find the Dockerfile.
REPO = pathlib.Path(__file__).resolve().parents[2]
DOCKERFILE = (REPO / "Dockerfile").read_text(encoding="utf-8")
INIT_SCRIPT = (REPO / "backend" / "scripts" / "docker-entrypoint.sh").read_text(encoding="utf-8")


# ── Dockerfile: uv pre-installed at build time ───────────────────────────────

class TestDockerfileUvPreinstall:

    def test_dockerfile_installs_uv_at_build_time(self):
        """Dockerfile must install uv via curl at build time (not only at runtime)."""
        # The fetch sits inside a `RUN /bin/bash -c '...'` wrapper rather than
        # immediately after RUN, so match on the fetch itself — a literal
        # "RUN curl" prefix would never match.
        assert "curl" in DOCKERFILE and "uv/install.sh" in DOCKERFILE, (
            "Dockerfile must install uv at build time via curl .../uv/install.sh"
        )

    def test_dockerfile_uv_installed_system_wide(self):
        """uv must be installed to a system-wide directory (/usr/local/bin) accessible
        to all users, not to a user-specific ~/.local/bin that another user can't see."""
        # The install command must target /usr/local/bin or use root to install globally
        uv_install_line = next(
            (line for line in DOCKERFILE.splitlines() if "uv/install.sh" in line),
            None,
        )
        assert uv_install_line is not None, "Could not find uv install line in Dockerfile"
        # Must either use UV_INSTALL_DIR pointing to /usr/local/bin, or run as root
        # (so the default install location is accessible to leap-aide user)
        has_system_dir = "/usr/local/bin" in uv_install_line or "UV_INSTALL_DIR=/usr/local/bin" in DOCKERFILE
        assert has_system_dir, (
            "uv must be installed to /usr/local/bin (system-wide) so leap-aide user "
            "can find it. Installing as leap-aide-init puts it in /home/leap-aide-init/.local/bin "
            "which is NOT on leap-aide's PATH."
        )

    def test_dockerfile_uv_installed_before_copy(self):
        """uv installation must happen before COPY . /app so it's in the image."""
        uv_pos = DOCKERFILE.find("uv/install.sh")
        # Match COPY regardless of flags (e.g. --chown=...) — only the destination matters.
        m = re.search(r"^COPY\b.*\s/app\b", DOCKERFILE, re.MULTILINE)
        assert uv_pos != -1, "uv install not found in Dockerfile"
        assert m is not None, "COPY ... /app not found in Dockerfile"
        copy_pos = m.start()
        assert uv_pos < copy_pos, "uv must be installed before COPY . /app"

    def test_dockerfile_uv_installed_as_root_or_before_user_switch(self):
        """uv must be installed as root (USER root) to reach /usr/local/bin.
        If installed as leap-aide-init, it lands in ~leap-aide-init/.local/bin,
        which the leap-aide user at runtime can't see.
        """
        lines = DOCKERFILE.splitlines()
        uv_line_idx = next(i for i, l in enumerate(lines) if "uv/install.sh" in l)
        # Find the last USER directive before the uv install line
        user_before = None
        for i in range(uv_line_idx - 1, -1, -1):
            if lines[i].strip().startswith("USER "):
                user_before = lines[i].strip().split()[1]
                break
        assert user_before == "root", (
            f"uv install must run as USER root (found USER {user_before!r}). "
            "Installing as leap-aide-init puts uv in /home/leap-aide-init/.local/bin "
            "which is not accessible to the leap-aide runtime user."
        )


# ── docker-entrypoint.sh: uv comes from the image, never from the network ──────

class TestInitScriptUvPreinstalled:
    """uv comes from the image (build time) — the entrypoint never touches it.

    The entrypoint used to fall back to `curl ... | sh` when `command -v uv`
    failed. That branch was unreachable for this image, and a production
    entrypoint that fetches and executes remote code at container start is a
    supply-chain surface. The runtime install was later removed entirely: the
    hermes-agent installer builds $AGENT_DIR/venv at image-build time and the
    entrypoint only consumes it (fail-fast when missing) — so the entrypoint
    needs neither uv nor any network install at runtime.
    """

    def test_init_script_does_not_download_uv(self):
        """No runtime fetch of uv — the image bakes it into /usr/local/bin."""
        assert "uv/install.sh" not in INIT_SCRIPT, (
            "docker-entrypoint.sh must not download uv at runtime; the image "
            "installs it at build time to /usr/local/bin"
        )
        assert not re.search(r"curl[^\n]*uv", INIT_SCRIPT), (
            "docker-entrypoint.sh must not curl anything uv-related at runtime"
        )

    def test_init_script_has_no_runtime_install(self):
        """No venv creation or pip/uv installs at runtime — the agent venv
        built at image-build time is the single Python environment."""
        assert "uv venv" not in INIT_SCRIPT, (
            "docker-entrypoint.sh must not create venvs at runtime"
        )
        assert "uv pip" not in INIT_SCRIPT and "pip install" not in INIT_SCRIPT, (
            "docker-entrypoint.sh must not install packages at runtime"
        )

    def test_init_script_requires_agent_venv(self):
        """A missing agent venv must abort with a clear message, not fall back."""
        assert "venv/bin/python" in INIT_SCRIPT, (
            "docker-entrypoint.sh must locate the hermes-agent venv built at "
            "image-build time"
        )
        assert "error_exit" in INIT_SCRIPT and "venv missing" in INIT_SCRIPT, (
            "docker-entrypoint.sh must error_exit when the agent venv is absent"
        )

    def test_init_script_does_not_patch_path_for_local_bin(self):
        """No ~/.local/bin PATH hack — that only served the removed curl install."""
        assert "/home/leap-aide/.local/bin" not in INIT_SCRIPT, (
            "docker-entrypoint.sh must not add ~/.local/bin to PATH; uv is "
            "installed system-wide to /usr/local/bin"
        )


# ── docker-entrypoint.sh: workspace directory permissions ────────────────────────

class TestWorkspacePermissions:

    def test_workspace_uses_sudo_mkdir(self):
        """docker-entrypoint.sh must use 'sudo mkdir' for the workspace directory.

        Docker auto-creates bind-mount directories as root if they don't exist,
        leaving them unwritable by leap-aide. sudo mkdir + chown fixes this.
        """
        # Find the workspace section
        ws_section = INIT_SCRIPT[
            INIT_SCRIPT.find("HERMES_WEBUI_DEFAULT_WORKSPACE"):
            INIT_SCRIPT.find("HERMES_WEBUI_DEFAULT_WORKSPACE") + 800
        ]
        assert "sudo mkdir" in ws_section, (
            "docker-entrypoint.sh must use 'sudo mkdir -p' for the workspace directory "
            "to handle the case where Docker created the bind-mount dir as root (#357)"
        )

    def test_workspace_uses_sudo_chown(self):
        """docker-entrypoint.sh must recursively chown the workspace to the runtime uid.

        Ownership is repaired (best-effort) before the writability probe, so a
        wrong-owner read-write mount is fixed instead of being mistaken for a
        read-only mount (#42). Recursion also repairs root-owned subdirectories
        Docker may have auto-created; a genuinely :ro mount makes the chown
        fail harmlessly (#670). The target must be WANTED_UID:WANTED_GID, not
        leap-aide's image uid: in root mode (WANTED_UID=0) the server runs as
        root and leap-aide is intentionally never remapped.
        """
        assert 'sudo chown -R "${WANTED_UID}:${WANTED_GID}" "$HERMES_WEBUI_DEFAULT_WORKSPACE"' in INIT_SCRIPT, (
            "docker-entrypoint.sh must 'sudo chown -R WANTED_UID:WANTED_GID' the workspace "
            "so the app user can write to it, including nested directories (#357, #42)"
        )
        assert 'sudo chown -R leap-aide:leap-aide' not in INIT_SCRIPT, (
            "the workspace chown must not hard-code leap-aide's image uid — root mode "
            "runs as 0, and rootless podman would map 1024 to a host subuid"
        )

    def test_workspace_mkdir_before_chown(self):
        """sudo mkdir must come before sudo chown in docker-entrypoint.sh."""
        mkdir_pos = INIT_SCRIPT.find('sudo mkdir -p "$HERMES_WEBUI_DEFAULT_WORKSPACE"')
        chown_pos = INIT_SCRIPT.find('sudo chown -R "${WANTED_UID}:${WANTED_GID}" "$HERMES_WEBUI_DEFAULT_WORKSPACE"')
        assert mkdir_pos != -1, "sudo mkdir for workspace not found"
        assert chown_pos != -1, "sudo chown for workspace not found"
        assert mkdir_pos < chown_pos, "sudo mkdir must come before sudo chown"

    def test_workspace_error_exit_on_mkdir_failure(self):
        """sudo mkdir must call error_exit on failure."""
        assert 'sudo mkdir -p "$HERMES_WEBUI_DEFAULT_WORKSPACE" || error_exit' in INIT_SCRIPT, (
            "sudo mkdir for workspace must call error_exit on failure"
        )

    def test_workspace_chown_precedes_writability_probe(self):
        """chown must run before the writability probe; :ro mounts stay supported.

        Regression for #42: guarding the chown behind [ -w ] was a
        chicken-and-egg bug — a wrong-owner read-write mount is not writable by
        leap-aide, so the guard misread it as a deliberate :ro mount and skipped
        the chown that would have repaired it for the container's lifetime.
        """
        chown_pos = INIT_SCRIPT.find('sudo chown -R "${WANTED_UID}:${WANTED_GID}" "$HERMES_WEBUI_DEFAULT_WORKSPACE"')
        probe_pos = INIT_SCRIPT.find('it="$HERMES_WEBUI_DEFAULT_WORKSPACE/.testfile"')
        assert chown_pos != -1, "workspace chown not found"
        assert probe_pos != -1, "workspace writability probe not found"
        assert chown_pos < probe_pos, (
            "the workspace chown must run before the writability probe (#42)"
        )
        # The old [ -w ]-first guard must not come back.
        assert '[ -w "$HERMES_WEBUI_DEFAULT_WORKSPACE" ]' not in INIT_SCRIPT, (
            "the workspace chown must not be guarded by [ -w ] — that is the #42 bug"
        )
        # Read-only (:ro) mounts must still start cleanly (#670).
        assert "read-only workspace is supported" in INIT_SCRIPT, (
            "docker-entrypoint.sh must print a clear message when workspace is read-only (#670)"
        )
        # rw-but-unwritable states must be called out loudly with actionable text.
        assert "is a read-write mount but not writable by leap-aide" in INIT_SCRIPT, (
            "docker-entrypoint.sh must warn loudly when an rw workspace is not writable (#42)"
        )

    def test_init_script_syntax_valid(self):
        """docker-entrypoint.sh must pass bash -n syntax check."""
        import subprocess
        result = subprocess.run(
            ["bash", "-n", str(REPO / "backend" / "scripts" / "docker-entrypoint.sh")],
            capture_output=True, text=True
        )
        assert result.returncode == 0, (
            f"docker-entrypoint.sh failed bash -n syntax check:\n{result.stderr}"
        )
