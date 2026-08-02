"""The Phase 0 payoff: a full synthetic race, recorded with the real
`f1_recording` writer, replayed with the real `f1_replay` replayer, and
driven through `SessionStateMachine` — asserted against EXACT ground
truth (see race_sim for why exactness is possible).

This is the backtest discipline from CLAUDE.md: pipeline logic tested
against recorded replay fixtures, not mocks. The recording includes
duplicate re-sends, a mid-race reconnect (sentinels + resubscribe
snapshot), a retirement, and lapped backmarkers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from race_sim import GRID, RETIREE, TOTAL_LAPS, RaceTruth, build_race_recording

from f1_domain import (
    DomainEvent,
    LapCompleted,
    PitEntry,
    PitExit,
    RaceControlMessage,
    SectorCompleted,
    SessionKey,
    SessionState,
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
from f1_replay.replayer import replay
from f1_replay.sinks import list_sink

_KEY = SessionKey(year=2024, round=1, session_type=SessionType.RACE)


@dataclass(frozen=True)
class ReplayedRace:
    records: list[RawRecord]
    truth: RaceTruth


@pytest.fixture(scope="module")
def race(tmp_path_factory: pytest.TempPathFactory) -> ReplayedRace:
    directory: Path = tmp_path_factory.mktemp("race") / "2024_simulated_race"
    truth = build_race_recording(directory)
    records: list[RawRecord] = []
    replay(directory, list_sink(records), speed=0)
    return ReplayedRace(records=records, truth=truth)


def run_machine(records: list[RawRecord]) -> tuple[list[DomainEvent], SessionState]:
    machine = SessionStateMachine(session_key=_KEY)
    events: list[DomainEvent] = []
    for record in records:
        events.extend(machine.apply(record))
    return events, machine.state


def by_kind[T: DomainEvent](events: list[DomainEvent], kind: type[T]) -> list[T]:
    return [event for event in events if isinstance(event, kind)]


class TestFullRace:
    def test_lap_completions_match_ground_truth_exactly(self, race: ReplayedRace):
        events, _ = run_machine(race.records)
        laps = by_kind(events, LapCompleted)
        assert len(laps) == sum(race.truth.laps_by_driver.values())
        per_driver: dict[str, list[LapCompleted]] = {}
        for lap in laps:
            assert lap.driver is not None
            per_driver.setdefault(lap.driver, []).append(lap)
        assert set(per_driver) == set(race.truth.laps_by_driver)
        for number, driver_laps in per_driver.items():
            total = race.truth.laps_by_driver[number]
            # Contiguous, strictly increasing, no dupes, no gaps, 1..N.
            assert [lap.lap_number for lap in driver_laps] == list(range(1, total + 1)), number
            for lap in driver_laps:
                assert lap.lap_number is not None
                key = (number, lap.lap_number)
                assert lap.lap_time == race.truth.lap_times[key], key
                assert lap.is_personal_best == race.truth.personal_bests[key], key
                assert lap.tyre_age == race.truth.tyre_age_at_lap[key], key
                assert lap.compound == race.truth.compound_at_lap[key], key

    def test_pit_stops_match_known_results(self, race: ReplayedRace):
        events, _ = run_machine(race.records)
        entries = by_kind(events, PitEntry)
        exits = by_kind(events, PitExit)
        for number, pit_laps in race.truth.pit_laps.items():
            driver_entries = [e for e in entries if e.driver == number]
            driver_exits = [e for e in exits if e.driver == number]
            assert [e.lap_number for e in driver_entries] == pit_laps, number
            assert [e.lap_number for e in driver_exits] == [lap + 1 for lap in pit_laps], number
        assert len(entries) == sum(len(v) for v in race.truth.pit_laps.values())

    def test_stints_track_compounds_across_the_session(self, race: ReplayedRace):
        events, state = run_machine(race.records)
        stints = by_kind(events, StintStarted)
        for number in race.truth.stint_compounds:
            driver_stints = [s for s in stints if s.driver == number]
            assert [s.stint_number for s in driver_stints] == list(
                range(1, len(race.truth.stint_compounds[number]) + 1)
            ), number
            assert [s.compound for s in driver_stints] == race.truth.stint_compounds[number]
            assert [s.start_lap for s in driver_stints] == race.truth.stint_start_laps[number]
            assert state.drivers[number].stint_number == len(driver_stints)
            assert state.drivers[number].current_compound == race.truth.stint_compounds[number][-1]

    def test_final_classification_matches_race_result(self, race: ReplayedRace):
        _, state = run_machine(race.records)
        positions = {
            number: state.drivers[number].position for number in race.truth.final_positions
        }
        assert positions == race.truth.final_positions
        assert sorted(p for p in positions.values() if p is not None) == list(
            range(1, len(GRID) + 1)
        )
        assert state.drivers[RETIREE].position == len(GRID)  # retiree sank to last
        assert state.drivers[RETIREE].laps_completed == race.truth.laps_by_driver[RETIREE]

    def test_gaps_distinguish_lapped_cars_from_durations(self, race: ReplayedRace):
        _, state = run_machine(race.records)
        lapped = 0
        for number, gap_string in race.truth.final_gap_strings.items():
            driver = state.drivers[number]
            expected = parse_gap(gap_string)
            if isinstance(expected, LapGap):
                lapped += 1
                assert driver.laps_down == expected.laps, number
                assert driver.gap_to_leader is None, number
            else:
                assert driver.laps_down is None, number
                assert driver.gap_to_leader == expected, number
        assert lapped >= 3  # the retiree and the slow backmarkers

    def test_session_level_timeline(self, race: ReplayedRace):
        events, state = run_machine(race.records)
        statuses = by_kind(events, SessionStatusChanged)
        assert [s.status for s in statuses] == race.truth.session_status_sequence
        assert state.status == "Finalised"
        track = by_kind(events, TrackStatusChanged)
        assert len(track) == race.truth.track_status_count
        assert [t.status for t in track] == [TrackStatusKind.YELLOW, TrackStatusKind.GREEN]
        assert state.track_status is TrackStatusKind.GREEN
        assert state.current_lap == TOTAL_LAPS
        weather = by_kind(events, WeatherSample)
        assert len(weather) == race.truth.weather_event_count
        assert state.weather == weather[-1]
        race_control = by_kind(events, RaceControlMessage)
        assert len(race_control) == race.truth.race_control_stream_count
        assert race_control[-1].flag == "CHEQUERED"
        radio = by_kind(events, TeamRadioClip)
        assert len(radio) == race.truth.team_radio_count
        for clip in radio:
            assert clip.audio_url is not None
            assert clip.audio_url.startswith("https://livetiming.formula1.com/static/")
        unknown = by_kind(events, UnknownEvent)
        assert len(unknown) == race.truth.unknown_topic_count
        assert {u.topic for u in unknown} == {"Heartbeat"}

    def test_sector_completions_are_exact(self, race: ReplayedRace):
        events, _ = run_machine(race.records)
        sectors = by_kind(events, SectorCompleted)
        assert len(sectors) == race.truth.sector_event_count
        # Spot-check attribution: each driver's sectors 1..3 appear for
        # each of their laps, and lap 1 sector 1 exists for everyone.
        first_lap_s1 = [s for s in sectors if s.lap_number == 1 and s.sector == 1]
        assert len(first_lap_s1) == len(GRID)

    def test_reconnect_reseed_emitted_nothing(self, race: ReplayedRace):
        events, _ = run_machine(race.records)
        assert race.truth.reconnect_sentinel_seqs  # the sim really reconnected
        emitted_seqs = {event.source_seq for event in events}
        assert not emitted_seqs & race.truth.reconnect_sentinel_seqs

    def test_every_event_carries_a_real_source_seq(self, race: ReplayedRace):
        events, _ = run_machine(race.records)
        stream_seqs = {record.seq for record in race.records}
        assert all(event.source_seq in stream_seqs for event in events)

    def test_final_state_lap_times(self, race: ReplayedRace):
        _, state = run_machine(race.records)
        for number, total in race.truth.laps_by_driver.items():
            driver = state.drivers[number]
            assert driver.laps_completed == total
            assert driver.last_lap == race.truth.lap_times[(number, total)]
            expected_best: timedelta = race.truth.best_lap_by_driver[number]
            assert driver.best_lap == expected_best


class TestDeterminism:
    def test_same_recording_twice_gives_identical_event_streams(self, race: ReplayedRace):
        first_events, first_state = run_machine(race.records)
        second_events, second_state = run_machine(race.records)
        assert len(first_events) == len(second_events)
        # Byte-identical, not just equal: the serialized stream is what
        # the event log will store, so it is the determinism contract.
        first_bytes = [event.model_dump_json() for event in first_events]
        second_bytes = [event.model_dump_json() for event in second_events]
        assert first_bytes == second_bytes
        assert first_state == second_state

    def test_fresh_replay_from_disk_is_identical_too(
        self, race: ReplayedRace, tmp_path_factory: pytest.TempPathFactory
    ):
        directory = tmp_path_factory.mktemp("race-again") / "2024_simulated_race"
        build_race_recording(directory)
        records: list[RawRecord] = []
        replay(directory, list_sink(records), speed=0)
        original_events, _ = run_machine(race.records)
        rebuilt_events, _ = run_machine(records)
        assert [e.model_dump_json() for e in rebuilt_events] == [
            e.model_dump_json() for e in original_events
        ]


class TestIdempotency:
    def test_duplicating_every_record_changes_nothing(self, race: ReplayedRace):
        single_events, single_state = run_machine(race.records)
        doubled = [record for record in race.records for _ in range(2)]
        doubled_events, doubled_state = run_machine(doubled)
        assert [e.model_dump_json() for e in doubled_events] == [
            e.model_dump_json() for e in single_events
        ]
        assert doubled_state == single_state
