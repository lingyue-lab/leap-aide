"""StreamManager: global singleton managing all ReplayableStreams.

Provides session-keyed stream lifecycle (start/end) and lookup,
replacing the raw STREAMS/STREAMS_LOCK globals.
"""
import threading
import uuid

from api.config import ReplayableStream


class StreamManager:
    """Global singleton managing all ReplayableStreams."""

    def __init__(self):
        self._lock = threading.Lock()
        self._streams: dict[str, ReplayableStream] = {}      # stream_id -> stream
        self._session_streams: dict[str, str] = {}            # session_id -> stream_id
        self._on_end_callbacks: list = []                     # end_stream 回调列表

    def register_on_end(self, callback):
        """注册 end_stream 回调，stream 关闭时触发（用于缓存失效等）。"""
        self._on_end_callbacks.append(callback)

    def start_stream(self, session_id: str) -> str | None:
        """Create a stream for session_id. Returns stream_id or None if already exists."""
        with self._lock:
            if session_id in self._session_streams:
                return None
            stream_id = uuid.uuid4().hex
            stream = ReplayableStream()
            self._streams[stream_id] = stream
            self._session_streams[session_id] = stream_id
            return stream_id

    def end_stream(self, session_id: str) -> str | None:
        """Close and remove stream for session_id. Returns removed stream_id or None."""
        with self._lock:
            stream_id = self._session_streams.pop(session_id, None)
            if stream_id:
                stream = self._streams.pop(stream_id, None)
                if stream:
                    stream.close()
        # 锁外触发回调，避免死锁（回调可能获取 models.LOCK）
        if stream_id:
            for cb in self._on_end_callbacks:
                try:
                    cb(session_id)
                except Exception:
                    pass
        return stream_id

    def get_stream(self, session_id: str) -> ReplayableStream | None:
        """Get the ReplayableStream for a session_id."""
        with self._lock:
            stream_id = self._session_streams.get(session_id)
            return self._streams.get(stream_id) if stream_id else None

    def get_stream_id(self, session_id: str) -> str | None:
        """Get the stream_id for a session_id."""
        with self._lock:
            return self._session_streams.get(session_id)

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
