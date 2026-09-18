"""
Hermes Web UI -- WebSocket PTY terminal backend.

Provides a real bash shell over WebSocket by:
  1. Performing a manual RFC 6455 WebSocket handshake on the raw socket.
  2. Forking a bash child process attached to a PTY master/slave pair.
  3. Running a select()-based I/O loop to bridge WebSocket frames ↔ PTY I/O.

Protocol (JSON text frames):
  Client → Server:
    {"type": "input",  "data": "<string>"}           -- user keystrokes
    {"type": "resize", "cols": N, "rows": N}          -- terminal resize
  Server → Client:
    {"type": "output", "data": "<base64-encoded>"}   -- PTY output bytes
    {"type": "error",  "message": "<string>"}        -- fatal error

Only available on POSIX systems (Linux / macOS).  On Windows the import of
`pty` / `fcntl` will fail; the endpoint returns 501 Not Implemented.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import select
import signal
import socket
import struct
import threading

logger = logging.getLogger(__name__)

# ── WebSocket constants (RFC 6455) ────────────────────────────────────────────

_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

_OP_CONTINUATION = 0x0
_OP_TEXT         = 0x1
_OP_BINARY       = 0x2
_OP_CLOSE        = 0x8
_OP_PING         = 0x9
_OP_PONG         = 0xA

_MAX_FRAME_BYTES = 1 * 1024 * 1024   # 1 MB sanity cap


# ── PTY availability check ────────────────────────────────────────────────────

try:
    import pty
    import fcntl
    import termios
    _PTY_AVAILABLE = True
except ImportError:
    _PTY_AVAILABLE = False


# ── Low-level WebSocket helpers ───────────────────────────────────────────────

def _ws_handshake(sock: socket.socket, client_key: str) -> None:
    """Send HTTP 101 Switching Protocols response to complete WS handshake."""
    accept = base64.b64encode(
        hashlib.sha1((client_key + _WS_MAGIC).encode()).digest()
    ).decode()
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    )
    sock.sendall(response.encode())


def _ws_recv_frame(sock: socket.socket) -> tuple[int, bytes] | None:
    """
    Read one WebSocket frame from *sock*.

    Returns (opcode, payload) or None on connection close / error.
    Handles client-side masking (required by RFC 6455).
    """
    try:
        # Byte 0: FIN + opcode
        b0 = _recv_exact(sock, 1)
        if b0 is None:
            return None
        opcode = b0[0] & 0x0F

        # Byte 1: MASK flag + payload length
        b1 = _recv_exact(sock, 1)
        if b1 is None:
            return None
        masked = bool(b1[0] & 0x80)
        length = b1[0] & 0x7F

        if length == 126:
            raw = _recv_exact(sock, 2)
            if raw is None:
                return None
            length = struct.unpack("!H", raw)[0]
        elif length == 127:
            raw = _recv_exact(sock, 8)
            if raw is None:
                return None
            length = struct.unpack("!Q", raw)[0]

        if length > _MAX_FRAME_BYTES:
            logger.warning("WS frame too large (%d bytes), closing", length)
            return None

        mask_key = b""
        if masked:
            mask_key = _recv_exact(sock, 4)
            if mask_key is None:
                return None

        payload = _recv_exact(sock, length)
        if payload is None:
            return None

        if masked:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

        return opcode, payload

    except (OSError, ConnectionResetError):
        return None


def _recv_exact(sock: socket.socket, n: int) -> bytes | None:
    """Receive exactly *n* bytes from *sock*, or None on EOF/error."""
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except OSError:
            return None
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _ws_send_text(sock: socket.socket, text: str) -> bool:
    """Send a WebSocket text frame. Returns False on error."""
    payload = text.encode("utf-8")
    return _ws_send_frame(sock, _OP_TEXT, payload)


def _ws_send_frame(sock: socket.socket, opcode: int, payload: bytes) -> bool:
    """Encode and send one WebSocket frame (server-side, no masking)."""
    length = len(payload)
    header = bytearray()
    header.append(0x80 | opcode)   # FIN=1

    if length < 126:
        header.append(length)
    elif length < 65536:
        header.append(126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(127)
        header.extend(struct.pack("!Q", length))

    try:
        sock.sendall(bytes(header) + payload)
        return True
    except OSError:
        return False


def _ws_close(sock: socket.socket) -> None:
    """Send WebSocket close frame and shut down the socket."""
    try:
        _ws_send_frame(sock, _OP_CLOSE, b"\x03\xe8")   # 1000 Normal Closure
    except OSError:
        pass
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


# ── PTY helpers ───────────────────────────────────────────────────────────────

def _resize_pty(master_fd: int, cols: int, rows: int) -> None:
    """Set the PTY window size (TIOCSWINSZ)."""
    try:
        fcntl.ioctl(
            master_fd,
            termios.TIOCSWINSZ,
            struct.pack("HHHH", rows, cols, 0, 0),
        )
    except Exception as exc:
        logger.debug("resize_pty failed: %s", exc)


# ── Main session loop ─────────────────────────────────────────────────────────

def _run_session(sock: socket.socket) -> None:
    """
    Fork a bash child, then bridge PTY ↔ WebSocket until either side closes.
    Runs in the request thread (blocks until session ends).
    """
    import tempfile

    pid: int | None = None
    master_fd: int | None = None

    # ── Build the bash rcfile BEFORE fork so both parent and child share the path ──
    ls_colors = (
        "rs=0:di=34:ln=36:mh=44;97:pi=40;33:so=35:do=35:"
        "bd=40;33:cd=40;33:or=40;91:mi=05;40;97:"
        "su=41;97:sg=43;30:ca=41;93:tw=42;30:"
        "ow=42;34:st=44;97:ex=32:"
        "*.tar=91:*.tgz=91:*.arc=91:*.arj=91:*.taz=91:"
        "*.lha=91:*.lz4=91:*.lzh=91:*.lzma=91:*.tlz=91:"
        "*.txz=91:*.tzo=91:*.t7z=91:*.zip=91:*.z=91:"
        "*.Z=91:*.dz=91:*.gz=91:*.lrz=91:*.lz=91:"
        "*.lzo=91:*.xz=91:*.bz2=91:*.bz=91:*.tbz=91:"
        "*.tbz2=91:*.tz=91:*.deb=91:*.rpm=91:*.jar=91:"
        "*.war=91:*.ear=91:*.sar=91:*.rar=91:*.alz=91:"
        "*.ace=91:*.zoo=91:*.cpio=91:*.7z=91:*.rz=91:*.cab=91:"
        "*.jpg=35:*.jpeg=35:*.gif=35:*.bmp=35:*.pbm=35:"
        "*.pgm=35:*.ppm=35:*.tga=35:*.xbm=35:*.xpm=35:"
        "*.tif=35:*.tiff=35:*.png=35:*.svg=35:*.svgz=35:"
        "*.mng=35:*.pcx=35:*.mov=35:*.mpg=35:*.mpeg=35:"
        "*.m2v=35:*.mkv=35:*.webm=35:*.ogm=35:*.mp4=35:"
        "*.m4v=35:*.mp4v=35:*.vob=35:*.qt=35:*.nuv=35:"
        "*.wmv=35:*.asf=35:*.rm=35:*.rmvb=35:*.flc=35:"
        "*.avi=35:*.fli=35:*.flv=35:*.gl=35:*.dl=35:"
        "*.xcf=35:*.xwd=35:*.yuv=35:*.cgm=35:*.emf=35:"
        "*.axv=35:*.anx=35:*.ogv=35:*.ogx=35:"
        "*.aac=36:*.au=36:*.flac=36:*.mid=36:*.midi=36:"
        "*.mka=36:*.mp3=36:*.mpc=36:*.ogg=36:*.ra=36:"
        "*.wav=36:*.axa=36:*.oga=36:*.spx=36:*.xspf=36:"
    )
    rc_content = (
        "# Hermes terminal init\n"
        "[ -f /etc/bash.bashrc ] && . /etc/bash.bashrc\n"
        "[ -f ~/.bashrc ] && . ~/.bashrc\n"
        # Override LS_COLORS AFTER sourcing .bashrc so dircolors can't overwrite ours
        f"export LS_COLORS='{ls_colors}'\n"
        "if ls --color=auto / >/dev/null 2>&1; then\n"
        "  alias ls='ls --color=auto'\n"
        "else\n"
        "  export CLICOLOR=1\n"
        "  export LSCOLORS=ExGxFxDxCxDxDxhbhdacad\n"
        "fi\n"
        "alias grep='grep --color=auto'\n"
        "alias fgrep='fgrep --color=auto'\n"
        "alias egrep='egrep --color=auto'\n"
        r'export PS1="\[\e[92m\]\u\[\e[90m\]@\[\e[94m\]\h\[\e[0m\] \[\e[93m\]\w\[\e[0m\] \[\e[91m\]❯\[\e[0m\] "' + "\n"
    )
    rc_fd_no, rc_path = tempfile.mkstemp(prefix="hermes_term_", suffix=".bashrc")
    try:
        os.write(rc_fd_no, rc_content.encode())
        os.close(rc_fd_no)
    except OSError as exc:
        logger.warning("Could not write rcfile: %s", exc)
        try:
            os.close(rc_fd_no)
        except OSError:
            pass
        rc_path = None

    try:
        pid, master_fd = pty.fork()
    except Exception as exc:
        logger.error("pty.fork() failed: %s", exc)
        _ws_send_text(sock, json.dumps({"type": "error", "message": f"PTY error: {exc}"}))
        _ws_close(sock)
        if rc_path:
            try:
                os.unlink(rc_path)
            except OSError:
                pass
        return

    if pid == 0:
        # ── Child process ──────────────────────────────────────────────────────
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env["CLICOLOR_FORCE"] = "1"
        env["GCC_COLORS"] = "error=01;31:warning=01;35:note=01;36:caret=01;32:locus=01:quote=01"
        env["LS_COLORS"] = ls_colors
        if rc_path:
            try:
                os.execvpe("/bin/bash", ["/bin/bash", "--rcfile", rc_path], env)
            except Exception:
                pass
        try:
            os.execvpe("/bin/bash", ["/bin/bash", "--login"], env)
        except FileNotFoundError:
            os.execvpe("/bin/sh", ["/bin/sh"], env)
        os._exit(1)

    sock.setblocking(False)
    try:
        _bridge_loop(sock, master_fd, pid)
    finally:
        # Clean up child process
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        try:
            os.close(master_fd)
        except OSError:
            pass
        # Clean up temp rcfile (bash has long finished reading it by now)
        if rc_path:
            try:
                os.unlink(rc_path)
            except OSError:
                pass
        _ws_close(sock)
        logger.debug("Terminal session ended (pid=%s)", pid)


def _bridge_loop(sock: socket.socket, master_fd: int, pid: int) -> None:
    """
    select()-based I/O loop:
      - PTY master readable  → base64-encode → send WS text frame
      - WS socket readable   → recv WS frame → decode → write to PTY master
    """
    sock_fd = sock.fileno()

    while True:
        # Build readable fd list; master_fd may become invalid after bash exits
        try:
            rlist, _, _ = select.select([sock_fd, master_fd], [], [], 1.0)
        except (ValueError, OSError):
            # master_fd is closed / invalid → session ended
            break

        if not rlist:
            # Timeout: check if child is still alive
            try:
                result = os.waitpid(pid, os.WNOHANG)
                if result[0] != 0:
                    # Child exited
                    break
            except ChildProcessError:
                break
            continue

        # ── PTY → WebSocket ────────────────────────────────────────────────────
        if master_fd in rlist:
            try:
                data = os.read(master_fd, 4096)
            except OSError:
                break
            if not data:
                break
            encoded = base64.b64encode(data).decode("ascii")
            frame = json.dumps({"type": "output", "data": encoded})
            if not _ws_send_text(sock, frame):
                break

        # ── WebSocket → PTY ────────────────────────────────────────────────────
        if sock_fd in rlist:
            result = _ws_recv_frame(sock)
            if result is None:
                break
            opcode, payload = result

            if opcode == _OP_CLOSE:
                break

            if opcode == _OP_PING:
                _ws_send_frame(sock, _OP_PONG, payload)
                continue

            if opcode in (_OP_TEXT, _OP_BINARY):
                try:
                    msg = json.loads(payload.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue

                msg_type = msg.get("type")

                if msg_type == "input":
                    raw = msg.get("data", "")
                    if isinstance(raw, str) and raw:
                        try:
                            os.write(master_fd, raw.encode("utf-8"))
                        except OSError:
                            break

                elif msg_type == "resize":
                    cols = int(msg.get("cols", 80))
                    rows = int(msg.get("rows", 24))
                    _resize_pty(master_fd, cols, rows)


# ── Public entry point (called from server.py) ────────────────────────────────

def handle_ws_terminal(handler) -> None:
    """
    Called from Handler.do_GET when path == '/api/ws/terminal' and the
    request carries 'Upgrade: websocket'.

    Performs the WS handshake synchronously, then runs the PTY session
    **in the current thread** (blocking until the session ends).

    This is correct for ThreadingHTTPServer: each connection already runs
    in its own thread, so blocking here does not prevent new connections
    from being served.  We must NOT spawn a new thread and return, because
    returning would cause HTTPServer to call socket.shutdown(SHUT_RDWR)
    which kills the connection even if we have a dup() of the fd.
    """
    if not _PTY_AVAILABLE:
        handler.send_response(501)
        handler.send_header("Content-Type", "text/plain")
        handler.end_headers()
        handler.wfile.write(b"PTY not available on this platform")
        return

    client_key = handler.headers.get("Sec-WebSocket-Key", "").strip()
    if not client_key:
        handler.send_response(400)
        handler.send_header("Content-Type", "text/plain")
        handler.end_headers()
        handler.wfile.write(b"Missing Sec-WebSocket-Key")
        return

    # Flush buffered output before taking over the socket.
    try:
        handler.wfile.flush()
    except OSError:
        pass

    raw_sock: socket.socket = handler.connection

    # Perform WebSocket handshake.
    try:
        _ws_handshake(raw_sock, client_key)
    except OSError as exc:
        logger.error("WS handshake failed: %s", exc)
        return

    # Signal to HTTPServer that we are managing this connection ourselves.
    # Setting close_connection = False prevents BaseHTTPRequestHandler from
    # trying to read the next request; we then run the blocking PTY session.
    handler.close_connection = True

    logger.info("Terminal WS session starting (blocking in request thread)")

    # Run the PTY session synchronously.  This blocks until the session ends.
    # HTTPServer will NOT shut down the socket while this function has not
    # returned (we are still inside do_GET on the request thread).
    _run_session(raw_sock)
