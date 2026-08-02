"""OpenF1 REST client: intervals/gaps, race control, team radio, sessions.

Free, unauthenticated, 2023+ (per CLAUDE.md's data sources). Every request
goes through a `RateLimiter` (default: OpenF1's free-tier 3 req/s, 30
req/min) and retries on HTTP 429 with backoff — honoring `Retry-After` when
the server sends one, otherwise exponential, capped at `max_backoff`.

`HttpSession`/`HttpResponse` are structural protocols (not `requests`
classes) so tests can pass a fake session/response pair with zero mocking
framework. A real `requests.Session` satisfies `HttpSession` structurally —
no adapter needed. HTTP 404 (endpoint has no rows for that session) is
returned as an empty list so missing OpenF1 tables do not fail acquisition.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol, cast

import requests

from f1_backfill.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

BASE_URL = "https://api.openf1.org/v1"


class OpenF1Error(RuntimeError):
    """Raised when OpenF1 returns a non-retryable error status."""


class OpenF1RateLimitError(OpenF1Error):
    """Raised when retries on repeated HTTP 429 responses are exhausted."""


class HttpResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def json(self) -> Any: ...
    def raise_for_status(self) -> None: ...


class HttpSession(Protocol):
    def get(
        self, url: str, *, params: Mapping[str, Any] | None = None, timeout: float | None = None
    ) -> HttpResponse: ...


class OpenF1Client:
    """Thin, rate-limited, retrying wrapper over the OpenF1 REST endpoints
    this project needs: intervals/gaps, race control, team radio, and
    session lookup (to map a FastF1 event onto OpenF1's `session_key`).
    """

    def __init__(
        self,
        *,
        session: HttpSession | None = None,
        rate_limiter: RateLimiter | None = None,
        max_retries: int = 5,
        base_backoff: float = 1.0,
        max_backoff: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
        base_url: str = BASE_URL,
    ) -> None:
        # requests.Session structurally satisfies HttpSession at runtime (its
        # `get`/`Response` surface is a superset of the protocol); the cast
        # just sidesteps pyright's stricter structural check on the extra
        # kwargs requests' real signature carries.
        self._session: HttpSession = (
            session if session is not None else cast(HttpSession, requests.Session())
        )
        self._rate_limiter = rate_limiter if rate_limiter is not None else RateLimiter()
        self._max_retries = max_retries
        self._base_backoff = base_backoff
        self._max_backoff = max_backoff
        self._sleep = sleep
        self._base_url = base_url

    def get_sessions(self, *, year: int) -> list[dict[str, Any]]:
        return self._get("/sessions", {"year": year})

    def get_intervals(self, session_key: int) -> list[dict[str, Any]]:
        return self._get("/intervals", {"session_key": session_key})

    def get_race_control(self, session_key: int) -> list[dict[str, Any]]:
        return self._get("/race_control", {"session_key": session_key})

    def get_team_radio(self, session_key: int) -> list[dict[str, Any]]:
        return self._get("/team_radio", {"session_key": session_key})

    def find_session_key(self, *, year: int, location: str, session_name: str) -> int | None:
        """Best-effort match of a FastF1 (season, location, session name)
        onto an OpenF1 `session_key`, by exact case-insensitive match of
        `location` and `session_name`. Returns `None` (rather than raising)
        if no match is found — OpenF1 may simply not have that session yet,
        which should not fail the whole FastF1 acquisition for it.
        """
        location_norm = location.strip().casefold()
        name_norm = session_name.strip().casefold()
        for row in self.get_sessions(year=year):
            row_location = str(row.get("location", "")).strip().casefold()
            row_name = str(row.get("session_name", "")).strip().casefold()
            if row_location == location_norm and row_name == name_norm:
                return int(row["session_key"])
        return None

    def _get(self, path: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        url = f"{self._base_url}{path}"
        attempt = 0
        while True:
            self._rate_limiter.acquire()
            response = self._session.get(url, params=params, timeout=30.0)
            if response.status_code == 429:
                attempt += 1
                if attempt > self._max_retries:
                    raise OpenF1RateLimitError(
                        f"Exceeded {self._max_retries} retries on 429 for {url} {dict(params)}"
                    )
                self._sleep(self._backoff_seconds(response, attempt))
                continue
            # OpenF1 returns 404 when a session exists but has no rows for
            # this endpoint (common for /intervals on practice/quali). Treat
            # that as an empty result rather than failing the whole session.
            if response.status_code == 404:
                return []
            response.raise_for_status()
            return cast(list[dict[str, Any]], response.json())

    def _backoff_seconds(self, response: HttpResponse, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return float(retry_after)
            except ValueError:
                logger.warning("Ignoring unparseable Retry-After header: %r", retry_after)
        return min(self._base_backoff * (2 ** (attempt - 1)), self._max_backoff)
