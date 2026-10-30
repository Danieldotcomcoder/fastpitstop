"""`mode=replay`: one shared replay per session, walked by every viewer at
its own pace.

Each replay viewer used to do the whole replay for itself: load and sort the
session's events (about 100 MB for a race), load its position track (about
165 MB more while the geometry was checked), run the kernel once per lap on
its own thread, and build the session's explanations on another. A load test
on 2025 Suzuka at 1x (2026-10-24) put that at 100-280 MB per viewer, all in
one process: 8 viewers took the API to 1.3 GB and page requests to 20 s, 16
timed pages out, 32 delivered no data at all. Production caps the API at
3 GB and 2 CPUs.

None of that work differs between viewers. Lap N's analysis is the same for
everyone; so is every envelope, apart from its wall-clock stamp. So a
`ReplayHub` builds the session's `ReplayTimeline` once (every envelope
serialized, on the race's own clock), runs its kernel pass and its
explanations once, and every viewer is a `ReplayCursor` over it: its own
speed, its own stamps, its own position in the race, and nothing else.
The timeline is plain text, about 25 MB for a race. The analysis, the
story's evidence and the kernel pass, runs in a worker process
(`f1_api.stream.replay_worker`), out of the way of the event loop.

Heavy work is also limited across sessions (`ReplayLimits`): at most
`MAX_CONCURRENT_BUILDS` timelines are built at once, and at most
`MAX_CONCURRENT_ANALYSES` sessions are analysed at once. A viewer of a
session whose build is queued waits on an open socket; a viewer whose
analysis is queued already has the race, and its snapshots and story
arrive late, which is spoiler-safe.

`ReplayHubs` keeps one hub per (settings, session, stream kinds, anchor)
while anyone watches, plus a grace period so a reload finds it warm. A hub
older than `MAX_SHARE_AGE_S` takes no new viewers (they get a fresh one),
so a session whose data changes, such as the positions import landing
after a race, is never served stale for long.

Spoiler-safety is unchanged: every envelope is stamped by the viewer's own
pace and released by the viewer's own Delay Sync clock.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Hashable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

from f1_api.stream.replay import ReplayTimeline, analysis_slot
from f1_api.stream.replay_worker import WorkerJob, run_worker
from f1_api.why import ExplanationSchedule, ScheduledExplanation
from f1_domain import SessionKey
from f1_why import EvidenceBundle

_log = logging.getLogger(__name__)

MAX_CONCURRENT_BUILDS = 2
"""Timelines built at once. A race's build reads its events and position
track and serializes them: about 3 s and a few hundred MB while it runs."""

MAX_CONCURRENT_ANALYSES = 2
"""Session analyses run at once, across sessions: worker processes for
shared replays (about 25 s of CPU and a few hundred MB each for a race),
kernel threads for the rest. Production has 2 CPUs."""

DEFAULT_GRACE_S = 120.0
"""How long a hub outlives its last viewer, so a reload finds it warm."""

MAX_SHARE_AGE_S = 3600.0
"""How old a hub may be and still take a new viewer."""


class ReplayLimits:
    """The bounds on replay work in one process: a pool that builds
    timelines, and the slots kernel passes and explanation batches share."""

    def __init__(
        self,
        *,
        builds: int = MAX_CONCURRENT_BUILDS,
        analyses: int = MAX_CONCURRENT_ANALYSES,
    ) -> None:
        self.builds = ThreadPoolExecutor(
            max_workers=builds, thread_name_prefix="f1-api-replay-build"
        )
        self.analyses = threading.BoundedSemaphore(analyses)


REPLAY_LIMITS = ReplayLimits()
"""The API process's limits."""


@dataclass(frozen=True, slots=True)
class WorkerPlan:
    """How a hub hands its heavy half to a worker process
    (`f1_api.stream.replay_worker`): the job for a built timeline, and how
    to narrate the story evidence the worker sends back."""

    job: Callable[[ReplayTimeline], WorkerJob]
    narrate: Callable[[list[EvidenceBundle]], list[ScheduledExplanation]] | None = None


class ReplayHub:
    """One session's replay, built and analysed once for all its viewers
    (see the module docstring).

    `build` makes the timeline (on the build pool). Its analysis then runs
    in an analysis slot: in a worker process when a `worker` plan is given
    (a shared replay), else on a thread here, with `explain` filling the
    explanation batch on another. Each must open whatever it reads itself:
    none runs on the viewer's thread."""

    def __init__(
        self,
        session_key: SessionKey,
        *,
        build: Callable[[], ReplayTimeline],
        explain: Callable[[ExplanationSchedule], None] | None = None,
        worker: WorkerPlan | None = None,
        limits: ReplayLimits = REPLAY_LIMITS,
    ) -> None:
        self.session_key = session_key
        self._build = build
        self._worker = worker
        self._limits = limits
        self._closed = threading.Event()
        schedule: ExplanationSchedule | None = None
        if worker is not None:
            schedule = ExplanationSchedule() if worker.narrate is not None else None
        elif explain is not None:
            schedule = ExplanationSchedule()
            threading.Thread(
                target=self._explain,
                args=(explain, schedule),
                name="f1-api-explanations",
                daemon=True,
            ).start()
        self._future: Future[ReplayTimeline] = limits.builds.submit(self._prepare, schedule)

    def _prepare(self, schedule: ExplanationSchedule | None) -> ReplayTimeline:
        if self._closed.is_set():
            return ReplayTimeline.empty(self.session_key)
        started = time.perf_counter()
        timeline = self._build()
        _log.info(
            "replay of %s built in %.1f s: %d envelopes, %.1f MB",
            self.session_key,
            time.perf_counter() - started,
            timeline.item_count,
            timeline.text_bytes / 2**20,
        )
        if schedule is not None:
            timeline.attach_explanations(schedule)
        if self._worker is not None:
            threading.Thread(
                target=self._run_worker,
                args=(self._worker, timeline, schedule),
                name="f1-api-replay-worker",
                daemon=True,
            ).start()
        else:
            timeline.start_analysis(self._limits.analyses)
        if self._closed.is_set():
            timeline.close()
        return timeline

    def _run_worker(
        self, plan: WorkerPlan, timeline: ReplayTimeline, schedule: ExplanationSchedule | None
    ) -> None:
        told = False

        def on_story(bundles: list[EvidenceBundle]) -> None:
            nonlocal told
            if schedule is None or plan.narrate is None:
                return
            told = True
            # Narration may be network calls: never in the way of the
            # worker's next result.
            threading.Thread(
                target=self._narrate,
                args=(plan.narrate, bundles, schedule),
                name="f1-api-explanations",
                daemon=True,
            ).start()

        try:
            if timeline.item_count:
                with analysis_slot(self._limits.analyses, self._closed) as granted:
                    if granted:
                        run_worker(
                            plan.job(timeline),
                            on_story=on_story,
                            on_output=timeline.add_output,
                            stopped=lambda: self._closed.is_set() or timeline.closed,
                        )
        except Exception:
            _log.warning(
                "replay analysis for %s failed; the replay carries on without it",
                self.session_key,
                exc_info=True,
            )
        finally:
            timeline.finish_analysis()
            if schedule is not None and not told:
                schedule.set([])

    def _narrate(
        self,
        narrate: Callable[[list[EvidenceBundle]], list[ScheduledExplanation]],
        bundles: list[EvidenceBundle],
        schedule: ExplanationSchedule,
    ) -> None:
        try:
            schedule.set(narrate(bundles))
        except BaseException as exc:  # a replay without explanations is still a replay
            _log.warning("explanations failed for %s", self.session_key, exc_info=True)
            schedule.fail(exc)

    def _explain(
        self, explain: Callable[[ExplanationSchedule], None], schedule: ExplanationSchedule
    ) -> None:
        # Never raises: a replay without explanations is still a replay.
        try:
            with analysis_slot(self._limits.analyses, self._closed) as granted:
                if granted:
                    explain(schedule)
                else:
                    schedule.set([])
        except BaseException as exc:
            _log.warning("explanations failed for %s", self.session_key, exc_info=True)
            schedule.fail(exc)

    @property
    def stopped(self) -> bool:
        return self._closed.is_set()

    @property
    def failed(self) -> bool:
        """The build raised: nobody new should join this hub."""
        return (
            self._future.done()
            and not self._future.cancelled()
            and self._future.exception() is not None
        )

    async def timeline(self) -> ReplayTimeline:
        """The built timeline, once it is. Shielded: a viewer who stops
        waiting must not cancel the build every other viewer waits on."""
        return await asyncio.shield(asyncio.wrap_future(self._future))

    def close(self) -> None:
        """Stop the analysis and explanations, now or once the build ends."""
        self._closed.set()
        self._future.add_done_callback(_close_timeline)


def _close_timeline(future: Future[ReplayTimeline]) -> None:
    if not future.cancelled() and future.exception() is None:
        future.result().close()


class _Entry:
    def __init__(self, key: Hashable, hub: ReplayHub, created: float) -> None:
        self.key = key
        self.hub = hub
        self.created = created
        self.viewers = 0
        self.timer: threading.Timer | None = None


class ReplayHubs:
    """One `ReplayHub` per key while anyone watches it, kept `grace_s` past
    its last viewer; a hub older than `max_age_s` takes no new viewers."""

    def __init__(
        self,
        *,
        grace_s: float = DEFAULT_GRACE_S,
        max_age_s: float = MAX_SHARE_AGE_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._grace_s = grace_s
        self._max_age_s = max_age_s
        self._clock = clock
        self._lock = threading.Lock()
        self._current: dict[Hashable, ReplayHub] = {}
        self._entries: dict[ReplayHub, _Entry] = {}

    def acquire(self, key: Hashable, make: Callable[[], ReplayHub]) -> ReplayHub:
        with self._lock:
            now = self._clock()
            hub = self._current.get(key)
            entry = self._entries.get(hub) if hub is not None else None
            if (
                entry is None
                or entry.hub.stopped
                or entry.hub.failed
                or now - entry.created > self._max_age_s
            ):
                if entry is not None and entry.viewers <= 0:
                    self._retire(entry)
                entry = _Entry(key, make(), now)
                self._entries[entry.hub] = entry
                self._current[key] = entry.hub
            if entry.timer is not None:
                entry.timer.cancel()
                entry.timer = None
            entry.viewers += 1
            return entry.hub

    def release(self, hub: ReplayHub) -> None:
        with self._lock:
            entry = self._entries.get(hub)
            if entry is None:
                return
            entry.viewers -= 1
            if entry.viewers > 0:
                return
            if self._current.get(entry.key) is not hub or self._grace_s <= 0 or hub.failed:
                self._retire(entry)
                return
            entry.timer = threading.Timer(self._grace_s, self._expire, args=(entry,))
            entry.timer.daemon = True
            entry.timer.start()

    def active(self) -> int:
        """Hubs held, in use or in their grace period."""
        with self._lock:
            return len(self._entries)

    def stop_all(self) -> None:
        """Retire every hub now (process shutdown, and test isolation)."""
        with self._lock:
            for entry in list(self._entries.values()):
                if entry.timer is not None:
                    entry.timer.cancel()
                self._retire(entry)

    def _expire(self, entry: _Entry) -> None:
        with self._lock:
            if entry.viewers <= 0 and self._entries.get(entry.hub) is entry:
                self._retire(entry)

    def _retire(self, entry: _Entry) -> None:
        entry.hub.close()
        self._entries.pop(entry.hub, None)
        if self._current.get(entry.key) is entry.hub:
            del self._current[entry.key]


REPLAY_HUBS = ReplayHubs()
"""The API process's replay hubs."""


__all__ = [
    "DEFAULT_GRACE_S",
    "MAX_CONCURRENT_ANALYSES",
    "MAX_CONCURRENT_BUILDS",
    "MAX_SHARE_AGE_S",
    "REPLAY_HUBS",
    "REPLAY_LIMITS",
    "ReplayHub",
    "ReplayHubs",
    "ReplayLimits",
    "WorkerPlan",
]
