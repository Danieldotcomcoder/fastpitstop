"""A sliding-window rate limiter for OpenF1's free tier (3 req/s, 30 req/min).

Deliberately simple: `acquire()` blocks (sleeping, not spinning) until
sending one more request would stay under both windows, then records the
request time. `clock`/`sleep` are injectable so tests can exercise the
waiting logic without real wall-clock time.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

DEFAULT_MAX_PER_SECOND = 3
DEFAULT_MAX_PER_MINUTE = 30


class RateLimiter:
    """Sliding-window limiter over two horizons: 1 second and 60 seconds."""

    def __init__(
        self,
        *,
        max_per_second: int = DEFAULT_MAX_PER_SECOND,
        max_per_minute: int = DEFAULT_MAX_PER_MINUTE,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._max_per_second = max_per_second
        self._max_per_minute = max_per_minute
        self._clock = clock
        self._sleep = sleep
        self._recent: deque[float] = deque()

    def acquire(self) -> None:
        """Block until one more request is allowed, then record it."""
        while True:
            now = self._clock()
            self._evict_older_than(now, 60.0)
            wait = self._wait_needed(now)
            if wait <= 0:
                break
            self._sleep(wait)
        self._recent.append(self._clock())

    def _wait_needed(self, now: float) -> float:
        wait = 0.0
        per_second_hits = [t for t in self._recent if now - t < 1.0]
        if len(per_second_hits) >= self._max_per_second:
            wait = max(wait, 1.0 - (now - per_second_hits[0]))
        if len(self._recent) >= self._max_per_minute:
            wait = max(wait, 60.0 - (now - self._recent[0]))
        return wait

    def _evict_older_than(self, now: float, horizon: float) -> None:
        while self._recent and now - self._recent[0] > horizon:
            self._recent.popleft()
