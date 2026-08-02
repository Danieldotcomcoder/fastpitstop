"""Layer two: the session state machine — where deltas become facts.

`SessionStateMachine` consumes the raw record stream (through layer one's
`decode`) and does the two jobs layer one refused: fold partial deltas
into materialized per-session/per-driver state (via `deep_merge`), and
emit `f1_domain.DomainEvent`s on MEANINGFUL transitions only.

Design rules (each mirrors a Phase 1 invariant in CLAUDE.md):

- Pure transform. `apply()` reads nothing but its input record and the
  machine's own accumulated state: no wall clock (event timestamps come
  from the record and its payload), no randomness, no I/O. The same
  record stream produces the same event stream, every time.
- Change-detection everywhere. The feed re-sends the same values
  constantly, so every emission compares against last-seen state (per
  driver, per field) and fires only on genuine change. Re-applying the
  *same* record (same seq) is additionally suppressed wholesale by a
  monotonic-seq guard — the `_meta.*` sentinels are exempt because the
  replayer's initial-snapshot record carries a placeholder seq (see
  docs/DATA_CONTRACTS.md: rely on emission order, not seq, for it).
- Seeding is silent. `seed()` — called directly at live connect time, or
  triggered by the `_meta.subscribe_snapshot` records the replayer and
  recorder inject at connect/reconnect — adopts the snapshot as the new
  baseline and emits NOTHING: neither for unchanged values nor for
  changes that happened during a connection gap. Gap-spanning changes
  surface in `state`; events are reserved for transitions actually
  observed in the delta stream.
- Every event's `source_seq` is the triggering record's seq, verbatim.

Transition semantics worth knowing (the fine print lives on each
`_apply_*` method):

- LapCompleted needs to OBSERVE the increment: a lap number's first-ever
  sighting only establishes the baseline. The lap time must be FRESH — a
  "lap time changed since the last completion" flag bridges the feed's
  orderings (time and lap count in one message, time first, or count
  first with the time trailing) so a stale previous time is never
  attributed to a new lap.
- StintStarted fires when a NEW stint index appears in TimingAppData
  with a compound (waiting for the compound if it trails) — that is the
  authoritative "new tyres" signal, and unlike pit-exit it also covers
  the opening stint and red-flag tyre changes.
- Lap attribution for mid-lap events (sectors, pit in/out, stint
  starts) is "the lap in progress", i.e. completed laps + 1 at the
  moment of the message.
- `SessionState.weather` only ever holds an emitted event, so its
  provenance (`ts_utc`/`source_seq`) is always real; seeding sets the
  weather dedup baseline but not the state.

One machine instance covers one session (one recording / one live
connection); feeding it a second session's stream is undefined.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TypedDict

from pydantic import JsonValue

from f1_domain import (
    CarPosition,
    CarTelemetry,
    Compound,
    DomainEvent,
    DriverListUpdated,
    DriverRef,
    DriverState,
    GapUpdated,
    LapCompleted,
    PitEntry,
    PitExit,
    PositionChanged,
    RaceControlMessage,
    SectorCompleted,
    SessionKey,
    SessionState,
    SessionStatusChanged,
    StintStarted,
    TeamRadioClip,
    TrackStatusChanged,
    TrackStatusKind,
    UnknownEvent,
    WeatherSample,
)
from f1_parse._json import as_bool_flag, as_dict, as_int, as_str
from f1_parse.car_data import CarDataSample
from f1_parse.decode import DecodedMessage, Family, decode
from f1_parse.driver_list import DriverEntryDelta, decode_driver_list
from f1_parse.lap_count import LapCountDecoded, decode_lap_count
from f1_parse.merge import deep_merge
from f1_parse.position import PositionSample
from f1_parse.race_control import RaceControlEntry, decode_race_control
from f1_parse.session_data import SessionDataDecoded, decode_session_data
from f1_parse.session_info import SessionInfoDecoded, decode_session_info
from f1_parse.team_radio import TeamRadioCapture, decode_team_radio
from f1_parse.times import parse_lap_time
from f1_parse.timing import TimingLinesDelta, decode_timing
from f1_parse.track_status import TrackStatusDecoded, decode_track_status
from f1_parse.weather import WeatherDecoded, decode_weather
from f1_recording import RawRecord

# The recording sentinels (docs/DATA_CONTRACTS.md, "Live recorder sentinel
# topics"). Named identically in f1_ingest (writer side) and f1_replay
# (reader side); repeated here rather than imported so f1_parse depends on
# the FORMAT, not on either of those packages.
_SUBSCRIBE_SNAPSHOT_TOPIC = "_meta.subscribe_snapshot"

_STATIC_BASE = "https://livetiming.formula1.com/static/"
"""Prefix turning SessionInfo's `Path` + TeamRadio's relative paths into
absolute audio URLs (the same base FastF1 uses for livetiming statics)."""

_MAX_SECTORS = 10
_MAX_STINTS = 50
"""Sanity caps on sparse-list indices, so one corrupt message can't balloon
the merged structures."""

_LAP_GAP_RE = re.compile(r"^\+?\s*(\d+)\s*(?:L|LAP|LAPS)$", re.IGNORECASE)

_TRACK_STATUS_KINDS: dict[str, TrackStatusKind] = {
    # Wire codes per the feed (FastF1's docs agree); "7" is "VSC ending",
    # which is still a VSC condition until the track goes green.
    "1": TrackStatusKind.GREEN,
    "2": TrackStatusKind.YELLOW,
    "4": TrackStatusKind.SC,
    "5": TrackStatusKind.RED,
    "6": TrackStatusKind.VSC,
    "7": TrackStatusKind.VSC,
}

_COMPOUNDS: dict[str, Compound] = {
    "SOFT": Compound.SOFT,
    "MEDIUM": Compound.MEDIUM,
    "HARD": Compound.HARD,
    "INTERMEDIATE": Compound.INTERMEDIATE,
    "WET": Compound.WET,
}


@dataclass(frozen=True, slots=True)
class LapGap:
    """A lapped car's gap to the leader: whole laps behind, NOT a duration."""

    laps: int


def parse_gap(value: str | None) -> timedelta | LapGap | None:
    """Parse one gap/interval string from the timing feed.

    "+1.234" / "17.5" / "1:02.345" -> timedelta; "1 L" / "+2 LAPS" ->
    `LapGap` (a distinct type — a lap count must never be mistaken for a
    duration); ""/None/unparseable -> None. The leader's own gap arrives
    as the empty string, so the leader comes back as None.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    lapped = _LAP_GAP_RE.match(text)
    if lapped:
        return LapGap(laps=int(lapped.group(1)))
    return parse_lap_time(text)


def _map_compound(text: str | None) -> Compound | None:
    if text is None:
        return None
    return _COMPOUNDS.get(text.strip().upper(), Compound.UNKNOWN)


def _non_empty(text: str | None) -> str | None:
    if text is None:
        return None
    stripped = text.strip()
    return stripped if stripped else None


def _number_sort_key(number: str) -> str:
    """Racing numbers sort numerically ("2" < "10"), non-numeric keys last."""
    return number.rjust(8, "0") if number.isdigit() else "~" + number


def _indexed[T](items: Sequence[T], explicit: Callable[[T], int | None]) -> list[tuple[int, T]]:
    """Pair accumulated-series entries with their effective index: the
    explicit sparse-dict index when present, else the list position (the
    snapshot/list form always restarts from 0 — it is the full series)."""
    result: list[tuple[int, T]] = []
    position = 0
    for item in items:
        index = explicit(item)
        if index is None:
            index = position
            position += 1
        result.append((index, item))
    return result


class _Envelope(TypedDict):
    session_key: SessionKey
    ts_utc: datetime
    source_seq: int


@dataclass(frozen=True, slots=True)
class _TimingFacts:
    """The transition-relevant slice of one driver's merged TimingData line.

    Values are kept VERBATIM (strings) and compared verbatim, so change
    detection can never be fooled by parse-time information loss; parsing
    happens only at emission. Empty strings normalize to None ("no value")
    everywhere except where the feed means them as a reset.
    """

    laps: int | None = None
    last_lap: str | None = None
    last_lap_pb: bool | None = None
    best_lap: str | None = None
    position: int | None = None
    gap_leader: str | None = None
    interval: str | None = None
    in_pit: bool | None = None
    pit_stops: int | None = None
    sectors: tuple[str | None, ...] = ()


@dataclass(frozen=True, slots=True)
class _StintFacts:
    """One stint entry from a driver's merged TimingAppData line."""

    compound: str | None = None
    total_laps: int | None = None
    """Laps run on the set during THIS stint (the feed ticks it per lap)."""
    start_laps: int | None = None
    """Laps already on the set when the stint began (>0 = used set)."""


@dataclass(slots=True)
class _DriverTracker:
    """Mutable per-driver accumulation. Internal only — `state` projects
    trackers into frozen `DriverState`s on demand."""

    timing: JsonValue = None
    """Merged TimingData line (raw, verbatim)."""
    app: JsonValue = None
    """Merged TimingAppData line (raw, verbatim)."""
    facts: _TimingFacts = field(default_factory=_TimingFacts)
    stints: tuple[_StintFacts, ...] = ()
    position: int | None = None
    """Canonical classification position — fed by both TimingData's
    `Position` and DriverList's `Line`, deduplicated here."""
    last_emitted_lap: int | None = None
    """Highest lap number a LapCompleted was emitted (or seeded) for."""
    lap_time_dirty: bool = False
    """True when LastLapTime changed since the last LapCompleted — i.e.
    the merged lap time is FRESH, not a leftover from the previous lap."""
    announced_stints: set[int] = field(default_factory=set[int])
    """Stint indices a StintStarted was emitted (or seeded) for."""


def _timing_facts(merged: JsonValue) -> _TimingFacts:
    line = as_dict(merged) or {}
    last_lap = as_dict(line.get("LastLapTime")) or {}
    best_lap = as_dict(line.get("BestLapTime")) or {}
    return _TimingFacts(
        laps=as_int(line.get("NumberOfLaps")),
        last_lap=_non_empty(as_str(last_lap.get("Value"))),
        last_lap_pb=as_bool_flag(last_lap.get("PersonalFastest")),
        best_lap=_non_empty(as_str(best_lap.get("Value"))),
        position=as_int(line.get("Position")),
        gap_leader=_gap_text(line, "GapToLeader", "TimeDiffToFastest"),
        interval=_gap_text(line, "IntervalToPositionAhead", "TimeDiffToPositionAhead"),
        in_pit=as_bool_flag(line.get("InPit")),
        pit_stops=as_int(line.get("NumberOfPitStops")),
        sectors=_sector_values(line.get("Sectors")),
    )


def _delta_carries_lap_time(delta: dict[str, JsonValue]) -> bool:
    """True when this delta itself re-states a last-lap time value (a
    flag-only `LastLapTime` update, e.g. just `PersonalFastest`, doesn't
    count)."""
    last_lap = as_dict(delta.get("LastLapTime"))
    if last_lap is None:
        return False
    return _non_empty(as_str(last_lap.get("Value"))) is not None


def _gap_text(line: dict[str, JsonValue], race_key: str, practice_key: str) -> str | None:
    """Extract a gap string: races send `GapToLeader`/`IntervalToPositionAhead`
    (plain string or `{"Value": ...}`), practice/quali send the
    `TimeDiffTo*` pair instead — "leader" then means "fastest"."""
    for key in (race_key, practice_key):
        value = line.get(key)
        if isinstance(value, dict):
            value = value.get("Value")
        text = _non_empty(as_str(value))
        if text is not None:
            return text
    return None


def _sparse_items(value: JsonValue | None, cap: int) -> list[tuple[int, JsonValue]]:
    """Index/value pairs from a merged feed list, which — depending on
    whether a snapshot (list) or a delta (digit-keyed dict) arrived first —
    may be either shape. Indices beyond `cap` are dropped as corrupt."""
    items: list[tuple[int, JsonValue]] = []
    if isinstance(value, list):
        items = list(enumerate(value))
    elif isinstance(value, dict):
        items = sorted((int(key), item) for key, item in value.items() if key.isdigit())
    return [(index, item) for index, item in items if 0 <= index < cap]


def _sector_values(sectors: JsonValue | None) -> tuple[str | None, ...]:
    values: list[str | None] = []
    for index, raw in _sparse_items(sectors, _MAX_SECTORS):
        while len(values) <= index:
            values.append(None)
        entry = as_dict(raw) or {}
        values[index] = _non_empty(as_str(entry.get("Value")))
    return tuple(values)


def _stint_facts(merged_app: JsonValue) -> tuple[_StintFacts, ...]:
    line = as_dict(merged_app) or {}
    result: list[_StintFacts] = []
    for index, raw in _sparse_items(line.get("Stints"), _MAX_STINTS):
        while len(result) <= index:
            result.append(_StintFacts())
        entry = as_dict(raw) or {}
        result[index] = _StintFacts(
            compound=_non_empty(as_str(entry.get("Compound"))),
            total_laps=as_int(entry.get("TotalLaps")),
            start_laps=as_int(entry.get("StartLaps")),
        )
    return tuple(result)


def _tyre_age(stint: _StintFacts) -> int | None:
    """Total laps on the driver's current set, prior usage included."""
    if stint.total_laps is None and stint.start_laps is None:
        return None
    return (stint.total_laps or 0) + (stint.start_laps or 0)


def _in_progress_lap(completed: int | None) -> int | None:
    return completed + 1 if completed is not None else None


class SessionStateMachine:
    """Folds the raw record stream into `SessionState`, emitting
    `DomainEvent`s on meaningful transitions. See the module docstring for
    the semantics; see `apply` for the entry point.
    """

    def __init__(self, session_key: SessionKey) -> None:
        self._session_key = session_key
        self._drivers: dict[str, _DriverTracker] = {}
        self._identities: dict[str, DriverRef] = {}
        self._session_status: str | None = None
        self._current_lap: int | None = None
        self._total_laps: int | None = None
        self._track_status_code: str | None = None
        self._track_status_kind: TrackStatusKind | None = None
        self._track_status_message: str | None = None
        self._weather: WeatherSample | None = None
        self._weather_baseline: tuple[object, ...] | None = None
        self._session_path: str | None = None
        self._rc_seen = 0
        self._tr_seen = 0
        self._status_seen = 0
        self._last_seq: int | None = None

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def apply(self, record: RawRecord) -> list[DomainEvent]:
        """Apply one raw record; return the domain events it caused.

        Pure with respect to the outside world (no I/O, no clock) and
        idempotent per record: re-applying an already-seen seq returns [].
        `_meta.subscribe_snapshot` records trigger a silent (re-)seed;
        `_meta.reconnect` markers are acknowledged and ignored.
        """
        if record.topic.startswith("_meta."):
            # Sentinels bypass the seq guard: the replayer's initial
            # snapshot record carries a placeholder seq shared with the
            # first real record (see docs/DATA_CONTRACTS.md).
            if record.topic == _SUBSCRIBE_SNAPSHOT_TOPIC:
                self.seed(record.payload)
            return []
        if self._last_seq is not None and record.seq <= self._last_seq:
            return []
        self._last_seq = record.seq
        message = decode(record)
        ts = datetime.fromtimestamp(record.ts_wall, tz=UTC)
        seq = record.seq
        data = message.data
        if message.family is Family.TIMING and isinstance(data, TimingLinesDelta):
            return self._apply_timing(message, data, ts, seq)
        if message.family is Family.CAR_DATA and isinstance(data, list):
            return self._apply_car_data(data, ts, seq)
        if message.family is Family.POSITION and isinstance(data, list):
            return self._apply_position(data, ts, seq)
        if message.family is Family.WEATHER and isinstance(data, WeatherDecoded):
            return self._apply_weather(data, ts, seq)
        if message.family is Family.TRACK_STATUS and isinstance(data, TrackStatusDecoded):
            return self._apply_track_status(data, ts, seq)
        if message.family is Family.RACE_CONTROL and isinstance(data, list):
            return self._apply_race_control(data, ts, seq)
        if message.family is Family.TEAM_RADIO and isinstance(data, list):
            return self._apply_team_radio(data, ts, seq)
        if message.family is Family.SESSION_INFO and isinstance(data, SessionInfoDecoded):
            self._apply_session_info(data)
            return []
        if message.family is Family.SESSION_DATA and isinstance(data, SessionDataDecoded):
            return self._apply_session_data(data, ts, seq)
        if message.family is Family.LAP_COUNT and isinstance(data, LapCountDecoded):
            self._apply_lap_count(data)
            return []
        if message.family is Family.DRIVER_LIST and isinstance(data, dict):
            return self._apply_driver_list(data, ts, seq)
        # Family.UNKNOWN (or a decoder degradation): preserve verbatim —
        # data we can't parse today is still data.
        return [UnknownEvent(**self._envelope(ts, seq), topic=record.topic, payload=record.payload)]

    def seed(self, subscribe_snapshot: JsonValue) -> None:
        """Adopt a full-state subscribe snapshot as the new baseline.

        Called directly by a live consumer at connect time, and by
        `apply()` for every `_meta.subscribe_snapshot` record (initial
        connect on replay, and every reconnect on both paths). NEVER
        emits: unchanged values must not fire, and changes that happened
        during a connection gap surface in `state` only — an event stream
        should contain transitions that were actually observed.

        Per-topic snapshot content REPLACES that topic's merged state
        (the snapshot is the feed's authoritative full state); topics
        absent from the snapshot keep whatever was known before.
        """
        snapshot = as_dict(subscribe_snapshot)
        if snapshot is None:
            return
        info = snapshot.get("SessionInfo")
        if info is not None:
            self._apply_session_info(decode_session_info(info))
        drivers = snapshot.get("DriverList")
        if drivers is not None:
            for number, entry in decode_driver_list(drivers).items():
                self._absorb_identity(number, entry)
                if entry.line is not None:
                    self._tracker(number).position = entry.line
        app = snapshot.get("TimingAppData")
        if app is not None:
            for number, line in decode_timing(app).lines.items():
                if not isinstance(line, dict):
                    continue
                tracker = self._tracker(number)
                tracker.app = deep_merge(None, line)
                tracker.stints = _stint_facts(tracker.app)
                tracker.announced_stints = set(range(len(tracker.stints)))
        timing = snapshot.get("TimingData")
        if timing is not None:
            for number, line in decode_timing(timing).lines.items():
                if not isinstance(line, dict):
                    continue
                tracker = self._tracker(number)
                tracker.timing = deep_merge(None, line)
                tracker.facts = _timing_facts(tracker.timing)
                tracker.last_emitted_lap = tracker.facts.laps
                tracker.lap_time_dirty = False
                if tracker.facts.position is not None:
                    tracker.position = tracker.facts.position
        lap_count = snapshot.get("LapCount")
        if lap_count is not None:
            self._apply_lap_count(decode_lap_count(lap_count))
        track = snapshot.get("TrackStatus")
        if track is not None:
            decoded = decode_track_status(track)
            self._track_status_code = decoded.status_code
            self._track_status_message = decoded.message
            self._track_status_kind = (
                _TRACK_STATUS_KINDS.get(decoded.status_code)
                if decoded.status_code is not None
                else None
            )
        session_data = snapshot.get("SessionData")
        if session_data is not None:
            entries = decode_session_data(session_data).status_series
            for index, entry in _indexed(entries, lambda e: e.index):
                if entry.session_status is not None:
                    self._session_status = entry.session_status
                self._status_seen = max(self._status_seen, index + 1)
        weather = snapshot.get("WeatherData")
        if weather is not None:
            # Baseline only: `state.weather` holds emitted events, whose
            # provenance must be a real stream record (module docstring).
            self._weather_baseline = _weather_values(decode_weather(weather))
        race_control = snapshot.get("RaceControlMessages")
        if race_control is not None:
            entries = decode_race_control(race_control)
            for index, _ in _indexed(entries, lambda e: e.index):
                self._rc_seen = max(self._rc_seen, index + 1)
        team_radio = snapshot.get("TeamRadio")
        if team_radio is not None:
            captures = decode_team_radio(team_radio)
            for index, _ in _indexed(captures, lambda c: c.index):
                self._tr_seen = max(self._tr_seen, index + 1)

    @property
    def state(self) -> SessionState:
        """The materialized "right now": a frozen projection of everything
        accumulated so far. Drivers are keyed by racing number, in numeric
        order."""
        numbers = sorted(set(self._drivers) | set(self._identities), key=_number_sort_key)
        drivers: dict[str, DriverState] = {}
        for number in numbers:
            drivers[number] = self._driver_state(number)
        return SessionState(
            session_key=self._session_key,
            status=self._session_status,
            current_lap=self._current_lap,
            track_status=self._track_status_kind,
            weather=self._weather,
            drivers=drivers,
        )

    # ------------------------------------------------------------------
    # Per-family transition logic
    # ------------------------------------------------------------------

    def _apply_timing(
        self, message: DecodedMessage, delta: TimingLinesDelta, ts: datetime, seq: int
    ) -> list[DomainEvent]:
        if message.topic == "TimingData":
            return self._apply_timing_data(delta, ts, seq)
        if message.topic == "TimingAppData":
            return self._apply_timing_app(delta, ts, seq)
        return []  # TimingStats: best-time/speed statistics, no transitions we model.

    def _apply_timing_data(
        self, delta: TimingLinesDelta, ts: datetime, seq: int
    ) -> list[DomainEvent]:
        events: list[DomainEvent] = []
        for number, line_delta in delta.lines.items():
            if not isinstance(line_delta, dict):
                continue  # bookkeeping keys like "_kf", not a driver line
            tracker = self._tracker(number)
            old = tracker.facts
            tracker.timing = deep_merge(tracker.timing, line_delta)
            new = _timing_facts(tracker.timing)
            tracker.facts = new
            if new.last_lap is not None and new.last_lap != old.last_lap:
                tracker.lap_time_dirty = True
            # A lap time is also fresh when THIS delta both increments the
            # lap and carries a time — even an unchanged one: consecutive
            # laps identical to the millisecond are rare but real, and the
            # feed re-stating the time alongside the increment IS the
            # attribution. (A persistent flag can't express this: it would
            # leak re-sent old times onto later increment-only deltas.)
            fresh_by_delta = new.laps != old.laps and _delta_carries_lap_time(line_delta)
            self._emit_sectors(events, number, old, new, ts, seq)
            self._emit_lap_completed(events, number, tracker, new, fresh_by_delta, ts, seq)
            self._emit_pit_events(events, number, old, new, ts, seq)
            if new.position is not None and new.position != old.position:
                self._emit_position(events, number, tracker, new.position, ts, seq)
            self._emit_gap(events, number, old, new, ts, seq)
        return events

    def _emit_sectors(
        self,
        events: list[DomainEvent],
        number: str,
        old: _TimingFacts,
        new: _TimingFacts,
        ts: datetime,
        seq: int,
    ) -> None:
        for index, value in enumerate(new.sectors):
            if value is None:
                continue
            previous = old.sectors[index] if index < len(old.sectors) else None
            if value == previous:
                continue
            time_value = parse_lap_time(value)
            if time_value is None:
                continue
            lap = _in_progress_lap(old.laps) if old.laps is not None else new.laps
            events.append(
                SectorCompleted(
                    **self._envelope(ts, seq),
                    driver=number,
                    lap_number=lap,
                    sector=index + 1,
                    time=time_value,
                )
            )

    def _emit_lap_completed(
        self,
        events: list[DomainEvent],
        number: str,
        tracker: _DriverTracker,
        new: _TimingFacts,
        fresh_by_delta: bool,
        ts: datetime,
        seq: int,
    ) -> None:
        """Fires when the lap number has incremented past the last emission
        AND a FRESH lap time is present — fresh meaning it changed since
        the last completion (`lap_time_dirty`) or rode in the very delta
        that incremented the lap (`fresh_by_delta`). A first-ever sighting
        of the lap counter only establishes the baseline — an increment
        must be observed, which is what makes re-sent lap data and
        post-reseed catch-up inert."""
        if new.laps is None:
            return
        if tracker.last_emitted_lap is None:
            # First sighting: any lap time seen so far belongs to this
            # baseline lap, so consume the freshness flag along with it.
            tracker.last_emitted_lap = new.laps
            tracker.lap_time_dirty = False
            return
        if new.laps <= tracker.last_emitted_lap:
            return
        if new.last_lap is None or not (tracker.lap_time_dirty or fresh_by_delta):
            return  # hold until this lap's own time arrives
        tracker.last_emitted_lap = new.laps
        tracker.lap_time_dirty = False
        stint = tracker.stints[-1] if tracker.stints else _StintFacts()
        sector_times = [parse_lap_time(value) for value in new.sectors]
        events.append(
            LapCompleted(
                **self._envelope(ts, seq),
                driver=number,
                lap_number=new.laps,
                lap_time=parse_lap_time(new.last_lap),
                sector_times=sector_times if any(t is not None for t in sector_times) else None,
                is_personal_best=new.last_lap_pb,
                compound=_map_compound(stint.compound),
                tyre_age=_tyre_age(stint),
            )
        )

    def _emit_pit_events(
        self,
        events: list[DomainEvent],
        number: str,
        old: _TimingFacts,
        new: _TimingFacts,
        ts: datetime,
        seq: int,
    ) -> None:
        # Lap attribution uses the PRE-merge lap count: pit entry usually
        # lands just before the in-lap's line crossing, and when both ride
        # in one message the entry still belongs to the completing lap.
        lap = _in_progress_lap(old.laps if old.laps is not None else new.laps)
        if new.in_pit is True and old.in_pit is not True:
            events.append(PitEntry(**self._envelope(ts, seq), driver=number, lap_number=lap))
        elif new.in_pit is False and old.in_pit is True:
            events.append(PitExit(**self._envelope(ts, seq), driver=number, lap_number=lap))

    def _emit_position(
        self,
        events: list[DomainEvent],
        number: str,
        tracker: _DriverTracker,
        candidate: int,
        ts: datetime,
        seq: int,
    ) -> None:
        """Shared dedup gate for both position sources (TimingData
        `Position`, DriverList `Line`): emit only when the canonical
        position actually moves, so the topics can't double-report."""
        if candidate == tracker.position:
            return
        tracker.position = candidate
        events.append(PositionChanged(**self._envelope(ts, seq), driver=number, position=candidate))

    def _emit_gap(
        self,
        events: list[DomainEvent],
        number: str,
        old: _TimingFacts,
        new: _TimingFacts,
        ts: datetime,
        seq: int,
    ) -> None:
        if new.gap_leader == old.gap_leader and new.interval == old.interval:
            return
        gap = parse_gap(new.gap_leader)
        interval = parse_gap(new.interval)
        events.append(
            GapUpdated(
                **self._envelope(ts, seq),
                driver=number,
                gap_to_leader=gap if isinstance(gap, timedelta) else None,
                laps_down=gap.laps if isinstance(gap, LapGap) else None,
                interval_to_ahead=interval if isinstance(interval, timedelta) else None,
            )
        )

    def _apply_timing_app(
        self, delta: TimingLinesDelta, ts: datetime, seq: int
    ) -> list[DomainEvent]:
        events: list[DomainEvent] = []
        for number, line_delta in delta.lines.items():
            if not isinstance(line_delta, dict):
                continue
            tracker = self._tracker(number)
            tracker.app = deep_merge(tracker.app, line_delta)
            tracker.stints = _stint_facts(tracker.app)
            for index, stint in enumerate(tracker.stints):
                if index in tracker.announced_stints:
                    continue
                if stint.compound is None:
                    continue  # announce once the compound arrives — never guess
                tracker.announced_stints.add(index)
                events.append(
                    StintStarted(
                        **self._envelope(ts, seq),
                        driver=number,
                        stint_number=index + 1,
                        compound=_map_compound(stint.compound),
                        start_lap=_in_progress_lap(tracker.facts.laps),
                        tyre_age_at_start=stint.start_laps,
                    )
                )
        return events

    def _apply_car_data(
        self, samples: Sequence[object], ts: datetime, seq: int
    ) -> list[DomainEvent]:
        events: list[DomainEvent] = []
        for sample in samples:
            if not isinstance(sample, CarDataSample):
                continue
            events.append(
                CarTelemetry(
                    **self._envelope(sample.ts_utc or ts, seq),
                    driver=sample.driver,
                    speed=sample.speed,
                    rpm=sample.rpm,
                    gear=sample.gear,
                    throttle=sample.throttle,
                    brake=sample.brake,
                    drs=sample.drs,
                )
            )
        return events

    def _apply_position(
        self, samples: Sequence[object], ts: datetime, seq: int
    ) -> list[DomainEvent]:
        events: list[DomainEvent] = []
        for sample in samples:
            if not isinstance(sample, PositionSample):
                continue
            events.append(
                CarPosition(
                    **self._envelope(sample.ts_utc or ts, seq),
                    driver=sample.driver,
                    x=sample.x,
                    y=sample.y,
                    z=sample.z,
                    status=sample.status,
                )
            )
        return events

    def _apply_weather(self, decoded: WeatherDecoded, ts: datetime, seq: int) -> list[DomainEvent]:
        values = _weather_values(decoded)
        if values == self._weather_baseline:
            return []
        self._weather_baseline = values
        event = WeatherSample(
            **self._envelope(ts, seq),
            air_temp=decoded.air_temp,
            track_temp=decoded.track_temp,
            humidity=decoded.humidity,
            pressure=decoded.pressure,
            wind_speed=decoded.wind_speed,
            wind_dir=decoded.wind_dir,
            rainfall=decoded.rainfall,
        )
        self._weather = event
        return [event]

    def _apply_track_status(
        self, decoded: TrackStatusDecoded, ts: datetime, seq: int
    ) -> list[DomainEvent]:
        if (
            decoded.status_code == self._track_status_code
            and decoded.message == self._track_status_message
        ):
            return []
        self._track_status_code = decoded.status_code
        self._track_status_message = decoded.message
        self._track_status_kind = (
            _TRACK_STATUS_KINDS.get(decoded.status_code)
            if decoded.status_code is not None
            else None
        )
        return [
            TrackStatusChanged(
                **self._envelope(ts, seq),
                status=self._track_status_kind,
                message=decoded.message,
            )
        ]

    def _apply_race_control(
        self, entries: Sequence[object], ts: datetime, seq: int
    ) -> list[DomainEvent]:
        events: list[DomainEvent] = []
        rc_entries = [entry for entry in entries if isinstance(entry, RaceControlEntry)]
        for index, entry in _indexed(rc_entries, lambda e: e.index):
            if index < self._rc_seen:
                continue  # already delivered (snapshot re-send or duplicate)
            self._rc_seen = index + 1
            events.append(
                RaceControlMessage(
                    **self._envelope(entry.ts_utc or ts, seq),
                    category=entry.category,
                    message=entry.message,
                    flag=entry.flag,
                    scope=entry.scope,
                    driver=entry.racing_number,
                    lap=entry.lap,
                )
            )
        return events

    def _apply_team_radio(
        self, entries: Sequence[object], ts: datetime, seq: int
    ) -> list[DomainEvent]:
        events: list[DomainEvent] = []
        captures = [entry for entry in entries if isinstance(entry, TeamRadioCapture)]
        for index, capture in _indexed(captures, lambda c: c.index):
            if index < self._tr_seen:
                continue
            self._tr_seen = index + 1
            events.append(
                TeamRadioClip(
                    **self._envelope(capture.ts_utc or ts, seq),
                    driver=capture.racing_number,
                    audio_url=self._resolve_audio(capture.path),
                    captured_at=capture.ts_utc,
                )
            )
        return events

    def _apply_session_info(self, decoded: SessionInfoDecoded) -> None:
        if decoded.path is not None:
            self._session_path = decoded.path

    def _apply_session_data(
        self, decoded: SessionDataDecoded, ts: datetime, seq: int
    ) -> list[DomainEvent]:
        events: list[DomainEvent] = []
        for index, entry in _indexed(decoded.status_series, lambda e: e.index):
            if index < self._status_seen:
                continue
            self._status_seen = index + 1
            # TrackStatus-flavoured entries are ignored here: the TrackStatus
            # topic is the single emitter for track state, and this series'
            # message-string vocabulary can't be deduplicated against its codes.
            if entry.session_status is None:
                continue
            if entry.session_status == self._session_status:
                continue
            self._session_status = entry.session_status
            events.append(
                SessionStatusChanged(
                    **self._envelope(entry.ts_utc or ts, seq), status=entry.session_status
                )
            )
        return events

    def _apply_lap_count(self, decoded: LapCountDecoded) -> None:
        if decoded.current_lap is not None:
            self._current_lap = decoded.current_lap
        if decoded.total_laps is not None:
            self._total_laps = decoded.total_laps

    def _apply_driver_list(
        self, entries: Mapping[str, object], ts: datetime, seq: int
    ) -> list[DomainEvent]:
        events: list[DomainEvent] = []
        identity_changed = False
        for number, entry in entries.items():
            if not isinstance(entry, DriverEntryDelta):
                continue
            if self._absorb_identity(number, entry):
                identity_changed = True
            if entry.line is not None:
                self._emit_position(events, number, self._tracker(number), entry.line, ts, seq)
        if identity_changed:
            roster = [self._identities[n] for n in sorted(self._identities, key=_number_sort_key)]
            events.append(DriverListUpdated(**self._envelope(ts, seq), drivers=roster))
        return events

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _envelope(self, ts: datetime, seq: int) -> _Envelope:
        return {"session_key": self._session_key, "ts_utc": ts, "source_seq": seq}

    def _tracker(self, number: str) -> _DriverTracker:
        tracker = self._drivers.get(number)
        if tracker is None:
            tracker = _DriverTracker()
            self._drivers[number] = tracker
        return tracker

    def _absorb_identity(self, number: str, entry: DriverEntryDelta) -> bool:
        """Fold one DriverList entry into the identity registry; True if
        the identity (code/team) genuinely changed."""
        current = self._identities.get(number)
        updated = DriverRef(
            number=number,
            code=entry.tla if entry.tla is not None else (current.code if current else None),
            team=(
                entry.team_name
                if entry.team_name is not None
                else (current.team if current else None)
            ),
        )
        if updated == current:
            return False
        self._identities[number] = updated
        return True

    def _resolve_audio(self, path: str | None) -> str | None:
        """Absolute audio URL when SessionInfo's path prefix is known;
        otherwise the feed-relative path verbatim (still resolvable later)."""
        if path is None:
            return None
        if self._session_path is None:
            return path
        return _STATIC_BASE + self._session_path + path

    def _driver_state(self, number: str) -> DriverState:
        identity = self._identities.get(number) or DriverRef(number=number)
        tracker = self._drivers.get(number)
        if tracker is None:
            return DriverState(driver=identity)
        facts = tracker.facts
        stint = tracker.stints[-1] if tracker.stints else _StintFacts()
        gap = parse_gap(facts.gap_leader)
        interval = parse_gap(facts.interval)
        return DriverState(
            driver=identity,
            position=tracker.position,
            last_lap=parse_lap_time(facts.last_lap),
            best_lap=parse_lap_time(facts.best_lap),
            current_compound=_map_compound(stint.compound),
            tyre_age=_tyre_age(stint),
            gap_to_leader=gap if isinstance(gap, timedelta) else None,
            laps_down=gap.laps if isinstance(gap, LapGap) else None,
            interval=interval if isinstance(interval, timedelta) else None,
            in_pit=facts.in_pit,
            stint_number=len(tracker.stints) if tracker.stints else None,
            laps_completed=facts.laps,
        )


def _weather_values(decoded: WeatherDecoded) -> tuple[object, ...]:
    return (
        decoded.air_temp,
        decoded.track_temp,
        decoded.humidity,
        decoded.pressure,
        decoded.wind_speed,
        decoded.wind_dir,
        decoded.rainfall,
    )
