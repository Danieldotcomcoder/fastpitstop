"""SessionStateMachine transition logic, pinned rule by rule with
hand-built records.

The whole-race behavior — counts against known results, determinism,
idempotency, all through the real Phase 0 replay path — lives in
test_state_machine_replay.py. These tests isolate each transition rule
so a regression names the exact rule that broke.
"""

from __future__ import annotations

from datetime import timedelta

from pydantic import JsonValue

from f1_domain import (
    Compound,
    DomainEvent,
    DriverListUpdated,
    GapUpdated,
    LapCompleted,
    PitEntry,
    PitExit,
    PositionChanged,
    RaceControlMessage,
    SectorCompleted,
    SessionKey,
    SessionStatusChanged,
    SessionType,
    StintStarted,
    TeamRadioClip,
    TrackStatusChanged,
    TrackStatusKind,
    UnknownEvent,
    WeatherSample,
)
from f1_parse import LapGap, SessionStateMachine, parse_gap
from f1_recording import RawRecord

_KEY = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
_T0 = 1_709_391_600.0  # 2024-03-02 15:00:00 UTC


class Feed:
    """Hands out records with auto-incrementing seq/timestamps, so tests
    read as a message sequence (the seq guard drops non-increasing seqs)."""

    def __init__(self) -> None:
        self._seq = 0

    def record(self, topic: str, payload: JsonValue) -> RawRecord:
        record = RawRecord(
            seq=self._seq,
            ts_wall=_T0 + self._seq,
            ts_mono=self._seq * 1_000_000_000,
            topic=topic,
            payload=payload,
        )
        self._seq += 1
        return record


def machine() -> SessionStateMachine:
    return SessionStateMachine(session_key=_KEY)


def timing(feed: Feed, number: str, line: JsonValue) -> RawRecord:
    return feed.record("TimingData", {"Lines": {number: line}})


def app(feed: Feed, number: str, line: JsonValue) -> RawRecord:
    return feed.record("TimingAppData", {"Lines": {number: line}})


def only[T: DomainEvent](events: list[DomainEvent], kind: type[T]) -> list[T]:
    return [event for event in events if isinstance(event, kind)]


class TestLapCompleted:
    def test_lap_and_time_in_one_message(self):
        sm, feed = machine(), Feed()
        sm.apply(timing(feed, "44", {"NumberOfLaps": 0}))
        events = sm.apply(
            timing(
                feed,
                "44",
                {"NumberOfLaps": 1, "LastLapTime": {"Value": "1:33.100", "PersonalFastest": True}},
            )
        )
        laps = only(events, LapCompleted)
        assert len(laps) == 1
        assert laps[0].driver == "44"
        assert laps[0].lap_number == 1
        assert laps[0].lap_time == timedelta(minutes=1, seconds=33.1)
        assert laps[0].is_personal_best is True
        assert laps[0].source_seq == 1

    def test_resent_lap_data_does_not_duplicate(self):
        sm, feed = machine(), Feed()
        sm.apply(timing(feed, "44", {"NumberOfLaps": 0}))
        crossing: JsonValue = {"NumberOfLaps": 1, "LastLapTime": {"Value": "1:33.100"}}
        assert only(sm.apply(timing(feed, "44", crossing)), LapCompleted)
        # The feed re-sends the same lap data in a FRESH record: no event.
        assert sm.apply(timing(feed, "44", crossing)) == []

    def test_same_record_applied_twice_is_inert(self):
        sm, feed = machine(), Feed()
        sm.apply(timing(feed, "44", {"NumberOfLaps": 0}))
        record = timing(feed, "44", {"NumberOfLaps": 1, "LastLapTime": {"Value": "1:33.100"}})
        assert only(sm.apply(record), LapCompleted)
        assert sm.apply(record) == []

    def test_time_arriving_before_lap_number(self):
        sm, feed = machine(), Feed()
        sm.apply(timing(feed, "44", {"NumberOfLaps": 0}))
        assert (
            only(sm.apply(timing(feed, "44", {"LastLapTime": {"Value": "1:31.5"}})), LapCompleted)
            == []
        )
        events = sm.apply(timing(feed, "44", {"NumberOfLaps": 1}))
        laps = only(events, LapCompleted)
        assert len(laps) == 1
        assert laps[0].lap_time == timedelta(minutes=1, seconds=31.5)

    def test_lap_number_first_holds_until_fresh_time(self):
        """A lap increment must not steal the PREVIOUS lap's time."""
        sm, feed = machine(), Feed()
        sm.apply(timing(feed, "44", {"NumberOfLaps": 0}))
        sm.apply(timing(feed, "44", {"NumberOfLaps": 1, "LastLapTime": {"Value": "1:33.0"}}))
        # Lap 2's count arrives alone; the merged time is still lap 1's.
        assert sm.apply(timing(feed, "44", {"NumberOfLaps": 2})) == []
        events = sm.apply(timing(feed, "44", {"LastLapTime": {"Value": "1:32.2"}}))
        laps = only(events, LapCompleted)
        assert len(laps) == 1
        assert laps[0].lap_number == 2
        assert laps[0].lap_time == timedelta(minutes=1, seconds=32.2)

    def test_first_sighting_establishes_baseline_only(self):
        """Joining mid-session: the first lap count seen is a baseline, not
        a completion we observed."""
        sm, feed = machine(), Feed()
        events = sm.apply(
            timing(feed, "44", {"NumberOfLaps": 30, "LastLapTime": {"Value": "1:33.0"}})
        )
        assert only(events, LapCompleted) == []
        # ... and its time must not leak onto the next increment either.
        assert sm.apply(timing(feed, "44", {"NumberOfLaps": 31})) == []
        laps = only(
            sm.apply(timing(feed, "44", {"LastLapTime": {"Value": "1:34.9"}})), LapCompleted
        )
        assert len(laps) == 1
        assert (laps[0].lap_number, laps[0].lap_time) == (31, timedelta(minutes=1, seconds=34.9))

    def test_identical_consecutive_lap_times_still_emit(self):
        """Two laps identical to the millisecond are rare but real. The
        time riding in the same delta as the increment IS the attribution,
        even though its value didn't change."""
        sm, feed = machine(), Feed()
        sm.apply(timing(feed, "77", {"NumberOfLaps": 0}))
        crossing: JsonValue = {"NumberOfLaps": 1, "LastLapTime": {"Value": "1:36.995"}}
        assert len(only(sm.apply(timing(feed, "77", crossing)), LapCompleted)) == 1
        twin: JsonValue = {"NumberOfLaps": 2, "LastLapTime": {"Value": "1:36.995"}}
        laps = only(sm.apply(timing(feed, "77", twin)), LapCompleted)
        assert [lap.lap_number for lap in laps] == [2]
        # ...but a re-send of the SAME completed lap stays inert.
        assert sm.apply(timing(feed, "77", dict(twin))) == []
        # ...and a flag-only LastLapTime delta with an increment still holds
        # (no value = nothing to attribute), until a fresh value lands.
        sm.apply(timing(feed, "77", {"NumberOfLaps": 3, "LastLapTime": {"PersonalFastest": True}}))
        laps = only(
            sm.apply(timing(feed, "77", {"LastLapTime": {"Value": "1:37.100"}})), LapCompleted
        )
        assert [(lap.lap_number, lap.lap_time) for lap in laps] == [
            (3, timedelta(minutes=1, seconds=37.1))
        ]

    def test_lap_carries_stint_context(self):
        sm, feed = machine(), Feed()
        sm.apply(timing(feed, "16", {"NumberOfLaps": 0}))
        sm.apply(
            app(
                feed,
                "16",
                {"Stints": {"0": {"Compound": "SOFT", "StartLaps": 2, "TotalLaps": 5}}},
            )
        )
        laps = only(
            sm.apply(timing(feed, "16", {"NumberOfLaps": 1, "LastLapTime": {"Value": "1:35.0"}})),
            LapCompleted,
        )
        assert laps[0].compound is Compound.SOFT
        assert laps[0].tyre_age == 7  # 2 already on the set + 5 this stint


class TestSectors:
    def test_sector_time_emits_with_in_progress_lap(self):
        sm, feed = machine(), Feed()
        sm.apply(timing(feed, "44", {"NumberOfLaps": 3}))
        events = sm.apply(timing(feed, "44", {"Sectors": {"0": {"Value": "28.111"}}}))
        sectors = only(events, SectorCompleted)
        assert len(sectors) == 1
        assert (sectors[0].sector, sectors[0].lap_number) == (1, 4)
        assert sectors[0].time == timedelta(seconds=28.111)

    def test_cleared_sector_value_is_not_an_event(self):
        sm, feed = machine(), Feed()
        sm.apply(timing(feed, "44", {"Sectors": {"1": {"Value": "31.2"}}}))
        assert sm.apply(timing(feed, "44", {"Sectors": {"1": {"Value": ""}}})) == []

    def test_resent_sector_value_is_deduplicated(self):
        sm, feed = machine(), Feed()
        sm.apply(timing(feed, "44", {"Sectors": {"1": {"Value": "31.2"}}}))
        assert sm.apply(timing(feed, "44", {"Sectors": {"1": {"Value": "31.2"}}})) == []


class TestPitStops:
    def test_entry_exit_cycle(self):
        sm, feed = machine(), Feed()
        sm.apply(timing(feed, "55", {"NumberOfLaps": 19, "InPit": False}))
        entered = sm.apply(timing(feed, "55", {"InPit": True, "NumberOfPitStops": 1}))
        entries = only(entered, PitEntry)
        assert len(entries) == 1
        assert entries[0].lap_number == 20  # the in-lap: lap in progress
        # Feed re-affirms InPit while stationary: no duplicate.
        assert only(sm.apply(timing(feed, "55", {"InPit": True})), PitEntry) == []
        sm.apply(timing(feed, "55", {"NumberOfLaps": 20, "LastLapTime": {"Value": "1:51.0"}}))
        exited = sm.apply(timing(feed, "55", {"InPit": False, "PitOut": True}))
        exits = only(exited, PitExit)
        assert len(exits) == 1
        assert exits[0].lap_number == 21  # the out-lap

    def test_pit_out_flag_alone_is_not_an_exit(self):
        sm, feed = machine(), Feed()
        sm.apply(timing(feed, "55", {"InPit": False}))
        assert only(sm.apply(timing(feed, "55", {"PitOut": True})), PitExit) == []


class TestStints:
    def test_new_stint_announces_with_compound(self):
        sm, feed = machine(), Feed()
        sm.apply(timing(feed, "1", {"NumberOfLaps": 24}))
        events = sm.apply(
            app(
                feed,
                "1",
                {
                    "Stints": {
                        "1": {"Compound": "HARD", "New": "false", "StartLaps": 3, "TotalLaps": 0}
                    }
                },
            )
        )
        stints = only(events, StintStarted)
        assert len(stints) == 1
        assert stints[0].stint_number == 2  # feed index 1 -> 1-based 2
        assert stints[0].compound is Compound.HARD
        assert stints[0].start_lap == 25
        assert stints[0].tyre_age_at_start == 3

    def test_stint_waits_for_trailing_compound(self):
        sm, feed = machine(), Feed()
        assert sm.apply(app(feed, "1", {"Stints": {"0": {"TotalLaps": 0}}})) == []
        stints = only(
            sm.apply(app(feed, "1", {"Stints": {"0": {"Compound": "MEDIUM"}}})), StintStarted
        )
        assert len(stints) == 1
        assert stints[0].compound is Compound.MEDIUM

    def test_stint_updates_do_not_reannounce(self):
        sm, feed = machine(), Feed()
        sm.apply(app(feed, "1", {"Stints": {"0": {"Compound": "SOFT", "TotalLaps": 0}}}))
        assert sm.apply(app(feed, "1", {"Stints": {"0": {"TotalLaps": 7}}})) == []
        assert sm.state.drivers["1"].tyre_age == 7

    def test_unrecognized_compound_maps_to_unknown_never_guessed(self):
        sm, feed = machine(), Feed()
        stints = only(
            sm.apply(app(feed, "1", {"Stints": {"0": {"Compound": "TEST_PROTO"}}})), StintStarted
        )
        assert stints[0].compound is Compound.UNKNOWN


class TestGaps:
    def test_parse_gap_forms(self):
        assert parse_gap(None) is None
        assert parse_gap("") is None
        assert parse_gap("+5.230") == timedelta(seconds=5.23)
        assert parse_gap("17.5") == timedelta(seconds=17.5)
        assert parse_gap("1:02.345") == timedelta(minutes=1, seconds=2.345)
        assert parse_gap("1 L") == LapGap(laps=1)
        assert parse_gap("+2 LAPS") == LapGap(laps=2)
        assert parse_gap("LAP 57") is None  # the leader's TV-style label, not a gap
        assert parse_gap("garbage") is None

    def test_gap_change_emits_and_resend_does_not(self):
        sm, feed = machine(), Feed()
        events = sm.apply(
            timing(
                feed,
                "4",
                {"GapToLeader": "+2.259", "IntervalToPositionAhead": {"Value": "+0.722"}},
            )
        )
        gaps = only(events, GapUpdated)
        assert len(gaps) == 1
        assert gaps[0].gap_to_leader == timedelta(seconds=2.259)
        assert gaps[0].interval_to_ahead == timedelta(seconds=0.722)
        assert gaps[0].laps_down is None
        assert sm.apply(timing(feed, "4", {"GapToLeader": "+2.259"})) == []

    def test_lapped_car_is_a_count_not_a_duration(self):
        sm, feed = machine(), Feed()
        sm.apply(timing(feed, "24", {"GapToLeader": "+79.100"}))
        gaps = only(sm.apply(timing(feed, "24", {"GapToLeader": "1 L"})), GapUpdated)
        assert len(gaps) == 1
        assert gaps[0].gap_to_leader is None
        assert gaps[0].laps_down == 1
        state = sm.state.drivers["24"]
        assert state.gap_to_leader is None
        assert state.laps_down == 1

    def test_leader_has_empty_gap(self):
        sm, feed = machine(), Feed()
        sm.apply(timing(feed, "1", {"GapToLeader": "+0.400"}))
        gaps = only(sm.apply(timing(feed, "1", {"GapToLeader": ""})), GapUpdated)
        assert len(gaps) == 1
        assert gaps[0].gap_to_leader is None
        assert gaps[0].laps_down is None

    def test_practice_style_time_diffs(self):
        sm, feed = machine(), Feed()
        gaps = only(
            sm.apply(
                timing(
                    feed,
                    "81",
                    {"TimeDiffToFastest": "+0.273", "TimeDiffToPositionAhead": "+0.014"},
                )
            ),
            GapUpdated,
        )
        assert gaps[0].gap_to_leader == timedelta(seconds=0.273)
        assert gaps[0].interval_to_ahead == timedelta(seconds=0.014)


class TestPositions:
    def test_position_change_emits_once(self):
        sm, feed = machine(), Feed()
        sm.apply(timing(feed, "63", {"Position": "5"}))
        events = sm.apply(timing(feed, "63", {"Position": "4"}))
        positions = only(events, PositionChanged)
        assert len(positions) == 1
        assert positions[0].position == 4
        assert sm.apply(timing(feed, "63", {"Position": "4"})) == []

    def test_driver_list_line_and_timing_position_deduplicate(self):
        sm, feed = machine(), Feed()
        sm.apply(timing(feed, "63", {"Position": "5"}))
        moved = sm.apply(feed.record("DriverList", {"63": {"Line": 4}}))
        assert len(only(moved, PositionChanged)) == 1
        # TimingData then restates what DriverList already told us.
        assert only(sm.apply(timing(feed, "63", {"Position": "4"})), PositionChanged) == []


class TestSessionLevelTopics:
    def test_track_status_maps_codes_and_dedups(self):
        sm, feed = machine(), Feed()
        events = sm.apply(feed.record("TrackStatus", {"Status": "6", "Message": "VSCDeployed"}))
        changed = only(events, TrackStatusChanged)
        assert len(changed) == 1
        assert changed[0].status is TrackStatusKind.VSC
        assert changed[0].message == "VSCDeployed"
        assert sm.apply(feed.record("TrackStatus", {"Status": "6", "Message": "VSCDeployed"})) == []
        unknown = only(
            sm.apply(feed.record("TrackStatus", {"Status": "9", "Message": "Mystery"})),
            TrackStatusChanged,
        )
        assert unknown[0].status is None  # unmapped code: never guess

    def test_session_status_series_dedups_by_index_and_value(self):
        sm, feed = machine(), Feed()
        started = sm.apply(
            feed.record(
                "SessionData",
                {
                    "StatusSeries": {
                        "1": {"Utc": "2024-03-02T15:03:00Z", "SessionStatus": "Started"}
                    }
                },
            )
        )
        assert [e.status for e in only(started, SessionStatusChanged)] == ["Started"]
        # Same index re-sent: dropped. New index, same value: dropped too.
        assert (
            sm.apply(
                feed.record("SessionData", {"StatusSeries": {"1": {"SessionStatus": "Started"}}})
            )
            == []
        )
        assert (
            sm.apply(
                feed.record("SessionData", {"StatusSeries": {"2": {"SessionStatus": "Started"}}})
            )
            == []
        )
        assert sm.state.status == "Started"

    def test_lap_count_updates_state_silently(self):
        sm, feed = machine(), Feed()
        assert sm.apply(feed.record("LapCount", {"CurrentLap": 12, "TotalLaps": 57})) == []
        assert sm.state.current_lap == 12

    def test_weather_emits_only_on_change(self):
        sm, feed = machine(), Feed()
        sample: JsonValue = {"AirTemp": "27.8", "TrackTemp": "34.1", "Rainfall": "0"}
        first = only(sm.apply(feed.record("WeatherData", sample)), WeatherSample)
        assert len(first) == 1
        assert sm.apply(feed.record("WeatherData", dict(sample))) == []
        second = only(
            sm.apply(feed.record("WeatherData", {**sample, "TrackTemp": "34.4"})), WeatherSample
        )
        assert len(second) == 1
        assert sm.state.weather == second[0]

    def test_race_control_indexes_dedup_across_forms(self):
        sm, feed = machine(), Feed()
        snapshot: JsonValue = {
            "Messages": [
                {"Utc": "2024-03-02T15:00:00Z", "Category": "Other", "Message": "A"},
                {"Utc": "2024-03-02T15:01:00Z", "Category": "Other", "Message": "B"},
            ]
        }
        assert (
            len(only(sm.apply(feed.record("RaceControlMessages", snapshot)), RaceControlMessage))
            == 2
        )
        delta: JsonValue = {"Messages": {"2": {"Category": "Flag", "Message": "C", "Lap": 3}}}
        new = only(sm.apply(feed.record("RaceControlMessages", delta)), RaceControlMessage)
        assert len(new) == 1
        assert new[0].message == "C"
        assert new[0].lap == 3
        # Re-sent delta and re-sent full list both add nothing.
        assert sm.apply(feed.record("RaceControlMessages", dict(delta))) == []
        assert sm.apply(feed.record("RaceControlMessages", dict(snapshot))) == []

    def test_team_radio_resolves_url_via_session_info(self):
        sm, feed = machine(), Feed()
        capture: JsonValue = {
            "Captures": [
                {
                    "Utc": "2024-03-02T15:10:00Z",
                    "RacingNumber": "44",
                    "Path": "TeamRadio/HAMLEW01_44.mp3",
                }
            ]
        }
        bare = only(sm.apply(feed.record("TeamRadio", capture)), TeamRadioClip)
        assert bare[0].audio_url == "TeamRadio/HAMLEW01_44.mp3"  # no prefix known yet
        sm.apply(feed.record("SessionInfo", {"Path": "2024/2024-03-02_Bahrain_Grand_Prix/Race/"}))
        later: JsonValue = {
            "Captures": {"1": {"RacingNumber": "44", "Path": "TeamRadio/HAMLEW02_44.mp3"}}
        }
        resolved = only(sm.apply(feed.record("TeamRadio", later)), TeamRadioClip)
        assert resolved[0].audio_url == (
            "https://livetiming.formula1.com/static/"
            "2024/2024-03-02_Bahrain_Grand_Prix/Race/TeamRadio/HAMLEW02_44.mp3"
        )

    def test_unknown_topic_becomes_unknown_event_verbatim(self):
        sm, feed = machine(), Feed()
        payload: JsonValue = {"Utc": "2024-03-02T15:00:00Z", "_kf": True}
        events = sm.apply(feed.record("Heartbeat", payload))
        unknown = only(events, UnknownEvent)
        assert len(unknown) == 1
        assert unknown[0].topic == "Heartbeat"
        assert unknown[0].payload == payload


class TestDriverList:
    def test_identity_change_emits_sorted_roster(self):
        sm, feed = machine(), Feed()
        events = sm.apply(
            feed.record(
                "DriverList",
                {
                    "44": {"Tla": "HAM", "TeamName": "Mercedes"},
                    "4": {"Tla": "NOR", "TeamName": "McLaren"},
                },
            )
        )
        rosters = only(events, DriverListUpdated)
        assert len(rosters) == 1
        drivers = rosters[0].drivers or []
        assert [d.number for d in drivers] == ["4", "44"]  # numeric order
        assert drivers[1].code == "HAM"
        # Verbatim re-send: identity unchanged, no event.
        assert sm.apply(feed.record("DriverList", {"44": {"Tla": "HAM"}})) == []

    def test_line_only_update_is_position_not_roster(self):
        sm, feed = machine(), Feed()
        sm.apply(feed.record("DriverList", {"44": {"Tla": "HAM"}}))
        events = sm.apply(feed.record("DriverList", {"44": {"Line": 2}}))
        assert only(events, DriverListUpdated) == []
        assert len(only(events, PositionChanged)) == 1


def _snapshot() -> JsonValue:
    """A connect-time snapshot: two drivers mid-race, one in the pits."""
    return {
        "SessionInfo": {"Path": "2024/Test/Race/"},
        "DriverList": {
            "1": {"Tla": "VER", "TeamName": "Red Bull Racing", "Line": 1},
            "16": {"Tla": "LEC", "TeamName": "Ferrari", "Line": 2},
        },
        "TimingData": {
            "Lines": {
                "1": {
                    "NumberOfLaps": 30,
                    "LastLapTime": {"Value": "1:34.100"},
                    "Position": "1",
                    "GapToLeader": "",
                    "InPit": False,
                    "NumberOfPitStops": 1,
                    "Sectors": {
                        "0": {"Value": "28.1"},
                        "1": {"Value": "31.0"},
                        "2": {"Value": "35.0"},
                    },
                },
                "16": {
                    "NumberOfLaps": 29,
                    "LastLapTime": {"Value": "1:35.000"},
                    "Position": "2",
                    "GapToLeader": "+22.000",
                    "InPit": True,
                    "NumberOfPitStops": 1,
                },
            }
        },
        "TimingAppData": {
            "Lines": {
                "1": {"Stints": [{"Compound": "MEDIUM", "TotalLaps": 12, "StartLaps": 0}]},
                "16": {
                    "Stints": [
                        {"Compound": "SOFT", "TotalLaps": 14, "StartLaps": 0},
                        {"Compound": "HARD", "TotalLaps": 0, "StartLaps": 0},
                    ]
                },
            }
        },
        "LapCount": {"CurrentLap": 31, "TotalLaps": 57},
        "TrackStatus": {"Status": "2", "Message": "Yellow"},
        "SessionData": {
            "StatusSeries": [
                {"Utc": "2024-03-02T14:00:00Z", "SessionStatus": "Inactive"},
                {"Utc": "2024-03-02T15:03:00Z", "SessionStatus": "Started"},
            ]
        },
        "WeatherData": {"AirTemp": "27.8", "TrackTemp": "34.1", "Rainfall": "0"},
        "RaceControlMessages": {
            "Messages": [{"Utc": "2024-03-02T15:00:00Z", "Category": "Other", "Message": "M0"}]
        },
        "TeamRadio": {"Captures": [{"RacingNumber": "1", "Path": "TeamRadio/x.mp3"}]},
    }


class TestSeeding:
    def test_seed_is_silent_and_populates_state(self):
        sm, feed = machine(), Feed()
        assert sm.apply(feed.record("_meta.subscribe_snapshot", _snapshot())) == []
        state = sm.state
        assert state.status == "Started"
        assert state.current_lap == 31
        assert state.track_status is TrackStatusKind.YELLOW
        assert state.weather is None  # baseline set, but no emitted event yet
        ver, lec = state.drivers["1"], state.drivers["16"]
        assert (ver.position, ver.laps_completed, ver.stint_number) == (1, 30, 1)
        assert ver.driver.code == "VER"
        assert lec.in_pit is True
        assert lec.current_compound is Compound.HARD
        assert lec.gap_to_leader == timedelta(seconds=22.0)

    def test_deltas_restating_snapshot_values_emit_nothing(self):
        sm, feed = machine(), Feed()
        sm.apply(feed.record("_meta.subscribe_snapshot", _snapshot()))
        assert (
            sm.apply(
                timing(
                    feed,
                    "1",
                    {
                        "NumberOfLaps": 30,
                        "LastLapTime": {"Value": "1:34.100"},
                        "Position": "1",
                        "GapToLeader": "",
                        "InPit": False,
                    },
                )
            )
            == []
        )
        assert (
            sm.apply(
                feed.record(
                    "WeatherData", {"AirTemp": "27.8", "TrackTemp": "34.1", "Rainfall": "0"}
                )
            )
            == []
        )
        assert sm.apply(feed.record("TrackStatus", {"Status": "2", "Message": "Yellow"})) == []
        assert (
            sm.apply(
                feed.record(
                    "RaceControlMessages",
                    {
                        "Messages": [
                            {"Utc": "2024-03-02T15:00:00Z", "Category": "Other", "Message": "M0"}
                        ]
                    },
                )
            )
            == []
        )

    def test_genuine_transitions_after_seed_emit(self):
        sm, feed = machine(), Feed()
        sm.apply(feed.record("_meta.subscribe_snapshot", _snapshot()))
        laps = only(
            sm.apply(timing(feed, "1", {"NumberOfLaps": 31, "LastLapTime": {"Value": "1:34.500"}})),
            LapCompleted,
        )
        assert len(laps) == 1
        assert laps[0].lap_number == 31
        exits = only(sm.apply(timing(feed, "16", {"InPit": False, "PitOut": True})), PitExit)
        assert len(exits) == 1

    def test_reseed_after_reconnect_absorbs_gap_changes_silently(self):
        sm, feed = machine(), Feed()
        sm.apply(feed.record("_meta.subscribe_snapshot", _snapshot()))
        # During the outage: VER completed two laps and pitted onto HARD,
        # a new race-control message landed, the track went green.
        resnapshot = _snapshot()
        assert isinstance(resnapshot, dict)
        resnapshot["TimingData"] = {
            "Lines": {
                "1": {
                    "NumberOfLaps": 32,
                    "LastLapTime": {"Value": "1:36.000"},
                    "Position": "1",
                    "GapToLeader": "",
                    "InPit": False,
                    "NumberOfPitStops": 2,
                },
                "16": {"NumberOfLaps": 31, "Position": "2", "InPit": False},
            }
        }
        resnapshot["TimingAppData"] = {
            "Lines": {
                "1": {
                    "Stints": [
                        {"Compound": "MEDIUM", "TotalLaps": 14, "StartLaps": 0},
                        {"Compound": "HARD", "TotalLaps": 1, "StartLaps": 0},
                    ]
                }
            }
        }
        resnapshot["TrackStatus"] = {"Status": "1", "Message": "AllClear"}
        resnapshot["RaceControlMessages"] = {
            "Messages": [
                {"Utc": "2024-03-02T15:00:00Z", "Category": "Other", "Message": "M0"},
                {"Utc": "2024-03-02T15:40:00Z", "Category": "Flag", "Message": "GREEN"},
            ]
        }
        assert sm.apply(feed.record("_meta.reconnect", {"attempt": 1})) == []
        assert sm.apply(feed.record("_meta.subscribe_snapshot", resnapshot)) == []
        state = sm.state
        assert state.drivers["1"].laps_completed == 32
        assert state.drivers["1"].current_compound is Compound.HARD
        assert state.track_status is TrackStatusKind.GREEN
        # The next increments/messages emit from the NEW baseline...
        laps = only(
            sm.apply(timing(feed, "1", {"NumberOfLaps": 33, "LastLapTime": {"Value": "1:34.800"}})),
            LapCompleted,
        )
        assert [lap.lap_number for lap in laps] == [33]
        # ...and gap-era data re-sent by the feed stays silent.
        assert sm.apply(feed.record("TrackStatus", {"Status": "1", "Message": "AllClear"})) == []
        assert (
            sm.apply(
                feed.record(
                    "RaceControlMessages",
                    {"Messages": {"1": {"Utc": "2024-03-02T15:40:00Z", "Message": "GREEN"}}},
                )
            )
            == []
        )

    def test_placeholder_seq_snapshot_does_not_eat_first_record(self):
        """The replayer's synthetic snapshot record carries seq=0 — the
        same seq as the first REAL stream record. The seq guard must not
        swallow that first record."""
        sm = machine()
        snapshot_record = RawRecord(
            seq=0, ts_wall=_T0, ts_mono=0, topic="_meta.subscribe_snapshot", payload=_snapshot()
        )
        assert sm.apply(snapshot_record) == []
        first_real = RawRecord(
            seq=0,
            ts_wall=_T0 + 1,
            ts_mono=1_000_000_000,
            topic="TimingData",
            payload={"Lines": {"1": {"NumberOfLaps": 31, "LastLapTime": {"Value": "1:34.5"}}}},
        )
        assert len(only(sm.apply(first_real), LapCompleted)) == 1
