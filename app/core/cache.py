import time
import threading
import logging

logger = logging.getLogger(__name__)

DEFAULT_TTL = 5 * 60  # 5 minutes


class SimpleCache:
    """Thread-safe in-memory cache with TTL."""

    def __init__(self):
        self._store: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.time() > expires_at:
                del self._store[key]
                return None
            logger.debug("Cache hit: %s", key)
            return value

    def set(self, key: str, value: object, ttl: int = DEFAULT_TTL):
        with self._lock:
            self._store[key] = (time.time() + ttl, value)

    def invalidate(self, key: str):
        with self._lock:
            self._store.pop(key, None)


cache = SimpleCache()
