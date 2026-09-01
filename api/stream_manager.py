"""StreamManager: global singleton managing all ReplayableStreams.

Provides session-keyed stream lifecycle (start/end) and lookup,
replacing the raw STREAMS/STREAMS_LOCK globals.
"""
import logging
import threading
import uuid

from api.config import ReplayableStream

logger = logging.getLogger(__name__)


class StreamManager:
    """Global singleton managing all ReplayableStreams."""

    def __init__(self):
        self._lock = threading.Lock()
        self._streams: dict[str, ReplayableStream] = {}      # stream_id -> stream
        self._session_streams: dict[str, str] = {}            # session_id -> stream_id
        self._on_end_callbacks: list = []                     # end_stream callbacks

    def register_on_end(self, callback):
        """Register an end_stream callback, triggered when a stream closes (used for cache eviction, etc.)."""
        self._on_end_callbacks.append(callback)

    def begin_stream(self, session) -> str | None:
        """Create a stream for *session*.  Returns stream_id or None if one already exists.

        Only updates ``_session_streams`` — session objects never hold
        ``active_stream_id``.  Pushes a begin_stream notification to the
        global notify stream so all connected frontends are informed.
        """
        session_id = session.session_id
        with self._lock:
            if session_id in self._session_streams:
                return None
            stream_id = uuid.uuid4().hex
            stream = ReplayableStream()
            self._streams[stream_id] = stream
            self._session_streams[session_id] = stream_id
        logger.debug("[begin_stream] session=%s stream=%s",
                     session_id[:12], stream_id[:8])
        # Push notify outside lock to avoid deadlocks
        from api.models import _notify_session_event
        _notify_session_event('begin_stream', session, stream_id)
        return stream_id

    def end_stream(self, session, stream_id: str = None) -> str | None:
        """Close and remove stream for *session*.  Returns removed stream_id or None.

        Only updates ``_session_streams`` — session objects never hold
        ``active_stream_id``.  Pushes an end_stream notification to the
        global notify stream so all connected frontends are informed.
        """
        session_id = session.session_id
        with self._lock:
            removed_id = self._session_streams.pop(session_id, None)
            if removed_id:
                stream = self._streams.pop(removed_id, None)
                if stream:
                    stream.close()
        # Fire callbacks outside the lock to avoid deadlocks (callbacks may acquire models.LOCK)
        if removed_id:
            logger.debug("[end_stream] session=%s removed=%s",
                         session_id[:12], removed_id[:8] if removed_id else None)
            # Push notify outside lock
            from api.models import _notify_session_event
            _notify_session_event('end_stream', session, removed_id)
            for cb in self._on_end_callbacks:
                try:
                    cb(session_id)
                except Exception:
                    pass
        return removed_id

    def get_stream(self, session_id: str) -> ReplayableStream | None:
        """Get the ReplayableStream for a session_id."""
        with self._lock:
            stream_id = self._session_streams.get(session_id)
            return self._streams.get(stream_id) if stream_id else None

    def get_stream_id(self, session_id: str) -> str | None:
        """Get the stream_id for a session_id."""
        with self._lock:
            return self._session_streams.get(session_id)

    def get_active_session_ids(self) -> list[str]:
        """Return session IDs that currently have an active stream."""
        with self._lock:
            return list(self._session_streams.keys())

    def get_stream_by_id(self, stream_id: str) -> ReplayableStream | None:
        """Get the ReplayableStream by stream_id."""
        with self._lock:
            return self._streams.get(stream_id)

    def subscribe(self, session_id: str):
        """Subscribe to a session's stream. Returns queue or None."""
        stream = self.get_stream(session_id)
        return stream.subscribe() if stream else None

    def active_count(self) -> int:
        """Return number of active streams."""
        with self._lock:
            return len(self._streams)

    def broadcast(self, event: str, data):
        """Broadcast an event to all active streams."""
        with self._lock:
            for stream in list(self._streams.values()):
                try:
                    stream.put(event, data)
                except Exception:
                    pass


stream_manager = StreamManager()
