"""`SessionRouter`: one live connection, several sessions.

The regression it exists for: the recorder ran from 2026 Azerbaijan FP2 to
the race on one connection, and every record after its first snapshot was
filed under "practice_2". F1 announces a new session over the open socket
with a `SessionInfo` keyframe, then streams keyframes for everything else;
the payloads below are cut down from that real recording (records 35253 ->
38105), not invented.
"""

from __future__ import annotations

from typing import Any

from pydantic import JsonValue

from f1_domain import (
    DomainEvent,
    DriverListUpdated,
    PositionChanged,
    SessionInfoUpdated,
    SessionKey,
    SessionStatusChanged,
    SessionType,
)
from f1_pipeline import Pipeline, SessionRouter, run, run_routed
from f1_pipeline.replay_source import replay_records
from f1_pipeline.verify import RECORDING_DIR, SESSION_KEY
from f1_recording import RawRecord
from f1_store import MemorySink

BAKU_FP2 = SessionKey(year=2026, round=15, session_type=SessionType.PRACTICE_2)
BAKU_FP3 = SessionKey(year=2026, round=15, session_type=SessionType.PRACTICE_3)
BAKU_RACE = SessionKey(year=2026, round=15, session_type=SessionType.RACE)

_MEETING = {
    "Key": 1295,
    "Name": "Azerbaijan Grand Prix",
    "Location": "Baku",
    "Number": 15,
    "Circuit": {"Key": 144, "ShortName": "Baku"},
}


def _session_info(key: int, name: str, kind: str, day: str, start: str) -> dict[str, Any]:
    return {
        "Meeting": _MEETING,
        "SessionStatus": "Inactive",
        "Key": key,
        "Type": kind,
        "Name": name,
        "StartDate": start,
        "Path": f"2026/2026-09-26_Azerbaijan_Grand_Prix/{day}_{name.replace(' ', '_')}/",
        "_kf": True,
    }


FP2_INFO = _session_info(11371, "Practice 2", "Practice", "2026-09-24", "2026-09-24T16:00:00")
FP3_INFO = _session_info(11372, "Practice 3", "Practice", "2026-09-25", "2026-09-25T12:30:00")
RACE_INFO = _session_info(11377, "Race", "Race", "2026-09-26", "2026-09-26T15:00:00")

# FP2 was long over when the recorder started: its snapshot says so.
FP2_SNAPSHOT: dict[str, Any] = {
    "SessionInfo": FP2_INFO,
    "SessionData": {
        "StatusSeries": [
            {"Utc": "2026-09-24T12:00:00Z", "SessionStatus": "Started"},
            {"Utc": "2026-09-24T13:00:00Z", "SessionStatus": "Finished"},
            {"Utc": "2026-09-24T13:10:00Z", "SessionStatus": "Finalised"},
            {"Utc": "2026-09-24T13:30:00Z", "SessionStatus": "Ends"},
        ]
    },
    "DriverList": {"1": {"Tla": "NOR", "TeamName": "McLaren", "Line": 1}},
}


def _driver_list(order: list[tuple[str, str, str]]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        number: {"RacingNumber": number, "Tla": tla, "TeamName": team, "Line": line}
        for line, (number, tla, team) in enumerate(order, start=1)
    }
    payload["_kf"] = True
    return payload


def _session_keyframes(info: dict[str, Any], order: list[tuple[str, str, str]]) -> list[Any]:
    """What F1 streams, in this order, within ~10 s of a new session."""
    return [
        ("SessionInfo", info),
        # The series restarts per session; here it only carries track status.
        (
            "SessionData",
            {
                "Series": [],
                "StatusSeries": [{"Utc": "2026-09-26T10:04:20.29Z", "TrackStatus": "Yellow"}],
                "_kf": True,
            },
        ),
        ("SessionStatus", {"Status": "Inactive", "Started": "Inactive", "_kf": True}),
        ("LapCount", {"CurrentLap": 1, "TotalLaps": 51, "_kf": True}),
        ("DriverList", _driver_list(order)),
    ]


RACE_ORDER = [("63", "RUS", "Mercedes"), ("1", "NOR", "McLaren"), ("16", "LEC", "Ferrari")]
FP3_ORDER = [("1", "NOR", "McLaren"), ("63", "RUS", "Mercedes"), ("16", "LEC", "Ferrari")]


def _weekend() -> list[RawRecord]:
    """FP2's snapshot, a quiet night of reconnects, FP3 announced and run
    to its end, then the race announced and started — one connection."""
    items: list[Any] = [
        ("_meta.subscribe_snapshot", FP2_SNAPSHOT),
        ("_meta.reconnect", {"attempt": 1}),
        ("_meta.subscribe_snapshot", FP2_SNAPSHOT),
        *_session_keyframes(FP3_INFO, FP3_ORDER),
        (
            "SessionData",
            {"StatusSeries": {"2": {"Utc": "2026-09-25T08:30:00Z", "SessionStatus": "Started"}}},
        ),
        (
            "SessionData",
            {"StatusSeries": {"45": {"Utc": "2026-09-25T09:30:00Z", "SessionStatus": "Finished"}}},
        ),
        (
            "SessionData",
            {"StatusSeries": {"49": {"Utc": "2026-09-25T09:47:32Z", "SessionStatus": "Ends"}}},
        ),
        *_session_keyframes(RACE_INFO, RACE_ORDER),
        # Index 8, far below where FP3's series ended (49): a machine carried
        # over from FP3 skips it, which is how the race never went live.
        (
            "SessionData",
            {
                "StatusSeries": {
                    "8": {"Utc": "2026-09-26T11:03:50.711Z", "SessionStatus": "Started"}
                }
            },
        ),
        ("Heartbeat", {"Utc": "2026-09-26T11:04:00Z"}),
    ]
    return [
        RawRecord(seq=i, ts_wall=1.79e9 + i, ts_mono=i * 1_000_000, topic=topic, payload=payload)
        for i, (topic, payload) in enumerate(items)
    ]


def _of(events: list[DomainEvent], key: SessionKey) -> list[DomainEvent]:
    return [e for e in events if e.session_key == key]


def test_each_announced_session_gets_its_own_key() -> None:
    sink = MemorySink()
    metrics = run_routed(_weekend(), sink)

    assert list(metrics) == [BAKU_FP2, BAKU_FP3, BAKU_RACE]
    # FP2 only ever had its (already-over) snapshot: nothing is written
    # under it, least of all a later session's timing.
    assert _of(sink.events, BAKU_FP2) == []
    assert _of(sink.events, BAKU_FP3)
    assert _of(sink.events, BAKU_RACE)


def test_the_race_announces_its_start_under_its_own_key() -> None:
    """The failure end to end: the race's "Started" must be emitted, and
    under the race."""
    sink = MemorySink()
    run_routed(_weekend(), sink)

    race_statuses = [
        e.status for e in _of(sink.events, BAKU_RACE) if isinstance(e, SessionStatusChanged)
    ]
    assert race_statuses == ["Started"]
    fp3_statuses = [
        e.status for e in _of(sink.events, BAKU_FP3) if isinstance(e, SessionStatusChanged)
    ]
    assert fp3_statuses == ["Started", "Finished", "Ends"]


def test_a_new_session_publishes_its_own_roster_order_and_venue() -> None:
    sink = MemorySink()
    run_routed(_weekend(), sink)
    race = _of(sink.events, BAKU_RACE)

    rosters = [e for e in race if isinstance(e, DriverListUpdated)]
    assert [[d.code for d in r.drivers or []] for r in rosters] == [["NOR", "LEC", "RUS"]]
    order = {e.driver: e.position for e in race if isinstance(e, PositionChanged)}
    assert order == {"63": 1, "1": 2, "16": 3}
    venues = [e for e in race if isinstance(e, SessionInfoUpdated)]
    assert [(v.meeting_name, v.circuit_short_name) for v in venues] == [
        ("Azerbaijan Grand Prix", "Baku")
    ]


def test_the_live_status_row_follows_each_session() -> None:
    """`sessions.status` decides liveness, so it is rewritten on every status
    change rather than only at shutdown, and each session gets its own."""
    written: list[tuple[SessionKey, str | None]] = []

    class Recording(MemorySink):
        def write_state(self, state: Any) -> None:
            written.append((state.session_key, state.status))
            super().write_state(state)

    router = SessionRouter(Recording())
    for record in _weekend():
        router.process(record)
        if record.topic == "Heartbeat":
            # Mid-race, before anything shuts down: the race row says so.
            assert written[-1] == (BAKU_RACE, "Started")
    router.finalize()

    # FP3's row ends on its own final status when the race takes over.
    assert (BAKU_FP3, "Ends") in written
    assert written[-1] == (BAKU_RACE, "Started")


def test_a_reconnect_after_an_announced_session_started_is_silent() -> None:
    """A mid-race reconnect re-seeds silently, as it does for any session:
    the announcement's keyframe burst was the baseline, so the reconnect's
    snapshot must not re-publish the whole running order a second time."""
    sink = MemorySink()
    router = SessionRouter(sink)
    for record in _weekend():
        router.process(record)
    race_snapshot: dict[str, Any] = {
        "SessionInfo": RACE_INFO,
        "DriverList": {n: {"Tla": t, "Line": i} for i, (n, t, _) in enumerate(RACE_ORDER, 1)},
    }
    before = len(sink.events)
    router.seed(race_snapshot)
    heartbeat: JsonValue = {"Utc": "2026-09-26T11:05:00Z"}
    router.process(
        RawRecord(seq=999, ts_wall=1.8e9, ts_mono=0, topic="Heartbeat", payload=heartbeat)
    )
    assert [e.kind for e in sink.events[before:]] == ["unknown"]


def test_a_reconnect_into_the_same_session_keeps_its_pipeline() -> None:
    router = SessionRouter(MemorySink())
    router.seed({"SessionInfo": RACE_INFO})
    first = router.pipeline
    router.seed({"SessionInfo": {**RACE_INFO, "SessionStatus": "Started"}})
    assert router.pipeline is first
    assert router.session_key == BAKU_RACE


def test_a_snapshot_naming_a_different_session_switches_to_it() -> None:
    """Reconnecting after missing the announcement (the process was down)
    still lands on the session the feed says is running."""
    router = SessionRouter(MemorySink())
    router.seed(FP2_SNAPSHOT)
    router.seed({"SessionInfo": RACE_INFO})
    assert router.session_key == BAKU_RACE


def test_a_partial_session_info_delta_does_not_rekey() -> None:
    router = SessionRouter(MemorySink())
    router.seed({"SessionInfo": {k: v for k, v in RACE_INFO.items() if k != "_kf"}})
    rec = RawRecord(
        seq=1,
        ts_wall=1.79e9,
        ts_mono=0,
        topic="SessionInfo",
        payload={"ArchiveStatus": {"Status": "Complete"}},
    )
    router.process(rec)
    assert router.session_key == BAKU_RACE


def test_explicit_overrides_pin_the_key() -> None:
    sink = MemorySink()
    metrics = run_routed(_weekend(), sink, year=2026, round_number=15, session_type="race")
    assert list(metrics) == [BAKU_RACE]
    assert {e.session_key for e in sink.events} == {BAKU_RACE}


def test_records_before_any_session_are_dropped_not_guessed() -> None:
    router = SessionRouter(MemorySink())
    payload: JsonValue = {"Utc": "2026-09-26T11:04:00Z"}
    rec = RawRecord(seq=1, ts_wall=1.79e9, ts_mono=0, topic="Heartbeat", payload=payload)
    assert router.process(rec) == []
    assert router.session_key is None


def test_routing_a_single_session_recording_changes_nothing() -> None:
    """The checked-in race recording names one session throughout, so the
    router must produce exactly what the pinned `run` does."""
    records = list(replay_records(RECORDING_DIR, speed=0))
    pinned = MemorySink()
    run(records, pinned, session_key=SESSION_KEY)
    routed = MemorySink()
    metrics = run_routed(records, routed, year=SESSION_KEY.year, round_number=SESSION_KEY.round)

    assert list(metrics) == [SESSION_KEY]
    assert [e.model_dump_json() for e in routed.events] == [
        e.model_dump_json() for e in pinned.events
    ]


def test_a_pipeline_writes_its_status_row_on_a_status_change() -> None:
    sink = MemorySink()
    pipeline = Pipeline(BAKU_RACE, sink)
    payload: JsonValue = {
        "StatusSeries": {"8": {"Utc": "2026-09-26T11:03:50.711Z", "SessionStatus": "Started"}}
    }
    pipeline.process(
        RawRecord(seq=1, ts_wall=1.79e9, ts_mono=0, topic="SessionData", payload=payload)
    )
    assert sink.state is not None
    assert sink.state.status == "Started"
