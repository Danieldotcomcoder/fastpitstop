"""RateLimiter: sliding-window waits over a fake clock (no real sleeping)."""

from __future__ import annotations

from f1_backfill.rate_limiter import RateLimiter


class FakeClock:
    """Callable fake monotonic clock paired with a fake `sleep` that
    advances it, mirroring `f1_replay`'s test convention for the same job.
    """

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_requests_under_the_limit_never_wait() -> None:
    clock = FakeClock()
    limiter = RateLimiter(max_per_second=3, max_per_minute=30, clock=clock, sleep=clock.sleep)

    for _ in range(3):
        limiter.acquire()

    assert clock.now == 0.0


def test_fourth_request_within_one_second_waits_for_the_per_second_window() -> None:
    clock = FakeClock()
    limiter = RateLimiter(max_per_second=3, max_per_minute=30, clock=clock, sleep=clock.sleep)

    for _ in range(3):
        limiter.acquire()
    limiter.acquire()  # the 4th must wait until the 1st is > 1s old

    assert clock.now >= 1.0


def test_per_minute_window_is_enforced_once_per_second_budget_is_spent() -> None:
    clock = FakeClock()
    limiter = RateLimiter(max_per_second=100, max_per_minute=5, clock=clock, sleep=clock.sleep)

    for _ in range(5):
        limiter.acquire()
    assert clock.now == 0.0  # per-second cap is generous enough to not matter here

    limiter.acquire()  # the 6th must wait for the 1st to fall out of the 60s window
    assert clock.now >= 60.0


def test_two_calls_a_minute_apart_never_wait() -> None:
    clock = FakeClock()
    limiter = RateLimiter(max_per_second=1, max_per_minute=1, clock=clock, sleep=clock.sleep)

    limiter.acquire()
    clock.now += 61.0
    limiter.acquire()

    assert clock.now == 61.0  # the second call needed no additional wait
