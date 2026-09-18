"""Global non-replayable fan-out SSE stream for session notifications.

Unlike ReplayableStream (used by /api/chat/session/stream/replay), this
stream does NOT store history.  New subscribers only receive events
published after they connect.  Queue size is 1000 to absorb push spikes
without dropping events.

Used by /api/chat/session/stream/notify endpoint.
"""
import logging
import queue
import threading

logger = logging.getLogger(__name__)


class NotifyStream:
    """Lightweight non-replayable fan-out SSE stream."""

    def __init__(self, max_queue_size: int = 1000):
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._max_queue_size = max_queue_size

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self._max_queue_size)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def put(self, event: str, data: dict) -> None:
        with self._lock:
            dead = []
            for sq in self._subscribers:
                try:
                    sq.put_nowait((event, data))
                except queue.Full:
                    dead.append(sq)
            for sq in dead:
                try:
                    self._subscribers.remove(sq)
                except ValueError:
                    pass


notify_stream = NotifyStream()
