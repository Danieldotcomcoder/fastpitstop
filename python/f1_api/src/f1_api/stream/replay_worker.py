"""The heavy half of a shared replay, in a worker process: the session's
story evidence and its kernel pass.

Sharing a replay between its viewers (`f1_api.stream.replay_hub`) made the
API's memory flat, but a load test on 2025 Suzuka (2026-10-30, 50 viewers
joining at once, 1x) still found the site stalling while a session was
analysed: 10.7 s to first data, envelopes up to 10 s late at the 95th
percentile, a page request taking 13.6 s. The kernel pass is about 20 s of
pure Python and the story evidence about 3 s more; on a thread they hold
the GIL against the event loop, and every socket write the loop makes
then waits out a switch interval. With both switched off the same load
gave 3.3 s, 0.6 s and 2.2 s. A shorter switch interval did not help.

So a shared replay runs them here, in a process of its own (spawned:
the API is threaded, and forking a threaded process is unsafe). The worker
re-reads the session's events itself, checks they are the ones the
timeline was built from (`events_fingerprint`), and sends each result back
the moment it exists: the story's evidence first (a few seconds; its first
card is often lights out), then every snapshot and call. The API narrates
the evidence, so a narrator's request budget stays one budget, and puts
the rest straight onto the timeline. The worker's memory, the whole kernel
included, goes back to the system when it exits.

A worker that dies, or finds different events, costs the replay its
snapshots and story, never the replay: the timeline already streams.
"""

from __future__ import annotations

import contextlib
import logging
import multiprocessing
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.connection import Connection

from f1_api.config import Settings
from f1_api.logging_config import configure_logging
from f1_api.radio import radio_search_index_cm
from f1_api.sessions import resolve_session
from f1_api.stream.replay import AnalysisSpec, events_fingerprint, kernel_outputs, replay_events
from f1_api.why import story_bundles
from f1_kernel import EventSource
from f1_why import EvidenceBundle

_log = logging.getLogger(__name__)

WORKER_POLL_S = 0.5
"""How often the API side looks up from a quiet worker to check whether
the replay is still wanted."""


@dataclass(frozen=True, slots=True)
class WorkerJob:
    """Everything a worker needs, as plain data: where the session comes
    from, which events the timeline was built from, and whether to make
    the story too."""

    settings: Settings
    spec: AnalysisSpec
    kinds: frozenset[str] | None
    story: bool


def _story(job: WorkerJob, source: EventSource) -> list[EvidenceBundle]:
    try:
        with radio_search_index_cm(job.settings) as radio_index:
            return story_bundles(
                source,
                job.spec.session_key,
                radio_index=radio_index,
                event_name=job.spec.event_name,
                max_explanations=job.settings.max_explanations,
            )
    except Exception:
        _log.warning(
            "story for %s failed; replaying without it", job.spec.session_key, exc_info=True
        )
        return []


def run_job(job: WorkerJob, conn: Connection) -> None:
    """The worker process: everything it makes goes down `conn` as it is
    made, then `("done",)`."""
    configure_logging()
    try:
        with resolve_session(job.settings, job.spec.session_key) as resolved:
            if resolved is None:
                return
            source = resolved.source
            if job.story:
                conn.send(("story", _story(job, source)))
            events = replay_events(source, job.spec.session_key, job.kinds)
            if events_fingerprint(events) != job.spec.fingerprint:
                _log.warning(
                    "the events of %s changed since its replay was built; no snapshots",
                    job.spec.session_key,
                )
                return
            for after, tail in kernel_outputs(job.spec, events):
                conn.send(("output", after, tail))
    except Exception:
        _log.warning("replay worker for %s failed", job.spec.session_key, exc_info=True)
    finally:
        with contextlib.suppress(OSError):
            conn.send(("done",))
        conn.close()


def run_worker(
    job: WorkerJob,
    *,
    on_story: Callable[[list[EvidenceBundle]], None],
    on_output: Callable[[int, str], None],
    stopped: Callable[[], bool],
) -> None:
    """Run `job` in a new worker process and hand each result over as it
    arrives. Returns when the worker is done, or at once (killing it) when
    `stopped` says nobody wants the replay any more."""
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(
        target=run_job,
        args=(job, send),
        name=f"f1-api-replay-{job.spec.session_key.year}-{job.spec.session_key.round}",
        daemon=True,
    )
    process.start()
    # Only the worker writes; without closing our copy, a worker that dies
    # would leave the pipe open and `recv` waiting forever.
    send.close()
    finished = False
    try:
        while not stopped():
            if not receive.poll(WORKER_POLL_S):
                continue
            try:
                message = receive.recv()
            except EOFError:
                _log.warning("replay worker for %s exited early", job.spec.session_key)
                return
            if message[0] == "done":
                finished = True
                return
            if message[0] == "story":
                on_story(message[1])
            elif message[0] == "output":
                on_output(message[1], message[2])
    finally:
        receive.close()
        if not finished:
            process.terminate()
        process.join(timeout=10.0)


__all__ = ["WORKER_POLL_S", "WorkerJob", "run_job", "run_worker"]
