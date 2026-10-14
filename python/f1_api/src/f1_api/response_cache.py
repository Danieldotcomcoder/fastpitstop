"""A per-process cache for the expensive whole-session reads.

Opening a replay asks for `/laps` and `/circuit`, and each recomputed its
answer from the session's events every time: 15 to 18 s apiece on the
production VM while other replays were opening (measured 2026-10-13). The
work held the API's threads, so every other request queued behind it,
including the web server's session-list read that a page's HTML waits for,
and the phone's page load went from 2.4 s to 6.4 s (ROADMAP W7).

A finished session's answer never changes, so it is kept (least recently
used first out, at most `size` of them). A running session's answer grows,
so it is kept for `live_ttl_s` only: long enough that a burst of viewers
opening `/live` at the start of a race costs one computation, not one each.
Requests for the same key while it is being computed wait for that one
computation instead of starting their own.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from threading import Lock


class ResponseCache[T]:
    def __init__(
        self,
        size: int,
        live_ttl_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._size = size
        self._live_ttl_s = live_ttl_s
        self._clock = clock
        # value, and when it stops being served (None: never, a finished session)
        self._entries: OrderedDict[Hashable, tuple[T, float | None]] = OrderedDict()
        self._lock = Lock()
        self._key_locks: dict[Hashable, Lock] = {}

    def get(self, key: Hashable, *, live: bool, compute: Callable[[], T]) -> T:
        """The cached answer for `key`, computing (once) when there is none
        or a running session's copy has expired."""
        hit = self._lookup(key)
        if hit is not None:
            return hit[0]
        with self._lock:
            key_lock = self._key_locks.setdefault(key, Lock())
        with key_lock:
            # Another request may have computed it while this one waited.
            hit = self._lookup(key)
            if hit is not None:
                return hit[0]
            value = compute()
            expires = self._clock() + self._live_ttl_s if live else None
            with self._lock:
                self._entries[key] = (value, expires)
                self._entries.move_to_end(key)
                while len(self._entries) > self._size:
                    self._entries.popitem(last=False)
                self._key_locks.pop(key, None)
            return value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _lookup(self, key: Hashable) -> tuple[T] | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            value, expires = entry
            if expires is not None and self._clock() >= expires:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return (value,)
