"""
Hermes Web UI — Per-session virtual display lifecycle manager.

Manages Xvfb + openbox + x11vnc triple for desktop-level browser observation.
Thread-safe: uses a module-level RLock for all mutable shared state.

Lifecycle is bound to session pin state (FR-009a):
    Session Pinned   → SessionDisplay.start()  → runs until unpinned/deleted
    Session Unpinned → no SessionDisplay       → eye button greyed out

chrome-wrapper.sh and agent-browser-wrapper.sh must be installed ONCE before
SessionDisplay is used:
    bash scripts/install-wrappers.sh
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

from api.config import STATE_DIR

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

DISPLAY_START = int(os.environ.get("HERMES_OBSERVE_DISPLAY_START", "99"))
DISPLAY_END = int(os.environ.get("HERMES_OBSERVE_DISPLAY_END", "90"))
DISPLAY_RANGE = DISPLAY_START - DISPLAY_END + 1

VNC_PORT_BASE = 5900
CDP_PORT_OFFSET = 1000  # CDP port = VNC port + 1000

MAX_XTERMS = int(os.environ.get("HERMES_OBSERVE_MAX_XTERMS", "6"))

SESSIONS_DISPLAY_DIR = STATE_DIR / "sessions_display"

# Ensure directory exists
SESSIONS_DISPLAY_DIR.mkdir(parents=True, exist_ok=True)

# ── Required binaries ─────────────────────────────────────────────────────────

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
        "Session observer requires: %s. Install with: apt install x11vnc openbox wmctrl",
        ", ".join(_MISSING_BINARIES),
    )


def purge_orphaned_configs() -> int:
    """Delete SessionDisplay configs for sessions that no longer exist
    or are no longer pinned.

    Called on server startup.  Checks both state.db and JSON files for
    session existence, and removes display configs for:
      - Sessions that no longer exist in either store
      - Sessions that exist but are NOT pinned (stale after crash/unpin race)
    """
    from api.config import SESSION_DIR

    # ── Collect existing session IDs from both stores ──────────────────
    existing_ids: set[str] = set()
    pinned_ids: set[str] = set()

    # 1. Check state.db (primary store)
    try:
        from api.models import _get_session_db
        db = _get_session_db()
        if db is not None:
            import sqlite3
            cur = db._conn.execute(
                "SELECT id, pinned FROM sessions WHERE source='webui'"
            )
            for row in cur.fetchall():
                sid = row[0] if isinstance(row, tuple) else row['id']
                pinned = row[1] if isinstance(row, tuple) else row['pinned']
                existing_ids.add(sid)
                if pinned:
                    pinned_ids.add(sid)
    except Exception:
        pass

    # 2. Fallback: check JSON files (backward compat)
    for f in SESSION_DIR.glob("*.json"):
        sid = f.stem
        if not sid.startswith('_'):
            existing_ids.add(sid)

    # ── Clean up ──────────────────────────────────────────────────────
    removed = 0
    if not SESSIONS_DISPLAY_DIR.exists():
        return removed
    for f in SESSIONS_DISPLAY_DIR.glob("*.json"):
        sid = f.stem
        if sid not in existing_ids:
            # Session no longer exists at all
            try:
                f.unlink()
                removed += 1
                logger.info("purge_orphaned: removed config for deleted session %s", sid)
            except OSError:
                pass
        elif pinned_ids and sid not in pinned_ids:
            # Session exists but is not pinned — stale display config
            try:
                f.unlink()
                removed += 1
                logger.info("purge_orphaned: removed config for unpinned session %s", sid)
            except OSError:
                pass
    if removed:
        logger.info("Purged %d orphaned session display config(s)", removed)
    return removed


def sync_gateway_session_cwd() -> int:
    """Sync the cwd of gateway/CLI sessions to match current terminal.cwd.

    Gateway sessions stored in state.db may have a stale ``cwd`` if the
    terminal working directory was changed in config.yaml after the
    session was created.  This runs once at startup to bring them in line.
    """
    try:
        from api.models import _resolve_gateway_workspace, _get_session_db
        current_cwd = _resolve_gateway_workspace()
        db = _get_session_db()
        if db is None:
            return 0
        updated = 0
        # Only sync messaging platform sessions (weixin, telegram, etc.)
        # where cwd is always controlled by the gateway config.
        # CLI sessions can have arbitrary cwd — don't touch them.
        cur = db._conn.execute(
            "SELECT id, cwd FROM sessions "
            "WHERE source IN ('weixin', 'telegram', 'whatsapp', 'signal', 'line', 'discord', 'slack')"
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
                    "sync_gateway_cwd: %s cwd %s → %s", sid, old_cwd, current_cwd,
                )
        if updated:
            db._conn.commit()
            logger.info("Synced cwd for %d gateway session(s)", updated)
        return updated
    except Exception:
        logger.debug("sync_gateway_session_cwd failed", exc_info=True)
        return 0


def ensure_pinned_displays() -> int:
    """Start SessionDisplay for all pinned sessions at server startup.

    Iterates all sessions; for each pinned one, starts (or reuses)
    a SessionDisplay. Called once after server initialization.

    Returns:
        Number of SessionDisplays started.
    """
    from api.models import all_sessions

    started = 0
    for s in all_sessions():
        if not s.get("pinned"):
            continue
        sid = s["session_id"]
        try:
            sd = SessionDisplay(sid)
            if not sd.is_running():
                sd.start()
                started += 1
        except Exception as e:
            logger.warning("Failed to start SessionDisplay for pinned session %s: %s", sid, e)
    if started:
        logger.info("Started %d SessionDisplay(s) for pinned sessions", started)
    return started


# ── Module-level shared state ─────────────────────────────────────────────────

_LOCK = threading.RLock()
_DISPLAYS: dict[str, "SessionDisplay"] = {}


def _lock_owner_alive(lock_file: Path) -> bool:
    """Check if the process that owns an X11 lock file is still running.

    X11 lock files contain the PID of the X server process as a decimal
    number on the first line.  Returns False if the file is unreadable,
    malformed, or the PID no longer exists.
    """
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


def _find_free_display_and_ports() -> tuple[int, int, int]:
    """Find a free Xvfb display number, VNC port, and CDP port.

    Returns:
        (display_num, vnc_port, cdp_port) tuple.

    Raises:
        RuntimeError: If the port/display pool is exhausted.
    """
    for offset in range(DISPLAY_RANGE):
        display = DISPLAY_START - offset
        vnc_port = VNC_PORT_BASE + offset
        cdp_port = vnc_port + CDP_PORT_OFFSET

        lock_file = Path(f"/tmp/.X{display}-lock")
        socket_dir = Path(f"/tmp/.X11-unix")
        socket_file = socket_dir / f"X{display}"

        # Check Xvfb lock file — if the owning process is dead, clean up
        if lock_file.exists():
            if _lock_owner_alive(lock_file):
                continue
            # Stale lock from a dead process — remove it
            try:
                lock_file.unlink()
            except OSError:
                pass
            try:
                socket_file.unlink(missing_ok=True)
            except OSError:
                pass

        # Check X11 socket
        if socket_file.exists():
            continue

        # Check if VNC port is already in use by another SessionDisplay JSON
        port_in_use = False
        for f in SESSIONS_DISPLAY_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                if data.get("display") == display or data.get("vnc_port") == vnc_port:
                    port_in_use = True
                    break
            except (json.JSONDecodeError, OSError):
                pass

        if port_in_use:
            continue

        return display, vnc_port, cdp_port

    raise RuntimeError(
        f"已达到最大观察会话数 ({DISPLAY_RANGE})，请等待其他会话超时释放"
    )


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


# ── SessionDisplay ────────────────────────────────────────────────────────────


class SessionDisplay:
    """Per-session Xvfb + openbox + x11vnc lifecycle manager.

    Only one SessionDisplay per session_id. Use get_or_create() or start()
    to obtain/initialize. All public methods are thread-safe via _LOCK.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.display_num: int | None = None
        self.vnc_port: int | None = None
        self.cdp_port: int | None = None
        self.xvfb_proc: subprocess.Popen | None = None
        self.openbox_proc: subprocess.Popen | None = None
        self.vnc_proc: subprocess.Popen | None = None
        self.chrome_proc: subprocess.Popen | None = None
        self.xterm_procs: dict[str, subprocess.Popen] = {}

    # ── Public API ──────────────────────────────────────────────────────────

    def start(self) -> dict:
        """Start Xvfb + openbox + x11vnc for this session. Idempotent.

        Called when a session is pinned (via POST /api/session/pin) or when
        the user clicks 👁 on an already-pinned session without a running
        SessionDisplay.

        Returns:
            Dict with display, vnc_port, cdp_port, status.

        Raises:
            RuntimeError: If port pool is exhausted or startup fails.
        """
        with _LOCK:
            # Check if already running
            existing = _DISPLAYS.get(self.session_id)
            if existing and existing.is_running():
                return existing.info_dict()

            # Allocate ports
            display, vnc_port, cdp_port = _find_free_display_and_ports()
            self.display_num = display
            self.vnc_port = vnc_port
            self.cdp_port = cdp_port

            logger.info(
                "SessionDisplay.start session=%s display=:%s vnc=%s cdp=%s",
                self.session_id, display, vnc_port, cdp_port,
            )

            try:
                # 1. Start Xvfb
                self.xvfb_proc = subprocess.Popen(
                    ["Xvfb", f":{display}", "-screen", "0", "1920x1080x24", "-ac"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                socket_path = Path(f"/tmp/.X11-unix/X{display}")
                if not _wait_for_socket(socket_path, timeout=5.0):
                    raise RuntimeError(f"Xvfb 启动失败 (display :{display}): socket 未出现")

                # 2. Start openbox window manager
                self.openbox_proc = subprocess.Popen(
                    ["openbox"],
                    env={**os.environ, "DISPLAY": f":{display}"},
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                time.sleep(0.5)  # let openbox initialize

                # 3. Start x11vnc
                self.vnc_proc = subprocess.Popen(
                    [
                        "x11vnc",
                        "-display", f":{display}",
                        "-rfbport", str(vnc_port),
                        "-forever",
                        "-shared",
                        "-nopw",
                        "-listen", "127.0.0.1",
                        "-quiet",
                    ],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                if not _wait_for_tcp_port("127.0.0.1", vnc_port, timeout=10.0):
                    raise RuntimeError(f"x11vnc 启动失败 (port {vnc_port}): 端口未就绪")

                # 4. Launch Chrome on the virtual display
                chrome_bin = self._find_chrome_binary()
                profile_dir = STATE_DIR / "chrome_profiles" / self.session_id
                profile_dir.mkdir(parents=True, exist_ok=True)

                self.chrome_proc = subprocess.Popen(
                    [
                        chrome_bin,
                        f"--remote-debugging-port={cdp_port}",
                        f"--user-data-dir={profile_dir}",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-features=TranslateUI",
                        "--disable-blink-features=AutomationControlled",
                        "--window-size=960,1080",
                        "--window-position=0,0",
                        "--disable-backgrounding-occluded-windows",
                    ],
                    env={**os.environ,
                         "DISPLAY": f":{display}",
                         # Bypass proxy for localhost CDP; external sites still use proxy
                         "NO_PROXY": (_np + ",127.0.0.1,localhost" if (_np := os.environ.get("NO_PROXY", "")) else "127.0.0.1,localhost")},
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if not _wait_for_tcp_port("127.0.0.1", cdp_port, timeout=10.0):
                    raise RuntimeError(f"Chrome CDP 启动失败 (port {cdp_port}): 端口未就绪")

                # 5. Write JSON config (includes cdp_url for agent-browser-wrapper)
                self._write_config()

                # 6. Register in module dict
                _DISPLAYS[self.session_id] = self

                logger.info(
                    "SessionDisplay ready session=%s display=:%s vnc=%s cdp=%s",
                    self.session_id, display, vnc_port, cdp_port,
                )
                return self.info_dict()

            except Exception:
                # Clean up on failure
                self.stop(remove_json=True)
                raise

    def stop(self, remove_json: bool = True) -> None:
        """Stop all processes and optionally remove JSON config."""
        with _LOCK:
            logger.info("SessionDisplay.stop session=%s", self.session_id)

            # Kill xterm procs
            for _tid, proc in list(self.xterm_procs.items()):
                try:
                    proc.terminate()
                except OSError:
                    pass
            self.xterm_procs.clear()

            # Kill Chrome
            if self.chrome_proc and self.chrome_proc.poll() is None:
                try:
                    self.chrome_proc.terminate()
                    try:
                        self.chrome_proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.chrome_proc.kill()
                        self.chrome_proc.wait(timeout=2)
                except OSError:
                    pass
            self.chrome_proc = None

            # Kill processes: x11vnc first, then openbox, then Xvfb
            for proc, name in [
                (self.vnc_proc, "x11vnc"),
                (self.openbox_proc, "openbox"),
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

            self.xvfb_proc = None
            self.openbox_proc = None
            self.vnc_proc = None
            self.display_num = None
            self.vnc_port = None
            self.cdp_port = None

            # Remove JSON config
            if remove_json:
                self._remove_config()

            # Remove from module dict
            _DISPLAYS.pop(self.session_id, None)

    def is_running(self) -> bool:
        """Check if Xvfb + VNC processes are alive."""
        if self.xvfb_proc and self.xvfb_proc.poll() is not None:
            return False
        if self.vnc_proc and self.vnc_proc.poll() is not None:
            return False
        return self.display_num is not None

    def info_dict(self) -> dict:
        """Return display info as a dict for API responses."""
        return {
            "display": self.display_num,
            "vnc_port": self.vnc_port,
            "cdp_port": self.cdp_port,
            "status": "active" if self.is_running() else "error",
        }

    # ── Static helpers ──────────────────────────────────────────────────────

    @staticmethod
    def get_info(session_id: str) -> dict | None:
        """Read session display info from persisted JSON (fast, no lock)."""
        json_path = SESSIONS_DISPLAY_DIR / f"{session_id}.json"
        if not json_path.exists():
            return None
        try:
            return json.loads(json_path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    # ── Window focus ──────────────────────────────────────────────────────

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

    # ── Internal ────────────────────────────────────────────────────────────

    def _write_config(self) -> None:
        """Write session display JSON config consumed by agent-browser-wrapper."""
        config = {
            "display": self.display_num,
            "vnc_port": self.vnc_port,
            "cdp_port": self.cdp_port,
            "cdp_url": f"http://127.0.0.1:{self.cdp_port}",
            "started_at": time.time(),
            "last_activity": time.time(),
        }
        json_path = SESSIONS_DISPLAY_DIR / f"{self.session_id}.json"
        json_path.write_text(json.dumps(config))

    def _remove_config(self) -> None:
        """Remove the session display JSON file."""
        json_path = SESSIONS_DISPLAY_DIR / f"{self.session_id}.json"
        try:
            json_path.unlink(missing_ok=True)
        except OSError:
            pass

# ── Module init: cleanup orphaned configs + sync gateway cwd ────────────────────

_purge_count = purge_orphaned_configs()
if _purge_count:
    logger.info("Cleaned up %d orphaned session display config(s) on startup", _purge_count)

_sync_count = sync_gateway_session_cwd()
if _sync_count:
    logger.info("Synced cwd for %d gateway session(s) on startup", _sync_count)
