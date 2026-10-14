"""`f1_api.response_cache`: a finished session's answer is computed once, a
running one's for a few seconds at a time, and simultaneous requests share
one computation."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from f1_api.response_cache import ResponseCache
from f1_api.routes import sessions as session_routes


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def counting(value: str) -> tuple[list[int], Callable[[], str]]:
    calls = [0]

    def compute() -> str:
        calls[0] += 1
        return value

    return calls, compute


def test_a_finished_session_is_computed_once() -> None:
    clock = Clock()
    cache: ResponseCache[str] = ResponseCache(4, 15.0, clock)
    calls, compute = counting("laps")
    for _ in range(3):
        clock.now += 1_000
        assert cache.get("race", live=False, compute=compute) == "laps"
    assert calls[0] == 1


def test_a_running_session_is_reused_only_briefly() -> None:
    clock = Clock()
    cache: ResponseCache[str] = ResponseCache(4, 15.0, clock)
    calls, compute = counting("laps")
    cache.get("race", live=True, compute=compute)
    clock.now = 14.9
    cache.get("race", live=True, compute=compute)
    assert calls[0] == 1
    clock.now = 15.0
    cache.get("race", live=True, compute=compute)
    assert calls[0] == 2


def test_the_least_recently_used_session_goes_first() -> None:
    cache: ResponseCache[str] = ResponseCache(2, 15.0, Clock())
    a_calls, a = counting("a")
    b_calls, b = counting("b")
    c_calls, c = counting("c")
    cache.get("a", live=False, compute=a)
    cache.get("b", live=False, compute=b)
    cache.get("a", live=False, compute=a)
    cache.get("c", live=False, compute=c)
    cache.get("a", live=False, compute=a)
    cache.get("b", live=False, compute=b)
    assert (a_calls[0], b_calls[0], c_calls[0]) == (1, 2, 1)


def test_simultaneous_requests_share_one_computation() -> None:
    cache: ResponseCache[str] = ResponseCache(4, 15.0)
    calls = [0]
    started = threading.Event()

    def slow() -> str:
        calls[0] += 1
        started.set()
        time.sleep(0.2)
        return "laps"

    results: list[str] = []
    threads = [
        threading.Thread(target=lambda: results.append(cache.get("race", live=False, compute=slow)))
        for _ in range(5)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert started.is_set()
    assert calls[0] == 1
    assert results == ["laps"] * 5


def test_a_failed_computation_is_not_kept() -> None:
    cache: ResponseCache[str] = ResponseCache(4, 15.0, Clock())

    def broken() -> str:
        raise RuntimeError("store unreachable")

    with pytest.raises(RuntimeError):
        cache.get("race", live=False, compute=broken)
    calls, compute = counting("laps")
    assert cache.get("race", live=False, compute=compute) == "laps"
    assert calls[0] == 1


def test_laps_and_circuit_are_computed_once_per_finished_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"laps": 0, "circuit": 0}
    import f1_kernel

    real_lap_frame = f1_kernel.lap_frame
    real_session_frames = f1_kernel.session_frames

    def lap_frame(*args: Any, **kwargs: Any) -> Any:
        calls["laps"] += 1
        return real_lap_frame(*args, **kwargs)

    def session_frames(*args: Any, **kwargs: Any) -> Any:
        calls["circuit"] += 1
        return real_session_frames(*args, **kwargs)

    monkeypatch.setattr(session_routes, "lap_frame", lap_frame)
    monkeypatch.setattr(session_routes, "session_frames", session_frames)
    key = client.get("/sessions").json()[0]["key"]
    first_laps = client.get(f"/sessions/{key}/laps")
    first_circuit = client.get(f"/sessions/{key}/circuit")
    assert client.get(f"/sessions/{key}/laps").json() == first_laps.json()
    assert client.get(f"/sessions/{key}/circuit").json() == first_circuit.json()
    assert calls == {"laps": 1, "circuit": 1}
