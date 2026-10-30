"""Replay-as-live: turn one session's own `DomainEvent` stream back into
timed WebSocket envelopes.

CLAUDE.md's Phase 0 moat is "develop against ~80 past races on any
weekday"; this is that primitive applied to the WebSocket the same way
`f1_replay.replay` applies it to the raw recording format — a client
connecting in `mode=replay` cannot tell the difference from a live session
except for wall-clock speed. Pacing reuses `f1_replay`'s own timing model
(`pacing_offset_s`, through `ReplayPace`) rather than re-deriving the
elapsed/speed/delay arithmetic a second time.

**One timeline, any number of viewers.** A session's replay is built once
(`ReplayTimeline`: every envelope serialized once, on the race's own
clock, with its kernel snapshots and explanations) and each viewer walks
it with a `ReplayCursor`, which adds only that viewer's pace and
wall-clock stamps. `f1_api.stream.replay_hub` shares one timeline between
everyone watching a session; `replay_envelopes` is one viewer on a
timeline of its own (the tests).

`event_time_utc` is REMAPPED to the wall-clock instant each envelope is
actually sent, rather than the event's own original historical `ts_utc`.
This is the one place `replay_envelopes` deliberately does NOT just forward
`DomainEvent.ts_utc` verbatim: Phase 3.3's Delay Sync gates every envelope
on `event_time_utc <= wall_now - user_delay` (`playbackClockMs`), which is
only meaningful if a replayed session's timestamps track real "now" the
same way a live session's do. A 2023-2025 backfill's `ts_utc` is always in
the actual historical past — comparing THAT against real wall-clock time
would make every envelope satisfy the release condition the instant it is
ingested, no matter the chosen delay, silently defeating the "cannot tell
the difference from a live session" guarantee this module's own docstring
promises and the Delay Sync slider along with it. `domain_event_envelope`
itself stays a straight passthrough (the `mode=live` path in
`f1_api.stream.websocket` calls it directly with a GENUINELY live event,
whose `ts_utc` already IS real "now" and must not be remapped); only this
module's own replay loop passes `override_ts`.

**`car_telemetry` is excluded from the stream by default** (`DEFAULT_STREAM_
KINDS`). `f1_kernel.HistoricalSource.events()` treats an unfiltered
(`kinds=None`) call as "include telemetry" — fine for a kernel backtest that
wants everything, ruinous over a WebSocket: a real race's resampled FastF1
telemetry across ~20 drivers is one to two orders of magnitude larger than
every other event kind combined, and no shipped frontend view consumes raw
`car_telemetry` off this stream (`BrakingPointDrift` reads the already-
reduced braking-point frame via the REST `/telemetry/{driver}` route
instead). Streaming it by default is what starved the browser tab and the
server's own send loop the first time this ran against a real backfilled
race rather than the small checked-in fixture. `include_telemetry=true` on
`/stream/{key}` is the on-demand escape hatch for a future caller that
genuinely wants the raw channel live — nothing here deletes the capability,
it just stops being the default.

**Pacing anchors to the first `lap_completed` ("lights out") by default**
(`anchor_to_first_lap`). A real "Race" session's own recording starts
roughly an hour before lights out — FastF1 opens the session around
pit-lane-open for the grid/anthem/formation-lap procedure — and during
that hour almost nothing timing-relevant happens (confirmed against the
2024 backfill: weather samples and the odd race-control message, nothing
else). Pacing every envelope relative to the session's raw start, as
before, meant a default `/race` connection was mostly dead air: at
`speed=5` roughly 12 real minutes before the first lap or position event
ever arrived. The fix is NOT to drop or filter that hour (it is real
session data — someone may want it later) and NOT a second delivery path
that skips Delay Sync's gate (that would be exactly the mistake
`include_telemetry` was designed to avoid making twice) — it is a pacing
recalibration, the same idea `delay-sync/calibration.ts`'s `calibrateFromTap`
already applies for a user tapping "lights out" by hand, just computed
here from data instead of a tap: elapsed time is measured from the first
`lap_completed`'s own `ts_utc` rather than the stream's first event, so
that event's own `target_offset` lands at (approximately) "now" instead of
an hour from now. Every pre-anchor event still gets sent — with a negative
elapsed time, which `pacing_offset_s` turns into a `target_offset` at or
before "now", so it is due at once and the whole pre-anchor hour arrives
in one immediate burst right after connecting. It still gets the same
remap and goes out through the same cursor as everything else — nothing
about the WebSocket route or the frontend's `TimelineBuffer`/
`playbackClockMs` gate changes, so those pre-anchor envelopes are exactly
as spoiler-safe, and exactly as subject to Delay Sync's delay, as
anything else: they simply release essentially
immediately once ingested, the same way any envelope with an
already-past `event_time_utc` would. A session with no `lap_completed` at
all (a red-flagged or heavily disrupted one) falls back to the stream's own
first event, unchanged from before.

**Measured car positions ride this timeline as FRAMES, not events.** A
race carries ~640k `car_position` samples (20 cars at ~4 Hz). One envelope
per sample would be ~200 MB over the socket and, worse, 640k objects in the
browser's released-event log; one pydantic event per sample server-side is
half a gigabyte per viewer. So positions come from the session's POSITION
TRACK (`f1_kernel.positions`, a columnar frame) and go out as one
`car_positions` envelope per instant the feed sampled, every car in it:
~30k envelopes of ~0.4 KB for a race. They are paced by their own
timestamp through exactly the same remap as every other envelope, so
Delay Sync gates them identically. Two kinds of frame are not sent: one
more than `POSITION_STALE_AFTER_S` of race time before the pacing anchor
(the pre-anchor burst would otherwise ship the whole pre-race hour, all of
it superseded on arrival), and, when unpaced (`speed <= 0`), every frame
but the last, since only the last can show.

**Phase 4.4 — team radio joins this same timeline.** A `team_radio_clip`
event already rode this stream (it is in `DEFAULT_STREAM_KINDS`); what this
prompt adds is enrichment (`radio_clip_envelope`, joining in the clip's
transcript/race-context from `radio_transcripts` via an optional
`radio_store`) and a deliberate pacing change: a radio clip's own
`elapsed_s` is measured from `captured_at` (the moment the exchange
actually happened), not `ts_utc` (`_event_pacing_ts`), because that is the
instant CLAUDE.md's Phase 4 invariant ("a radio clip's captured_at cannot
surface ahead of the user's playback clock") is actually about. It still
goes through the exact same wall-clock remap every other envelope does —
never the raw historical timestamp — so it is exactly as spoiler-safe.

**Phase 5.4 — explanations join this same timeline.** Historical sessions
precompute every anomaly's explanation once (`precompute_session_
explanations`) and emit an `explanation` envelope paced on that anomaly's
own `anomaly_time` — the instant CLAUDE.md's Phase 5 spoiler-safety
invariant ("an explanation about lap 40 cannot surface before the user's
clock reaches lap 40") gates on. Live sessions generate on anomaly against
the same cache (`explain_cached`); either way the payload stays lightweight
(text + evidence ids/links) and the client fetches full evidence detail
lazily via `GET /anomalies/{id}/explanation`. Same wall-clock remap as
every other envelope — never the raw historical `anomaly_time` on the wire.
"""

from __future__ import annotations

import hashlib
import heapq
import logging
import threading
import time
from collections.abc import Callable, Collection, Generator, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import polars as pl

from f1_api.calls import CallTracker, call_envelope
from f1_api.convert import crossover_rows, rejoin_rows
from f1_api.schemas import StreamEnvelope
from f1_api.why import (
    CitedEvidenceRef,
    ExplanationSchedule,
    anomaly_id_path,
    precompute_session_explanations,
)
from f1_domain import DomainEvent, LapCounterUpdated, SessionKey, TeamRadioClip
from f1_domain.events import DOMAIN_EVENT_ADAPTER
from f1_kernel import (
    CallKind,
    EventSource,
    KernelParams,
    KernelReport,
    LiveKernelSession,
    PositionFrame,
    PositionTrackSource,
    position_frames,
    race_distance_laps,
    track_from_events,
)
from f1_radio.search import SearchIndex
from f1_radio.store import TranscriptRecord, TranscriptStore
from f1_replay import pacing_offset_s
from f1_why import Anomaly, Explanation, ExplanationStore, Narrator

_log = logging.getLogger(__name__)

KERNEL_SNAPSHOT_KIND = "kernel_snapshot"
RADIO_CLIP_KIND = "radio_clip"
EXPLANATION_KIND = "explanation"
CAR_POSITIONS_KIND = "car_positions"
POSITION_KIND = "car_position"

POSITION_STALE_AFTER_S = 180.0
"""Race seconds before the pacing anchor beyond which a position frame is
not sent. A viewer sees the pre-anchor stretch only by delaying their clock
(Delay Sync holds at most 120 s, `DelaySyncController`'s maximum), so at 1x
nothing older can ever be shown. Measured in RACE time, not wall time, so
the cut does not widen with replay speed: at 16x a wall-time cut would ship
the whole pre-race hour (~11k frames) to every viewer to cover a 120 s
delay nobody sets on a fast-forward. The price is that such a viewer sees
the estimated map until their clock reaches the frames."""

DEFAULT_STREAM_KINDS: frozenset[str] = frozenset(
    {
        "lap_completed",
        "lap_counter_updated",
        "sector_completed",
        "car_position",
        "position_changed",
        "gap_updated",
        "pit_entry",
        "pit_exit",
        "stint_started",
        "track_status_changed",
        "race_control_message",
        "weather_sample",
        "team_radio_clip",
        "session_status_changed",
        "session_clock_updated",
        "qualifying_part_changed",
        "qualifying_best_lap",
        "qualifying_knockout",
        "driver_list_updated",
        "unknown",
    }
)
"""Every `DomainEvent.kind` except `car_telemetry` — the WS stream's default
`kinds` pushdown (see this module's docstring). Kept as an explicit allowlist
(mirroring `f1_kernel.sources.LAP_FRAME_KINDS`) rather than "all kinds minus
telemetry" computed from `f1_domain`, so a new event kind added later is
opt-in to the stream by a deliberate edit here, not silently included.
`car_position` here means "stream positions"; they go out as
`car_positions` frames, never one envelope per sample."""


def _coordinate(value: float) -> float | int:
    """The feed's positions are whole units (~1/10 m); sending them as ints
    keeps a frame's JSON short. Anything finer is kept to one decimal."""
    return int(value) if value.is_integer() else round(value, 1)


def car_positions_envelope(event_time: datetime, frame: PositionFrame) -> StreamEnvelope:
    """One instant's positions for every car the feed placed, as
    `{"cars": {driver: [x, y]}}` in the feed's own units."""
    payload: dict[str, Any] = {
        "cars": {driver: [_coordinate(x), _coordinate(y)] for driver, x, y in frame.cars}
    }
    return StreamEnvelope(event_time_utc=event_time, kind=CAR_POSITIONS_KIND, payload=payload)


def live_position_frames(events: Iterable[DomainEvent]) -> Iterator[DomainEvent | PositionFrame]:
    """`events` with each run of `car_position` events sharing one
    timestamp (one `Position.z` entry) folded into a `PositionFrame`, in
    order. The live path's batching; replay reads a position track."""
    pending: list[tuple[str, float, float]] = []
    pending_ts: datetime | None = None
    for event in events:
        if event.kind == POSITION_KIND:
            driver = getattr(event, "driver", None)
            x = getattr(event, "x", None)
            y = getattr(event, "y", None)
            if pending_ts is not None and event.ts_utc != pending_ts:
                yield PositionFrame(pending_ts, tuple(sorted(pending)))
                pending = []
            pending_ts = event.ts_utc
            if isinstance(driver, str) and x is not None and y is not None:
                pending.append((driver, float(x), float(y)))
            continue
        if pending_ts is not None:
            yield PositionFrame(pending_ts, tuple(sorted(pending)))
            pending, pending_ts = [], None
        yield event
    if pending_ts is not None:
        yield PositionFrame(pending_ts, tuple(sorted(pending)))


def source_position_track(source: EventSource, session_key: SessionKey) -> pl.DataFrame | None:
    """The source's own measured positions, the cheapest way it offers."""
    if isinstance(source, PositionTrackSource):
        return source.position_track(session_key)
    return track_from_events(source.events(session_key, kinds=[POSITION_KIND]))


def domain_event_envelope(
    event: DomainEvent, *, override_ts: datetime | None = None
) -> StreamEnvelope:
    """`override_ts`, when given, replaces `event.ts_utc` in the outgoing
    envelope — see this module's docstring for why `replay_envelopes` needs
    that and `mode=live` (which never passes it) must not."""
    payload = DOMAIN_EVENT_ADAPTER.dump_python(event, mode="json")
    ts = override_ts if override_ts is not None else event.ts_utc
    return StreamEnvelope(event_time_utc=ts, kind=event.kind, payload=payload)


def kernel_snapshot_envelope(
    event_time: datetime, report: KernelReport, call_kinds: Collection[CallKind] = ()
) -> StreamEnvelope:
    """One lap's analysis. `call_kinds` are the kinds this stream serves
    (`f1_api.calls.enabled_call_kinds`), sent so the desk knows which call
    panels to show. Each car's rejoin projection IS the rejoin call, so it
    goes out only when that kind is served: a kind that has not passed its
    held-out backtest never reaches a screen (ROADMAP W5)."""
    payload = {
        "decision_lap": report.decision_lap,
        "crossovers": [c.model_dump(mode="json") for c in crossover_rows(report.crossovers)],
        "rejoins": (
            [r.model_dump(mode="json") for r in rejoin_rows(report.rejoins)]
            if CallKind.REJOIN in call_kinds
            else []
        ),
        "call_kinds": sorted(kind.value for kind in call_kinds),
    }
    return StreamEnvelope(event_time_utc=event_time, kind=KERNEL_SNAPSHOT_KIND, payload=payload)


def has_snapshot_news(report: KernelReport, call_kinds: Collection[CallKind]) -> bool:
    """Whether a lap's analysis is worth an envelope: a crossover, or a
    rejoin projection when rejoin calls are served."""
    return bool(report.crossovers) or (CallKind.REJOIN in call_kinds and bool(report.rejoins))


def radio_clip_envelope(
    event: TeamRadioClip, transcript: TranscriptRecord | None, *, override_ts: datetime
) -> StreamEnvelope:
    """Phase 4.4's timeline integration: a `team_radio_clip` event plus (if
    one exists yet) its transcript and race context, joined into one
    envelope so a client never needs a second REST round-trip mid-race just
    to know what a driver said. Kind is `RADIO_CLIP_KIND`
    ("radio_clip") — distinct from the raw `DomainEvent.kind`
    ("team_radio_clip") so a consumer never has to tell a bare event from an
    enriched one by inspecting the payload's shape.

    `transcript=None` (not yet transcribed, or no `radio_store` configured)
    degrades every transcript field to `None`/`has_transcript=False` rather
    than omitting the clip — CLAUDE.md's Phase 4 "nothing silently dropped"
    discipline, and `audio_url` alone is still useful the moment the raw
    event arrives, well before Phase 4.2's offline batch step ever reaches
    it. This never carries `TranscriptRecord.embedding` (an internal
    ranking detail, not something a client renders) or `audio_path` (a
    local server-side cache path, meaningless off this machine) — only
    `audio_url`, so the frontend fetches audio lazily from the clip's own
    URL when the user plays it, never streamed over this socket.
    """
    payload: dict[str, Any] = {
        "session_key": event.session_key.model_dump(mode="json"),
        "driver": event.driver,
        "audio_url": event.audio_url,
        "captured_at": event.captured_at.isoformat() if event.captured_at is not None else None,
        "has_transcript": transcript is not None,
        "transcript_text": transcript.transcript_text if transcript is not None else None,
        "language": transcript.language if transcript is not None else None,
        "confidence": transcript.confidence if transcript is not None else None,
        "lap_number": transcript.lap_number if transcript is not None else None,
        "position": transcript.position if transcript is not None else None,
        "track_status": transcript.track_status if transcript is not None else None,
        "track_status_flags": transcript.track_status_flags if transcript is not None else None,
    }
    return StreamEnvelope(event_time_utc=override_ts, kind=RADIO_CLIP_KIND, payload=payload)


def explanation_envelope(
    override_ts: datetime,
    session_key: SessionKey,
    anomaly: Anomaly,
    explanation: Explanation,
    cited: tuple[CitedEvidenceRef, ...],
) -> StreamEnvelope:
    """Phase 5.4's timeline integration: a lightweight explanation payload
    paced to `anomaly.anomaly_time`. Kind is `EXPLANATION_KIND`
    ("explanation") — a synthetic kind, like `kernel_snapshot` /
    `radio_clip`, never a `DomainEvent.kind`. Payload carries text +
    evidence ids/links only; the client fetches full evidence detail via
    `GET /anomalies/{id}/explanation` (the telemetry/radio lazy-fetch
    lesson again). `override_ts` is the remapped wall-clock instant for
    Delay Sync — never the raw historical `anomaly_time` on a replay path.
    """
    payload: dict[str, Any] = {
        "anomaly_id": anomaly_id_path(session_key, anomaly),
        "kind": anomaly.kind.value,
        "lap_number": anomaly.lap_number,
        "drivers": list(anomaly.drivers),
        "text": explanation.text,
        "confidence": explanation.confidence.value,
        "grounded": explanation.grounded,
        "cited_evidence": [
            {
                "evidence_id": item.evidence_id,
                "kind": item.kind,
                "href": item.href,
            }
            for item in cited
        ],
    }
    return StreamEnvelope(event_time_utc=override_ts, kind=EXPLANATION_KIND, payload=payload)


def envelope_for_event(
    event: DomainEvent,
    *,
    radio_store: TranscriptStore | None = None,
    override_ts: datetime | None = None,
) -> StreamEnvelope:
    """One event -> one outgoing envelope: `radio_clip_envelope` (transcript
    joined in) for a `team_radio_clip` event, `domain_event_envelope` for
    everything else. Shared by `replay_envelopes` below and `mode=live`'s
    polling loop (`f1_api.stream.websocket._stream_live`) so a radio clip is
    never enriched differently depending on which mode is streaming it.

    For a radio clip specifically, `override_ts` defaults to the clip's own
    `captured_at` (falling back to `ts_utc` when unknown) rather than
    `ts_utc` outright — `captured_at` is the moment the exchange actually
    happened, the instant CLAUDE.md's Phase 4 invariant ("a radio clip's
    captured_at cannot surface ahead of the user's playback clock") gates
    on, and `ts_utc` only ever records when our pipeline observed the event.
    """
    if isinstance(event, TeamRadioClip):
        transcript = (
            radio_store.get(event.session_key, event.driver, event.captured_at)
            if radio_store is not None
            else None
        )
        ts = override_ts if override_ts is not None else (event.captured_at or event.ts_utc)
        return radio_clip_envelope(event, transcript, override_ts=ts)
    return domain_event_envelope(event, override_ts=override_ts)


class LapSnapshotFeed:
    """Feeds a growing `DomainEvent` stream into a `LiveKernelSession`,
    recomputing only once per newly-seen race lap rather than once per raw
    event.

    `LiveKernelSession.push` recomputes the WHOLE kernel from scratch every
    call — fine at the cadence its own docs describe ("the shape Phase 3
    drives after each completed lap"), ruinous at "once per raw event": a
    single lap produces a `lap_completed` per DRIVER (not one per lap) plus
    every `CarTelemetry` sample in between, so pushing on every event
    recomputes the whole session up to 20-ish times more often than the
    "once per lap" cadence actually needs. This buffers every event and
    only flushes (and returns a fresh report) on the FIRST `lap_completed`
    of a lap number higher than any flushed so far — the leader's pace, not
    the field's fan-out. Higher, not merely different: comparing with the
    last flushed number alone re-flushed every time a lapped car completed
    an older lap after the leader started a new one (260 full recomputes
    for a 57-lap race instead of 57), and every one of them paused the
    replay stream for up to a second.
    """

    def __init__(
        self,
        session_key: SessionKey,
        params: KernelParams | None = None,
        *,
        total_laps: int | None = None,
        event_name: str | None = None,
    ) -> None:
        # The event name is how a backfilled session finds its circuit, and
        # with it the calibrated pit loss every undercut call depends on.
        # Without it each snapshot fell back to the cross-circuit default.
        self._live = LiveKernelSession(
            session_key=session_key,
            params=params if params is not None else KernelParams(),
            total_laps=total_laps,
            event_name=event_name,
        )
        self._pending: list[DomainEvent] = []
        self._highest_flushed_lap: int | None = None
        # The race distance: given, or read off the feed's lap counter.
        self._race_laps = total_laps

    def feed(self, event: DomainEvent) -> KernelReport | None:
        """Buffer `event`; returns a fresh `KernelReport` exactly on the
        event that completes a new lap number, `None` otherwise. Car
        positions are not buffered: no analytic reads them, and a live
        session would otherwise pile ~640k of them into the kernel."""
        if event.kind == POSITION_KIND:
            return None
        self._pending.append(event)
        if isinstance(event, LapCounterUpdated) and event.total_laps:
            self._race_laps = event.total_laps
        if event.kind != "lap_completed":
            return None
        lap_number = getattr(event, "lap_number", None)
        if lap_number is None:
            return None
        # Once the leader has finished, every car crossing the line is news:
        # the rest of the podium, and a car that stopped on the last laps.
        # Before this, nothing after the winner's crossing was ever analysed
        # live (the 2026 Singapore GP's Albon, out after lap 59 of 62, never
        # got a card, and the finish card had no second or third).
        finished = (
            self._race_laps is not None
            and self._highest_flushed_lap is not None
            and self._highest_flushed_lap >= self._race_laps
        )
        if (
            not finished
            and self._highest_flushed_lap is not None
            and lap_number <= self._highest_flushed_lap
        ):
            return None
        report = self._live.push(self._pending)
        self._pending = []
        self._highest_flushed_lap = max(self._highest_flushed_lap or 0, lap_number)
        return report

    def flush(self) -> KernelReport:
        """Everything still buffered, analysed now: the race's last word,
        once the stream has ended."""
        report = self._live.push(self._pending)
        self._pending = []
        return report

    @property
    def events(self) -> tuple[DomainEvent, ...]:
        """Everything flushed into the kernel so far (car positions aside) —
        what `f1_why.starting_grid` reads the grid from."""
        return self._live.events


def _pacing_anchor_ts(events: list[DomainEvent], *, anchor_to_first_lap: bool) -> datetime:
    """The `ts_utc` that counts as `elapsed_s = 0` for pacing purposes — see
    this module's docstring for why that is the first `lap_completed`, not
    the stream's first event, by default."""
    if anchor_to_first_lap:
        first_lap_ts = next((e.ts_utc for e in events if e.kind == "lap_completed"), None)
        if first_lap_ts is not None:
            return first_lap_ts
    return events[0].ts_utc


def _event_pacing_ts(event: DomainEvent) -> datetime:
    """The timestamp `elapsed_s` is measured from for ONE event's own
    release timing — `captured_at` for a team radio clip (falling back to
    `ts_utc` when unknown), `ts_utc` for everything else. The overall
    stream stays sorted by `ts_utc` (the spine every other kind uses); only
    a radio clip's own pacing target differs, so it releases at the moment
    it was actually captured rather than the moment our pipeline observed
    it — see `envelope_for_event`'s docstring for why that is the instant
    CLAUDE.md's Phase 4 spoiler-safety invariant cares about."""
    if isinstance(event, TeamRadioClip) and event.captured_at is not None:
        return event.captured_at
    return event.ts_utc


class ReplayPace:
    """Where on the wall clock each moment of the replay goes out, at a
    speed the viewer can change mid-stream: a new speed carries on from the
    current moment of the race instead of restarting it.

    Until the first change this is exactly `f1_replay`'s timing model
    (`pacing_offset_s`: `delay + elapsed / speed`). A change re-anchors the
    line at the moment of the race reached on the wall clock now, so the
    replay carries on from there at the new rate. Offsets handed out never
    go backwards, even when a change lands while the replay runs late,
    so envelopes stay stamped in send order for Delay Sync. A change also
    wakes a `wait_for` or `pause` waiting for the next moment, so it takes
    effect at once (the WebSocket, which waits on its event loop with
    `remaining`, wakes itself). Thread-safe. `speed <= 0` is as fast as
    possible."""

    def __init__(self, speed: float, *, delay: float = 0.0) -> None:
        self._lock = threading.Lock()
        self._changed = threading.Event()
        self._speed = speed
        self._delay = delay
        self._anchor_elapsed = 0.0
        self._anchor_offset = delay
        self._re_anchored = False
        self._last_offset = float("-inf")
        self._last_elapsed = 0.0
        self._start_wall: float | None = None
        self._clock: Callable[[], float] = time.monotonic

    def begin(self, start_wall: float, clock: Callable[[], float]) -> None:
        """The replay starts now: `start_wall` on `clock`, the moment
        offsets are counted from."""
        with self._lock:
            self._start_wall = start_wall
            self._clock = clock

    @property
    def speed(self) -> float:
        with self._lock:
            return self._speed

    def _offset_locked(self, elapsed_s: float) -> float:
        if not self._re_anchored:
            return pacing_offset_s(elapsed_s, speed=self._speed, delay=self._delay)
        ahead = elapsed_s - self._anchor_elapsed
        return self._anchor_offset + (ahead / self._speed if self._speed > 0 else 0.0)

    def offset(self, elapsed_s: float) -> float:
        """The wall-clock offset (seconds after the replay began) that
        `elapsed_s` of the session goes out at, before monotonicity."""
        with self._lock:
            return self._offset_locked(elapsed_s)

    def set_speed(self, speed: float, *, now_offset_s: float | None = None) -> None:
        """Carry on from the moment of the race reached now (or at
        `now_offset_s`, wall time since the replay began) at `speed`. Before
        the replay has begun, simply its starting speed."""
        with self._lock:
            if speed == self._speed:
                return
            if now_offset_s is None:
                if self._start_wall is None:
                    self._speed = speed
                    return
                now_offset_s = self._clock() - self._start_wall
            if self._speed > 0:
                base_elapsed = self._anchor_elapsed if self._re_anchored else 0.0
                base_offset = self._anchor_offset if self._re_anchored else self._delay
                reached = base_elapsed + (now_offset_s - base_offset) * self._speed
            else:
                reached = self._last_elapsed
            self._anchor_elapsed = max(reached, self._last_elapsed)
            self._anchor_offset = max(now_offset_s, self._last_offset)
            self._re_anchored = True
            self._speed = speed
        self._changed.set()

    def remaining(self, elapsed_s: float) -> float:
        """Seconds until `elapsed_s` is due on the wall clock `begin` set;
        zero or less means now. The non-blocking half of `wait_for`, for a
        caller that waits its own way (the WebSocket, on its event loop)."""
        with self._lock:
            if self._start_wall is None:
                raise RuntimeError("ReplayPace.begin must be called first")
            return self._start_wall + self._offset_locked(elapsed_s) - self._clock()

    def commit(self, elapsed_s: float) -> float:
        """`elapsed_s` goes out now: its offset, never earlier than one
        already handed out (so stamps stay in send order)."""
        with self._lock:
            offset = max(self._offset_locked(elapsed_s), self._last_offset)
            self._last_offset = offset
            self._last_elapsed = max(self._last_elapsed, elapsed_s)
            return offset

    def pause(self, seconds: float) -> None:
        """Wait up to `seconds`, waking early on a speed change."""
        if self._changed.wait(timeout=seconds):
            self._changed.clear()

    def wait_for(
        self,
        elapsed_s: float,
        *,
        start_wall: float,
        clock: Callable[[], float],
        sleep: Callable[[float], None] | None,
    ) -> float:
        """Block until `elapsed_s` is due, then return its offset. With a
        `sleep` given (the tests), sleep with it; otherwise wait on this
        pace, so a speed change wakes the wait."""
        while True:
            with self._lock:
                target = self._offset_locked(elapsed_s)
            remaining = start_wall + target - clock()
            if remaining <= 0:
                break
            if sleep is not None:
                sleep(remaining)
                continue
            self.pause(remaining)
        return self.commit(elapsed_s)


_STAMP_PLACEHOLDER = datetime(2000, 1, 1, tzinfo=UTC)
"""What a shared envelope is stamped with while its tail is cut off
(`envelope_tail`). Never sent: every viewer writes its own stamp."""

_STAMP_HEAD = '{"event_time_utc":"2000-01-01T00:00:00Z",'

ANALYSIS_POLL_S = 0.25
"""How often a viewer at the end of the race looks again for the shared
analysis to finish (the race's last snapshots and settled calls)."""


def utc_stamp(ts: datetime) -> str:
    """`ts` exactly as pydantic writes an aware UTC datetime in JSON."""
    return ts.isoformat().replace("+00:00", "Z")


def envelope_tail(envelope: StreamEnvelope) -> str:
    """`envelope`'s JSON after its timestamp (`"kind":...,"payload":{...}}`),
    for an envelope stamped `_STAMP_PLACEHOLDER`. A shared replay serializes
    each envelope once; a viewer only prefixes its own stamp
    (`stamped_text`)."""
    text = envelope.model_dump_json()
    if not text.startswith(_STAMP_HEAD):
        raise ValueError("envelope_tail needs an envelope stamped with the placeholder")
    return text[len(_STAMP_HEAD) :]


def stamped_text(stamp: str, tail: str) -> str:
    """One envelope as sent: a viewer's own stamp on a shared tail."""
    return '{"event_time_utc":"' + stamp + '",' + tail


def replay_events(
    source: EventSource, session_key: SessionKey, kinds: Collection[str] | None
) -> list[DomainEvent]:
    """The events a replay streams, in `ts_utc` order. Car positions are
    left out: they ride the timeline as frames."""
    event_kinds = None if kinds is None else frozenset(kinds) - {POSITION_KIND}
    return sorted(
        (
            event
            for event in source.events(session_key, kinds=event_kinds)
            if event.kind != POSITION_KIND
        ),
        key=lambda event: event.ts_utc,
    )


@contextmanager
def analysis_slot(slots: threading.Semaphore | None, closed: threading.Event) -> Generator[bool]:
    """Hold one of `slots` (no limit when None) for the length of a heavy
    job. Yields False when `closed` is set before the job may start, so a
    replay nobody watches any more gives up its place in the queue."""
    if slots is None:
        yield not closed.is_set()
        return
    while not slots.acquire(timeout=0.5):
        if closed.is_set():
            yield False
            return
    try:
        yield not closed.is_set()
    finally:
        slots.release()


@dataclass(frozen=True, slots=True)
class _Item:
    """One envelope on the shared timeline: when it goes out, in race
    seconds from the pacing anchor, and its serialized tail."""

    elapsed_s: float
    tail: str
    frame: bool = False


@dataclass(frozen=True, slots=True)
class _Output:
    """A kernel snapshot or call, sent once the item at index `after` (the
    event that completed its lap) has gone out. `after` equal to the item
    count is the end of the race."""

    after: int
    tail: str


def events_fingerprint(events: Sequence[DomainEvent]) -> str:
    """Which events, in which order: the same in any process (`hash()` of a
    string is not), so a worker can check it re-read what the build read."""
    digest = hashlib.blake2b(digest_size=16)
    for event in events:
        digest.update(f"{event.kind}|{event.ts_utc.isoformat()}\n".encode())
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class AnalysisSpec:
    """What a kernel pass over a timeline needs besides the events: which
    item each event is (in `ts_utc` order), and the session it belongs to.
    Plain data, so a worker process can run the pass."""

    session_key: SessionKey
    event_items: tuple[int, ...]
    fingerprint: str
    item_count: int
    total_laps: int | None
    event_name: str | None
    call_kinds: tuple[CallKind, ...]


def kernel_outputs(
    spec: AnalysisSpec,
    events: Sequence[DomainEvent],
    *,
    stopped: Callable[[], bool] = lambda: False,
) -> Iterator[tuple[int, str]]:
    """The kernel snapshots and calls a timeline's events produce, as
    `(after, tail)`: sent once item `after` (the event that completed the
    lap) has gone out. The end-of-race settlements come last, after
    `spec.item_count`. One pass, the race's whole kernel: about 20 s of CPU
    for a race."""
    feed = LapSnapshotFeed(spec.session_key, total_laps=spec.total_laps, event_name=spec.event_name)
    calls = CallTracker(spec.call_kinds) if spec.call_kinds else None
    for index, event in zip(spec.event_items, events, strict=True):
        if stopped():
            return
        try:
            report = feed.feed(event)
        except Exception:
            _log.warning("kernel snapshot failed; the replay continues", exc_info=True)
            continue
        if report is None:
            continue
        if has_snapshot_news(report, spec.call_kinds):
            snapshot = kernel_snapshot_envelope(_STAMP_PLACEHOLDER, report, spec.call_kinds)
            yield index, envelope_tail(snapshot)
        if calls is not None:
            try:
                for call in calls.update(report):
                    yield index, envelope_tail(call_envelope(_STAMP_PLACEHOLDER, call))
            except Exception:
                _log.warning("call ledger failed for one lap", exc_info=True)
    if calls is not None and not stopped():
        # The calls the end of the race settles (a rival who never stopped).
        try:
            for call in calls.update(feed.flush(), final=True):
                yield spec.item_count, envelope_tail(call_envelope(_STAMP_PLACEHOLDER, call))
        except Exception:
            _log.warning("settling the calls failed; the replay ends", exc_info=True)


def _event_tail(event: DomainEvent, radio_store: TranscriptStore | None) -> str:
    """The event's tail, with its radio transcript when the store can give
    one. A failed lookup costs the transcript, never the clip or the build:
    one shared build is every viewer's replay."""
    try:
        envelope = envelope_for_event(
            event, radio_store=radio_store, override_ts=_STAMP_PLACEHOLDER
        )
    except Exception:
        if radio_store is None:
            raise
        _log.warning("radio enrichment failed; sending the clip without it", exc_info=True)
        envelope = envelope_for_event(event, radio_store=None, override_ts=_STAMP_PLACEHOLDER)
    return envelope_tail(envelope)


class ReplayTimeline:
    """One session's replay, built once and walked by any number of viewers,
    each at its own pace (`ReplayCursor`).

    Everything here is the same for every viewer: the events and position
    frames in order, each serialized once and placed on the race's own clock
    (seconds from the pacing anchor); the kernel snapshots and calls, each
    tied to the event that completed its lap; and the session's
    explanations. Only the wall-clock stamp differs between viewers, and
    that is all a viewer adds.

    The analysis runs ahead of every viewer as fast as it can, not at
    anyone's pace: on a thread here (`start_analysis`), or in a worker
    process that hands its results in (`add_output`, `finish_analysis`;
    see `f1_api.stream.replay_worker`). A viewer who reaches a lap before
    its analysis exists gets it a moment late, stamped when it goes out:
    late is spoiler-safe, early cannot happen.

    `build` holds on to the events for `start_analysis` unless told not to
    (`keep_events=False`, about 100 MB for a race): a worker reads them
    again itself, and checks against `spec().fingerprint` that it read the
    same ones, or the replay goes without snapshots rather than tie them to
    the wrong laps."""

    def __init__(
        self,
        session_key: SessionKey,
        *,
        anchor: datetime,
        items: list[_Item],
        spec: AnalysisSpec,
        events: Sequence[DomainEvent] | None,
    ) -> None:
        self.session_key = session_key
        self._anchor = anchor
        self._items = items
        self._last_frame = next(
            (index for index in range(len(items) - 1, -1, -1) if items[index].frame), -1
        )
        self._spec = spec
        self._events = events
        self._lock = threading.Lock()
        self._outputs: list[_Output] = []
        self._analysed = threading.Event()
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None
        self._schedule: ExplanationSchedule | None = None
        self._explained: list[_Item] | None = None
        if not items:
            self._analysed.set()

    @classmethod
    def empty(cls, session_key: SessionKey) -> ReplayTimeline:
        """A session with nothing to replay."""
        spec = AnalysisSpec(session_key, (), events_fingerprint(()), 0, None, None, ())
        return cls(session_key, anchor=_STAMP_PLACEHOLDER, items=[], spec=spec, events=None)

    @classmethod
    def build(
        cls,
        source: EventSource,
        session_key: SessionKey,
        *,
        kinds: Collection[str] | None = DEFAULT_STREAM_KINDS,
        anchor_to_first_lap: bool = True,
        radio_store: TranscriptStore | None = None,
        event_name: str | None = None,
        positions: pl.DataFrame | None = None,
        call_kinds: Collection[CallKind] = (),
        keep_events: bool = True,
    ) -> ReplayTimeline:
        """The session's whole replay, serialized. Measured positions come
        from `positions` when the caller has the track, otherwise from the
        source, whenever `kinds` asks for `car_position`."""
        events = replay_events(source, session_key, kinds)
        if not events:
            return cls.empty(session_key)
        track: pl.DataFrame | None = None
        if kinds is None or POSITION_KIND in kinds:
            track = (
                positions if positions is not None else source_position_track(source, session_key)
            )
        frames: Iterator[PositionFrame] = position_frames(track) if track is not None else iter(())
        anchor = _pacing_anchor_ts(events, anchor_to_first_lap=anchor_to_first_lap)

        items: list[_Item] = []
        event_items: list[int] = []
        timeline: Iterator[DomainEvent | PositionFrame] = heapq.merge(
            events, frames, key=lambda item: item.ts_utc
        )
        for item in timeline:
            if isinstance(item, PositionFrame):
                elapsed_s = (item.ts_utc - anchor).total_seconds()
                if elapsed_s < -POSITION_STALE_AFTER_S:
                    continue
                tail = envelope_tail(car_positions_envelope(_STAMP_PLACEHOLDER, item))
                items.append(_Item(elapsed_s, tail, frame=True))
                continue
            event_items.append(len(items))
            elapsed_s = (_event_pacing_ts(item) - anchor).total_seconds()
            items.append(_Item(elapsed_s, _event_tail(item, radio_store)))

        spec = AnalysisSpec(
            session_key,
            tuple(event_items),
            events_fingerprint(events),
            len(items),
            # A replay holds the whole race, so its snapshots can use the
            # real distance from lap 1 rather than the slice seen so far.
            race_distance_laps(events),
            event_name,
            tuple(call_kinds),
        )
        return cls(
            session_key,
            anchor=anchor,
            items=items,
            spec=spec,
            events=events if keep_events else None,
        )

    @property
    def item_count(self) -> int:
        return len(self._items)

    @property
    def last_frame(self) -> int:
        """Index of the last position frame, -1 when there is none."""
        return self._last_frame

    @property
    def text_bytes(self) -> int:
        """Roughly what the serialized timeline holds, for the logs."""
        return sum(len(item.tail) for item in self._items)

    def spec(self) -> AnalysisSpec:
        return self._spec

    def item(self, index: int) -> _Item:
        return self._items[index]

    def output(self, index: int) -> tuple[_Output | None, bool]:
        """The `index`-th snapshot or call (None when not made yet), and
        whether the analysis is over, read together so a viewer never
        misses the last ones."""
        with self._lock:
            found = self._outputs[index] if index < len(self._outputs) else None
            return found, self._analysed.is_set()

    def attach_explanations(self, schedule: ExplanationSchedule) -> None:
        """The session's explanation batch, landing whenever it is ready."""
        with self._lock:
            self._schedule = schedule

    def explanations(self) -> list[_Item] | None:
        """The explanations, serialized and on the race clock; None while
        the batch is still being made, empty when there is none."""
        with self._lock:
            if self._explained is not None:
                return self._explained
            schedule = self._schedule
        if schedule is None or not self._items:
            return []
        landed = schedule.items()
        if landed is None:
            return None
        explained = [
            _Item(
                (item.anomaly.anomaly_time - self._anchor).total_seconds(),
                envelope_tail(
                    explanation_envelope(
                        _STAMP_PLACEHOLDER,
                        self.session_key,
                        item.anomaly,
                        item.explanation,
                        item.cited,
                    )
                ),
            )
            for item in landed
        ]
        with self._lock:
            if self._explained is None:
                self._explained = explained
                # The batch's evidence bundles are not needed any more.
                self._schedule = None
            return self._explained

    def add_output(self, after: int, tail: str) -> None:
        """One snapshot or call, in the order the kernel pass made them."""
        with self._lock:
            self._outputs.append(_Output(after, tail))

    def finish_analysis(self) -> None:
        """No more snapshots or calls are coming: viewers at the end of the
        race may finish."""
        self._events = None
        with self._lock:
            self._analysed.set()

    def start_analysis(self, slots: threading.Semaphore | None = None) -> None:
        """Run the kernel pass on one thread over the events `build` kept,
        once one of `slots` is free (at once without a limit)."""
        if self._thread is not None or self._analysed.is_set():
            return
        self._thread = threading.Thread(
            target=self._analyse, args=(slots,), name="f1-api-snapshots", daemon=True
        )
        self._thread.start()

    def wait_analysed(self, timeout: float | None = None) -> bool:
        return self._analysed.wait(timeout)

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def close(self) -> None:
        """Stop the analysis now; what it has not computed is dropped."""
        self._closed.set()

    def _analyse(self, slots: threading.Semaphore | None) -> None:
        try:
            with analysis_slot(slots, self._closed) as granted:
                events = self._events
                if granted and events is not None:
                    for after, tail in kernel_outputs(
                        self._spec, events, stopped=self._closed.is_set
                    ):
                        self.add_output(after, tail)
        except Exception:
            _log.warning(
                "replay analysis failed for %s; the replay carries on without it",
                self.session_key,
                exc_info=True,
            )
        finally:
            self.finish_analysis()


@dataclass(frozen=True, slots=True)
class Due:
    """What a viewer sends now, and how long until the next thing is due:
    None when the replay is over. `analysing` means the viewer waits on the
    shared analysis (the race's last snapshots), not on the clock."""

    texts: list[str]
    wait_s: float | None
    analysing: bool = False


class ReplayCursor:
    """One viewer's walk through a shared `ReplayTimeline`, at its own pace.

    The order is the one a replay always had: before each item, every
    explanation due at or before it, each at its own moment; then the item
    at its moment; then any snapshot or call its lap produced, stamped with
    that same moment (or later, if the analysis was behind). Stamps are
    `ReplayPace`'s offsets on this viewer's wall clock, so they never go
    backwards. Unpaced (`speed <= 0`), every position frame is due at once
    and only the last can show, so the rest are skipped. Never blocks:
    `due` says how long to wait, and the caller waits its own way."""

    def __init__(
        self,
        timeline: ReplayTimeline,
        pace: ReplayPace,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._timeline = timeline
        self._pace = pace
        start_wall = clock()
        self._start_utc = wall_clock()
        pace.begin(start_wall, clock)
        self._item = 0
        self._output = 0
        self._explanation = 0
        self._stamp: str | None = None
        self._explanations: list[_Item] | None = None

    def due(self, limit: int = 256) -> Due:
        """Everything due now, up to `limit` envelopes."""
        texts: list[str] = []
        while len(texts) < limit:
            step = self._step()
            if isinstance(step, str):
                texts.append(step)
                continue
            wait_s, analysing = step
            return Due(texts, wait_s, analysing)
        return Due(texts, 0.0)

    def _stamped(self, elapsed_s: float, tail: str) -> str:
        offset = self._pace.commit(elapsed_s)
        self._stamp = utc_stamp(self._start_utc + timedelta(seconds=offset))
        return stamped_text(self._stamp, tail)

    def _step(self) -> str | tuple[float | None, bool]:
        """The next text due, or (seconds to wait or None at the end,
        whether that wait is for the analysis)."""
        timeline = self._timeline
        count = timeline.item_count
        output, analysed = timeline.output(self._output)
        if output is not None and (output.after < self._item or self._item >= count):
            # Only ever after the item that completed its lap, which set a stamp.
            assert self._stamp is not None
            self._output += 1
            return stamped_text(self._stamp, output.tail)
        if self._explanations is None:
            self._explanations = timeline.explanations()
        pending = self._explanations
        while self._item < count:
            item = timeline.item(self._item)
            if (
                pending is not None
                and self._explanation < len(pending)
                and pending[self._explanation].elapsed_s <= item.elapsed_s
            ):
                return self._explain(pending)
            if item.frame and self._pace.speed <= 0 and self._item != timeline.last_frame:
                self._item += 1
                continue
            wait_s = self._pace.remaining(item.elapsed_s)
            if wait_s > 0:
                return wait_s, False
            self._item += 1
            return self._stamped(item.elapsed_s, item.tail)
        if not analysed:
            return ANALYSIS_POLL_S, True
        # Explanations whose moment comes after the last event still land.
        if pending is not None and self._explanation < len(pending):
            return self._explain(pending)
        return None, False

    def _explain(self, pending: list[_Item]) -> str | tuple[float | None, bool]:
        item = pending[self._explanation]
        wait_s = self._pace.remaining(item.elapsed_s)
        if wait_s > 0:
            return wait_s, False
        self._explanation += 1
        return self._stamped(item.elapsed_s, item.tail)


def replay_envelopes(
    source: EventSource,
    session_key: SessionKey,
    *,
    speed: float = 1.0,
    delay: float = 0.0,
    kinds: Collection[str] | None = DEFAULT_STREAM_KINDS,
    anchor_to_first_lap: bool = True,
    radio_store: TranscriptStore | None = None,
    narration_model: Narrator | None = None,
    explanation_store: ExplanationStore | None = None,
    explanation_schedule: ExplanationSchedule | None = None,
    max_explanations: int | None = None,
    radio_index: SearchIndex | None = None,
    event_name: str | None = None,
    positions: pl.DataFrame | None = None,
    call_kinds: Collection[CallKind] = (),
    pace: ReplayPace | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] | None = None,
    wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Generator[StreamEnvelope]:
    """One viewer's replay as `StreamEnvelope`s, on a timeline of its own:
    the synchronous form of what the WebSocket does with a shared one
    (`f1_api.stream.replay_hub`), and the same `ReplayTimeline` and
    `ReplayCursor` underneath, so the tests exercise the bytes a viewer is
    sent.

    One envelope per `DomainEvent` (in `ts_utc` order), paced by
    `f1_replay`'s elapsed/speed + delay formula, plus a `kernel_snapshot`
    envelope on the lap that first completes each new lap number (see
    `LapSnapshotFeed`), when that recompute produced at least one crossover,
    plus — when `narration_model` and `explanation_store` are both given —
    an `explanation` envelope at each anomaly's own `anomaly_time`
    (Phase 5.4; historical sessions precompute the batch up front).
    `explanation_schedule` is the non-blocking form: a batch someone else is
    computing, picked up whenever it lands.

    `kinds` defaults to `DEFAULT_STREAM_KINDS` (everything except
    `car_telemetry` — see this module's docstring); pass `None` to stream
    every kind, `car_telemetry` included, on demand.

    `anchor_to_first_lap` (default `True`) paces `elapsed_s` relative to the
    first `lap_completed` event rather than the stream's own first event —
    see this module's docstring for why. Every event still gets yielded,
    pre-anchor ones simply arrive in an immediate burst rather than paced
    across the real gap; pass `False` for the old behaviour (pace from the
    stream's raw start).

    Each envelope's `event_time_utc` is `wall_clock()`'s reading at the
    start PLUS the offset the pace gives its moment — i.e. the real
    wall-clock instant this envelope is actually sent, not the source
    event's own historical `ts_utc` (see this module's docstring for why).
    `clock` (monotonic) drives the actual waiting, `wall_clock` (real UTC,
    separately injectable for tests) only labels the envelope — the two are
    captured from the same instant at the top so they stay one consistent
    timeline.

    `radio_store`, when given, backfills a `team_radio_clip` event's own
    transcript/context into an enriched `radio_clip_envelope` (Phase 4.4) —
    see `envelope_for_event`'s docstring. `None` (the default) still emits
    the clip, just with every transcript field `None`.

    Measured positions go out as `car_positions` frames (see this module's
    docstring) whenever `kinds` asks for `car_position`: from `positions`
    when the caller already has the track, otherwise from the source itself.

    `pace` lets the caller change the speed while the replay runs: the
    replay carries on from the current moment at the new rate
    (`ReplayPace`). Without one, `speed` and `delay` fix it for the whole
    replay. `sleep`, when given (the tests), replaces the real wait, which
    a speed change interrupts.
    """
    pace = pace if pace is not None else ReplayPace(speed, delay=delay)
    timeline = ReplayTimeline.build(
        source,
        session_key,
        kinds=kinds,
        anchor_to_first_lap=anchor_to_first_lap,
        radio_store=radio_store,
        event_name=event_name,
        positions=positions,
        call_kinds=call_kinds,
    )
    if timeline.item_count == 0:
        return
    schedule = explanation_schedule
    if schedule is None and narration_model is not None and explanation_store is not None:
        schedule = ExplanationSchedule()
        schedule.set(
            precompute_session_explanations(
                source,
                session_key,
                narration_model,
                explanation_store,
                radio_index=radio_index,
                max_explanations=max_explanations,
            )
        )
    if schedule is not None:
        timeline.attach_explanations(schedule)
    timeline.start_analysis()
    try:
        cursor = ReplayCursor(timeline, pace, clock=clock, wall_clock=wall_clock)
        while True:
            # One at a time, so a speed change between two envelopes applies
            # to the very next one.
            due = cursor.due(limit=1)
            for text in due.texts:
                yield StreamEnvelope.model_validate_json(text)
            if due.wait_s is None:
                return
            if due.analysing:
                timeline.wait_analysed(due.wait_s)
            elif due.wait_s > 0:
                if sleep is not None:
                    sleep(due.wait_s)
                else:
                    pace.pause(due.wait_s)
    finally:
        # A viewer who leaves closes this generator mid-race; the analysis
        # must not outlive it.
        timeline.close()


__all__ = [
    "CAR_POSITIONS_KIND",
    "DEFAULT_STREAM_KINDS",
    "EXPLANATION_KIND",
    "KERNEL_SNAPSHOT_KIND",
    "POSITION_STALE_AFTER_S",
    "RADIO_CLIP_KIND",
    "AnalysisSpec",
    "Due",
    "LapSnapshotFeed",
    "ReplayCursor",
    "ReplayPace",
    "ReplayTimeline",
    "analysis_slot",
    "car_positions_envelope",
    "domain_event_envelope",
    "envelope_for_event",
    "envelope_tail",
    "events_fingerprint",
    "explanation_envelope",
    "has_snapshot_news",
    "kernel_outputs",
    "kernel_snapshot_envelope",
    "live_position_frames",
    "radio_clip_envelope",
    "replay_envelopes",
    "replay_events",
    "source_position_track",
    "stamped_text",
    "utc_stamp",
]
