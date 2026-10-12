"""Circuit pages (ROADMAP S2): the races held at a circuit and how each was
run, from the backfilled results and stints. Strategy only, never a result:
how many stops the finishers made and their tyre plans, nothing about who
won (the circuit page must not spoil a race the viewer hasn't watched)."""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from f1_api.config import Settings
from f1_api.sessions import SessionEntry, list_sessions
from f1_domain import SessionKey, SessionType
from f1_historical_adapter.reader import session_directory
from f1_kernel import Circuit, circuit_for_session

_FINISHED = ("Finished", "Lapped")


@dataclass(frozen=True, slots=True)
class RaceStrategy:
    finishers: int
    stops: dict[int, int]
    """Stops made -> how many finishers made that many."""
    plans: list[tuple[tuple[str, ...], int]]
    """Tyre plans (compounds in order) and how many finishers ran each,
    most common first."""


_strategy_cache: dict[tuple[str, SessionKey], RaceStrategy | None] = {}
_strategy_lock = threading.Lock()


def race_strategy(historical_root: Path, key: SessionKey) -> RaceStrategy | None:
    """How a backfilled race was run, or None when its tables are absent.
    A car that retired is left out: a crash on lap 2 is not a no-stop plan."""
    cache_key = (str(historical_root), key)
    with _strategy_lock:
        if cache_key in _strategy_cache:
            return _strategy_cache[cache_key]
    # The two tables directly, not through `load_session_tables`, which also
    # reads laps and race control to rebuild the session clock.
    directory = session_directory(historical_root, key)
    results, stints = (
        pd.read_parquet(path) if path.is_file() else None
        for path in (directory / "results.parquet", directory / "stints.parquet")
    )
    strategy = _strategy(results, stints)
    if results is not None and stints is not None:
        # Kept only once the tables exist: a race ingested live is archived
        # later, and a remembered "absent" would hide it until a restart.
        with _strategy_lock:
            _strategy_cache[cache_key] = strategy
    return strategy


def _strategy(results: pd.DataFrame | None, stints: pd.DataFrame | None) -> RaceStrategy | None:
    if results is None or stints is None or results.empty or stints.empty:
        return None
    status = results["Status"].astype(str)
    finished = results[status.isin(_FINISHED) | status.str.startswith("+")]
    codes = set(finished["Abbreviation"].astype(str))
    stops: Counter[int] = Counter()
    plans: Counter[tuple[str, ...]] = Counter()
    for code, group in stints.groupby("Driver"):
        if str(code) not in codes:
            continue
        ordered = group.sort_values("Stint")
        compounds = tuple(str(c).lower() for c in ordered["Compound"] if isinstance(c, str) and c)
        if not compounds:
            continue
        stops[len(compounds) - 1] += 1
        plans[compounds] += 1
    if not plans:
        return None
    return RaceStrategy(
        finishers=sum(plans.values()),
        stops=dict(sorted(stops.items())),
        plans=plans.most_common(),
    )


def entry_circuit(entry: SessionEntry) -> Circuit | None:
    """The circuit a listed session ran at: by the feed's venue name for a
    live-ingested session, by the schedule location for a backfilled one."""
    return circuit_for_session(entry.event_name, entry.circuit_name or entry.location)


def races_at(settings: Settings, circuit_id: str) -> list[SessionEntry]:
    """Every race any source holds at `circuit_id`, newest first."""
    races = [
        entry
        for entry in list_sessions(settings)
        if entry.session_key.session_type is SessionType.RACE
        and (circuit := entry_circuit(entry)) is not None
        and circuit.circuit_id == circuit_id
    ]
    return sorted(races, key=lambda e: (e.session_key.year, e.session_key.round), reverse=True)


__all__ = ["RaceStrategy", "entry_circuit", "race_strategy", "races_at"]
