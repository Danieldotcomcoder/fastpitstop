"""`mode=live`: one shared feed per running session, fanned out to every viewer.

Each live viewer used to run the whole live path for itself: its own store
connection polled twice a second, its own `LiveKernelSession` recomputing
the kernel every lap (a single replay's kernel has been measured past
400 MB), its own anomaly detection and narration calls, and two or three
database connections held for as long as the tab stayed open. Production
Postgres allows 25 connections and the API 2 GB, so a handful of people
opening `/live` during a race would have exhausted one or the other, and a
viewer joining at lap 40 queued forty kernel recomputes before seeing a
single snapshot.

`LiveHub` does that work once per session. A background thread polls the
store, turns each new event into its wire envelope, feeds the kernel and
the narrator, and appends everything, already serialized, to one
append-only backlog. A viewer is just a cursor into that backlog: joining
at any point sends everything so far (the race as it stands, every kernel
snapshot and explanation already made), then follows the tail. Viewers hold
no database connection at all.

`LiveHubs` keeps one hub per (session, stream kinds) while anyone watches,
plus a grace period so a viewer who reconnects (a phone switching networks)
finds the hub, and its analysis, still running.

Spoiler-safety is unchanged: every envelope carries the event's own
`event_time_utc` and is released by the viewer's own Delay Sync clock. A
backlog shared between viewers on different delays is fine for exactly
that reason; nothing here decides what anyone may see.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections import deque
from collections.abc import Callable, Collection, Container, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from f1_api.config import Settings
from f1_api.schemas import StreamEnvelope
from f1_api.sessions import resolve_session
from f1_api.stream.replay import (
    POSITION_KIND,
    LapSnapshotFeed,
    car_positions_envelope,
    envelope_for_event,
    explanation_envelope,
    kernel_snapshot_envelope,
    live_position_frames,
)
from f1_api.why import cited_evidence_refs, explain_session_anomaly, worth_showing
from f1_domain import DomainEvent, SessionKey
from f1_kernel import EventSource, KernelReport, PositionFrame
from f1_radio.search import SearchIndex
from f1_radio.store import TranscriptStore
from f1_why import (
    AnomalyKind,
    AnomalyParams,
    ExplanationStore,
    Narrator,
    StoryParams,
    as_told,
    detect_anomalies,
    select_story,
    starting_grid,
    story_key,
)

LIVE_STORY_PARAMS = StoryParams(max_moments=10_000)
"""No salience trim live: which moments are the least salient is only known
once the race is over. `Settings.max_explanations` caps the cards instead."""

_log = logging.getLogger(__name__)

LIVE_POLL_INTERVAL_S = 0.5
"""How often a hub looks for new events.

Twice a second, not once: at one second the dashboard visibly stutters
against a broadcast. Affordable because the poll is incremental
(`FollowSource`) and runs once per session, not once per viewer.
"""

RECONNECT_WAIT_S = 2.0
"""How long a hub waits before reopening its store after a failure."""

DEFAULT_GRACE_S = 120.0
"""How long a hub outlives its last viewer, so a reconnect finds it warm."""

JOIN_GAP_HISTORY_S = 300.0
"""How much gap history a viewer joining mid-session receives in full.

Gap updates are most of a race (about 25,000 of 29,000 envelopes), and the
race desk only ever shows the latest gap per driver: each update replaces
the tower's three gap fields outright. A viewer joining at lap 40 needs, per
driver, the last update before this window plus every update inside it,
which reproduces the tower exactly for any Delay Sync delay up to the
window (the slider stops at 120 s). Sending the whole history instead took
long enough on a slow connection for the server's keepalive to drop the
viewer, who then reconnected and started the download again, forever.
"""

GAP_KIND = "gap_updated"


@dataclass(frozen=True, slots=True)
class _Published:
    """One envelope as sent, with what `join` needs to compact the backlog
    without parsing it again."""

    text: str
    kind: str
    driver: str | None
    event_time: datetime


LiveDeps = tuple[TranscriptStore | None, Narrator, ExplanationStore, SearchIndex | None]
"""What a hub needs beyond the event source: the radio store (to enrich
radio clips), the narration model, the explanation cache, the radio index."""


FOLLOW_OVERLAP = timedelta(seconds=120)
"""How far behind its newest event a hub re-reads on every poll.

A hub follows a session by event TIME: the recorder's sequence numbers
restart whenever its process restarts, and following by sequence froze the
2026 Bahrain GP qualifying for every viewer after a worker restart (every
new event sorted below the cursor). Some events carry the feed's own
timestamp (a status change, say), which can be a few seconds earlier than
events already stored, so each poll re-reads this much and skips what it
has sent. An event stored later than this behind the newest one would be
missed; the recorder now writes each event as it arrives, so none is.
"""

Identity = tuple[int, int, datetime]


@runtime_checkable
class FollowSource(Protocol):
    """An `EventSource` a hub can follow by time (`f1_kernel.StoreSource`).
    Parquet and in-memory sources are not, and fall back to a full re-read.
    Structural rather than a concrete import so the fallback stays honest in
    tests."""

    def events_since(
        self,
        session_key: SessionKey,
        since: datetime | None,
        *,
        kinds: Collection[str] | None = None,
        seen: Container[Identity] = (),
    ) -> Iterator[tuple[DomainEvent, Identity]]: ...


class LiveHub:
    """The live stream of one session, computed once and shared by every
    viewer (see the module docstring)."""

    def __init__(
        self,
        session_key: SessionKey,
        *,
        settings: Settings,
        event_name: str | None,
        kinds: Collection[str] | None,
        open_source: Callable[[], AbstractContextManager[EventSource | None]],
        open_deps: Callable[[], AbstractContextManager[LiveDeps]],
        poll_interval_s: float = LIVE_POLL_INTERVAL_S,
    ) -> None:
        self.session_key = session_key
        self._settings = settings
        self._event_name = event_name
        self._kinds = kinds
        self._open_source = open_source
        self._open_deps = open_deps
        self._poll_interval_s = poll_interval_s
        self._lock = threading.Lock()
        self._backlog: list[_Published] = []
        # What the poll has sent, kept on the hub (not the poll) so a store
        # that fails and is reopened resumes rather than repeats: the newest
        # event time, and the identities inside the re-read overlap.
        self._newest: datetime | None = None
        self._sent: dict[Identity, datetime] = {}
        self._seen_count = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"f1-api-live-{session_key.round}", daemon=True
        )
        self._thread.start()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def since(self, index: int) -> list[str]:
        """Serialized envelopes from `index` on, in the order they were
        made: how a viewer follows the tail after `join`."""
        with self._lock:
            return [entry.text for entry in self._backlog[index:]]

    def join(self, now: datetime | None = None) -> tuple[list[str], int]:
        """What a viewer joining now receives first, and the index its tail
        starts from: the session so far, with gap history older than
        `JOIN_GAP_HISTORY_S` reduced to each driver's last update (see
        there for why that changes nothing the viewer can see)."""
        with self._lock:
            entries = list(self._backlog)
        cutoff = (now or datetime.now(UTC)) - timedelta(seconds=JOIN_GAP_HISTORY_S)

        def superseded_gap(entry: _Published) -> bool:
            return entry.kind == GAP_KIND and entry.driver is not None and entry.event_time < cutoff

        # The update to keep is each driver's LATEST by event time, not the
        # last published: Delay Sync applies events in event-time order, and
        # a batch can arrive carrying an older timestamp than the one before.
        last_old_gap: dict[str, tuple[datetime, int]] = {}
        for index, entry in enumerate(entries):
            if superseded_gap(entry):
                key = entry.driver or ""
                kept = last_old_gap.get(key)
                if kept is None or entry.event_time >= kept[0]:
                    last_old_gap[key] = (entry.event_time, index)
        keep = {index for _, index in last_old_gap.values()}
        texts = [
            entry.text
            for index, entry in enumerate(entries)
            if index in keep or not superseded_gap(entry)
        ]
        return texts, len(entries)

    def stop(self) -> None:
        self._stop.set()

    def _publish(self, envelope: StreamEnvelope) -> None:
        driver = envelope.payload.get("driver")
        entry = _Published(
            text=envelope.model_dump_json(),
            kind=envelope.kind,
            driver=driver if isinstance(driver, str) else None,
            event_time=envelope.event_time_utc,
        )
        with self._lock:
            self._backlog.append(entry)

    def _run(self) -> None:
        # Every failure below is retried, never fatal: this thread is the
        # whole live race for every viewer of it. A store that drops
        # (Postgres restarted, a connection reset) is reopened, and the
        # cursor on the hub means nothing is repeated or skipped.
        while not self._stop.is_set():
            try:
                with self._open_deps() as (radio_store, model, store, radio_index):
                    analysis = _LiveAnalysis(
                        self._settings,
                        self.session_key,
                        event_name=self._event_name,
                        narration_model=model,
                        explanation_store=store,
                        radio_index=radio_index,
                    )
                    try:
                        self._follow(analysis, radio_store)
                    finally:
                        analysis.close()
            except Exception:
                _log.warning("live hub for %s failed; restarting", self.session_key, exc_info=True)
                self._stop.wait(RECONNECT_WAIT_S)

    def _follow(self, analysis: _LiveAnalysis, radio_store: TranscriptStore | None) -> None:
        while not self._stop.is_set():
            try:
                with self._open_source() as source:
                    if source is None:
                        _log.warning("live hub: %s is not in the store", self.session_key)
                    else:
                        self._poll(source, analysis, radio_store)
            except Exception:
                _log.warning(
                    "live hub for %s lost its store; reopening", self.session_key, exc_info=True
                )
            self._stop.wait(RECONNECT_WAIT_S)

    def _poll(
        self,
        source: EventSource,
        analysis: _LiveAnalysis,
        radio_store: TranscriptStore | None,
    ) -> None:
        """Poll `source` until stopped (returns) or it fails (raises)."""
        follow = source if isinstance(source, FollowSource) else None
        while not self._stop.is_set():
            if follow is not None:
                since = None if self._newest is None else self._newest - FOLLOW_OVERLAP
                batch = list(
                    follow.events_since(self.session_key, since, kinds=self._kinds, seen=self._sent)
                )
                events = [event for event, _ in batch]
            else:
                every = sorted(
                    source.events(self.session_key, kinds=self._kinds), key=lambda e: e.ts_utc
                )
                batch = []
                events = every[self._seen_count :]
            for item in live_position_frames(events):
                if isinstance(item, PositionFrame):
                    self._publish(car_positions_envelope(item.ts_utc, item))
                    continue
                self._publish(_envelope(item, radio_store))
                analysis.submit(item)
            # Record what was sent only once the batch is published: a
            # failure above re-reads it, rather than losing what was not.
            if follow is not None:
                self._remember(batch)
            else:
                self._seen_count += len(events)
            for envelope in analysis.ready():
                self._publish(envelope)
            self._stop.wait(self._poll_interval_s)

    def _remember(self, batch: list[tuple[DomainEvent, Identity]]) -> None:
        for event, identity in batch:
            self._sent[identity] = event.ts_utc
            if self._newest is None or event.ts_utc > self._newest:
                self._newest = event.ts_utc
        if self._newest is not None:
            # Identities older than the next poll's window are never read
            # again, so they need not be remembered.
            horizon = self._newest - FOLLOW_OVERLAP
            for identity in [i for i, ts in self._sent.items() if ts < horizon]:
                del self._sent[identity]


def _envelope(event: DomainEvent, radio_store: TranscriptStore | None) -> StreamEnvelope:
    """The event's envelope, with its radio transcript when the store can
    give one. A failed lookup (the radio store's connection dropped) costs
    the transcript, never the clip or the rest of the poll."""
    if radio_store is not None:
        try:
            return envelope_for_event(event, radio_store=radio_store)
        except Exception:
            _log.warning("radio enrichment failed; sending the clip without it", exc_info=True)
    return envelope_for_event(event, radio_store=None)


class LiveHubs:
    """One `LiveHub` per (session, stream kinds) while anyone is watching it,
    kept `grace_s` past its last viewer."""

    def __init__(self, *, grace_s: float = DEFAULT_GRACE_S) -> None:
        self._grace_s = grace_s
        self._lock = threading.Lock()
        self._hubs: dict[tuple[SessionKey, frozenset[str] | None], _Entry] = {}

    def acquire(
        self,
        session_key: SessionKey,
        kinds: Collection[str] | None,
        make: Callable[[], LiveHub],
    ) -> LiveHub:
        key = (session_key, frozenset(kinds) if kinds is not None else None)
        with self._lock:
            entry = self._hubs.get(key)
            if entry is None or entry.hub.stopped:
                entry = _Entry(make())
                self._hubs[key] = entry
            if entry.timer is not None:
                entry.timer.cancel()
                entry.timer = None
            entry.viewers += 1
            return entry.hub

    def release(self, hub: LiveHub) -> None:
        with self._lock:
            for key, entry in self._hubs.items():
                if entry.hub is hub:
                    entry.viewers -= 1
                    if entry.viewers <= 0:
                        if self._grace_s <= 0:
                            self._retire(key, entry)
                        else:
                            entry.timer = threading.Timer(
                                self._grace_s, self._expire, args=(key, entry)
                            )
                            entry.timer.daemon = True
                            entry.timer.start()
                    return

    def active(self) -> int:
        with self._lock:
            return len(self._hubs)

    def stop_all(self) -> None:
        """Retire every hub now (process shutdown, and test isolation)."""
        with self._lock:
            for key, entry in list(self._hubs.items()):
                if entry.timer is not None:
                    entry.timer.cancel()
                self._retire(key, entry)

    def _expire(self, key: tuple[SessionKey, frozenset[str] | None], entry: _Entry) -> None:
        with self._lock:
            if entry.viewers <= 0 and self._hubs.get(key) is entry:
                self._retire(key, entry)

    def _retire(self, key: tuple[SessionKey, frozenset[str] | None], entry: _Entry) -> None:
        entry.hub.stop()
        del self._hubs[key]


class _Entry:
    def __init__(self, hub: LiveHub) -> None:
        self.hub = hub
        self.viewers = 0
        self.timer: threading.Timer | None = None


LIVE_HUBS = LiveHubs()
"""The API process's hubs."""


class _LiveAnalysis:
    """Kernel snapshots and explanations for one live session, on its own
    thread.

    Each completed lap recomputes the whole kernel (up to a second on a real
    race), then runs anomaly detection and may call the narration model over
    the network. Off the hub's polling thread, so timing keeps flowing while
    a lap is analysed. Envelopes carry their lap's or anomaly's own instant
    and may go out after it, which is spoiler-safe: Delay Sync releases a
    late envelope on arrival, and nothing here can make one early.

    Explanations read evidence through their own session source, never the
    poll's: a store source wraps one database connection, and the poll uses
    that from another thread."""

    def __init__(
        self,
        settings: Settings,
        session_key: SessionKey,
        *,
        event_name: str | None,
        narration_model: Narrator,
        explanation_store: ExplanationStore,
        radio_index: SearchIndex | None,
    ) -> None:
        self._settings = settings
        self._session_key = session_key
        self._event_name = event_name
        self._model = narration_model
        self._store = explanation_store
        self._radio_index = radio_index
        self._feed = LapSnapshotFeed(session_key, event_name=event_name)
        self._considered: set[str] = set()
        self._told: dict[str, str] = {}
        """Each neutralization card considered: the retirements it named."""
        self._shown = 0
        self._closed = threading.Event()
        self._inbox: queue.SimpleQueue[DomainEvent | None] = queue.SimpleQueue()
        self._ready: deque[StreamEnvelope] = deque()
        self._thread = threading.Thread(target=self._run, name="f1-api-live-analysis", daemon=True)
        self._thread.start()

    def submit(self, event: DomainEvent) -> None:
        if event.kind != POSITION_KIND:
            self._inbox.put(event)

    def ready(self) -> Iterator[StreamEnvelope]:
        """Envelopes finished since the last call, oldest first."""
        while self._ready:
            yield self._ready.popleft()

    def close(self) -> None:
        self._closed.set()
        self._inbox.put(None)

    def _run(self) -> None:
        while (event := self._inbox.get()) is not None and not self._closed.is_set():
            try:
                report = self._feed.feed(event)
                if report is None:
                    continue
                if report.crossovers:
                    self._ready.append(kernel_snapshot_envelope(event.ts_utc, report))
                self._explain(report)
            except Exception:
                _log.warning(
                    "live analysis failed for one lap; the stream continues", exc_info=True
                )

    def _explain(self, report: KernelReport) -> None:
        # The replay's story rules, applied to the race so far: each moment
        # is judged on its own, so the live story and the replay's are the
        # same cards. One card per real event (`story_key`): a live race
        # re-detects every lap, and a tyre cliff found on lap 30 is found
        # again on lap 31, anchored to lap 31. A moment the narrator has
        # nothing to say about is considered once and skipped, never shown
        # as "no clear cause". `Settings.max_explanations` caps the cards.
        if self._shown >= self._settings.max_explanations:
            return
        grid = starting_grid(self._feed.events, report.frames.laps)
        # Neutralizations already shown keep what their card said, so a
        # retirement confirmed after the card went out gets its own card
        # (`f1_why.as_told`).
        anomalies = as_told(
            detect_anomalies(report.frames, report, AnomalyParams(), grid=grid), self._told
        )
        fresh = [
            moment
            for moment in select_story(anomalies, LIVE_STORY_PARAMS)
            if story_key(moment) not in self._considered
        ]
        if not fresh:
            return
        with resolve_session(self._settings, self._session_key) as resolved:
            if resolved is None:
                return
            for moment in fresh:
                if self._closed.is_set() or self._shown >= self._settings.max_explanations:
                    return
                if moment.kind is AnomalyKind.RACE_FINISH and len(moment.drivers) < 3:
                    # Wait for the podium: considered now, the card would
                    # name the winner alone and never be told again.
                    continue
                explanation, bundle = explain_session_anomaly(
                    resolved.source,
                    self._session_key,
                    moment,
                    self._model,
                    self._store,
                    report=report,
                    radio_index=self._radio_index,
                    event_name=self._event_name,
                )
                self._considered.add(story_key(moment))
                shown = worth_showing(explanation)
                if moment.kind is AnomalyKind.NEUTRALIZATION:
                    self._told[story_key(moment)] = (
                        str(moment.summary.get("retired") or "") if shown else ""
                    )
                if not shown:
                    continue
                cited = cited_evidence_refs(self._session_key, bundle, explanation)
                self._ready.append(
                    explanation_envelope(
                        moment.anomaly_time, self._session_key, moment, explanation, cited
                    )
                )
                self._shown += 1


__all__ = ["FOLLOW_OVERLAP", "LIVE_HUBS", "FollowSource", "LiveDeps", "LiveHub", "LiveHubs"]
