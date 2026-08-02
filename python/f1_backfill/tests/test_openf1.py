"""OpenF1Client: mocked HTTP layer via fake HttpSession/HttpResponse.

No mocking framework needed - `HttpSession`/`HttpResponse` are structural
protocols (see `f1_backfill.openf1`), so a small hand-written fake session
is enough to test rate limiting, 429 backoff (both `Retry-After` and
exponential), and session-key lookup without any real network call.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from f1_backfill.openf1 import OpenF1Client, OpenF1RateLimitError
from f1_backfill.rate_limiter import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeResponse:
    def __init__(
        self, status_code: int, payload: Any = None, headers: Mapping[str, str] | None = None
    ) -> None:
        self.status_code = status_code
        self.headers: Mapping[str, str] = headers or {}
        self._payload = payload

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Replays a scripted list of responses, one per `.get()` call, and
    records every call for assertions.
    """

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, Mapping[str, Any] | None]] = []

    def get(
        self, url: str, *, params: Mapping[str, Any] | None = None, timeout: float | None = None
    ) -> FakeResponse:
        self.calls.append((url, params))
        return self._responses.pop(0)


def _client(session: FakeSession, *, clock: FakeClock | None = None, **kwargs: Any) -> OpenF1Client:
    clock = clock or FakeClock()
    rate_limiter = RateLimiter(clock=clock, sleep=clock.sleep)
    return OpenF1Client(session=session, rate_limiter=rate_limiter, sleep=clock.sleep, **kwargs)


def test_get_intervals_returns_parsed_json_and_hits_the_right_endpoint() -> None:
    payload = [{"date": "2023-09-17T13:31:02.395000+00:00", "driver_number": 1, "interval": 0.003}]
    session = FakeSession([FakeResponse(200, payload)])
    client = _client(session)

    result = client.get_intervals(9165)

    assert result == payload
    [(url, params)] = session.calls
    assert url == "https://api.openf1.org/v1/intervals"
    assert params == {"session_key": 9165}


def test_404_returns_empty_list() -> None:
    session = FakeSession([FakeResponse(404)])
    client = _client(session)

    assert client.get_intervals(9489) == []


def test_rate_limiter_is_consulted_before_every_request() -> None:
    session = FakeSession([FakeResponse(200, []), FakeResponse(200, [])])
    clock = FakeClock()
    calls: list[None] = []

    class _CountingLimiter(RateLimiter):
        def acquire(self) -> None:
            calls.append(None)
            super().acquire()

    client = OpenF1Client(
        session=session, rate_limiter=_CountingLimiter(clock=clock, sleep=clock.sleep)
    )
    client.get_intervals(1)
    client.get_race_control(1)

    assert len(calls) == 2


def test_429_with_retry_after_header_waits_the_specified_seconds_then_succeeds() -> None:
    session = FakeSession(
        [
            FakeResponse(429, headers={"Retry-After": "5"}),
            FakeResponse(200, [{"ok": True}]),
        ]
    )
    clock = FakeClock()
    client = _client(session, clock=clock)

    result = client.get_race_control(1)

    assert result == [{"ok": True}]
    assert clock.now == 5.0
    assert len(session.calls) == 2


def test_429_without_retry_after_backs_off_exponentially() -> None:
    session = FakeSession(
        [
            FakeResponse(429),
            FakeResponse(429),
            FakeResponse(200, [{"ok": True}]),
        ]
    )
    clock = FakeClock()
    client = _client(session, clock=clock, base_backoff=1.0, max_backoff=60.0)

    result = client.get_team_radio(1)

    assert result == [{"ok": True}]
    assert clock.now == 1.0 + 2.0  # 1st retry after 1s, 2nd after 2s (2^1)


def test_429_backoff_is_capped_at_max_backoff() -> None:
    session = FakeSession([FakeResponse(429) for _ in range(4)] + [FakeResponse(200, [])])
    clock = FakeClock()
    client = _client(session, clock=clock, base_backoff=10.0, max_backoff=15.0)

    client.get_team_radio(1)

    # Uncapped backoffs would be 10, 20, 40, 80; capped at 15: 10, 15, 15, 15 = 55.
    assert clock.now == 55.0


def test_429_exhausting_retries_raises() -> None:
    session = FakeSession([FakeResponse(429) for _ in range(3)])
    clock = FakeClock()
    client = _client(session, clock=clock, max_retries=2, base_backoff=0.01)

    with pytest.raises(OpenF1RateLimitError):
        client.get_team_radio(1)


def test_find_session_key_matches_location_and_session_name_case_insensitively() -> None:
    sessions_payload = [
        {
            "session_key": 9599,
            "location": "Marina Bay",
            "session_name": "Practice 1",
            "year": 2024,
        },
        {
            "session_key": 9603,
            "location": "Marina Bay",
            "session_name": "Race",
            "year": 2024,
        },
    ]
    session = FakeSession([FakeResponse(200, sessions_payload)])
    client = _client(session)

    result = client.find_session_key(year=2024, location="marina bay", session_name="RACE")

    assert result == 9603


def test_find_session_key_returns_none_when_no_match() -> None:
    session = FakeSession([FakeResponse(200, [])])
    client = _client(session)

    result = client.find_session_key(year=2024, location="Nowhere", session_name="Race")

    assert result is None
