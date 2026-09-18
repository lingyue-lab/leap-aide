"""
Hermes Web UI -- WebSocket ↔ VNC TCP proxy.

Bridges a browser noVNC client to a local VNC server via WebSocket.

Flow:
    Browser (noVNC RFB)
        └─ WebSocket /api/ws/vnc
              └─ api/vnc.py  (this module)
                    └─ TCP → VNC server (default: 127.0.0.1:5900)

Protocol notes:
  - RFB (VNC) is a raw binary protocol.  WebSocket frames are forwarded
    byte-for-byte without any JSON wrapping or base64 encoding.
  - Both binary (0x2) and text (0x1) WS opcodes from the client are
    forwarded to the VNC socket; in practice noVNC only sends binary.
  - The VNC TCP stream is forwarded back as WebSocket binary frames.

Configuration (environment variables):
  HERMES_VNC_HOST   VNC server host  (default: 127.0.0.1)
  HERMES_VNC_PORT   VNC server port  (default: 5900)
"""

from __future__ import annotations

import logging
import os
import select
import socket

logger = logging.getLogger(__name__)

# ── TCP keepalive ───────────────────────────────────────────────────────────────

def _set_tcp_keepalive(sock: socket.socket, idle_sec: int = 60, interval_sec: int = 10, probes: int = 5) -> None:
    """Enable TCP keepalive on a socket to prevent idle disconnection.

    Without keepalive, proxies, NAT gateways, and Docker bridge networks
    may silently drop idle connections after their conntrack timeout
    (typically 300-1800s).  Keepalive probes at OS level keep the connection
    alive regardless of application-layer data flow.

    Defaults: first probe after 60s idle, every 10s thereafter, 5 failures
    → connection declared dead after ~110s of unresponsiveness.
    """
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        # Linux-specific tuning (gracefully skipped on other platforms)
        if hasattr(socket, 'TCP_KEEPIDLE'):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, idle_sec)
        elif hasattr(socket, 'TCP_KEEPALIVE'):  # macOS
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPALIVE, idle_sec)
        if hasattr(socket, 'TCP_KEEPINTVL'):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, interval_sec)
        if hasattr(socket, 'TCP_KEEPCNT'):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, probes)
    except OSError:
        pass  # best-effort — some container environments restrict setsockopt

# ── Configuration ─────────────────────────────────────────────────────────────

VNC_HOST: str = os.environ.get("HERMES_VNC_HOST", "127.0.0.1")
VNC_PORT: int = int(os.environ.get("HERMES_VNC_PORT", "5901"))

# ── Import low-level WS helpers from terminal.py (no code duplication) ────────
# These helpers are stable, well-tested primitives used by the terminal backend.
from app.api.terminal import (
    _ws_handshake,
    _ws_recv_frame,
    _ws_send_frame,
    _ws_close,
    _recv_exact,           # noqa: F401 – imported for completeness / tests
    _OP_BINARY,
    _OP_TEXT,
    _OP_CLOSE,
    _OP_PING,
    _OP_PONG,
)

_MAX_TCP_CHUNK = 65536   # bytes read from VNC socket per iteration


# ── Bridge loop ───────────────────────────────────────────────────────────────

def _bridge_vnc(ws_sock: socket.socket, vnc_sock: socket.socket) -> None:
    """
    Bidirectional select()-based bridge between a WebSocket connection and
    a VNC TCP socket.

    Runs **synchronously** in the calling thread until either side closes
    the connection or an unrecoverable error occurs.
    """
    ws_sock.setblocking(False)
    vnc_sock.setblocking(False)

    ws_fd  = ws_sock.fileno()
    vnc_fd = vnc_sock.fileno()

    while True:
        try:
            rlist, _, _ = select.select([ws_fd, vnc_fd], [], [], 5.0)
        except (ValueError, OSError) as exc:
            logger.debug("VNC bridge select error: %s", exc)
            break

        # ── WebSocket → VNC TCP ───────────────────────────────────────────────
        if ws_fd in rlist:
            result = _ws_recv_frame(ws_sock)
            if result is None:
                logger.debug("VNC bridge: WebSocket closed by client")
                break

            opcode, payload = result

            if opcode == _OP_CLOSE:
                logger.debug("VNC bridge: received WS close frame")
                break

            if opcode == _OP_PING:
                _ws_send_frame(ws_sock, _OP_PONG, payload)
                continue

            if opcode in (_OP_BINARY, _OP_TEXT) and payload:
                try:
                    vnc_sock.sendall(payload)
                except OSError as exc:
                    logger.debug("VNC bridge: VNC send error: %s", exc)
                    break

        # ── VNC TCP → WebSocket ───────────────────────────────────────────────
        if vnc_fd in rlist:
            try:
                data = vnc_sock.recv(_MAX_TCP_CHUNK)
            except OSError as exc:
                logger.debug("VNC bridge: VNC recv error: %s", exc)
                break

            if not data:
                logger.debug("VNC bridge: VNC server closed connection")
                break

            if not _ws_send_frame(ws_sock, _OP_BINARY, data):
                logger.debug("VNC bridge: WebSocket send error")
                break


# ── Public entry point (called from server.py) ────────────────────────────────

def handle_ws_vnc(handler, path_port: int | None = None) -> None:
    """
    Called from Handler.do_GET when path matches '/api/ws/vnc' and the
    request carries 'Upgrade: websocket'.

    Port resolution priority:
    1. path_port from URL path: /api/ws/vnc/<port>
    2. ?port= query param: /api/ws/vnc?port=<port>
    3. VNC_PORT env var (default 5901)

    1. Validates the WebSocket upgrade request.
    2. Connects to the local VNC server (TCP).
    3. Completes the WebSocket handshake.
    4. Runs the bidirectional bridge loop in the current thread (blocking).

    This is correct for ThreadingHTTPServer: each connection already runs
    in its own thread, so blocking here does not prevent new connections
    from being served.
    """
    from urllib.parse import parse_qs, urlparse

    # Port resolution: path > query param > env default
    if path_port is not None:
        vnc_port = path_port
    else:
        # Parse per-session VNC port from query string: /api/ws/vnc?port=5900
        parsed_url = urlparse(handler.path)
        qs = parse_qs(parsed_url.query)
        requested_port = qs.get("port", [None])[0]

        if requested_port:
            try:
                vnc_port = int(requested_port)
            except ValueError:
                handler.send_response(400)
                handler.send_header("Content-Type", "text/plain")
                handler.end_headers()
                handler.wfile.write(b"Invalid port parameter")
                return
        else:
            vnc_port = VNC_PORT  # Fallback to env var (backward compatible)
    client_key = handler.headers.get("Sec-WebSocket-Key", "").strip()
    if not client_key:
        handler.send_response(400)
        handler.send_header("Content-Type", "text/plain")
        handler.end_headers()
        handler.wfile.write(b"Missing Sec-WebSocket-Key")
        return

    # ── Connect to VNC server before completing the WS handshake ─────────────
    # If VNC is unavailable, we return a proper HTTP error (not a garbled WS).
    try:
        vnc_sock = socket.create_connection((VNC_HOST, vnc_port), timeout=5)
    except (ConnectionRefusedError, OSError) as exc:
        logger.warning("VNC connect failed (%s:%d): %s", VNC_HOST, vnc_port, exc)
        handler.send_response(503)
        handler.send_header("Content-Type", "text/plain")
        handler.end_headers()
        handler.wfile.write(
            f"VNC server unavailable at {VNC_HOST}:{vnc_port}".encode()
        )
        return

    _set_tcp_keepalive(vnc_sock)

    # noVNC requires the server to advertise 'binary' sub-protocol.
    # Flush buffered output before taking over the raw socket.
    try:
        handler.wfile.flush()
    except OSError:
        pass

    raw_sock: socket.socket = handler.connection
    _set_tcp_keepalive(raw_sock)

    # Complete WebSocket handshake (sends HTTP 101).
    try:
        _ws_handshake(raw_sock, client_key)
    except OSError as exc:
        logger.error("VNC WS handshake failed: %s", exc)
        vnc_sock.close()
        return

    # Prevent BaseHTTPRequestHandler from trying to read the next request or
    # closing the socket after do_GET returns.
    handler.close_connection = True

    logger.info(
        "VNC WS session starting (proxy → %s:%d)", VNC_HOST, vnc_port
    )

    try:
        _bridge_vnc(raw_sock, vnc_sock)
    finally:
        try:
            vnc_sock.close()
        except OSError:
            pass
        _ws_close(raw_sock)
        logger.debug("VNC WS session ended")
