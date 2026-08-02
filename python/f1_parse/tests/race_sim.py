"""Deterministic synthetic race recording for the state-machine replay tests.

No real recording exists yet (Phase 0's live-capture exit criterion is
still pending a race weekend — see docs/PHASE0.md), so this module builds
one: a full 20-car race written through the REAL `f1_recording` writer
and consumed through the REAL `f1_replay` replayer, in the real wire
shapes (partial TimingData deltas, sparse-list stint updates, duplicate
re-sends, a sentinel-marked reconnect with a full resubscribe snapshot,
a retirement, lapped backmarkers). Because the race is synthesized, the
tests can assert EXACT ground truth — lap counts, lap times, pit laps,
stint compounds, final classification — not just plausibility. When a
real recording lands, keep these tests and add a sibling suite pinned
against that race's known results.

Everything is deterministic: one seeded RNG, timestamps derived from
simulated race time, and a stable message ordering. Building the same
race twice yields byte-identical recordings.

Feed behaviors deliberately mirrored, because the machine must survive
them:
- every lap-crossing message is RE-SENT once (fresh seq, same content);
- sector values are cleared ("") before the next lap's values arrive;
- pit stops span several messages (InPit -> in-lap crossing -> new stint
  entry -> pit-out), and tyre age ticks up in trailing TimingAppData;
- one driver retires and sinks through the classification;
- the four slowest cars finish laps down (gaps become "1 L" / "2 L");
- a mid-race disconnect: `_meta.reconnect` + `_meta.subscribe_snapshot`
  reflecting exactly what had been sent up to that instant. The
  reconnect is placed at a scanned "quiet" moment — outside every pit
  sequence and clear of every crossing — because a resubscribe snapshot
  can only be honest about state the wire has actually carried.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import JsonValue

from f1_domain import Compound
from f1_parse import parse_lap_time
from f1_recording import Manifest, RecordingWriter

T0_WALL = 1_709_391_600.0  # 2024-03-02 15:00:00 UTC
TOTAL_LAPS = 57
SESSION_PATH = "2024/2024-03-02_Simulated_Grand_Prix/2024-03-02_Race/"

GRID: list[tuple[str, str, str]] = [
    ("1", "VER", "Red Bull Racing"),
    ("16", "LEC", "Ferrari"),
    ("63", "RUS", "Mercedes"),
    ("11", "PER", "Red Bull Racing"),
    ("44", "HAM", "Mercedes"),
    ("55", "SAI", "Ferrari"),
    ("14", "ALO", "Aston Martin"),
    ("4", "NOR", "McLaren"),
    ("81", "PIA", "McLaren"),
    ("18", "STR", "Aston Martin"),
    ("27", "HUL", "Haas F1 Team"),
    ("22", "TSU", "RB"),
    ("23", "ALB", "Williams"),
    ("2", "SAR", "Williams"),
    ("77", "BOT", "Kick Sauber"),
    ("24", "ZHO", "Kick Sauber"),
    ("3", "RIC", "RB"),
    ("20", "MAG", "Haas F1 Team"),
    ("10", "GAS", "Alpine"),
    ("31", "OCO", "Alpine"),
]

RETIREE = "20"
RETIRE_AFTER_LAP = 40

_COMPOUND_WIRE = {
    Compound.SOFT: "SOFT",
    Compound.MEDIUM: "MEDIUM",
    Compound.HARD: "HARD",
}


@dataclass(frozen=True)
class RaceTruth:
    """Exact expected outcomes, recorded while the messages are generated."""

    laps_by_driver: dict[str, int]
    lap_times: dict[tuple[str, int], timedelta]
    personal_bests: dict[tuple[str, int], bool]
    tyre_age_at_lap: dict[tuple[str, int], int]
    compound_at_lap: dict[tuple[str, int], Compound]
    best_lap_by_driver: dict[str, timedelta]
    pit_laps: dict[str, list[int]]
    """In-lap numbers, per driver."""
    stint_compounds: dict[str, list[Compound]]
    stint_start_laps: dict[str, list[int]]
    final_positions: dict[str, int]
    final_gap_strings: dict[str, str]
    """The last gap-to-leader string sent per driver, verbatim."""
    sector_event_count: int
    weather_event_count: int
    race_control_stream_count: int
    session_status_sequence: list[str]
    track_status_count: int
    team_radio_count: int
    unknown_topic_count: int
    reconnect_sentinel_seqs: set[int]
    """Seqs of the two mid-stream `_meta.*` records (seeding must be silent)."""


@dataclass
class _Message:
    time: float
    order: int
    topic: str
    payload: JsonValue


@dataclass
class _DriverPlan:
    number: str
    laps: int
    lap_times: list[float]
    """lap_times[L-1] = duration of lap L, seconds."""
    cross: list[float]
    """cross[L-1] = wall-offset seconds when lap L completes."""
    pit_laps: list[int]
    compounds: list[Compound]


@dataclass
class _DriverSent:
    """Exactly what has been put on the wire for one driver so far — the
    resubscribe snapshot is built from this and nothing else, so snapshot
    and wire can never disagree."""

    laps: int = 0
    last_lap: str | None = None
    best_lap_s: float | None = None
    best_lap_str: str | None = None
    position: int = 0
    gap: str = ""
    interval: str = ""
    in_pit: bool = False
    pit_stops: int = 0
    stints: list[dict[str, JsonValue]] = field(default_factory=list[dict[str, JsonValue]])


def _fmt_lap(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    minutes, rem = divmod(total_ms, 60_000)
    return f"{minutes}:{rem // 1000:02d}.{rem % 1000:03d}"


def _fmt_sector(seconds: float) -> str:
    return f"{seconds:.3f}"


def _utc(offset: float) -> str:
    instant = datetime.fromtimestamp(T0_WALL + offset, tz=UTC)
    return instant.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class _Sim:
    def __init__(self) -> None:
        self.rng = random.Random(2024)
        self.plans: dict[str, _DriverPlan] = {}
        self.messages: list[_Message] = []
        self._order = 0
        self._build_plans()

    # -- planning ------------------------------------------------------

    def _build_plans(self) -> None:
        for index, (number, _, _) in enumerate(GRID):
            laps = TOTAL_LAPS
            if number == RETIREE:
                laps = RETIRE_AFTER_LAP
            elif index >= 16:
                laps = TOTAL_LAPS - 2  # backmarkers get lapped and finish short
            if index % 2 == 0:
                pit_laps = [27 + index % 5]
                compounds = [Compound.MEDIUM, Compound.HARD]
            else:
                pit_laps = [18 + index % 4, 38 + index % 4]
                compounds = [Compound.SOFT, Compound.HARD, Compound.MEDIUM]
            pit_laps = [lap for lap in pit_laps if lap < laps]
            compounds = compounds[: len(pit_laps) + 1]
            pace = 96.0 + index * 0.06 + (2.5 if index >= 16 else 0.0)
            lap_times: list[float] = []
            for lap in range(1, laps + 1):
                duration = pace + self.rng.uniform(-0.8, 0.8)
                if lap in pit_laps:
                    duration += 4.0  # slow in-lap
                if lap - 1 in pit_laps:
                    duration += 18.0  # out-lap: stationary time + pit lane
                lap_times.append(duration)
            elapsed = index * 0.5  # grid stagger at lights-out
            cross: list[float] = []
            for duration in lap_times:
                elapsed += duration
                cross.append(elapsed)
            self.plans[number] = _DriverPlan(
                number=number,
                laps=laps,
                lap_times=lap_times,
                cross=cross,
                pit_laps=pit_laps,
                compounds=compounds,
            )

    def _pick_reconnect_time(self) -> float:
        """A deterministic quiet moment mid-race: outside every pit
        sequence (entry message through out-lap crossing) and >2s away
        from every crossing, so the resubscribe snapshot never has to
        describe a state the wire only half-delivered."""
        windows: list[tuple[float, float]] = []
        crossings: list[float] = []
        for plan in self.plans.values():
            crossings.extend(plan.cross)
            for pit_lap in plan.pit_laps:
                in_cross = plan.cross[pit_lap - 1]
                out_cross = plan.cross[pit_lap]
                windows.append((in_cross - 10.0, out_cross + 2.0))
        candidate = self.plans[GRID[0][0]].cross[32] + 30.0  # leader mid-lap 34
        for _ in range(500):
            in_pit_window = any(start <= candidate <= end for start, end in windows)
            near_crossing = any(abs(cross - candidate) < 2.0 for cross in crossings)
            if not in_pit_window and not near_crossing:
                return candidate
            candidate += 5.0
        raise AssertionError("no quiet reconnect slot found (sim plans changed?)")

    # -- message emission ----------------------------------------------

    def _emit(self, time: float, topic: str, payload: JsonValue) -> None:
        self.messages.append(_Message(time=time, order=self._order, topic=topic, payload=payload))
        self._order += 1

    def _timing(self, time: float, lines: dict[str, JsonValue]) -> None:
        self._emit(time, "TimingData", {"Lines": lines})

    def _app(self, time: float, number: str, line: JsonValue) -> None:
        self._emit(time, "TimingAppData", {"Lines": {number: line}})

    # -- the build ------------------------------------------------------

    def build(self) -> tuple[JsonValue, list[_Message], RaceTruth]:
        sent: dict[str, _DriverSent] = {}
        for position, (number, _, _) in enumerate(GRID, start=1):
            sent[number] = _DriverSent(position=position)

        truth_lap_times: dict[tuple[str, int], timedelta] = {}
        truth_pb: dict[tuple[str, int], bool] = {}
        truth_age: dict[tuple[str, int], int] = {}
        truth_compound: dict[tuple[str, int], Compound] = {}
        truth_stint_starts: dict[str, list[int]] = {n: [1] for n in self.plans}
        sector_events = 0

        race_end = max(plan.cross[-1] for plan in self.plans.values())

        # Weather: one sample per minute; sample 0 seeds the snapshot
        # baseline, and sample 5 repeats sample 4 verbatim (dedup food).
        minutes = int(race_end // 60) + 2
        weather_samples: list[dict[str, JsonValue]] = []
        for k in range(minutes):
            step = k - 1 if k == 5 else k
            weather_samples.append(
                {
                    "AirTemp": f"{27.8 + 0.01 * step:.2f}",
                    "TrackTemp": f"{34.1 + 0.02 * step:.2f}",
                    "Humidity": "58.0",
                    "Pressure": "1018.7",
                    "WindSpeed": "2.0",
                    "WindDirection": "212",
                    "Rainfall": "0",
                }
            )
        for k in range(1, minutes):
            self._emit(k * 60.0, "WeatherData", dict(weather_samples[k]))
        weather_event_count = minutes - 2  # stream sends minutes-1; one is a dup

        # Session status timeline (index 0, "Inactive", lives in the snapshot).
        self._emit(
            2.0,
            "SessionData",
            {"StatusSeries": {"1": {"Utc": _utc(2.0), "SessionStatus": "Started"}}},
        )
        self._emit(
            race_end + 5.0,
            "SessionData",
            {"StatusSeries": {"2": {"Utc": _utc(race_end + 5.0), "SessionStatus": "Finished"}}},
        )
        self._emit(
            race_end + 65.0,
            "SessionData",
            {"StatusSeries": {"3": {"Utc": _utc(race_end + 65.0), "SessionStatus": "Finalised"}}},
        )

        # Race control (index 0 lives in the snapshot) and a mid-race
        # yellow phase on the track-status topic.
        yellow_at = self.plans[GRID[0][0]].cross[29] + 10.0
        green_at = yellow_at + 40.0
        rcm: list[tuple[float, dict[str, JsonValue]]] = [
            (
                3.0,
                {
                    "Utc": _utc(3.0),
                    "Category": "Flag",
                    "Flag": "GREEN",
                    "Scope": "Track",
                    "Message": "GREEN LIGHT PIT EXIT OPEN",
                },
            ),
            (
                yellow_at,
                {
                    "Utc": _utc(yellow_at),
                    "Category": "Flag",
                    "Flag": "YELLOW",
                    "Scope": "Sector",
                    "Sector": 7,
                    "Message": "YELLOW IN TRACK SECTOR 7",
                },
            ),
            (
                green_at,
                {
                    "Utc": _utc(green_at),
                    "Category": "Flag",
                    "Flag": "CLEAR",
                    "Scope": "Sector",
                    "Sector": 7,
                    "Message": "CLEAR IN TRACK SECTOR 7",
                },
            ),
            (
                race_end + 2.0,
                {
                    "Utc": _utc(race_end + 2.0),
                    "Category": "Flag",
                    "Flag": "CHEQUERED",
                    "Scope": "Track",
                    "Message": "CHEQUERED FLAG",
                },
            ),
        ]
        for index, (time, entry) in enumerate(rcm, start=1):
            self._emit(time, "RaceControlMessages", {"Messages": {str(index): dict(entry)}})
        self._emit(yellow_at, "TrackStatus", {"Status": "2", "Message": "Yellow"})
        self._emit(green_at, "TrackStatus", {"Status": "1", "Message": "AllClear"})

        # Team radio: one list-form capture, then sparse deltas.
        radio: list[tuple[float, dict[str, JsonValue]]] = [
            (600.0, {"Utc": _utc(600.0), "RacingNumber": "1", "Path": "TeamRadio/VER_1.mp3"}),
            (1800.0, {"Utc": _utc(1800.0), "RacingNumber": "44", "Path": "TeamRadio/HAM_2.mp3"}),
            (3600.0, {"Utc": _utc(3600.0), "RacingNumber": "16", "Path": "TeamRadio/LEC_3.mp3"}),
        ]
        self._emit(radio[0][0], "TeamRadio", {"Captures": [dict(radio[0][1])]})
        for index, (time, capture) in enumerate(radio[1:], start=1):
            self._emit(time, "TeamRadio", {"Captures": {str(index): dict(capture)}})

        # Heartbeats: unknown-topic passthrough food.
        heartbeat_count = 0
        beat = 10.0
        while beat < race_end:
            self._emit(beat, "Heartbeat", {"Utc": _utc(beat)})
            heartbeat_count += 1
            beat += 300.0

        # Opening stints: one TimingAppData delta per driver just after
        # lights-out (the snapshot's stint lists are empty). `sent` picks
        # them up in the sweep at each driver's first crossing, long
        # before any reconnect can happen.
        for index, (number, _, _) in enumerate(GRID):
            plan = self.plans[number]
            self._app(
                5.0 + index * 0.3,
                number,
                {
                    "Stints": {
                        "0": {
                            "Compound": _COMPOUND_WIRE[plan.compounds[0]],
                            "New": "true",
                            "StartLaps": 0,
                            "TotalLaps": 0,
                        }
                    }
                },
            )

        # Pit sequences (self-contained payloads, times precomputed).
        for number, plan in self.plans.items():
            for stop, pit_lap in enumerate(plan.pit_laps, start=1):
                in_cross = plan.cross[pit_lap - 1]
                self._timing(in_cross - 5.0, {number: {"InPit": True, "NumberOfPitStops": stop}})
                self._app(
                    in_cross + 9.0,
                    number,
                    {
                        "Stints": {
                            str(stop): {
                                "Compound": _COMPOUND_WIRE[plan.compounds[stop]],
                                "New": "true",
                                "StartLaps": 0,
                                "TotalLaps": 0,
                            }
                        }
                    },
                )
                self._timing(in_cross + 14.0, {number: {"InPit": False, "PitOut": True}})

        # Retirement marker (unmodeled keys: pass through with no event).
        self._timing(
            self.plans[RETIREE].cross[-1] + 20.0, {RETIREE: {"Retired": True, "Stopped": True}}
        )

        # ---- The crossing sweep -------------------------------------
        # Every lap completion in time order, maintaining the running
        # classification exactly the way the wire would show it.
        crossings = sorted(
            (plan.cross[lap - 1], number, lap)
            for number, plan in self.plans.items()
            for lap in range(1, plan.laps + 1)
        )
        laps_done: dict[str, int] = dict.fromkeys(self.plans, 0)
        last_cross: dict[str, float] = {
            number: index * 0.5 for index, (number, _, _) in enumerate(GRID)
        }
        stint_index: dict[str, int] = dict.fromkeys(self.plans, 0)
        stint_total_sent: dict[str, int] = dict.fromkeys(self.plans, 0)
        current_lap_sent = 1
        reconnect_at = self._pick_reconnect_time()
        reconnect_done = False

        for t_cross, number, lap in crossings:
            if not reconnect_done and t_cross > reconnect_at:
                self._emit(reconnect_at, "_meta.reconnect", {"attempt": 1})
                self._emit(
                    reconnect_at + 0.5,
                    "_meta.subscribe_snapshot",
                    self._resubscribe_snapshot(
                        sent,
                        weather_samples,
                        current_lap_sent,
                        rcm,
                        radio,
                        yellow_at,
                        green_at,
                        reconnect_at,
                    ),
                )
                reconnect_done = True

            plan = self.plans[number]
            state = sent[number]
            duration = plan.lap_times[lap - 1]
            lap_str = _fmt_lap(duration)

            # Mirror wire history into `sent` before this crossing reads it:
            # the opening stint (first crossing) or the pit-stop stint entry
            # that went out 9s after the previous (in-lap) crossing.
            if lap == 1:
                state.stints.append(
                    {
                        "Compound": _COMPOUND_WIRE[plan.compounds[0]],
                        "New": "true",
                        "StartLaps": 0,
                        "TotalLaps": 0,
                    }
                )
            if lap - 1 in plan.pit_laps:
                stop = plan.pit_laps.index(lap - 1) + 1
                stint_index[number] = stop
                stint_total_sent[number] = 0
                state.stints.append(
                    {
                        "Compound": _COMPOUND_WIRE[plan.compounds[stop]],
                        "New": "true",
                        "StartLaps": 0,
                        "TotalLaps": 0,
                    }
                )
                state.in_pit = False
                state.pit_stops = stop
                truth_stint_starts[number].append(lap)
            if lap in plan.pit_laps:
                state.in_pit = True
                state.pit_stops = plan.pit_laps.index(lap) + 1

            # Sector messages (S3 rides with the crossing; values are
            # cleared before the next lap's arrive, like the real feed).
            s1 = duration * 0.30
            s2 = duration * 0.34
            s3 = duration - s1 - s2
            self._timing(
                t_cross - duration * 0.55,
                {number: {"Sectors": {"0": {"Value": _fmt_sector(s1)}, "2": {"Value": ""}}}},
            )
            self._timing(
                t_cross - duration * 0.22, {number: {"Sectors": {"1": {"Value": _fmt_sector(s2)}}}}
            )
            sector_events += 3

            # Classification as of this crossing.
            laps_done[number] = lap
            last_cross[number] = t_cross
            ranking = sorted(self.plans, key=lambda n: (-laps_done[n], last_cross[n]))
            my_rank = ranking.index(number) + 1
            leader = ranking[0]
            if number == leader:
                gap = ""
            elif laps_done[leader] > lap:
                gap = f"{laps_done[leader] - lap} L"
            else:
                gap = f"+{t_cross - self.plans[leader].cross[lap - 1]:.3f}"
            if my_rank == 1:
                interval = ""
            else:
                ahead = ranking[my_rank - 2]
                if laps_done[ahead] > lap:
                    interval = f"{laps_done[ahead] - lap} L"
                else:
                    interval = f"+{t_cross - self.plans[ahead].cross[lap - 1]:.3f}"

            is_pb = state.best_lap_s is None or duration < state.best_lap_s
            line: dict[str, JsonValue] = {
                "NumberOfLaps": lap,
                "LastLapTime": {"Value": lap_str, "PersonalFastest": is_pb},
                "Sectors": {
                    "0": {"Value": ""},
                    "1": {"Value": ""},
                    "2": {"Value": _fmt_sector(s3)},
                },
                "Position": str(my_rank),
                "GapToLeader": gap,
                "IntervalToPositionAhead": {"Value": interval},
            }
            if is_pb:
                state.best_lap_s = duration
                state.best_lap_str = lap_str
                line["BestLapTime"] = {"Value": lap_str}
            lines: dict[str, JsonValue] = {number: line}
            # Bundle position deltas for everyone this crossing reshuffled.
            for other_rank, other in enumerate(ranking, start=1):
                if other != number and sent[other].position != other_rank:
                    lines[other] = {"Position": str(other_rank)}
                    sent[other].position = other_rank
            self._timing(t_cross, lines)
            # The feed habitually re-sends completed-lap data: fresh seq,
            # same content — it must be inert.
            self._timing(
                t_cross + 0.4,
                {
                    number: {
                        "NumberOfLaps": lap,
                        "LastLapTime": {"Value": lap_str, "PersonalFastest": is_pb},
                    }
                },
            )

            truth_lap_times[(number, lap)] = parse_lap_time(lap_str) or timedelta()
            truth_pb[(number, lap)] = is_pb
            truth_age[(number, lap)] = stint_total_sent[number]
            truth_compound[(number, lap)] = plan.compounds[stint_index[number]]

            state.laps = lap
            state.last_lap = lap_str
            state.position = my_rank
            state.gap = gap
            state.interval = interval

            # Tyre-age tick lands after the crossing, like the real feed;
            # mirrored into `sent` so a later snapshot agrees with the wire.
            stint_total_sent[number] += 1
            state.stints[stint_index[number]]["TotalLaps"] = stint_total_sent[number]
            self._app(
                t_cross + 1.2,
                number,
                {"Stints": {str(stint_index[number]): {"TotalLaps": stint_total_sent[number]}}},
            )

            if my_rank == 1 and current_lap_sent < lap + 1 <= TOTAL_LAPS:
                current_lap_sent = lap + 1
                self._emit(t_cross + 0.3, "LapCount", {"CurrentLap": current_lap_sent})

        # ---- Assemble ------------------------------------------------
        self.messages.sort(key=lambda m: (m.time, m.order))
        final_ranking = sorted(self.plans, key=lambda n: (-laps_done[n], last_cross[n]))
        truth = RaceTruth(
            laps_by_driver={n: plan.laps for n, plan in self.plans.items()},
            lap_times=truth_lap_times,
            personal_bests=truth_pb,
            tyre_age_at_lap=truth_age,
            compound_at_lap=truth_compound,
            best_lap_by_driver={
                n: parse_lap_time(sent[n].best_lap_str) or timedelta() for n in self.plans
            },
            pit_laps={n: list(plan.pit_laps) for n, plan in self.plans.items()},
            stint_compounds={n: list(plan.compounds) for n, plan in self.plans.items()},
            stint_start_laps=truth_stint_starts,
            final_positions={n: final_ranking.index(n) + 1 for n in self.plans},
            final_gap_strings={n: sent[n].gap for n in self.plans},
            sector_event_count=sector_events,
            weather_event_count=weather_event_count,
            race_control_stream_count=len(rcm),
            session_status_sequence=["Started", "Finished", "Finalised"],
            track_status_count=2,
            team_radio_count=len(radio),
            unknown_topic_count=heartbeat_count,
            reconnect_sentinel_seqs=set(),  # filled in by build_race_recording
        )
        return self._initial_snapshot(weather_samples), self.messages, truth

    # -- snapshots ------------------------------------------------------

    def _session_info(self) -> JsonValue:
        return {
            "Meeting": {"Name": "Simulated Grand Prix", "Key": 9999},
            "Name": "Race",
            "Type": "Race",
            "Key": 9999,
            "Path": SESSION_PATH,
        }

    def _initial_snapshot(self, weather_samples: list[dict[str, JsonValue]]) -> JsonValue:
        timing_lines: dict[str, JsonValue] = {}
        app_lines: dict[str, JsonValue] = {}
        driver_list: dict[str, JsonValue] = {}
        for position, (number, tla, team) in enumerate(GRID, start=1):
            timing_lines[number] = {
                "NumberOfLaps": 0,
                "Position": str(position),
                "GapToLeader": "",
                "IntervalToPositionAhead": {"Value": ""},
                "InPit": False,
                "NumberOfPitStops": 0,
            }
            app_lines[number] = {"Stints": []}
            driver_list[number] = {
                "RacingNumber": number,
                "Tla": tla,
                "TeamName": team,
                "Line": position,
            }
        return {
            "SessionInfo": self._session_info(),
            "DriverList": driver_list,
            "TimingData": {"Lines": timing_lines},
            "TimingAppData": {"Lines": app_lines},
            "LapCount": {"CurrentLap": 1, "TotalLaps": TOTAL_LAPS},
            "TrackStatus": {"Status": "1", "Message": "AllClear"},
            "SessionData": {"StatusSeries": [{"Utc": _utc(0.0), "SessionStatus": "Inactive"}]},
            "WeatherData": dict(weather_samples[0]),
            "RaceControlMessages": {
                "Messages": [
                    {"Utc": _utc(0.0), "Category": "Other", "Message": "DRIVERS TO THE GRID"}
                ]
            },
        }

    def _resubscribe_snapshot(
        self,
        sent: dict[str, _DriverSent],
        weather_samples: list[dict[str, JsonValue]],
        current_lap_sent: int,
        rcm: list[tuple[float, dict[str, JsonValue]]],
        radio: list[tuple[float, dict[str, JsonValue]]],
        yellow_at: float,
        green_at: float,
        at: float,
    ) -> JsonValue:
        """The resubscribe snapshot: exactly the state already on the wire
        at `at` — nothing the feed hasn't said yet, nothing it unsaid."""
        timing_lines: dict[str, JsonValue] = {}
        app_lines: dict[str, JsonValue] = {}
        driver_list: dict[str, JsonValue] = {}
        for number, tla, team in GRID:
            state = sent[number]
            line: dict[str, JsonValue] = {
                "NumberOfLaps": state.laps,
                "Position": str(state.position),
                "GapToLeader": state.gap,
                "IntervalToPositionAhead": {"Value": state.interval},
                "InPit": state.in_pit,
                "NumberOfPitStops": state.pit_stops,
            }
            if state.last_lap is not None:
                line["LastLapTime"] = {"Value": state.last_lap}
            if state.best_lap_str is not None:
                line["BestLapTime"] = {"Value": state.best_lap_str}
            timing_lines[number] = line
            app_lines[number] = {"Stints": [dict(stint) for stint in state.stints]}
            driver_list[number] = {
                "RacingNumber": number,
                "Tla": tla,
                "TeamName": team,
                "Line": state.position,
            }
        if at >= green_at:
            track_status: JsonValue = {"Status": "1", "Message": "AllClear"}
        elif at >= yellow_at:
            track_status = {"Status": "2", "Message": "Yellow"}
        else:
            track_status = {"Status": "1", "Message": "AllClear"}
        messages: list[JsonValue] = [
            {"Utc": _utc(0.0), "Category": "Other", "Message": "DRIVERS TO THE GRID"}
        ]
        messages.extend(dict(entry) for time, entry in rcm if time <= at)
        captures: list[JsonValue] = [dict(capture) for time, capture in radio if time <= at]
        last_weather = weather_samples[min(int(at // 60), len(weather_samples) - 1)]
        return {
            "SessionInfo": self._session_info(),
            "DriverList": driver_list,
            "TimingData": {"Lines": timing_lines},
            "TimingAppData": {"Lines": app_lines},
            "LapCount": {"CurrentLap": current_lap_sent, "TotalLaps": TOTAL_LAPS},
            "TrackStatus": track_status,
            "SessionData": {
                "StatusSeries": [
                    {"Utc": _utc(0.0), "SessionStatus": "Inactive"},
                    {"Utc": _utc(2.0), "SessionStatus": "Started"},
                ]
            },
            "WeatherData": dict(last_weather),
            "RaceControlMessages": {"Messages": messages},
            "TeamRadio": {"Captures": captures},
        }


def build_race_recording(directory: Path) -> RaceTruth:
    """Write the synthetic race as a real `f1_recording` directory; return
    the exact expected outcomes."""
    snapshot, messages, truth = _Sim().build()
    manifest = Manifest(
        year=2024,
        grand_prix="Simulated Grand Prix",
        session_type="Race",
        session_start_utc=datetime.fromtimestamp(T0_WALL, tz=UTC),
        recorder_version="race-sim/1.0",
        topics=sorted({m.topic for m in messages if not m.topic.startswith("_")}),
    )
    sentinel_seqs: set[int] = set()
    with RecordingWriter(directory, manifest, snapshot) as writer:
        for message in messages:
            record = writer.append(
                message.topic,
                message.payload,
                ts_wall=T0_WALL + message.time,
                ts_mono=int(message.time * 1_000_000_000),
                sync=False,
            )
            if message.topic.startswith("_meta."):
                sentinel_seqs.add(record.seq)
    return replace(truth, reconnect_sentinel_seqs=sentinel_seqs)
