"""
Hermes Web UI -- WebSocket events endpoint for unified streaming.

Replaces SSE endpoints (/api/chat/session/stream/notify and
/api/chat/session/stream/replay) with a single WebSocket connection.

Protocol:
  Client -> Server:
    {"type": "subscribe_replay", "session_id": "xxx"}
    {"type": "unsubscribe_replay", "session_id": "xxx"}
    {"type": "ping"}

  Server -> Client:
    {"channel": "notify", "event": "...", "data": {...}}
    {"channel": "replay", "session_id": "xxx", "event": "...", "data": {...}}
    {"type": "pong"}
"""

from __future__ import annotations

import json
import logging
import queue
import select
import socket
import time

from api.config import (
    WS_HEARTBEAT_INTERVAL,
    WS_MAX_REPLAY_SUBS,
    WS_QUEUE_TIMEOUT,
)
from api.terminal import (
    _OP_CLOSE,
    _OP_PING,
    _OP_PONG,
    _OP_TEXT,
    _ws_close,
    _ws_handshake,
    _ws_recv_frame,
    _ws_send_frame,
    _ws_send_text,
)

logger = logging.getLogger(__name__)


def _ws_send_json(sock: socket.socket, obj: dict) -> bool:
    """Send a JSON object as a WebSocket text frame."""
    return _ws_send_text(sock, json.dumps(obj, ensure_ascii=False))


def _handle_client_message(
    sock: socket.socket,
    payload: bytes,
    replay_subs: dict[str, queue.Queue],
) -> None:
    """Route an incoming client message to the appropriate handler."""
    try:
        msg = json.loads(payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("WS invalid JSON: %s", payload[:100])
        return

    msg_type = msg.get("type")

    if msg_type == "subscribe_replay":
        session_id = msg.get("session_id")
        if session_id:
            _subscribe_replay(sock, session_id, replay_subs)

    elif msg_type == "unsubscribe_replay":
        session_id = msg.get("session_id")
        if session_id and session_id in replay_subs:
            q = replay_subs.pop(session_id)
            _unsubscribe_stream(session_id, q)
            logger.info("WS unsubscribe_replay: session_id=%s", session_id)

    elif msg_type == "ping":
        _ws_send_json(sock, {"type": "pong"})

    else:
        logger.warning("WS unknown message type: %s", msg_type)


def _subscribe_replay(
    sock: socket.socket,
    session_id: str,
    replay_subs: dict[str, queue.Queue],
) -> None:
    """Subscribe to a session's streaming replay."""
    from api.stream_manager import stream_manager

    # Check subscription limit
    if len(replay_subs) >= WS_MAX_REPLAY_SUBS:
        _ws_send_json(sock, {
            "channel": "replay",
            "session_id": session_id,
            "event": "error",
            "data": {"message": "max subscriptions exceeded"},
        })
        logger.warning(
            "WS max replay subs exceeded: %d >= %d",
            len(replay_subs),
            WS_MAX_REPLAY_SUBS,
        )
        return

    # Find the stream for this session using stream_manager
    stream = stream_manager.get_stream(session_id)

    if not stream:
        _ws_send_json(sock, {
            "channel": "replay",
            "session_id": session_id,
            "event": "no_stream",
            "data": None,
        })
        return

    # If already subscribed, unsubscribe old queue first
    if session_id in replay_subs:
        old_q = replay_subs[session_id]
        stream.unsubscribe(old_q)

    q = stream.subscribe()
    replay_subs[session_id] = q
    logger.info("WS subscribe_replay: session_id=%s", session_id)

    # Replay history
    _ws_send_json(sock, {
        "channel": "replay",
        "session_id": session_id,
        "event": "replay_start",
        "data": None,
    })
    for event, data in stream.replay():
        _ws_send_json(sock, {
            "channel": "replay",
            "session_id": session_id,
            "event": event,
            "data": data,
        })
    _ws_send_json(sock, {
        "channel": "replay",
        "session_id": session_id,
        "event": "replay_end",
        "data": None,
    })


def _unsubscribe_stream(session_id: str, q: queue.Queue) -> None:
    """Remove a subscription from the stream."""
    from api.stream_manager import stream_manager

    stream = stream_manager.get_stream(session_id)
    if stream:
        stream.unsubscribe(q)


def _drain_queue(
    q: queue.Queue,
    sock: socket.socket,
    channel: str,
    session_id: str | None = None,
) -> int:
    """Drain all pending events from a queue and send them as WS frames."""
    count = 0
    while True:
        try:
            item = q.get_nowait()
        except queue.Empty:
            break

        if item is None:
            # Sentinel: stream closed
            break

        event, data = item
        msg = {"channel": channel, "event": event, "data": data}
        if session_id:
            msg["session_id"] = session_id
        if not _ws_send_json(sock, msg):
            return count
        count += 1

    return count


def _ws_events_loop(sock: socket.socket) -> None:
    """
    Main WebSocket event loop for /api/ws/events.

    Runs in the request thread (blocking). Handles:
    - Client messages (subscribe/unsubscribe/ping)
    - Notify stream events
    - Replay stream events
    - Heartbeat
    """
    # Auto-subscribe to notify stream
    notify_q = None
    try:
        from api.notify_stream import notify_stream
        notify_q = notify_stream.subscribe()
    except ImportError:
        logger.warning("notify_stream not available")

    replay_subs: dict[str, queue.Queue] = {}
    msg_count = 0
    last_heartbeat = time.time()

    # Send subscribe_success event so frontend can load baseline state
    _ws_send_json(sock, {
        "channel": "notify",
        "event": "subscribe_success",
        "data": {"ts": time.time()},
    })

    # Replay begin_stream for all existing active streams so the frontend
    # can reconnect to them (e.g. child agent streams already running before
    # this WebSocket connected).  The frontend's notifyManager handles
    # begin_stream by calling StreamManager.startSessionStream(), which
    # establishes the WebSocket replay connection and populates streaming state.
    try:
        from api.stream_manager import stream_manager
        from api.models import SESSIONS, LOCK as _MODELS_LOCK
        for sid in stream_manager.get_active_session_ids():
            stream_id = stream_manager._session_streams.get(sid)
            if stream_id:
                with _MODELS_LOCK:
                    session = SESSIONS.get(sid)
                if session:
                    _ws_send_json(sock, {
                        "channel": "notify",
                        "event": "begin_stream",
                        "data": {
                            "session_id": sid,
                            "stream_id": stream_id,
                            "ts": time.time(),
                        },
                    })
    except Exception:
        pass  # non-fatal: streams may have ended between enumeration and send

    try:
        while True:
            # 1. Drain replay queues (non-blocking) FIRST — ensures replay
            #    events (especially 'done') are sent before any notify events,
            #    eliminating the end_stream/done race condition.
            for sid, q in list(replay_subs.items()):
                count = _drain_queue(q, sock, channel="replay", session_id=sid)
                msg_count += count

            # 2. Non-blocking check for client messages
            try:
                readable, _, _ = select.select([sock], [], [], 0)
                if readable:
                    result = _ws_recv_frame(sock)
                    if result is None:
                        break
                    opcode, payload = result
                    if opcode == _OP_CLOSE:
                        break
                    if opcode == _OP_PING:
                        _ws_send_frame(sock, _OP_PONG, payload)
                        continue
                    if opcode == _OP_TEXT:
                        _handle_client_message(sock, payload, replay_subs)
                        msg_count += 1
            except (OSError, ValueError):
                break

            # 3. Drain notify queue (blocking WS_QUEUE_TIMEOUT seconds)
            if notify_q:
                try:
                    item = notify_q.get(timeout=WS_QUEUE_TIMEOUT)
                    if item is None:
                        # Stream closed
                        notify_q = None
                    else:
                        event, data = item
                        _ws_send_json(sock, {
                            "channel": "notify",
                            "event": event,
                            "data": data,
                        })
                        msg_count += 1
                except queue.Empty:
                    pass

            # 4. Heartbeat (every WS_HEARTBEAT_INTERVAL seconds)
            now = time.time()
            if now - last_heartbeat > WS_HEARTBEAT_INTERVAL:
                try:
                    _ws_send_frame(sock, _OP_PING, b"")
                    last_heartbeat = now
                except OSError:
                    break

    finally:
        # Cleanup
        if notify_q:
            try:
                from api.notify_stream import notify_stream
                notify_stream.unsubscribe(notify_q)
            except ImportError:
                pass

        for sid, q in replay_subs.items():
            _unsubscribe_stream(sid, q)
        replay_subs.clear()

        _ws_close(sock)
        logger.info("WS events connection closed (msg_count=%d)", msg_count)


def handle_ws_events(handler) -> None:
    """
    Entry point for /api/ws/events WebSocket endpoint.

    Called from Handler.do_GET when path == '/api/ws/events' and the
    request carries 'Upgrade: websocket'.

    Performs the WS handshake synchronously, then runs the event loop
    in the current thread (blocking until connection closes).
    """
    from api.auth import is_auth_enabled, parse_cookie, verify_session

    # Auth check
    if is_auth_enabled():
        cv = parse_cookie(handler)
        if not (cv and verify_session(cv)):
            handler.send_response(401)
            handler.send_header("Content-Type", "text/plain")
            handler.end_headers()
            handler.wfile.write(b"Unauthorized")
            return

    client_key = handler.headers.get("Sec-WebSocket-Key", "").strip()
    if not client_key:
        handler.send_response(400)
        handler.send_header("Content-Type", "text/plain")
        handler.end_headers()
        handler.wfile.write(b"Missing Sec-WebSocket-Key")
        return

    # Flush buffered output before taking over the socket
    try:
        handler.wfile.flush()
    except OSError:
        pass

    raw_sock: socket.socket = handler.connection

    # Perform WebSocket handshake
    try:
        _ws_handshake(raw_sock, client_key)
    except OSError as exc:
        logger.error("WS handshake failed: %s", exc)
        return

    # Signal to HTTPServer that we are managing this connection ourselves
    handler.close_connection = True

    client_ip = handler.client_address[0] if handler.client_address else "unknown"
    logger.info("WS events connected: %s", client_ip)

    # Run the event loop synchronously
    _ws_events_loop(raw_sock)
