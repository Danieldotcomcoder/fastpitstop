"""A tiny scripted race for the calls tests: every car's lap times and
stops written out, so the truth of each rejoin and duel is known exactly.

A uniquely named module (not `conftest`) so test modules import it
directly, like `kernel_fixture`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl

from f1_domain import SessionKey, SessionType
from f1_kernel import SessionFrames
from f1_kernel.schemas import LAP_FRAME_SCHEMA, STINT_FRAME_SCHEMA, empty_frame

KEY = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
START = datetime(2024, 3, 2, 15, 0, tzinfo=UTC)
LAP_S = 90.0


def scripted_race(
    *,
    offsets: dict[str, float],
    laps: int,
    stops: dict[str, tuple[int, float]] | None = None,
    neutral_laps: frozenset[int] = frozenset(),
) -> SessionFrames:
    """Every car laps in `LAP_S`, starting `offsets[car]` seconds behind
    the line. `stops[car] = (in_lap, loss_s)` pits it at the end of
    `in_lap`, losing `loss_s` split over the in-lap and the out-lap.
    `neutral_laps` run under a safety car (for every car)."""
    stops = stops or {}
    rows: list[dict[str, object]] = []
    for driver, offset in offsets.items():
        clock = START + timedelta(seconds=offset)
        stop = stops.get(driver)
        for lap in range(1, laps + 1):
            lap_time = LAP_S
            if stop is not None and lap == stop[0]:
                lap_time += stop[1] * 0.4
            if stop is not None and lap == stop[0] + 1:
                lap_time += stop[1] * 0.6
            end = clock + timedelta(seconds=lap_time)
            second = stop is not None and lap > stop[0]
            neutral = lap in neutral_laps
            rows.append(
                {
                    "session_year": KEY.year,
                    "session_round": KEY.round,
                    "session_type": KEY.session_type.value,
                    "driver": driver,
                    "lap_number": lap,
                    "lap_start_utc": clock,
                    "lap_end_utc": end,
                    "lap_time_s": lap_time,
                    "sector1_s": None,
                    "sector2_s": None,
                    "sector3_s": None,
                    "is_personal_best": False,
                    "compound": "hard" if second else "medium",
                    "tyre_age": lap - stop[0] if second and stop else lap,
                    "stint_number": 2 if second else 1,
                    "position": None,
                    "gap_ahead_s": None,
                    "gap_ahead_min_s": None,
                    "gap_ahead_samples": 0,
                    "gap_to_leader_s": None,
                    "track_status": "sc" if neutral else "green",
                    "track_status_flags": ["sc"] if neutral else [],
                    "is_pit_in": stop is not None and lap == stop[0],
                    "is_pit_out": stop is not None and lap == stop[0] + 1,
                    "provenance": "historical",
                }
            )
            clock = end
    frame = pl.DataFrame(rows, schema=LAP_FRAME_SCHEMA).sort(["driver", "lap_number"])
    return SessionFrames(
        session_key=KEY, laps=frame, stints=empty_frame(STINT_FRAME_SCHEMA), circuit=None
    )


def released_by(frames: SessionFrames, moment: datetime) -> SessionFrames:
    """The race as a viewer at `moment` has it: every lap completed by then."""
    laps = frames.laps.filter(pl.col("lap_end_utc") <= moment)
    return SessionFrames(
        session_key=frames.session_key,
        laps=laps,
        stints=frames.stints,
        circuit=frames.circuit,
        event_name=frames.event_name,
    )
