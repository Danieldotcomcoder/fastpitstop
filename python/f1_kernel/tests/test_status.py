"""The track-status timeline: both ways of building it, and the modelling
decisions that change which laps get excluded."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from f1_domain import (
    DomainEvent,
    Provenance,
    RaceControlMessage,
    SessionKey,
    SessionType,
    TrackStatusChanged,
    TrackStatusKind,
)
from f1_kernel import status_during, statuses_during, track_status_timeline

KEY = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
T0 = datetime(2024, 3, 2, 15, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def rc(
    seconds: float,
    *,
    category: str,
    message: str,
    flag: str | None = None,
    scope: str | None = None,
    seq: int = 0,
) -> RaceControlMessage:
    return RaceControlMessage(
        session_key=KEY,
        ts_utc=at(seconds),
        source_seq=seq,
        provenance=Provenance.HISTORICAL,
        category=category,
        message=message,
        flag=flag,
        scope=scope,
    )


def ts(seconds: float, status: TrackStatusKind, *, seq: int = 0) -> TrackStatusChanged:
    return TrackStatusChanged(session_key=KEY, ts_utc=at(seconds), source_seq=seq, status=status)


# ---------------------------------------------------------------------------
# from the feed's own track-status topic (live / replay)
# ---------------------------------------------------------------------------


def test_track_status_events_build_intervals() -> None:
    events: list[DomainEvent] = [
        ts(0, TrackStatusKind.GREEN, seq=1),
        ts(100, TrackStatusKind.SC, seq=2),
        ts(400, TrackStatusKind.GREEN, seq=3),
    ]

    timeline = track_status_timeline(events)

    assert len(timeline) == 1
    assert timeline[0].status is TrackStatusKind.SC
    assert timeline[0].start_utc == at(100)
    assert timeline[0].end_utc == at(400)
    assert timeline[0].source == "track_status"


def test_repeated_status_messages_do_not_split_an_interval() -> None:
    """The feed re-sends constantly; a re-send is not a new safety car."""
    events: list[DomainEvent] = [
        ts(100, TrackStatusKind.SC, seq=1),
        ts(150, TrackStatusKind.SC, seq=2),
        ts(400, TrackStatusKind.GREEN, seq=3),
    ]

    timeline = track_status_timeline(events)

    assert len(timeline) == 1
    assert timeline[0].start_utc == at(100)


def test_an_unfinished_status_stays_open() -> None:
    """A red flag that never clears before the stream ends still covers
    every lap after it."""
    timeline = track_status_timeline([ts(100, TrackStatusKind.RED, seq=1)])

    assert timeline[0].end_utc is None
    assert status_during(timeline, at(500), at(600)) is TrackStatusKind.RED


def test_track_status_is_preferred_when_present() -> None:
    """Mixing the two sources would double-count. When the feed's own signal
    exists, race control is not consulted."""
    events: list[DomainEvent] = [
        ts(100, TrackStatusKind.SC, seq=1),
        ts(200, TrackStatusKind.GREEN, seq=2),
        rc(50, category="SafetyCar", message="VIRTUAL SAFETY CAR DEPLOYED", seq=3),
    ]

    timeline = track_status_timeline(events)

    assert [interval.status for interval in timeline] == [TrackStatusKind.SC]


# ---------------------------------------------------------------------------
# reconstructed from race control (historical)
# ---------------------------------------------------------------------------


def test_safety_car_reconstructed_from_race_control() -> None:
    events: list[DomainEvent] = [
        rc(100, category="SafetyCar", message="SAFETY CAR DEPLOYED", seq=1),
        rc(400, category="SafetyCar", message="SAFETY CAR IN THIS LAP", seq=2),
        rc(
            460,
            category="Flag",
            flag="CLEAR",
            scope="Track",
            message="TRACK CLEAR",
            seq=3,
        ),
    ]

    timeline = track_status_timeline(events)

    assert len(timeline) == 1
    assert timeline[0].status is TrackStatusKind.SC
    assert timeline[0].source == "race_control"
    assert timeline[0].start_utc == at(100)


def test_in_this_lap_does_not_end_the_safety_car() -> None:
    """It announces the car comes in at the END of the current lap. Closing
    the interval there would mark the rest of that lap green."""
    events: list[DomainEvent] = [
        rc(100, category="SafetyCar", message="SAFETY CAR DEPLOYED", seq=1),
        rc(400, category="SafetyCar", message="SAFETY CAR IN THIS LAP", seq=2),
        rc(
            460,
            category="Flag",
            flag="CLEAR",
            scope="Track",
            message="TRACK CLEAR",
            seq=3,
        ),
    ]

    timeline = track_status_timeline(events)

    assert status_during(timeline, at(410), at(450)) is TrackStatusKind.SC
    assert timeline[0].end_utc == at(460)


def test_virtual_safety_car_ends_immediately_when_announced() -> None:
    events: list[DomainEvent] = [
        rc(100, category="SafetyCar", message="VIRTUAL SAFETY CAR DEPLOYED", seq=1),
        rc(200, category="SafetyCar", message="VIRTUAL SAFETY CAR ENDING", seq=2),
    ]

    timeline = track_status_timeline(events)

    assert timeline[0].status is TrackStatusKind.VSC
    assert timeline[0].end_utc == at(200)
    assert status_during(timeline, at(250), at(300)) is TrackStatusKind.GREEN


def test_sector_yellows_are_tracked_as_a_set() -> None:
    """Two sectors go yellow; the track leaves yellow only when the LAST one
    clears, not the first."""
    events: list[DomainEvent] = [
        rc(
            100,
            category="Flag",
            flag="YELLOW",
            scope="Sector",
            message="YELLOW IN TRACK SECTOR 2",
            seq=1,
        ),
        rc(
            120,
            category="Flag",
            flag="YELLOW",
            scope="Sector",
            message="YELLOW IN TRACK SECTOR 7",
            seq=2,
        ),
        rc(
            200,
            category="Flag",
            flag="CLEAR",
            scope="Sector",
            message="CLEAR IN TRACK SECTOR 2",
            seq=3,
        ),
        rc(
            300,
            category="Flag",
            flag="CLEAR",
            scope="Sector",
            message="CLEAR IN TRACK SECTOR 7",
            seq=4,
        ),
    ]

    timeline = track_status_timeline(events)

    assert len(timeline) == 1
    assert timeline[0].status is TrackStatusKind.YELLOW
    assert timeline[0].end_utc == at(300)
    assert status_during(timeline, at(210), at(250)) is TrackStatusKind.YELLOW


def test_a_track_wide_clear_ends_every_sector_yellow() -> None:
    """Race control sometimes calls TRACK CLEAR without clearing each sector
    individually; the timeline must not stay yellow for the rest of the race."""
    events: list[DomainEvent] = [
        rc(
            100,
            category="Flag",
            flag="YELLOW",
            scope="Sector",
            message="YELLOW IN TRACK SECTOR 2",
            seq=1,
        ),
        rc(
            200,
            category="Flag",
            flag="CLEAR",
            scope="Track",
            message="TRACK CLEAR",
            seq=2,
        ),
    ]

    timeline = track_status_timeline(events)

    assert timeline[0].end_utc == at(200)
    assert status_during(timeline, at(300), at(400)) is TrackStatusKind.GREEN


def test_driver_scoped_flags_are_ignored() -> None:
    """Blue and black-and-white flags are addressed to one car and say
    nothing about the track."""
    events: list[DomainEvent] = [
        rc(
            100,
            category="Flag",
            flag="BLUE",
            scope="Driver",
            message="WAVED BLUE FLAG FOR CAR 2",
            seq=1,
        ),
        rc(
            150,
            category="Flag",
            flag="BLACK AND WHITE",
            scope="Driver",
            message="TRACK LIMITS",
            seq=2,
        ),
    ]

    assert track_status_timeline(events) == []


def test_a_red_flag_ends_an_ongoing_safety_car() -> None:
    """2024 round 21 (Sao Paulo): the safety car was deployed and then the
    race was RED-FLAGGED, so the car never "came in". Leaving the interval
    open would neutralize the whole rest of the race."""
    events: list[DomainEvent] = [
        rc(100, category="SafetyCar", message="SAFETY CAR DEPLOYED", seq=1),
        rc(
            400,
            category="Flag",
            flag="RED",
            scope="Track",
            message="RED FLAG",
            seq=2,
        ),
        rc(
            1800,
            category="Flag",
            flag="CLEAR",
            scope="Track",
            message="TRACK CLEAR",
            seq=3,
        ),
    ]

    timeline = track_status_timeline(events)

    by_status = {interval.status: interval for interval in timeline}
    assert by_status[TrackStatusKind.SC].end_utc == at(400)
    assert by_status[TrackStatusKind.RED].end_utc == at(1800)
    assert status_during(timeline, at(2000), at(2100)) is TrackStatusKind.GREEN


def test_a_safety_car_ends_at_the_track_clear_even_without_in_this_lap() -> None:
    """The safety car can end some other way (a red flag, the chequered
    flag). Across every 2024 race, TRACK CLEAR is only called once the car
    is already in, so it is safe to treat it as the end."""
    events: list[DomainEvent] = [
        rc(100, category="SafetyCar", message="SAFETY CAR DEPLOYED", seq=1),
        rc(
            400,
            category="Flag",
            flag="CLEAR",
            scope="Track",
            message="TRACK CLEAR",
            seq=2,
        ),
    ]

    timeline = track_status_timeline(events)

    assert timeline[0].end_utc == at(400)
    assert status_during(timeline, at(500), at(600)) is TrackStatusKind.GREEN


def test_a_full_safety_car_supersedes_a_virtual_one() -> None:
    """2024 rounds 5 and 23: a VSC upgraded to a full safety car seconds
    later. The VSC period ended there; it did not run alongside."""
    events: list[DomainEvent] = [
        rc(100, category="SafetyCar", message="VIRTUAL SAFETY CAR DEPLOYED", seq=1),
        rc(120, category="SafetyCar", message="SAFETY CAR DEPLOYED", seq=2),
        rc(400, category="SafetyCar", message="SAFETY CAR IN THIS LAP", seq=3),
        rc(
            460,
            category="Flag",
            flag="CLEAR",
            scope="Track",
            message="TRACK CLEAR",
            seq=4,
        ),
    ]

    timeline = track_status_timeline(events)

    by_status = {interval.status: interval for interval in timeline}
    assert by_status[TrackStatusKind.VSC].end_utc == at(120)
    assert by_status[TrackStatusKind.SC].end_utc == at(460)
    assert status_during(timeline, at(200), at(300)) is TrackStatusKind.SC
    assert statuses_during(timeline, at(200), at(300)) == [TrackStatusKind.SC]


def test_safety_car_through_the_pit_lane_changes_nothing() -> None:
    """2024 round 23 sends this mid-period; it is a routing instruction to
    the safety car, not a change of track state."""
    events: list[DomainEvent] = [
        rc(100, category="SafetyCar", message="SAFETY CAR DEPLOYED", seq=1),
        rc(200, category="SafetyCar", message="SAFETY CAR THROUGH THE PIT LANE", seq=2),
        rc(
            400,
            category="Flag",
            flag="CLEAR",
            scope="Track",
            message="TRACK CLEAR",
            seq=3,
        ),
    ]

    timeline = track_status_timeline(events)

    assert len(timeline) == 1
    assert timeline[0].start_utc == at(100)
    assert timeline[0].end_utc == at(400)


def test_red_flag_is_reconstructed() -> None:
    events: list[DomainEvent] = [
        rc(
            100,
            category="Flag",
            flag="RED",
            scope="Track",
            message="RED FLAG",
            seq=1,
        ),
        rc(
            900,
            category="Flag",
            flag="CLEAR",
            scope="Track",
            message="TRACK CLEAR",
            seq=2,
        ),
    ]

    timeline = track_status_timeline(events)

    assert timeline[0].status is TrackStatusKind.RED
    assert status_during(timeline, at(200), at(300)) is TrackStatusKind.RED


# ---------------------------------------------------------------------------
# querying a window
# ---------------------------------------------------------------------------


def test_the_most_severe_overlapping_status_wins() -> None:
    events: list[DomainEvent] = [
        rc(
            100,
            category="Flag",
            flag="YELLOW",
            scope="Sector",
            message="YELLOW IN TRACK SECTOR 2",
            seq=1,
        ),
        rc(110, category="SafetyCar", message="SAFETY CAR DEPLOYED", seq=2),
        rc(
            300,
            category="Flag",
            flag="CLEAR",
            scope="Track",
            message="TRACK CLEAR",
            seq=3,
        ),
    ]

    timeline = track_status_timeline(events)

    assert status_during(timeline, at(100), at(200)) is TrackStatusKind.SC
    assert statuses_during(timeline, at(100), at(200)) == [
        TrackStatusKind.SC,
        TrackStatusKind.YELLOW,
    ]


def test_a_window_touching_nothing_is_green() -> None:
    timeline = track_status_timeline(
        [
            rc(100, category="SafetyCar", message="SAFETY CAR DEPLOYED", seq=1),
            rc(
                200,
                category="Flag",
                flag="CLEAR",
                scope="Track",
                message="TRACK CLEAR",
                seq=2,
            ),
        ]
    )

    assert status_during(timeline, at(300), at(400)) is TrackStatusKind.GREEN
    assert statuses_during(timeline, at(300), at(400)) == []


def test_touching_endpoints_do_not_count_as_overlap() -> None:
    """A status that begins exactly as a lap ends did not affect that lap."""
    timeline = track_status_timeline(
        [
            rc(200, category="SafetyCar", message="SAFETY CAR DEPLOYED", seq=1),
            rc(
                300,
                category="Flag",
                flag="CLEAR",
                scope="Track",
                message="TRACK CLEAR",
                seq=2,
            ),
        ]
    )

    assert status_during(timeline, at(100), at(200)) is TrackStatusKind.GREEN
    assert status_during(timeline, at(300), at(400)) is TrackStatusKind.GREEN
    assert status_during(timeline, at(199), at(201)) is TrackStatusKind.SC


def test_chequered_is_not_a_restriction() -> None:
    """It is an instant, not a state: the final lap of a race is a normal
    racing lap and must not be excluded as non-green."""
    timeline = track_status_timeline([ts(1000, TrackStatusKind.CHEQUERED, seq=1)])

    assert status_during(timeline, at(900), at(1100)) is TrackStatusKind.GREEN
    assert statuses_during(timeline, at(900), at(1100)) == []


def test_no_status_information_at_all_is_green() -> None:
    assert track_status_timeline([]) == []
    assert status_during([], at(0), at(100)) is TrackStatusKind.GREEN
