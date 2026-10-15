"""Calls on the stream and over REST (ROADMAP W5).

The kernel decides what a call is and how it resolved (`f1_kernel.calls`);
this module only decides which kinds are served and when a change is news.

- `enabled_call_kinds`: the kinds cleared by a passing held-out backtest
  (`f1_kernel.CLEARED_CALL_KINDS`), unless `Settings.call_kinds` says
  otherwise (test harnesses and local development only).
- `CallTracker`: run once per analysed lap, it returns only the calls that
  are new or whose status changed since the last lap. The stream sends each
  as one `call` envelope stamped with that lap's own instant: the moment
  everything the call needed had crossed the line, or later, never earlier.
  A viewer's browser folds the released envelopes by `call_id`, so the
  scorecard it shows can only count what Delay Sync has released.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import polars as pl

from f1_api.config import Settings
from f1_api.convert import call_out, scorecard_out
from f1_api.schemas import CallLedgerOut, CallOut, StreamEnvelope
from f1_kernel import (
    CLEARED_CALL_KINDS,
    CallKind,
    KernelParams,
    KernelReport,
    SessionFrames,
    call_ledger,
    scorecard,
)

CALL_KIND = "call"
"""The stream envelope kind; its payload is one `CallOut`."""


def enabled_call_kinds(settings: Settings) -> tuple[CallKind, ...]:
    if settings.call_kinds is None:
        return tuple(sorted(CLEARED_CALL_KINDS))
    return tuple(sorted(CallKind(kind) for kind in settings.call_kinds))


class CallTracker:
    """What changed in the ledger since the last lap. Not thread-safe: one
    per stream (or per live hub), driven from its one analysis thread."""

    def __init__(self, kinds: Sequence[CallKind]) -> None:
        self._kinds = tuple(kinds)
        self._sent: dict[str, CallOut] = {}

    def update(self, report: KernelReport, *, final: bool = False) -> list[CallOut]:
        if not self._kinds:
            return []
        calls = call_ledger(
            report.frames,
            report.params,
            total_laps=report.total_laps,
            final=final,
            kinds=self._kinds,
        )
        changed: list[CallOut] = []
        for call in calls:
            out = call_out(call)
            if self._sent.get(out.call_id) != out:
                self._sent[out.call_id] = out
                changed.append(out)
        return changed


def call_envelope(event_time: datetime, call: CallOut) -> StreamEnvelope:
    return StreamEnvelope(
        event_time_utc=event_time, kind=CALL_KIND, payload=call.model_dump(mode="json")
    )


def session_call_ledger(
    frames: SessionFrames,
    params: KernelParams,
    kinds: Sequence[CallKind],
    *,
    upto_lap: int | None,
    total_laps: int | None,
    running: bool = False,
) -> CallLedgerOut:
    """The ledger as a viewer who has watched to the end of `upto_lap` has
    it: every lap past it left out, so no call, outcome or stop beyond it is
    in the answer. None is the whole race, settled (`final`) unless it is
    still `running`."""
    laps = frames.laps
    final = upto_lap is None and not running
    if upto_lap is not None:
        laps = laps.filter(pl.col("lap_number") <= upto_lap)
    stints = frames.stints
    if upto_lap is not None and not stints.is_empty():
        stints = stints.filter(pl.col("start_lap") <= upto_lap)
    sliced = SessionFrames(
        session_key=frames.session_key,
        laps=laps,
        stints=stints,
        circuit=frames.circuit,
        event_name=frames.event_name,
    )
    calls = (
        call_ledger(sliced, params, total_laps=total_laps, final=final, kinds=kinds)
        if kinds
        else []
    )
    return CallLedgerOut(
        session_key=frames.session_key,
        kinds=list(kinds),
        upto_lap=upto_lap,
        final=final,
        calls=[call_out(call) for call in calls],
        scorecard=scorecard_out(scorecard(calls)),
    )


__all__ = [
    "CALL_KIND",
    "CallTracker",
    "call_envelope",
    "enabled_call_kinds",
    "session_call_ledger",
]
