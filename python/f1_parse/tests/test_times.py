"""Time parsing: every shape the feed sends, plus the poisonous edge
cases (empty string is NOT zero; 7-digit fractions must not raise)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from f1_parse import parse_clock_offset, parse_lap_time, parse_utc


class TestParseUtc:
    def test_iso_with_z(self):
        assert parse_utc("2024-03-02T15:43:21.123Z") == datetime(
            2024, 3, 2, 15, 43, 21, 123000, tzinfo=UTC
        )

    def test_dotnet_seven_digit_fraction_is_truncated_not_rejected(self):
        # The feed timestamps with .NET ticks: 7 fractional digits.
        assert parse_utc("2024-03-02T15:22:48.1234567Z") == datetime(
            2024, 3, 2, 15, 22, 48, 123456, tzinfo=UTC
        )

    def test_naive_string_is_assumed_utc(self):
        parsed = parse_utc("2024-03-02T15:00:00")
        assert parsed == datetime(2024, 3, 2, 15, 0, tzinfo=UTC)
        assert parsed is not None and parsed.tzinfo is UTC

    def test_explicit_offset_is_converted_to_utc(self):
        parsed = parse_utc("2024-03-02T18:00:00+03:00")
        assert parsed == datetime(2024, 3, 2, 15, 0, tzinfo=UTC)

    @pytest.mark.parametrize("bad", [None, "", "   ", "not-a-date", "15:00"])
    def test_absent_or_garbage_returns_none(self, bad: str | None):
        assert parse_utc(bad) is None


class TestParseLapTime:
    def test_minutes_seconds(self):
        assert parse_lap_time("1:27.897") == timedelta(minutes=1, seconds=27.897)

    def test_bare_seconds(self):
        assert parse_lap_time("26.259") == timedelta(seconds=26.259)

    def test_hours_form(self):
        assert parse_lap_time("1:02:03.456") == timedelta(hours=1, minutes=2, seconds=3.456)

    def test_leading_plus_gap_form(self):
        assert parse_lap_time("+0.722") == timedelta(seconds=0.722)

    def test_empty_string_means_no_time_not_zero(self):
        assert parse_lap_time("") is None
        assert parse_lap_time("   ") is None

    @pytest.mark.parametrize("bad", [None, "1 L", "2 LAPS", "-", "a:b.c", "1:2:3:4.5"])
    def test_absent_or_garbage_returns_none(self, bad: str | None):
        assert parse_lap_time(bad) is None


class TestParseClockOffset:
    def test_positive(self):
        assert parse_clock_offset("03:00:00") == timedelta(hours=3)

    def test_negative(self):
        assert parse_clock_offset("-05:30:00") == timedelta(hours=-5, minutes=-30)

    @pytest.mark.parametrize("bad", [None, "", "3:00", "aa:bb:cc"])
    def test_absent_or_garbage_returns_none(self, bad: str | None):
        assert parse_clock_offset(bad) is None
