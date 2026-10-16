"""Calls on the stream and over REST (ROADMAP W5), against the checked-in
fixture races.

The stream property: replayed with a fake clock, every `call` envelope is
stamped no earlier than the replayed crossing that locked it, nor (once
resolved) than the crossing that settled it. Delay Sync releases on that
stamp, so a call can never reach the screen before the viewer's clock
reaches what it is about."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest
from api_fixture import GREEN_SESSION_KEY, HISTORICAL_ROOT, SESSION_KEY
from fastapi.testclient import TestClient

from f1_api.calls import CALL_KIND, enabled_call_kinds
from f1_api.config import Settings
from f1_api.main import create_app
from f1_api.stream.replay import replay_envelopes
from f1_domain import SessionKey
from f1_kernel import (
    CLEARED_CALL_KINDS,
    CallKind,
    HistoricalSource,
    KernelParams,
    call_ledger,
    race_distance_laps,
    session_frames,
)

BOTH = frozenset({"undercut", "rejoin"})
FAKE_WALL_START = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def _key(path: str) -> SessionKey:
    year, rnd, kind = path.split("-")
    return SessionKey.model_validate({"year": int(year), "round": int(rnd), "session_type": kind})


def test_only_cleared_kinds_are_served_by_default(settings: Settings) -> None:
    assert enabled_call_kinds(settings) == tuple(sorted(CLEARED_CALL_KINDS))
    assert enabled_call_kinds(dataclasses.replace(settings, call_kinds=frozenset())) == ()
    assert enabled_call_kinds(dataclasses.replace(settings, call_kinds=BOTH)) == (
        CallKind.REJOIN,
        CallKind.UNDERCUT,
    )


def test_the_ledger_route_serves_nothing_until_a_kind_has_cleared(client: TestClient) -> None:
    body = client.get(f"/sessions/{SESSION_KEY}/calls").json()

    assert body["kinds"] == sorted(k.value for k in CLEARED_CALL_KINDS)
    if not CLEARED_CALL_KINDS:
        assert body["calls"] == []


@pytest.mark.parametrize("path", [SESSION_KEY, GREEN_SESSION_KEY])
def test_the_whole_race_ledger_is_the_kernels(settings: Settings, path: str) -> None:
    client = TestClient(create_app(dataclasses.replace(settings, call_kinds=BOTH)))
    key = _key(path)
    source = HistoricalSource(HISTORICAL_ROOT)
    frames = session_frames(source, key)
    distance = race_distance_laps(
        source.events(key, kinds=["lap_counter_updated", "lap_completed"])
    )
    expected = call_ledger(frames, KernelParams(), total_laps=distance)

    body = client.get(f"/sessions/{path}/calls").json()

    assert body["final"] is True
    assert [c["call_id"] for c in body["calls"]] == [c.call_id for c in expected]
    assert [c["status"] for c in body["calls"]] == [c.status.value for c in expected]
    card = body["scorecard"]
    assert card["right"] == sum(1 for c in expected if c.status.value == "right")
    assert card["wrong"] == sum(1 for c in expected if c.status.value == "wrong")


def test_upto_leaves_out_everything_past_the_lap_watched(settings: Settings) -> None:
    client = TestClient(create_app(dataclasses.replace(settings, call_kinds=BOTH)))
    final = client.get(f"/sessions/{SESSION_KEY}/calls").json()["calls"]
    first_lock = min(c["in_lap"] for c in final)

    before = client.get(f"/sessions/{SESSION_KEY}/calls", params={"upto": first_lock - 1}).json()
    during = client.get(f"/sessions/{SESSION_KEY}/calls", params={"upto": first_lock + 1}).json()

    assert before["calls"] == [] and before["final"] is False
    assert during["calls"], "a call locked by then should be listed"
    for call in during["calls"]:
        # Locked no later than the laps watched (a rejoin at its in-lap, an
        # undercut at its out-lap).
        assert call["in_lap"] <= first_lock + 1
        if call["kind"] == "undercut":
            assert call["in_lap"] + 1 <= first_lock + 1
        # Nothing it reports happened past the last lap watched.
        if call["rival_stop_lap"] is not None and call["status"] in ("right", "wrong"):
            assert call["rival_stop_lap"] + 1 <= first_lock + 1


@pytest.mark.parametrize("path", [SESSION_KEY, GREEN_SESSION_KEY])
def test_no_call_envelope_is_stamped_before_what_it_is_about(path: str) -> None:
    key = _key(path)
    source = HistoricalSource(HISTORICAL_ROOT)
    fake = _FakeClock()
    envelopes = list(
        replay_envelopes(
            source,
            key,
            speed=50,
            clock=fake.now,
            sleep=fake.sleep,
            wall_clock=lambda: FAKE_WALL_START,
            call_kinds=(CallKind.UNDERCUT, CallKind.REJOIN),
        )
    )
    # Each lap's crossing, as the replay re-timed it.
    crossing = {
        datetime.fromisoformat(e.payload["ts_utc"]): e.event_time_utc
        for e in envelopes
        if e.kind == "lap_completed"
    }
    distance = race_distance_laps(
        source.events(key, kinds=["lap_counter_updated", "lap_completed"])
    )
    truth = {
        c.call_id: c
        for c in call_ledger(session_frames(source, key), KernelParams(), total_laps=distance)
    }
    calls = [e for e in envelopes if e.kind == CALL_KIND]

    assert calls, "the fixture race streamed no calls at all"
    seen: dict[str, str] = {}
    for envelope in calls:
        call = envelope.payload
        kernel = truth[call["call_id"]]
        assert envelope.event_time_utc >= crossing[kernel.locked_at_utc]
        if call["status"] != "pending":
            assert kernel.resolved_at_utc is not None
            assert envelope.event_time_utc >= crossing[kernel.resolved_at_utc]
            assert call["status"] == kernel.status.value
        # Sent when new or changed, and a resolved call is never re-sent.
        assert seen.get(call["call_id"]) in (None, "pending")
        seen[call["call_id"]] = call["status"]
    # By the end of the stream every call has settled, as in the kernel.
    assert seen == {call_id: c.status.value for call_id, c in truth.items()}


def test_the_stream_sends_no_calls_when_no_kind_is_enabled() -> None:
    fake = _FakeClock()
    envelopes = replay_envelopes(
        HistoricalSource(HISTORICAL_ROOT),
        _key(SESSION_KEY),
        speed=0,
        clock=fake.now,
        sleep=fake.sleep,
        wall_clock=lambda: FAKE_WALL_START,
    )

    assert not [e for e in envelopes if e.kind == CALL_KIND]


def test_kernel_snapshots_carry_every_cars_rejoin_projection() -> None:
    fake = _FakeClock()
    envelopes = replay_envelopes(
        HistoricalSource(HISTORICAL_ROOT),
        _key(SESSION_KEY),
        speed=0,
        clock=fake.now,
        sleep=fake.sleep,
        wall_clock=lambda: FAKE_WALL_START,
        call_kinds=(CallKind.REJOIN,),
    )
    snapshot = next(e for e in envelopes if e.kind == "kernel_snapshot")

    assert snapshot.payload["call_kinds"] == ["rejoin"]
    rejoins = snapshot.payload["rejoins"]
    assert rejoins
    assert {"driver", "position", "car_ahead", "margin_s", "confidence"} <= set(rejoins[0])
    assert [r["position_now"] for r in rejoins] == sorted(r["position_now"] for r in rejoins)


def test_no_rejoin_projection_reaches_the_stream_unless_rejoin_calls_are_served() -> None:
    """The projection is the rejoin call: a kind that has not passed its
    held-out backtest never reaches a screen (ROADMAP W5)."""
    fake = _FakeClock()
    envelopes = replay_envelopes(
        HistoricalSource(HISTORICAL_ROOT),
        _key(SESSION_KEY),
        speed=0,
        clock=fake.now,
        sleep=fake.sleep,
        wall_clock=lambda: FAKE_WALL_START,
        call_kinds=(CallKind.UNDERCUT,),
    )
    snapshots = [e for e in envelopes if e.kind == "kernel_snapshot"]

    assert snapshots
    assert all(e.payload["rejoins"] == [] for e in snapshots)
    assert all(e.payload["call_kinds"] == ["undercut"] for e in snapshots)
