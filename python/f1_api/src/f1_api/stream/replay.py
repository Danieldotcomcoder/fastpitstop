"""Replay-as-live: turn one session's own `DomainEvent` stream back into
timed WebSocket envelopes.

CLAUDE.md's Phase 0 moat is "develop against ~80 past races on any
weekday"; this is that primitive applied to the WebSocket the same way
`f1_replay.replay` applies it to the raw recording format — a client
connecting in `mode=replay` cannot tell the difference from a live session
except for wall-clock speed. Pacing reuses `f1_replay`'s own timing model
(`pacing_offset_s` + `sleep_until`) rather than re-deriving the elapsed/
speed/delay arithmetic a second time.

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
before "now", so `sleep_until` never blocks for it and the whole pre-anchor
hour arrives in one immediate burst right after connecting. It still goes
through `domain_event_envelope`'s same remap and out over the same queue
`_stream_replay` sends from — nothing about the WebSocket route or the
frontend's `TimelineBuffer`/`playbackClockMs` gate changes, so those
pre-anchor envelopes are exactly as spoiler-safe, and exactly as subject to
Delay Sync's delay, as anything else: they simply release essentially
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
timestamp through exactly the same `_emit_paced` remap as every other
envelope, so Delay Sync gates them identically. Two kinds of frame are
not sent: one more than `POSITION_STALE_AFTER_S` of race time before the
pacing anchor (the pre-anchor burst would otherwise ship the whole
pre-race hour, all of it superseded on arrival), and, when unpaced
(`speed <= 0`), every frame but the last, since only the last can show.

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

import heapq
import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable, Collection, Generator, Iterable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import polars as pl

from f1_api.calls import CallTracker, call_envelope
from f1_api.convert import crossover_rows, rejoin_rows
from f1_api.schemas import StreamEnvelope
from f1_api.why import (
    CitedEvidenceRef,
    ExplanationSchedule,
    ScheduledExplanation,
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
from f1_replay import pacing_offset_s, sleep_until
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


def kernel_snapshot_envelope(event_time: datetime, report: KernelReport) -> StreamEnvelope:
    payload = {
        "decision_lap": report.decision_lap,
        "crossovers": [c.model_dump(mode="json") for c in crossover_rows(report.crossovers)],
        "rejoins": [r.model_dump(mode="json") for r in rejoin_rows(report.rejoins)],
    }
    return StreamEnvelope(event_time_utc=event_time, kind=KERNEL_SNAPSHOT_KIND, payload=payload)


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


class _SnapshotWorker:
    """Runs a `LapSnapshotFeed` on its own thread, so a replay keeps its
    pace while the kernel recomputes.

    A recompute takes up to a second on a real race. Run inline, it paused
    everything behind it once a lap; with car positions on the stream that
    froze every car and then jumped it forward (at 16x, ~250 m). Here the
    pacing loop only hands events over and picks up finished reports. A
    report is stamped with its lap's own instant and may go out a moment
    after it: late is spoiler-safe (Delay Sync releases it on arrival),
    early cannot happen. Reports come back in lap order; one thread, one
    feed."""

    def __init__(self, feed: LapSnapshotFeed, calls: CallTracker | None = None) -> None:
        self._feed = feed
        self._calls = calls
        self._last_ts: datetime | None = None
        self._closed = threading.Event()
        self._inbox: queue.SimpleQueue[tuple[DomainEvent, datetime] | None] = queue.SimpleQueue()
        self._ready: deque[StreamEnvelope] = deque()
        self._thread = threading.Thread(target=self._run, name="f1-api-snapshots", daemon=True)
        self._thread.start()

    def submit(self, event: DomainEvent, live_ts: datetime) -> None:
        if event.kind != POSITION_KIND:
            self._inbox.put((event, live_ts))

    def ready(self) -> Iterator[StreamEnvelope]:
        """Snapshots and calls finished since the last call, oldest first."""
        while self._ready:
            yield self._ready.popleft()

    def finish(self) -> Iterator[StreamEnvelope]:
        """Wait for everything submitted, then the remaining snapshots, and
        the calls the end of the race settles (a rival who never stopped)."""
        self._inbox.put(None)
        self._thread.join()
        if self._calls is not None and self._last_ts is not None:
            try:
                for call in self._calls.update(self._feed.flush(), final=True):
                    self._ready.append(call_envelope(self._last_ts, call))
            except Exception:
                _log.warning("settling the calls failed; the replay ends", exc_info=True)
        yield from self.ready()

    def close(self) -> None:
        """Stop now, dropping whatever was not computed yet."""
        self._closed.set()
        self._inbox.put(None)

    def _run(self) -> None:
        while (item := self._inbox.get()) is not None and not self._closed.is_set():
            event, live_ts = item
            self._last_ts = live_ts
            try:
                report = self._feed.feed(event)
            except Exception:
                _log.warning("kernel snapshot failed; the replay continues", exc_info=True)
                continue
            if report is None:
                continue
            if report.crossovers:
                self._ready.append(kernel_snapshot_envelope(live_ts, report))
            if self._calls is not None:
                try:
                    for call in self._calls.update(report):
                        self._ready.append(call_envelope(live_ts, call))
                except Exception:
                    _log.warning("call ledger failed for one lap", exc_info=True)


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


def _emit_paced(
    *,
    pacing_ts: datetime,
    pacing_anchor_ts: datetime,
    start_wall: float,
    start_wall_utc: datetime,
    speed: float,
    delay: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> datetime:
    """Sleep until `pacing_ts` on the replay timeline and return the
    remapped wall-clock `event_time_utc` for the envelope about to be
    yielded — shared by domain events and explanation envelopes so both
    pass the exact same Delay Sync arithmetic."""
    elapsed_s = (pacing_ts - pacing_anchor_ts).total_seconds()
    target_offset = pacing_offset_s(elapsed_s, speed=speed, delay=delay)
    sleep_until(start_wall, target_offset, clock=clock, sleep=sleep)
    return start_wall_utc + timedelta(seconds=target_offset)


def _flush_explanations_upto(
    scheduled: list[ScheduledExplanation],
    *,
    upto: datetime,
    index: int,
    session_key: SessionKey,
    pacing_anchor_ts: datetime,
    start_wall: float,
    start_wall_utc: datetime,
    speed: float,
    delay: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> tuple[int, list[StreamEnvelope]]:
    """Emit every still-pending explanation whose `anomaly_time` is at or
    before `upto`, advancing `index`. Returns the new index and the
    envelopes (caller yields them)."""
    emitted: list[StreamEnvelope] = []
    while index < len(scheduled) and scheduled[index].anomaly.anomaly_time <= upto:
        item = scheduled[index]
        live_ts = _emit_paced(
            pacing_ts=item.anomaly.anomaly_time,
            pacing_anchor_ts=pacing_anchor_ts,
            start_wall=start_wall,
            start_wall_utc=start_wall_utc,
            speed=speed,
            delay=delay,
            clock=clock,
            sleep=sleep,
        )
        emitted.append(
            explanation_envelope(live_ts, session_key, item.anomaly, item.explanation, item.cited)
        )
        index += 1
    return index, emitted


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
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Generator[StreamEnvelope]:
    """Yield one envelope per `DomainEvent` (in `ts_utc` order), paced by
    `f1_replay`'s elapsed/speed + delay formula, plus a `kernel_snapshot`
    envelope on the lap that first completes each new lap number (see
    `LapSnapshotFeed`), when that recompute produced at least one crossover,
    plus — when `narration_model` and `explanation_store` are both given —
    an `explanation` envelope at each anomaly's own `anomaly_time`
    (Phase 5.4; historical sessions precompute the batch up front).

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
    moment `start_wall` PLUS the same `target_offset` `sleep_until` just
    blocked for — i.e. the real wall-clock instant this envelope is actually
    sent, not the source event's own historical `ts_utc` (see this module's
    docstring for why). `clock` (monotonic) drives the actual blocking,
    `wall_clock` (real UTC, separately injectable for tests) only labels the
    envelope — the two are captured from the same instant at the top so they
    stay one consistent timeline.

    `radio_store`, when given, backfills a `team_radio_clip` event's own
    transcript/context into an enriched `radio_clip_envelope` (Phase 4.4) —
    see `envelope_for_event`'s docstring. `None` (the default) still emits
    the clip, just with every transcript field `None`.

    Measured positions go out as `car_positions` frames (see this module's
    docstring) whenever `kinds` asks for `car_position`: from `positions`
    when the caller already has the track (the WebSocket looks in the
    post-session archive too), otherwise from the source itself.

    Synchronous and real-sleeping (like `f1_replay.replay`), so a caller in
    an async context must run this in a thread — see
    `f1_api.stream.websocket`.
    """
    event_kinds = None if kinds is None else frozenset(kinds) - {POSITION_KIND}
    events: list[DomainEvent] = sorted(
        (
            event
            for event in source.events(session_key, kinds=event_kinds)
            if event.kind != POSITION_KIND
        ),
        key=lambda event: event.ts_utc,
    )
    if not events:
        return
    track: pl.DataFrame | None = None
    if kinds is None or POSITION_KIND in kinds:
        track = positions if positions is not None else source_position_track(source, session_key)
    frames: Iterator[PositionFrame] = iter(())
    if track is not None:
        if speed <= 0:
            # Unpaced, every frame is due at once; only the last can show.
            track = track.filter(pl.col("ts_utc") == pl.col("ts_utc").max())
        frames = position_frames(track)

    # A replay holds the whole race, so its snapshots can use the real
    # distance from lap 1 rather than the slice seen so far.
    snapshots = _SnapshotWorker(
        LapSnapshotFeed(session_key, total_laps=race_distance_laps(events), event_name=event_name),
        CallTracker(tuple(call_kinds)) if call_kinds else None,
    )
    try:
        start_wall = clock()
        start_wall_utc = wall_clock()
        pacing_anchor_ts = _pacing_anchor_ts(events, anchor_to_first_lap=anchor_to_first_lap)

        # Explanations arrive one of two ways. `explanation_schedule` is the
        # NON-BLOCKING form the WebSocket uses: someone else is computing the
        # batch on another thread and this loop simply picks it up whenever it
        # lands (see `f1_api.why.ExplanationSchedule` for why — it used to run
        # inline here and cost ~2 minutes of dead air before the first
        # envelope). Passing a model+store with no schedule keeps the original
        # inline behaviour, which is what the unit tests exercise.
        scheduled: list[ScheduledExplanation] = []
        if (
            explanation_schedule is None
            and narration_model is not None
            and explanation_store is not None
        ):
            scheduled = precompute_session_explanations(
                source,
                session_key,
                narration_model,
                explanation_store,
                radio_index=radio_index,
                max_explanations=max_explanations,
            )
        expl_index = 0

        timeline: Iterator[DomainEvent | PositionFrame] = heapq.merge(
            events, frames, key=lambda item: item.ts_utc
        )
        for item in timeline:
            if explanation_schedule is not None and not scheduled:
                # `None` while the batch is still being built; a list (possibly
                # empty) once it is. Anomalies whose moment already passed then
                # flush on this same iteration — late is always safe, early
                # never happens.
                landed = explanation_schedule.items()
                if landed:
                    scheduled = landed

            if isinstance(item, PositionFrame):
                if (item.ts_utc - pacing_anchor_ts).total_seconds() < -POSITION_STALE_AFTER_S:
                    continue
                expl_index, early = _flush_explanations_upto(
                    scheduled,
                    upto=item.ts_utc,
                    index=expl_index,
                    session_key=session_key,
                    pacing_anchor_ts=pacing_anchor_ts,
                    start_wall=start_wall,
                    start_wall_utc=start_wall_utc,
                    speed=speed,
                    delay=delay,
                    clock=clock,
                    sleep=sleep,
                )
                yield from early
                live_ts = _emit_paced(
                    pacing_ts=item.ts_utc,
                    pacing_anchor_ts=pacing_anchor_ts,
                    start_wall=start_wall,
                    start_wall_utc=start_wall_utc,
                    speed=speed,
                    delay=delay,
                    clock=clock,
                    sleep=sleep,
                )
                yield car_positions_envelope(live_ts, item)
                yield from snapshots.ready()
                continue

            event = item
            event_pacing = _event_pacing_ts(event)
            expl_index, early = _flush_explanations_upto(
                scheduled,
                upto=event_pacing,
                index=expl_index,
                session_key=session_key,
                pacing_anchor_ts=pacing_anchor_ts,
                start_wall=start_wall,
                start_wall_utc=start_wall_utc,
                speed=speed,
                delay=delay,
                clock=clock,
                sleep=sleep,
            )
            yield from early

            live_ts = _emit_paced(
                pacing_ts=event_pacing,
                pacing_anchor_ts=pacing_anchor_ts,
                start_wall=start_wall,
                start_wall_utc=start_wall_utc,
                speed=speed,
                delay=delay,
                clock=clock,
                sleep=sleep,
            )
            yield envelope_for_event(event, radio_store=radio_store, override_ts=live_ts)
            snapshots.submit(event, live_ts)
            yield from snapshots.ready()

        yield from snapshots.finish()

        # A batch that landed only after the final event still has to be
        # emitted — otherwise a fast replay (speed=0, or a short session)
        # would finish before the worker thread ever handed anything over.
        if explanation_schedule is not None and not scheduled:
            landed = explanation_schedule.items()
            if landed:
                scheduled = landed

        # Explanations whose anomaly_time sits after the last streamed event
        # (or between the last event and "end of session") still have to land.
        remaining = scheduled[expl_index:]
        if remaining:
            last_pacing = max(item.anomaly.anomaly_time for item in remaining)
            _, late = _flush_explanations_upto(
                scheduled,
                upto=last_pacing,
                index=expl_index,
                session_key=session_key,
                pacing_anchor_ts=pacing_anchor_ts,
                start_wall=start_wall,
                start_wall_utc=start_wall_utc,
                speed=speed,
                delay=delay,
                clock=clock,
                sleep=sleep,
            )
            yield from late
    finally:
        # A viewer who leaves closes this generator mid-race; the
        # worker must not outlive it.
        snapshots.close()


__all__ = [
    "CAR_POSITIONS_KIND",
    "DEFAULT_STREAM_KINDS",
    "EXPLANATION_KIND",
    "KERNEL_SNAPSHOT_KIND",
    "POSITION_STALE_AFTER_S",
    "RADIO_CLIP_KIND",
    "LapSnapshotFeed",
    "car_positions_envelope",
    "domain_event_envelope",
    "envelope_for_event",
    "explanation_envelope",
    "kernel_snapshot_envelope",
    "live_position_frames",
    "radio_clip_envelope",
    "replay_envelopes",
    "source_position_track",
]
