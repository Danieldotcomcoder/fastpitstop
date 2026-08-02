"""Per-topic decoder tests, driven by the fixture payloads (see conftest
for the fixture provenance and how to regenerate from a real recording)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import JsonValue

from f1_parse import (
    decode_driver_list,
    decode_lap_count,
    decode_race_control,
    decode_session_data,
    decode_session_info,
    decode_team_radio,
    decode_timing,
    decode_track_status,
    decode_weather,
)

FixtureLoader = Callable[[str], list[JsonValue]]


class TestTiming:
    def test_splits_lines_per_driver_verbatim(self, fixture_payloads: FixtureLoader):
        delta = decode_timing(fixture_payloads("timing_data.json")[0])
        assert set(delta.lines) == {"44", "1"}
        line_44 = delta.lines["44"]
        assert isinstance(line_44, dict)
        assert line_44["GapToLeader"] == "+2.259"
        # Partial delta passed through untouched — sector "1" only, nested.
        assert line_44["Sectors"] == {"1": {"Value": "26.259", "PersonalFastest": False}}
        assert delta.other == {}

    def test_session_level_keys_go_to_other(self, fixture_payloads: FixtureLoader):
        delta = decode_timing(fixture_payloads("timing_data.json")[1])
        assert delta.other == {"SessionPart": 2}
        assert set(delta.lines) == {"63"}

    def test_garbage_payload_decodes_to_empty_delta(self):
        delta = decode_timing("not a dict at all")
        assert delta.lines == {}
        assert delta.other == {}


class TestWeather:
    def test_numeric_strings_become_floats(self, fixture_payloads: FixtureLoader):
        decoded = decode_weather(fixture_payloads("weather_data.json")[0])
        assert decoded.air_temp == 27.8
        assert decoded.track_temp == 34.1
        assert decoded.humidity == 58.0
        assert decoded.pressure == 1018.7
        assert decoded.wind_speed == 2.0
        assert decoded.wind_dir == 212.0
        assert decoded.rainfall is False

    def test_rainfall_flag_true(self, fixture_payloads: FixtureLoader):
        decoded = decode_weather(fixture_payloads("weather_data.json")[1])
        assert decoded.rainfall is True

    def test_missing_fields_are_none(self):
        decoded = decode_weather({"AirTemp": "20.0"})
        assert decoded.air_temp == 20.0
        assert decoded.track_temp is None
        assert decoded.rainfall is None


class TestTrackStatus:
    def test_code_and_message_pass_through(self, fixture_payloads: FixtureLoader):
        payloads = fixture_payloads("track_status.json")
        first = decode_track_status(payloads[0])
        assert (first.status_code, first.message) == ("1", "AllClear")
        vsc = decode_track_status(payloads[3])
        assert (vsc.status_code, vsc.message) == ("6", "VSCDeployed")


class TestRaceControl:
    def test_snapshot_list_form(self, fixture_payloads: FixtureLoader):
        entries = decode_race_control(fixture_payloads("race_control_messages.json")[0])
        assert len(entries) == 2
        assert entries[0].category == "Flag"
        assert entries[0].flag == "GREEN"
        assert entries[0].scope == "Track"
        assert entries[0].index is None
        # Naive feed timestamps are UTC.
        assert entries[0].ts_utc == datetime(2024, 3, 2, 15, 0, tzinfo=UTC)

    def test_sparse_delta_form_keeps_index(self, fixture_payloads: FixtureLoader):
        entries = decode_race_control(fixture_payloads("race_control_messages.json")[1])
        assert len(entries) == 1
        assert entries[0].index == 5
        assert entries[0].sector == 7
        assert entries[0].flag == "YELLOW"

    def test_car_event_fields(self, fixture_payloads: FixtureLoader):
        entries = decode_race_control(fixture_payloads("race_control_messages.json")[2])
        assert entries[0].racing_number == "23"
        assert entries[0].lap == 24


class TestTeamRadio:
    def test_snapshot_list_form(self, fixture_payloads: FixtureLoader):
        captures = decode_team_radio(fixture_payloads("team_radio.json")[0])
        assert len(captures) == 1
        assert captures[0].racing_number == "44"
        assert captures[0].path == "TeamRadio/HAMLEW01_44_20240302_151012.mp3"
        assert captures[0].ts_utc == datetime(2024, 3, 2, 15, 10, 12, 345000, tzinfo=UTC)

    def test_sparse_delta_form_with_dotnet_fraction(self, fixture_payloads: FixtureLoader):
        captures = decode_team_radio(fixture_payloads("team_radio.json")[1])
        assert captures[0].index == 3
        assert captures[0].ts_utc == datetime(2024, 3, 2, 15, 22, 48, 123456, tzinfo=UTC)


class TestSessionInfo:
    def test_full_decode(self, fixture_payloads: FixtureLoader):
        info = decode_session_info(fixture_payloads("session_info.json")[0])
        assert info.meeting_name == "Bahrain Grand Prix"
        assert info.meeting_key == 1229
        assert info.country == "Bahrain"
        assert info.circuit == "Sakhir"
        assert info.session_name == "Race"
        assert info.session_type == "Race"
        assert info.session_key == 9472
        assert info.gmt_offset == timedelta(hours=3)
        assert info.path == "2024/2024-03-02_Bahrain_Grand_Prix/2024-03-02_Race/"
        # StartDate is circuit-local wall time; decoded as-is (UTC-tagged),
        # offset application is layer two's job.
        assert info.start_date == datetime(2024, 3, 2, 18, 0, tzinfo=UTC)


class TestDriverList:
    def test_full_snapshot(self, fixture_payloads: FixtureLoader):
        entries = decode_driver_list(fixture_payloads("driver_list.json")[0])
        # "_kf": true is bookkeeping, not a driver — skipped.
        assert set(entries) == {"1", "44"}
        ver = entries["1"]
        assert ver.tla == "VER"
        assert ver.team_name == "Red Bull Racing"
        assert ver.line == 1
        # Unmodeled keys are preserved, not dropped.
        assert ver.extra["Reference"] == "MAXVER01"
        assert ver.extra["CountryCode"] == "NED"

    def test_partial_line_only_update(self, fixture_payloads: FixtureLoader):
        entries = decode_driver_list(fixture_payloads("driver_list.json")[1])
        ham = entries["44"]
        assert ham.line == 3
        assert ham.tla is None  # partial delta: identity fields absent
        assert ham.racing_number == "44"  # falls back to the dict key


class TestSessionData:
    def test_snapshot_list_form(self):
        decoded = decode_session_data(
            {
                "StatusSeries": [
                    {"Utc": "2024-03-02T14:00:00Z", "SessionStatus": "Inactive"},
                    {"Utc": "2024-03-02T15:03:07.123Z", "SessionStatus": "Started"},
                ]
            }
        )
        assert len(decoded.status_series) == 2
        first = decoded.status_series[0]
        assert first.index is None
        assert first.session_status == "Inactive"
        assert first.track_status is None
        assert decoded.status_series[1].ts_utc == datetime(2024, 3, 2, 15, 3, 7, 123000, tzinfo=UTC)

    def test_sparse_delta_form_keeps_index_and_track_status(self):
        decoded = decode_session_data(
            {"StatusSeries": {"3": {"Utc": "2024-03-02T15:41:00Z", "TrackStatus": "Yellow"}}}
        )
        assert len(decoded.status_series) == 1
        entry = decoded.status_series[0]
        assert entry.index == 3
        assert entry.track_status == "Yellow"
        assert entry.session_status is None

    def test_garbage_payload_decodes_to_empty(self):
        assert decode_session_data("nonsense").status_series == []


class TestLapCount:
    def test_full_and_partial_forms(self):
        full = decode_lap_count({"CurrentLap": 12, "TotalLaps": 57})
        assert (full.current_lap, full.total_laps) == (12, 57)
        partial = decode_lap_count({"CurrentLap": 13})
        assert (partial.current_lap, partial.total_laps) == (13, None)

    def test_garbage_payload_decodes_to_empty(self):
        decoded = decode_lap_count([1, 2])
        assert (decoded.current_lap, decoded.total_laps) == (None, None)
