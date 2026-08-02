"""The backtest harness: run an analytic over many sessions, tabulate.

This is the test method for the whole of Phase 2. CLAUDE.md is explicit
that backtesting against 2023-2025 is the primary way analytics are
verified, not mocks — so every later prompt's analytic gets pointed at this
and run over a corpus of real races.

    bt = Backtest(HistoricalSource("data/historical"))
    result = bt.run(my_analytic, sessions=bt.sessions(session_types=[SessionType.RACE]))
    print(result.table)

An analytic is any `(SessionFrames, KernelParams) -> rows` callable. It may
return a mapping (one row), a sequence of mappings, a polars DataFrame, or
None (nothing to report). Whatever it returns is prefixed with the session's
identity so the comparison table can be grouped, filtered and diffed.

Two properties matter and are deliberate:

- **One bad session does not end the run.** Over ~80 races something will
  be missing or malformed; failures are captured per session in
  `result.errors` and the rest continues. `raise_on_error=True` when you
  are debugging one.
- **The parameter set is recorded.** `result.params` is the exact
  `KernelParams` used, so a stored comparison table can be reproduced —
  a result without its parameters is not a result.

`Backtest.compare` runs SEVERAL named analytics over the same sessions into
one table with an `analytic` column, which is how a change to an analytic
is judged against its predecessor over the same races.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import polars as pl

from f1_domain import SessionKey, SessionType
from f1_kernel.frames import SessionFrames
from f1_kernel.load import session_frames
from f1_kernel.params import KernelParams
from f1_kernel.sources import EventSource, SessionRef

type AnalyticRows = Mapping[str, object] | Sequence[Mapping[str, object]] | pl.DataFrame | None
type Analytic = Callable[[SessionFrames, KernelParams], AnalyticRows]
"""Every analytic takes frames plus params and returns rows. Nothing reads a
global constant (CLAUDE.md), which is why params is not optional."""

SESSION_COLUMNS: tuple[str, ...] = (
    "session_year",
    "session_round",
    "session_type",
    "event_name",
)


@dataclass(frozen=True, slots=True)
class SessionFailure:
    """One session that failed, kept rather than swallowed."""

    session_key: SessionKey
    error: str

    def __str__(self) -> str:
        key = self.session_key
        return f"{key.year} R{key.round:02d} {key.session_type.value}: {self.error}"


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """The comparison table plus everything needed to interpret it."""

    table: pl.DataFrame
    params: KernelParams
    sessions_run: int
    sessions_failed: int
    errors: tuple[SessionFailure, ...] = ()
    wall_seconds: float = 0.0
    """Measured strictly around the run. Reporting only — it is never an
    input to an analytic, so determinism holds."""

    def summary(self) -> str:
        rows = self.table.height
        line = (
            f"backtest: {self.sessions_run} session(s) -> {rows} row(s) in {self.wall_seconds:.2f}s"
        )
        if self.sessions_failed:
            line += f"; {self.sessions_failed} failed"
        return line

    def raise_for_errors(self) -> None:
        """Raise if any session failed — for a CI-style backtest that should
        be all-green rather than best-effort."""
        if self.errors:
            joined = "\n  ".join(str(error) for error in self.errors)
            raise BacktestError(f"{len(self.errors)} session(s) failed:\n  {joined}")


class BacktestError(RuntimeError):
    """At least one session in a backtest failed."""


@dataclass
class Backtest:
    """Runs analytics over a corpus of sessions from one source."""

    source: EventSource
    params: KernelParams = field(default_factory=KernelParams)

    def sessions(
        self,
        *,
        session_types: Iterable[SessionType] | None = None,
        seasons: Iterable[int] | None = None,
        limit: int | None = None,
    ) -> list[SessionRef]:
        """The corpus, filtered. Races are usually what you want — practice
        and qualifying have no race-relevant stint or gap structure."""
        refs = self.source.sessions()
        if session_types is not None:
            wanted = set(session_types)
            refs = [ref for ref in refs if ref.session_key.session_type in wanted]
        if seasons is not None:
            years = set(seasons)
            refs = [ref for ref in refs if ref.session_key.year in years]
        return refs[:limit] if limit is not None else refs

    def load(self, ref: SessionRef | SessionKey) -> SessionFrames:
        """Frames for one session, resolving its event name (hence circuit)
        when the source knows it."""
        if isinstance(ref, SessionKey):
            return session_frames(self.source, ref)
        return session_frames(self.source, ref.session_key, event_name=ref.event_name)

    def run(
        self,
        analytic: Analytic,
        sessions: Iterable[SessionRef | SessionKey] | None = None,
        *,
        params: KernelParams | None = None,
        raise_on_error: bool = False,
        name: str | None = None,
    ) -> BacktestResult:
        """Run one analytic over `sessions` (default: the whole corpus) and
        collect every returned row into one table."""
        resolved_params = params if params is not None else self.params
        refs = list(sessions) if sessions is not None else self.sessions()
        started = time.perf_counter()

        frames: list[pl.DataFrame] = []
        errors: list[SessionFailure] = []
        ran = 0
        for ref in refs:
            key = ref if isinstance(ref, SessionKey) else ref.session_key
            try:
                loaded = self.load(ref)
                rows = analytic(loaded, resolved_params)
            except Exception as exc:
                if raise_on_error:
                    raise
                errors.append(SessionFailure(session_key=key, error=f"{type(exc).__name__}: {exc}"))
                continue
            ran += 1
            table = _as_frame(rows)
            if table is None or table.is_empty():
                continue
            frames.append(_with_session_columns(table, loaded, name))

        return BacktestResult(
            table=_concat(frames),
            params=resolved_params,
            sessions_run=ran,
            sessions_failed=len(errors),
            errors=tuple(errors),
            wall_seconds=time.perf_counter() - started,
        )

    def compare(
        self,
        analytics: Mapping[str, Analytic],
        sessions: Iterable[SessionRef | SessionKey] | None = None,
        *,
        params: KernelParams | None = None,
        raise_on_error: bool = False,
    ) -> BacktestResult:
        """Run several named analytics over the SAME sessions into one
        table, tagged with an `analytic` column — the "did this change help?"
        view. Session loading is not shared between analytics, so each one
        sees identical, independently built frames."""
        refs = list(sessions) if sessions is not None else self.sessions()
        started = time.perf_counter()

        frames: list[pl.DataFrame] = []
        errors: list[SessionFailure] = []
        ran = 0
        for label, analytic in analytics.items():
            result = self.run(
                analytic, refs, params=params, raise_on_error=raise_on_error, name=label
            )
            ran += result.sessions_run
            errors.extend(result.errors)
            if not result.table.is_empty():
                frames.append(result.table)

        return BacktestResult(
            table=_concat(frames),
            params=params if params is not None else self.params,
            sessions_run=ran,
            sessions_failed=len(errors),
            errors=tuple(errors),
            wall_seconds=time.perf_counter() - started,
        )


def _as_frame(rows: AnalyticRows) -> pl.DataFrame | None:
    """Normalize whatever an analytic returned into a frame."""
    if rows is None:
        return None
    if isinstance(rows, pl.DataFrame):
        return rows
    if isinstance(rows, Mapping):
        return pl.DataFrame([dict(rows)])
    materialized = [dict(row) for row in rows]
    return pl.DataFrame(materialized) if materialized else None


def _with_session_columns(
    table: pl.DataFrame, frames: SessionFrames, name: str | None
) -> pl.DataFrame:
    """Prefix a result with the session it came from (and the analytic name,
    when comparing), without clobbering columns the analytic already set."""
    key = frames.session_key
    additions: dict[str, object] = {
        "session_year": key.year,
        "session_round": key.round,
        "session_type": key.session_type.value,
        "event_name": frames.event_name,
    }
    if name is not None:
        additions = {"analytic": name, **additions}
    missing = {column: value for column, value in additions.items() if column not in table.columns}
    if missing:
        table = table.with_columns(
            [pl.lit(value, allow_object=False).alias(column) for column, value in missing.items()]
        )
    ordered = [column for column in (*additions,) if column in table.columns]
    rest = [column for column in table.columns if column not in ordered]
    return table.select([*ordered, *rest])


def _concat(frames: Sequence[pl.DataFrame]) -> pl.DataFrame:
    """Stack per-session results, tolerating analytics whose columns differ
    between sessions (a missing column becomes null rather than an error)."""
    if not frames:
        return pl.DataFrame()
    if len(frames) == 1:
        return frames[0]
    return pl.concat(frames, how="diagonal_relaxed")
