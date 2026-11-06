"""Hermes Web UI - Global shared browser desktop lifecycle manager.

One Xvfb + fcitx5 + openbox + x11vnc + Chrome stack for the whole
instance (issue #37): a "public living room" that every session's
browser tools share.  Chrome is spawned by the in-process receptionist
over ``--remote-debugging-pipe`` (no TCP CDP port; see
``app.api.browser_receptionist``), so the only network CDP surface is
the receptionist on 127.0.0.1.

State file (single source of truth for wrappers / browser_cdp patch):
    ``$HERMES_WEBUI_STATE_DIR/browser_desktop.json``

Lifecycle: started at boot (``ensure_global_desktop()``), stopped on
server shutdown.  A ``DesktopSupervisor`` thread watches component
health and restarts the full stack or just Chrome as needed.

chrome-wrapper.sh / agent-browser-wrapper.sh must be installed ONCE
before the global desktop is used: ``bash backend/scripts/install-wrappers.sh``
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app.api.config import STATE_DIR

logger = logging.getLogger(__name__)

# -- Configuration ---------------------------------------------------------------

DISPLAY_NUM = int(os.environ.get("HERMES_GLOBAL_DISPLAY", "99"))
VNC_PORT = int(os.environ.get("HERMES_GLOBAL_VNC_PORT", "5900"))
RECEPTIONIST_PORT = int(os.environ.get("HERMES_RECEPTIONIST_PORT", "6900"))
HEALTH_INTERVAL = float(os.environ.get("HERMES_GLOBAL_HEALTH_INTERVAL", "15"))
DESKTOP_ENABLED = os.environ.get("HERMES_GLOBAL_DESKTOP", "1") not in ("0", "off", "no")

MAX_XTERMS = int(os.environ.get("HERMES_OBSERVE_MAX_XTERMS", "6"))

BROWSER_DESKTOP_JSON = STATE_DIR / "browser_desktop.json"
LEGACY_DISPLAY_DIR = STATE_DIR / "sessions_display"

# -- Required binaries -----------------------------------------------------------

_REQUIRED_BINARIES = {
    "Xvfb": "/usr/bin/Xvfb",
    "x11vnc": shutil.which("x11vnc") or "/usr/bin/x11vnc",
    "openbox": shutil.which("openbox") or "/usr/bin/openbox",
    "wmctrl": shutil.which("wmctrl") or "/usr/bin/wmctrl",
}

_MISSING_BINARIES = [
    name for name, path in _REQUIRED_BINARIES.items()
    if not os.path.isfile(path)
]
if _MISSING_BINARIES:
    logger.warning(
        "Browser desktop requires: %s. Install with: apt install x11vnc openbox wmctrl",
        ", ".join(_MISSING_BINARIES),
    )


def sync_gateway_session_cwd() -> int:
    """Sync the cwd of gateway/CLI sessions to match current terminal.cwd.

    Gateway sessions stored in state.db may have a stale ``cwd`` if the
    terminal working directory was changed in config.yaml after the
    session was created.  This runs once at startup to bring them in line.
    """
    try:
        from app.api.models import (
            _resolve_gateway_workspace, _get_session_db, MESSAGING_SOURCE_TAGS,
        )
        current_cwd = _resolve_gateway_workspace()
        db = _get_session_db()
        if db is None:
            return 0
        updated = 0
        # Only sync messaging platform sessions (weixin, telegram, etc.)
        # where cwd is always controlled by the gateway config.
        # CLI sessions can have arbitrary cwd - don't touch them.
        placeholders = ", ".join("?" for _ in MESSAGING_SOURCE_TAGS)
        cur = db._conn.execute(
            f"SELECT id, cwd FROM sessions WHERE source IN ({placeholders})",
            tuple(sorted(MESSAGING_SOURCE_TAGS)),
        )
        for row in cur.fetchall():
            sid = row[0] if isinstance(row, tuple) else row['id']
            old_cwd = row[1] if isinstance(row, tuple) else row['cwd']
            if old_cwd and old_cwd != current_cwd:
                db._conn.execute(
                    "UPDATE sessions SET cwd = ? WHERE id = ?",
                    (current_cwd, sid),
                )
                updated += 1
                logger.info(
                    "sync_gateway_cwd: %s cwd %s -> %s", sid, old_cwd, current_cwd,
                )
        if updated:
            db._conn.commit()
            logger.info("Synced cwd for %d gateway session(s)", updated)
        return updated
    except Exception:
        logger.debug("sync_gateway_session_cwd failed", exc_info=True)
        return 0


def cleanup_legacy_configs() -> None:
    """One-time removal of the per-session display dir (no migration)."""
    try:
        if LEGACY_DISPLAY_DIR.exists():
            shutil.rmtree(LEGACY_DISPLAY_DIR, ignore_errors=True)
            logger.info("Removed legacy sessions_display dir")
    except OSError:
        pass


# -- Module-level shared state -----------------------------------------------

_LOCK = threading.RLock()


def _lock_owner_alive(lock_file: Path) -> bool:
    """Check if the process that owns an X11 lock file is still running."""
    try:
        content = lock_file.read_text().strip()
        pid = int(content.split("\n")[0])
    except (ValueError, OSError):
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _wait_for_socket(path: Path, timeout: float = 5.0) -> bool:
    """Poll for a filesystem path to appear."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return True
        time.sleep(0.1)
    return False


def _wait_for_tcp_port(host: str, port: int, timeout: float = 10.0) -> bool:
    """Poll for a TCP port to become available."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            sock = socket.create_connection((host, port), timeout=0.5)
            sock.close()
            return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.2)
    return False


# -- fcitx5 profile helpers ----------------------------------------------------

# Only these sections list input methods. The group header carries its own
# "Name=" line ("Name=Default") which names the GROUP, not an IM.
_FCITX5_ITEM_SECTION_RE = re.compile(r"^\[Groups/\d+/Items/\d+\]$")


def _fcitx5_profile_input_methods(text: str) -> list[str]:
    """Return the input-method names listed in an fcitx5 profile.

    Reads the ``[Groups/N/Items/M]`` sections only.  The group header's
    ``Name=`` line names the group, not an input method -- reading it as
    one made fcitx5's pristine default profile (which carries
    ``Name=Default``) look user-customized, so the pinyin seeding was
    skipped on every start where the profile file already existed.
    """
    names: list[str] = []
    in_item = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_item = bool(_FCITX5_ITEM_SECTION_RE.match(line))
            continue
        if in_item and line.startswith("Name="):
            names.append(line[len("Name="):].strip())
    return names


# -- GlobalDesktop -----------------------------------------------------------


@dataclass
class XtermEntry:
    """One tool-call xterm window on the global display."""

    proc: subprocess.Popen
    session_id: str
    started_at: float


class GlobalDesktop:
    """Process-wide Xvfb + openbox + x11vnc + receptionist manager."""

    def __init__(self) -> None:
        self.display_num: int | None = None
        self.vnc_port: int | None = None
        self.receptionist_port: int | None = None
        self.xvfb_proc: subprocess.Popen | None = None
        self.fcitx_proc: subprocess.Popen | None = None
        self.openbox_proc: subprocess.Popen | None = None
        self.vnc_proc: subprocess.Popen | None = None
        self.xterm_procs: dict[str, XtermEntry] = {}
        self._dbus_pid: int = 0
        # D-Bus session bus env (DBUS_SESSION_BUS_ADDRESS/PID) created in
        # start(). Chrome's fcitx GTK IM module needs the address to reach
        # the fcitx5 daemon — without it, Shift+Space toggles nothing.
        self._fcitx_env: dict[str, str] = {}
        self._supervisor: "DesktopSupervisor | None" = None
        self._started = False

    # -- Public API -----------------------------------------------------------

    def start(self) -> dict:
        """Start the global desktop stack. Idempotent.

        Raises:
            RuntimeError: If startup fails.
        """
        with _LOCK:
            if self._started and self.is_running():
                return self.info_dict()

            logger.info(
                "GlobalDesktop.start display=:%s vnc=%s receptionist=%s",
                DISPLAY_NUM, VNC_PORT, RECEPTIONIST_PORT,
            )
            self.display_num = DISPLAY_NUM
            self.vnc_port = VNC_PORT
            self.receptionist_port = RECEPTIONIST_PORT

            try:
                # 1. Start Xvfb
                self.xvfb_proc = subprocess.Popen(
                    ["Xvfb", f":{DISPLAY_NUM}", "-screen", "0",
                     "1920x1080x24", "-ac"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                socket_path = Path(f"/tmp/.X11-unix/X{DISPLAY_NUM}")
                if not _wait_for_socket(socket_path, timeout=5.0):
                    raise RuntimeError(
                        f"Xvfb startup failed (display :{DISPLAY_NUM}): socket not found"
                    )

                # Fcitx5 input method environment
                fcitx_env = {
                    **os.environ,
                    "DISPLAY": f":{DISPLAY_NUM}",
                    "GTK_IM_MODULE": "fcitx",
                    "QT_IM_MODULE": "fcitx",
                    "XMODIFIERS": "@im=fcitx",
                    "INPUT_METHOD": "fcitx",
                    "GLFW_IM_MODULE": "ibus",
                }

                # 2. Start D-Bus session bus (shared by fcitx5 and Chrome)
                dbus_out = subprocess.check_output(
                    ["dbus-launch", "--sh-syntax"],
                    env=fcitx_env, stderr=subprocess.DEVNULL,
                ).decode()
                for line in dbus_out.splitlines():
                    if line.startswith("export "):
                        line = line[7:]
                    if "=" in line:
                        k, _, v = line.partition("=")
                        k = k.strip()
                        v = v.strip().rstrip(";").strip("'\"")
                        if k in ("DBUS_SESSION_BUS_ADDRESS", "DBUS_SESSION_BUS_PID"):
                            fcitx_env[k] = v
                self._dbus_pid = int(fcitx_env.get("DBUS_SESSION_BUS_PID", "0"))
                # Persist the bus env so Chrome launches (and restarts) can
                # hand DBUS_SESSION_BUS_ADDRESS to Chrome — os.environ of the
                # server process never sees the dbus-launch bus.
                self._fcitx_env = {
                    k: fcitx_env[k]
                    for k in ("DBUS_SESSION_BUS_ADDRESS", "DBUS_SESSION_BUS_PID")
                    if k in fcitx_env
                }

                # 3. Configure and start fcitx5 input method daemon
                self._ensure_fcitx5_profile()
                self.fcitx_proc = subprocess.Popen(
                    ["fcitx5", "-d"],
                    env=fcitx_env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(2.0)  # let fcitx5 fully initialize

                # The trigger key can only be set once fcitx5 owns
                # org.fcitx.Fcitx5 on the session bus; a fixed sleep raced
                # the daemon on slow nodes and left the trigger key at
                # fcitx5's default (Control+space) — Shift+Space then
                # toggled nothing. Wait for the name, then set the key.
                self._wait_for_fcitx5_dbus(fcitx_env, timeout=15.0)

                # Set trigger key via D-Bus (like the GUI config tool does)
                self._configure_fcitx5_trigger_key(fcitx_env)

                # 4. Start openbox window manager
                self.openbox_proc = subprocess.Popen(
                    ["openbox"],
                    env=fcitx_env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                time.sleep(0.5)  # let openbox initialize

                # 5. Start x11vnc
                self.vnc_proc = subprocess.Popen(
                    [
                        "x11vnc",
                        "-display", f":{DISPLAY_NUM}",
                        "-rfbport", str(VNC_PORT),
                        "-forever",
                        "-shared",
                        "-nopw",
                        "-listen", "127.0.0.1",
                        "-quiet",
                    ],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                if not _wait_for_tcp_port("127.0.0.1", VNC_PORT, timeout=10.0):
                    raise RuntimeError(
                        f"x11vnc startup failed (port {VNC_PORT}): port not ready"
                    )

                # 6. Start the receptionist thread (spawns Chrome via pipe)
                self._start_receptionist()

                # 7. Write state JSON once the receptionist is healthy.
                #    start_in_thread() returns as soon as the loop thread is
                #    spawned; the aiohttp site binds a moment later, so a
                #    single immediate check loses that race and would leave
                #    the state file missing — pinning every client to the
                #    headless fallback until a full restart.
                if self._await_receptionist_healthy(timeout=10.0):
                    self._write_config()
                else:
                    logger.warning(
                        "GlobalDesktop: receptionist not healthy after start; "
                        "browser clients will fall back to local headless"
                    )

                # 8. Start the supervisor
                self._supervisor = DesktopSupervisor(self)
                self._supervisor.start()
                self._started = True

                logger.info(
                    "GlobalDesktop ready display=:%s vnc=%s receptionist=%s",
                    DISPLAY_NUM, VNC_PORT, RECEPTIONIST_PORT,
                )
                return self.info_dict()

            except Exception:
                self.stop(remove_json=True)
                raise

    def stop(self, remove_json: bool = True) -> None:
        """Stop all processes and optionally remove state JSON."""
        # Stop the supervisor BEFORE taking _LOCK: its tick takes _LOCK
        # for full-stack restarts, so joining it under the lock deadlocks.
        sup, self._supervisor = self._supervisor, None
        if sup:
            sup.stop()
        with _LOCK:
            logger.info("GlobalDesktop.stop")
            self._started = False

            # Kill xterm procs
            for entry in list(self.xterm_procs.values()):
                try:
                    entry.proc.terminate()
                except OSError:
                    pass
            self.xterm_procs.clear()

            # Stop the receptionist (owns Chrome)
            self._stop_receptionist()

            # Kill processes: x11vnc first, then openbox, then fcitx5, then Xvfb
            for proc, name in [
                (self.vnc_proc, "x11vnc"),
                (self.openbox_proc, "openbox"),
                (self.fcitx_proc, "fcitx5"),
                (self.xvfb_proc, "Xvfb"),
            ]:
                if proc and proc.poll() is None:
                    try:
                        proc.terminate()
                        try:
                            proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait(timeout=2)
                    except OSError:
                        pass

            # Kill D-Bus session daemon
            if self._dbus_pid:
                try:
                    os.kill(self._dbus_pid, signal.SIGTERM)
                except OSError:
                    pass
                self._dbus_pid = 0

            self.xvfb_proc = None
            self.openbox_proc = None
            self.vnc_proc = None
            self.display_num = None
            self.vnc_port = None
            self.receptionist_port = None

            if remove_json:
                self._remove_config()

    def is_running(self) -> bool:
        """Check if Xvfb + VNC processes are alive."""
        if self.xvfb_proc and self.xvfb_proc.poll() is not None:
            return False
        if self.vnc_proc and self.vnc_proc.poll() is not None:
            return False
        return self.display_num is not None

    def info_dict(self) -> dict:
        """Return display info as a dict for API responses."""
        healthy = self.is_running() and self._receptionist_healthy()
        return {
            "display": self.display_num,
            "vnc_port": self.vnc_port,
            "receptionist_port": self.receptionist_port,
            "receptionist_url": (
                f"http://127.0.0.1:{self.receptionist_port}"
                if self.receptionist_port else None
            ),
            "status": "active" if healthy else ("degraded" if self.is_running() else "error"),
        }

    # -- Receptionist integration ---------------------------------------------

    def _receptionist(self):
        from app.api import browser_receptionist

        return browser_receptionist.get_receptionist()

    def _receptionist_healthy(self) -> bool:
        r = self._receptionist()
        return bool(r and r.is_healthy())

    def _await_receptionist_healthy(self, timeout: float) -> bool:
        """Poll receptionist health until healthy or timeout (any thread)."""
        deadline = time.monotonic() + timeout
        while True:
            if self._receptionist_healthy():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)

    def _start_receptionist(self) -> None:
        from app.api import browser_receptionist

        chrome_bin = self._find_chrome_binary()
        profile_dir = STATE_DIR / "chrome_profiles" / "global"
        chrome_env = {
            **os.environ,
            "DISPLAY": f":{DISPLAY_NUM}",
            "GTK_IM_MODULE": "fcitx",
            "QT_IM_MODULE": "fcitx",
            "XMODIFIERS": "@im=fcitx",
            **self._fcitx_env,
        }
        chrome_env.pop("XDG_CONFIG_HOME", None)
        chrome_env["NO_PROXY"] = (
            (_np := os.environ.get("NO_PROXY", ""))
            and f"{_np},127.0.0.1,localhost"
            or "127.0.0.1,localhost"
        )
        extra_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=TranslateUI",
            "--disable-blink-features=AutomationControlled",
            "--window-size=960,1080",
            "--window-position=0,0",
            "--disable-backgrounding-occluded-windows",
        ]
        r = browser_receptionist.get_receptionist()
        if r is None:
            r = browser_receptionist.Receptionist(port=RECEPTIONIST_PORT)
            browser_receptionist.set_receptionist(r)
        r.start_in_thread(
            chrome_bin=chrome_bin,
            profile_dir=profile_dir,
            display=f":{DISPLAY_NUM}",
            extra_args=extra_args,
            env=chrome_env,
        )

    def _stop_receptionist(self) -> None:
        from app.api import browser_receptionist

        r = browser_receptionist.get_receptionist()
        if r:
            try:
                r.stop()
            except Exception:
                logger.debug("receptionist stop failed", exc_info=True)
            browser_receptionist.set_receptionist(None)

    def restart_receptionist(self) -> bool:
        """Restart the receptionist thread + Chrome (X stack untouched)."""
        with _LOCK:
            logger.warning("GlobalDesktop: restarting receptionist")
            self._stop_receptionist()
            try:
                self._start_receptionist()
                healthy = self._await_receptionist_healthy(timeout=10.0)
                if healthy:
                    self._write_config()
                return healthy
            except Exception:
                logger.exception("GlobalDesktop: receptionist restart failed")
                return False

    def cdp_available(self) -> bool:
        """True when the receptionist endpoint is advertised as usable.

        Cheap file read — safe to call on every agent turn.
        """
        info = self.get_info()
        return bool(
            info and info.get("status") == "active"
            and info.get("receptionist_url")
        )

    def close_session_tabs(self, session_id: str) -> int:
        """Turn-end cleanup: drop leaked connections for this session."""
        r = self._receptionist()
        if not r:
            return 0
        import asyncio

        loop = r._loop
        if not loop or not loop.is_running():
            return 0
        try:
            fut = asyncio.run_coroutine_threadsafe(
                r.close_idle_connections(session_id, idle_seconds=30.0), loop
            )
            return int(fut.result(timeout=5) or 0)
        except Exception:
            logger.debug("close_session_tabs failed", exc_info=True)
            return 0

    # -- Static helpers -----------------------------------------------------

    @staticmethod
    def get_info() -> dict | None:
        """Read the global desktop state from persisted JSON (no lock)."""
        if not BROWSER_DESKTOP_JSON.exists():
            return None
        try:
            return json.loads(BROWSER_DESKTOP_JSON.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    @staticmethod
    def _find_chrome_binary() -> str:
        """Find the Chrome/Chromium binary from the Playwright cache."""
        import glob
        candidates = glob.glob("/root/.cache/ms-playwright/**/chrome.real", recursive=True)
        if candidates:
            return candidates[0]
        candidates = glob.glob("/root/.cache/ms-playwright/**/chrome", recursive=True)
        if candidates:
            return candidates[0]
        raise FileNotFoundError("No Chrome binary found in Playwright cache")

    # -- Window focus -----------------------------------------------------

    def focus_chrome(self) -> None:
        """Bring the Chrome window to the foreground on Xvfb."""
        if not self.is_running() or not self.display_num:
            return
        try:
            subprocess.run(
                ["wmctrl", "-a", "Chrome"],
                env={**os.environ, "DISPLAY": f":{self.display_num}"},
                timeout=2,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    def focus_xterm(self, window_title: str) -> None:
        """Bring an xterm window to the foreground on Xvfb."""
        if not self.is_running() or not self.display_num:
            return
        try:
            subprocess.run(
                ["wmctrl", "-a", window_title],
                env={**os.environ, "DISPLAY": f":{self.display_num}"},
                timeout=2,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    # -- Internal -----------------------------------------------------------

    def _write_config(self) -> None:
        """Write the global desktop state JSON consumed by wrappers/patch."""
        config = {
            "display": self.display_num,
            "vnc_port": self.vnc_port,
            "receptionist_port": self.receptionist_port,
            "receptionist_url": f"http://127.0.0.1:{self.receptionist_port}",
            "status": "active" if self._receptionist_healthy() else "degraded",
            "started_at": time.time(),
            "last_activity": time.time(),
        }
        BROWSER_DESKTOP_JSON.parent.mkdir(parents=True, exist_ok=True)
        BROWSER_DESKTOP_JSON.write_text(json.dumps(config))

    def _refresh_config_status(self) -> None:
        """Update status/last_activity in the state JSON (supervisor)."""
        info = self.get_info()
        if not info:
            # State file missing (boot raced the listener bind, or an old
            # build removed it) — recreate it now that the supervisor sees
            # a healthy receptionist, instead of leaving clients on the
            # headless fallback until the next full restart.
            if self._receptionist_healthy():
                self._write_config()
            return
        info["status"] = "active" if self._receptionist_healthy() else "degraded"
        info["last_activity"] = time.time()
        try:
            BROWSER_DESKTOP_JSON.write_text(json.dumps(info))
        except OSError:
            pass

    def _remove_config(self) -> None:
        try:
            BROWSER_DESKTOP_JSON.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _ensure_fcitx5_profile() -> None:
        """Ensure the fcitx5 profile includes the Pinyin input method.

        fcitx5's first-run default profile is keyboard-only; with no IM in
        the group, the configured trigger key (Shift+Space) toggles nothing
        observable. Seeds a keyboard-us + pinyin profile when the file is
        missing, or when the group only holds keyboard-* entries (the
        pristine default). Any profile that already lists a non-keyboard IM
        is user-owned and left untouched. Must run BEFORE fcitx5 starts —
        the daemon saves its in-memory profile on exit and would clobber
        a file written while it runs.

        The pristine default carries a group-level ``Name=Default`` line;
        it is NOT an input method.  Treating it as one made the seeding
        bail out on every start where the profile file already existed —
        e.g. the k8s deployment, which runs the service as ``leap-aide``
        with a persistent ``$HOME``, where fcitx5's own first-run profile
        (keyboard-us only) was written once and then never upgraded.
        """
        profile = Path.home() / ".config" / "fcitx5" / "profile"
        try:
            text = profile.read_text(encoding="utf-8") if profile.is_file() else ""
        except OSError:
            text = ""
        if "Name=pinyin" in text:
            return
        custom = [
            name for name in _fcitx5_profile_input_methods(text)
            if not name.startswith("keyboard-")
        ]
        if custom:
            return  # user added a custom IM — do not touch
        try:
            profile.parent.mkdir(parents=True, exist_ok=True)
            profile.write_text(
                "[Groups/0]\n"
                "Name=Default\n"
                "Default Layout=us\n"
                "DefaultIM=pinyin\n"
                "\n"
                "[Groups/0/Items/0]\n"
                "Name=keyboard-us\n"
                "Layout=\n"
                "\n"
                "[Groups/0/Items/1]\n"
                "Name=pinyin\n"
                "Layout=\n"
                "\n"
                "[GroupOrder]\n"
                "0=Default\n",
                encoding="utf-8",
            )
            logger.info("fcitx5 profile seeded with pinyin (%s)", profile)
        except OSError as exc:
            logger.warning("fcitx5 profile seed failed: %s", exc)

    @staticmethod
    def _wait_for_fcitx5_dbus(fcitx_env: dict, timeout: float = 15.0) -> bool:
        """Block until fcitx5 owns org.fcitx.Fcitx5 on the session bus.

        The trigger key can only be set once the daemon published its
        D-Bus controller.  Polling for the name is race-free; a fixed
        sleep was not (on a slow or CPU-throttled node the SetConfig
        call landed before the daemon was up and the trigger key stayed
        at fcitx5's default, Control+space).
        """
        script = (
            "import dbus, dbus.mainloop.glib\n"
            "dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)\n"
            "bus = dbus.SessionBus()\n"
            "try:\n"
            "    bus.get_name_owner('org.fcitx.Fcitx5')\n"
            "    print('ready')\n"
            "except Exception:\n"
            "    print('not-ready')\n"
        )
        deadline = time.monotonic() + timeout
        while True:
            try:
                # Use system python3 (venv lacks dbus-python)
                result = subprocess.run(
                    ["/usr/bin/python3", "-c", script],
                    env=fcitx_env, timeout=5,
                    capture_output=True,
                )
                if b"ready" in result.stdout:
                    return True
            except Exception as exc:  # noqa: BLE001 - best-effort probe
                logger.debug("fcitx5 D-Bus probe failed: %s", exc)
            if time.monotonic() >= deadline:
                break
            time.sleep(0.5)
        logger.warning(
            "fcitx5 D-Bus controller not ready after %.1fs — the trigger "
            "key stays at fcitx5's default (Control+space)",
            timeout,
        )
        return False

    @staticmethod
    def _configure_fcitx5_trigger_key(fcitx_env: dict) -> None:
        """Set fcitx5 trigger key via D-Bus (same as GUI config tool).

        Uses fcitx://config/global URI and SetConfig API to set
        TriggerKeys to Shift+space.  Retried a few times: the daemon may
        still be registering its bus name when the first call lands.
        """
        script = (
            "import dbus, dbus.mainloop.glib\n"
            "dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)\n"
            "bus = dbus.SessionBus()\n"
            "proxy = bus.get_object('org.fcitx.Fcitx5', '/controller')\n"
            "c = dbus.Interface(proxy, 'org.fcitx.Fcitx.Controller1')\n"
            "tk = dbus.Dictionary(signature='sv')\n"
            "tk['0'] = dbus.String('Shift+space', variant_level=1)\n"
            "hk = dbus.Dictionary(signature='sv')\n"
            "hk['TriggerKeys'] = tk\n"
            "cfg = dbus.Dictionary(signature='sv')\n"
            "cfg['Hotkey'] = hk\n"
            "c.SetConfig('fcitx://config/global', cfg)\n"
            "c.Save()\n"
        )
        last_error = ""
        for attempt in range(3):
            try:
                # Use system python3 (venv lacks dbus-python)
                result = subprocess.run(
                    ["/usr/bin/python3", "-c", script],
                    env=fcitx_env, timeout=5,
                    capture_output=True,
                )
                if result.returncode == 0:
                    logger.info("fcitx5 trigger key set to Shift+Space via D-Bus")
                    return
                last_error = result.stderr.decode().strip()
            except Exception as e:  # noqa: BLE001 - best-effort configuration
                last_error = str(e)
            if attempt < 2:
                time.sleep(1.0)
        logger.warning(
            "fcitx5 SetConfig failed after 3 attempts: %s — the trigger "
            "key stays at fcitx5's default (Control+space)",
            last_error,
        )


# -- Module-level singleton + supervisor -----------------------------------------


class DesktopSupervisor:
    """Daemon thread: periodic health check + component-wise restart."""

    def __init__(self, desktop: GlobalDesktop) -> None:
        self._desktop = desktop
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._fail_streak = 0

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="desktop-supervisor", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        self._thread = None

    def _run(self) -> None:
        while not self._stop_evt.wait(self._interval()):
            try:
                self._tick()
                self._fail_streak = 0
            except Exception:
                logger.exception("desktop-supervisor: tick failed")

    def _interval(self) -> float:
        # Back off on repeated full-stack failures to avoid thrash.
        return min(HEALTH_INTERVAL * (2 ** min(self._fail_streak, 3)), 60.0)

    def _tick(self) -> None:
        d = self._desktop
        if not d.is_running():
            self._fail_streak += 1
            logger.warning(
                "desktop-supervisor: stack dead; full restart (streak=%d)",
                self._fail_streak,
            )
            d.stop(remove_json=False)
            d.start()
            return
        # X stack alive — make sure the receptionist thread + Chrome are too.
        from app.api import browser_receptionist

        r = browser_receptionist.get_receptionist()
        if r is None or not (r._thread and r._thread.is_alive()) or not r.is_healthy():
            logger.warning("desktop-supervisor: receptionist down; restarting")
            if not d.restart_receptionist():
                self._fail_streak += 1
                return
        d._refresh_config_status()


_GLOBAL = GlobalDesktop()


def get_global_desktop() -> GlobalDesktop:
    return _GLOBAL


def ensure_global_desktop() -> bool:
    """Start the global desktop at boot. Non-fatal on failure.

    Returns True when the desktop (or at least its receptionist) came up.
    """
    cleanup_legacy_configs()
    if not DESKTOP_ENABLED:
        logger.info("GlobalDesktop disabled (HERMES_GLOBAL_DESKTOP=0)")
        # Drop any stale state from a previous enabled boot: the wrapper
        # script and browser_cdp both read this file directly, and a
        # leftover "active" entry would advertise a receptionist that
        # never starts (tools would error instead of falling back to
        # local headless).
        try:
            BROWSER_DESKTOP_JSON.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    try:
        _GLOBAL.start()
        return True
    except Exception as exc:
        logger.warning(
            "GlobalDesktop failed to start: %s (browser tools fall back "
            "to local headless)", exc,
        )
        return False


def stop_global_desktop() -> None:
    """Stop the global desktop on server shutdown (keep state file)."""
    try:
        _GLOBAL.stop(remove_json=False)
    except Exception:
        logger.debug("GlobalDesktop stop failed", exc_info=True)
