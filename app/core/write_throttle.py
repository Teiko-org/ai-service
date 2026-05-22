"""Per-session sliding-window rate limiter for write tools."""

from __future__ import annotations

import threading
import time
from collections import deque

# Per-session quota for write tools. The /ask rate limit is per-IP and
# too coarse: a single /ask call can fan out into several writes.
DEFAULT_LIMITS = (
    (5, 60),       # short burst
    (30, 60 * 60), # sustained ceiling
)


class WriteThrottleError(Exception):
    """User-safe error raised when the limit is exceeded."""


class WriteThrottle:
    def __init__(self, limits: tuple[tuple[int, int], ...] = DEFAULT_LIMITS) -> None:
        self._limits = limits
        self._max_window = max(window for _, window in limits)
        self._events: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, session_id: str | None, tool_name: str) -> None:
        key = ((session_id or "anonymous"), tool_name)
        now = time.time()
        with self._lock:
            events = self._events.setdefault(key, deque())
            cutoff = now - self._max_window
            while events and events[0] < cutoff:
                events.popleft()
            for max_count, window in self._limits:
                window_start = now - window
                count_in_window = sum(1 for ts in events if ts >= window_start)
                if count_in_window >= max_count:
                    raise WriteThrottleError(
                        f"Muitas confirmacoes de '{tool_name}' em pouco tempo "
                        f"(limite: {max_count} a cada {window}s). "
                        "Aguarde antes de tentar de novo."
                    )
            events.append(now)


write_throttle = WriteThrottle()
