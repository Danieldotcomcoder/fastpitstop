"""Event sources: the filters must be a pushdown, never a different answer.

If `events(kinds=...)` returned anything other than the full stream
filtered the same way, the kernel's "one code path over live and historical"
guarantee would be quietly false — a lap frame built with a kind filter
would differ from one built without.
"""

from __future__ import annotations

import pytest
from kernel_fixture import GREEN_RACE, HISTORICAL_ROOT, SC_RACE

from f1_domain import SessionKey, SessionType
from f1_kernel import HistoricalSource, MemorySource, lap_frame, list_historical_sessions
from f1_kernel.sources import LAP_FRAME_KINDS, session_type_from_label


def test_kind_filter_is_a_pushdown(source: HistoricalSource) -> None:
    everything = list(source.events(SC_RACE))
    filtered = list(source.events(SC_RACE, kinds=["lap_completed"]))

    expected = [event for event in everything if event.kind == "lap_completed"]
    assert len(filtered) == len(expected)
    assert [event.model_dump(exclude={"source_seq"}) for event in filtered] == [
        event.model_dump(exclude={"source_seq"}) for event in expected
    ]


def test_driver_filter_is_a_pushdown(source: HistoricalSource) -> None:
    everything = list(source.events(SC_RACE, kinds=["lap_completed"]))
    filtered = list(source.events(SC_RACE, kinds=["lap_completed"], drivers=["44"]))

    expected = [event for event in everything if getattr(event, "driver", None) == "44"]
    assert len(filtered) == len(expected)
    assert {event.lap_number for event in filtered} == {  # type: ignore[attr-defined]
        event.lap_number  # type: ignore[attr-defined]
        for event in expected
    }


def test_a_filtered_stream_builds_the_same_lap_frame(source: HistoricalSource) -> None:
    """The load path uses a kind filter; it must not change the frame."""
    from f1_kernel.frames import lap_frame_from_events

    unfiltered = lap_frame_from_events(source.events(SC_RACE), SC_RACE)
    filtered = lap_frame(source, SC_RACE)

    assert filtered.equals(unfiltered)


def test_lap_frame_kinds_cover_what_the_lap_frame_reads() -> None:
    """A kind missing from the pushdown set would silently drop a column's
    worth of data."""
    assert {
        "lap_completed",
        "stint_started",
        "pit_entry",
        "pit_exit",
        "position_changed",
        "gap_updated",
        "race_control_message",
        "track_status_changed",
    } <= LAP_FRAME_KINDS


def test_telemetry_is_excluded_unless_asked_for(source: HistoricalSource) -> None:
    """Telemetry is large and resampled; it must not ride along by accident."""
    lap_events = list(source.events(SC_RACE, kinds=LAP_FRAME_KINDS))

    assert not any(event.kind == "car_telemetry" for event in lap_events)
    assert any(
        event.kind == "car_telemetry"
        for event in source.events(SC_RACE, kinds=["car_telemetry"], drivers=["1"])
    )


def test_memory_source_filters_the_same_way(source: HistoricalSource) -> None:
    events = list(source.events(SC_RACE))
    memory = MemorySource(events, event_names={SC_RACE: "Bahrain Grand Prix"})

    assert [e.kind for e in memory.events(SC_RACE, kinds=["lap_completed"])] == [
        e.kind for e in events if e.kind == "lap_completed"
    ]
    assert memory.sessions()[0].event_name == "Bahrain Grand Prix"


def test_memory_source_ignores_other_sessions(source: HistoricalSource) -> None:
    events = list(source.events(SC_RACE)) + list(source.events(GREEN_RACE))
    memory = MemorySource(events)

    assert {ref.session_key for ref in memory.sessions()} == {SC_RACE, GREEN_RACE}
    assert all(e.session_key == SC_RACE for e in memory.events(SC_RACE))


def test_session_wide_events_survive_a_driver_filter(source: HistoricalSource) -> None:
    """Weather, track status and track-scope race control belong to every
    driver's frame — filtering to one driver must not drop them."""
    filtered = list(source.events(SC_RACE, drivers=["44"]))

    assert any(event.kind == "race_control_message" for event in filtered)


def test_missing_session_raises(source: HistoricalSource) -> None:
    missing = SessionKey(year=2024, round=99, session_type=SessionType.RACE)

    with pytest.raises(FileNotFoundError):
        list(source.events(missing))


def test_catalog_lists_sessions_with_event_names() -> None:
    sessions = list_historical_sessions(HISTORICAL_ROOT)

    assert [ref.label for ref in sessions] == ["2024 R01 race", "2024 R02 race"]
    assert all(ref.event_name for ref in sessions)


def test_catalog_can_be_filtered() -> None:
    assert list_historical_sessions(HISTORICAL_ROOT, session_types=[SessionType.QUALIFYING]) == []
    assert len(list_historical_sessions(HISTORICAL_ROOT, seasons=[2024])) == 2
    assert list_historical_sessions(HISTORICAL_ROOT, seasons=[2023]) == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Race", SessionType.RACE),
        ("race", SessionType.RACE),
        ("Practice 1", SessionType.PRACTICE_1),
        ("Practice_1", SessionType.PRACTICE_1),
        ("Sprint Qualifying", SessionType.SPRINT_QUALIFYING),
        ("Sprint Shootout", SessionType.SPRINT_QUALIFYING),
        ("sprint_qualifying", SessionType.SPRINT_QUALIFYING),
        ("Qualifying", SessionType.QUALIFYING),
        ("something else", SessionType.UNKNOWN),
    ],
)
def test_session_type_aliases(raw: str, expected: SessionType) -> None:
    """The catalog spells session types FastF1's way and the database spells
    them the enum's way; both must land on the same type."""
    assert session_type_from_label(raw) == expected
