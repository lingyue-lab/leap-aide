"""Behavioural tests for `ctl.sh start-docker` / `stop-docker`.

The wrapper never talks to a real container engine here: `podman`, `docker` and
`podman-compose` are replaced by shell stubs on PATH that record their argv.
That makes every decision (engine preference, docker fallback, rootless
keep-id override, postflight failure) deterministic and runnable on a host with
no container runtime at all.

Run: python3 backend/tests/test_ctl_docker_wrapper.py -v
"""
import os
import pathlib
import shutil
import subprocess
import tempfile
import time
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
CTL = REPO_ROOT / "scripts" / "ctl.sh"
COMPOSE = REPO_ROOT / "docker-compose.yml"

# Every stub appends its argv to $STUB_LOG, so tests can assert both "which
# engine ran" and "with which arguments". Podman-compose/docker additionally
# simulate `exec ... touch` by creating the probe file the postflight check
# stats on the host side.
_PODMAN_STUB = r"""#!/usr/bin/env bash
printf 'podman %s\n' "$*" >>"${STUB_LOG:?}"
case "${1:-}" in
  info)
    if [[ "${2:-}" == "--format" ]]; then printf '%s\n' "${STUB_ROOTLESS:-false}"; fi
    exit 0 ;;
  ps)      printf '%s\n' "${STUB_CID:-cid123}"; exit 0 ;;
  inspect) printf '%s\n' "${STUB_RUNNING:-true}"; exit 0 ;;
  exec)
    [[ "${STUB_EXEC_FAIL:-0}" == "1" ]] && exit 1
    mkdir -p "$HOME/.hermes_workspaces"; : >"$HOME/.hermes_workspaces/.uid_probe"; exit 0 ;;
  logs)    printf 'stub container log: boom\n'; exit 0 ;;
  compose)
    if [[ "${2:-}" == "version" ]]; then exit "${STUB_PODMAN_COMPOSE_OK:-1}"; fi
    exit 0 ;;
esac
exit 0
"""

_DOCKER_STUB = r"""#!/usr/bin/env bash
printf 'docker %s\n' "$*" >>"${STUB_LOG:?}"
case "${1:-}" in
  info)
    if [[ "${2:-}" == "--format" ]]; then printf '%s\n' "${STUB_DOCKER_SECOPT:-name=seccomp}"; fi
    exit 0 ;;
  ps)      printf '%s\n' "${STUB_CID:-cid123}"; exit 0 ;;
  inspect) printf '%s\n' "${STUB_RUNNING:-true}"; exit 0 ;;
  exec)
    [[ "${STUB_EXEC_FAIL:-0}" == "1" ]] && exit 1
    mkdir -p "$HOME/.hermes_workspaces"; : >"$HOME/.hermes_workspaces/.uid_probe"; exit 0 ;;
  logs)    printf 'stub container log: boom\n'; exit 0 ;;
  compose)
    if [[ "${2:-}" == "version" ]]; then exit "${STUB_DOCKER_COMPOSE_OK:-0}"; fi
    exit 0 ;;
esac
exit 0
"""

_PODMAN_COMPOSE_STUB = r"""#!/usr/bin/env bash
printf 'podman-compose %s\n' "$*" >>"${STUB_LOG:?}"
printf 'ENV WANTED_UID=%s WANTED_GID=%s\n' "${WANTED_UID:-}" "${WANTED_GID:-}" >>"${STUB_LOG:?}"
for a in "$@"; do
  if [[ -f "$a" && "$a" == *leap-aide-userns* ]]; then
    cp "$a" "${STUB_OVERRIDE_CAPTURE:?}"
  fi
  if [[ "$a" == "exec" ]]; then
    mkdir -p "$HOME/.hermes_workspaces"
    : >"$HOME/.hermes_workspaces/.uid_probe"
  fi
done
exit 0
"""


class CtlDockerWrapperTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)

        (self.root / "scripts").mkdir()
        shutil.copy2(CTL, self.root / "scripts" / "ctl.sh")
        shutil.copy2(COMPOSE, self.root / "docker-compose.yml")

        self.home = self.root / "home"
        (self.home / ".hermes_workspaces").mkdir(parents=True)

        self.bin = self.root / "bin"
        self.bin.mkdir()

        self.log = self.root / "stub.log"
        self.log.touch()

        self.env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.home),
            "STUB_LOG": str(self.log),
            "STUB_OVERRIDE_CAPTURE": str(self.root / "override-captured.yml"),
            "TMPDIR": str(self.root),
            # The host running these tests may itself be inside a LeapAide
            # container (an image built with `touch /.within_container`), so
            # point the guard at a path that does not exist by default.
            "WITHIN_CONTAINER_MARKER": str(self.root / "not-a-container"),
            # start-docker waits for /health on the host (issue #3); the stub
            # engines below never serve one, so skip the wait in these tests.
            "CTL_HEALTH_WAIT_TIMEOUT": "0",
        }

    def tearDown(self):
        self._tmp.cleanup()

    # ── helpers ──────────────────────────────────────────────────────────────

    def stub(self, name, body):
        path = self.bin / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    def run_ctl(self, *args, **overrides):
        env = dict(self.env)
        env.update(overrides)
        return subprocess.run(
            ["bash", str(self.root / "scripts" / "ctl.sh"), *args],
            capture_output=True, text=True, env=env, timeout=120, cwd="/",
        )

    def logged(self):
        return self.log.read_text(encoding="utf-8")

    # ── engine selection ─────────────────────────────────────────────────────

    def test_rootful_podman_prefers_podman_compose_with_host_uid(self):
        self.stub("podman", _PODMAN_STUB)
        self.stub("podman-compose", _PODMAN_COMPOSE_STUB)

        result = self.run_ctl("start-docker", "example/img:1")

        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.logged()
        self.assertIn("podman-compose", log)
        self.assertIn("up -d", log)
        self.assertIn(f"WANTED_UID={os.getuid()}", log)
        self.assertIn(f"WANTED_GID={os.getgid()}", log)

    def test_rootless_podman_adds_keepid_override(self):
        self.stub("podman", _PODMAN_STUB)
        self.stub("podman-compose", _PODMAN_COMPOSE_STUB)

        result = self.run_ctl("start-docker", "example/img:1", STUB_ROOTLESS="true")

        self.assertEqual(result.returncode, 0, result.stderr)
        captured = pathlib.Path(self.env["STUB_OVERRIDE_CAPTURE"]).read_text(encoding="utf-8")
        self.assertIn("userns_mode: keep-id", captured)
        up_lines = [line for line in self.logged().splitlines() if "up -d" in line]
        self.assertEqual(len(up_lines), 1, self.logged())
        self.assertEqual(up_lines[0].count(" -f "), 2, up_lines[0])

    def test_rootless_without_podman_compose_refuses_to_start(self):
        self.stub("podman", _PODMAN_STUB)

        result = self.run_ctl("start-docker", "example/img:1",
                              STUB_ROOTLESS="true", STUB_PODMAN_COMPOSE_OK="0")

        self.assertEqual(result.returncode, 1)
        self.assertIn("keep-id", result.stderr)
        self.assertNotIn("up -d", self.logged())

    def test_docker_engine_is_used_when_podman_is_absent(self):
        self.stub("docker", _DOCKER_STUB)

        result = self.run_ctl("start-docker", "example/img:1")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("docker compose", self.logged())
        self.assertIn("up -d", self.logged())

    def test_falls_back_to_docker_when_podman_has_no_compose(self):
        self.stub("podman", _PODMAN_STUB)   # `podman compose version` exits 1
        self.stub("docker", _DOCKER_STUB)

        result = self.run_ctl("start-docker", "example/img:1")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("falling back to docker compose", result.stderr)
        self.assertIn("docker compose", self.logged())
        self.assertIn("up -d", self.logged())

    def test_engine_flag_skips_detection(self):
        self.stub("podman", _PODMAN_STUB)
        self.stub("podman-compose", _PODMAN_COMPOSE_STUB)
        self.stub("docker", _DOCKER_STUB)

        result = self.run_ctl("start-docker", "example/img:1", "--engine", "docker")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("falling back", result.stderr)
        self.assertIn("docker compose", self.logged())
        self.assertNotIn("podman-compose", self.logged())

    # ── safety rails ─────────────────────────────────────────────────────────

    def test_refuses_to_run_inside_the_container(self):
        marker = self.root / "within-container"
        marker.touch()
        self.stub("podman", _PODMAN_STUB)

        result = self.run_ctl("start-docker", "example/img:1",
                              WITHIN_CONTAINER_MARKER=str(marker))

        self.assertEqual(result.returncode, 1)
        self.assertIn("host-side command", result.stderr)
        self.assertEqual(self.logged(), "")

    def test_print_does_not_execute_anything(self):
        self.stub("podman", _PODMAN_STUB)
        self.stub("podman-compose", _PODMAN_COMPOSE_STUB)

        result = self.run_ctl("start-docker", "example/img:1", "--print")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WANTED_UID=", result.stdout)
        self.assertIn("up -d", result.stdout)
        self.assertNotIn("up -d", self.logged(), "print mode must not start anything")

    def test_ownership_mismatch_exits_nonzero(self):
        self.stub("podman", _PODMAN_STUB)
        self.stub("podman-compose", _PODMAN_COMPOSE_STUB)

        result = self.run_ctl("start-docker", "example/img:1",
                              WANTED_UID="4242", WANTED_GID="4242")

        self.assertEqual(result.returncode, 1)
        self.assertIn("ownership mismatch", result.stderr)
        self.assertIn("stop-docker", result.stderr)

    def test_container_exit_is_reported_early_with_logs(self):
        self.stub("podman", _PODMAN_STUB)
        self.stub("podman-compose", _PODMAN_COMPOSE_STUB)

        started = time.monotonic()
        result = self.run_ctl("start-docker", "example/img:1",
                              STUB_EXEC_FAIL="1", STUB_RUNNING="false")
        elapsed = time.monotonic() - started

        self.assertEqual(result.returncode, 1)
        self.assertIn("container exited during startup", result.stderr)
        self.assertIn("stub container log", result.stderr)
        self.assertLess(elapsed, 10, f"took {elapsed:.1f}s; must fail fast instead of retrying")

    def test_extra_compose_file_is_passed_through(self):
        self.stub("podman", _PODMAN_STUB)
        self.stub("podman-compose", _PODMAN_COMPOSE_STUB)

        result = self.run_ctl("start-docker", "example/img:1",
                              "-f", "docker-compose.dev.yml")

        self.assertEqual(result.returncode, 0, result.stderr)
        up_line = [line for line in self.logged().splitlines() if "up -d" in line][0]
        self.assertIn("docker-compose.dev.yml", up_line)
        self.assertEqual(up_line.count(" -f "), 2, up_line)

    # ── stop ─────────────────────────────────────────────────────────────────

    def test_stop_docker_runs_compose_down(self):
        self.stub("podman", _PODMAN_STUB)
        self.stub("podman-compose", _PODMAN_COMPOSE_STUB)

        result = self.run_ctl("stop-docker")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("down --remove-orphans", self.logged())


if __name__ == "__main__":
    unittest.main()
