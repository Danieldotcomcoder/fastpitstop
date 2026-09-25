"""FastF1 extraction: laps, car telemetry, car positions, weather, results,
stints/compounds.

`load_session` is the single boundary with the real `fastf1` library — it
returns a `SessionLike`, a small Protocol declaring only the attributes
`extract_tables` actually reads. FastF1's own `Session` class sets these
dynamically inside `.load()` with no class-level type annotations, so
without this boundary every attribute access on it would infer as
`Unknown` under pyright's strict mode; `SessionLike` gives the rest of this
module (and its tests, which can pass a fake session) a fully-typed
surface instead. Tests inject a fake `session_loader` rather than mocking
network calls here — see the module docstring in `pipeline.py` for why
FastF1 itself (unlike OpenF1) isn't mocked at the HTTP layer.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, cast

import pandas as pd

from f1_backfill.positions import POSITIONS_TABLE, position_table

# FastF1's HTTP layer has no default timeout; a hung F1 CDN request can
# block the whole backfill indefinitely. Apply a per-request deadline.
_FASTF1_REQUEST_TIMEOUT_S = 120.0


class SessionLike(Protocol):
    laps: pd.DataFrame
    car_data: Mapping[str, pd.DataFrame]
    pos_data: Mapping[str, pd.DataFrame]
    weather_data: pd.DataFrame
    results: pd.DataFrame

    def load(
        self, *, laps: bool = ..., telemetry: bool = ..., weather: bool = ..., messages: bool = ...
    ) -> None: ...


def configure_cache(cache_dir: Path) -> None:
    """Enable FastF1's on-disk cache (required before any `load_session`
    call; FastF1 raises if you skip this).
    """
    import fastf1

    cache_dir.mkdir(parents=True, exist_ok=True)
    fastf1.Cache.enable_cache(str(cache_dir))
    _install_fastf1_request_timeout(_FASTF1_REQUEST_TIMEOUT_S)


def _install_fastf1_request_timeout(timeout: float) -> None:
    """Ensure FastF1 GETs carry a timeout (idempotent if called twice)."""
    from fastf1.req import Cache

    cache_cls: Any = Cache
    if getattr(cache_cls, "_f1_backfill_timeout", None) == timeout:
        return

    original = cache_cls.requests_get.__func__

    @classmethod
    def requests_get(cls: type[Any], url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", timeout)
        return original(cls, url, **kwargs)

    cache_cls.requests_get = requests_get
    cache_cls._f1_backfill_timeout = timeout


def load_session(season: int, round_no: int, session_type: str) -> SessionLike:
    import fastf1

    session = fastf1.get_session(season, round_no, session_type)
    # messages=True so FastF1 can compute deleted-lap flags for quali/sprint
    # results (messages=False triggers a TypeError inside FastF1 that it
    # logs as a scary traceback, then continues with weaker results).
    session.load(laps=True, telemetry=True, weather=True, messages=True)
    return cast(SessionLike, session)


def extract_tables(session: SessionLike) -> dict[str, pd.DataFrame]:
    """Pull the CLEAN historical tables this project keeps out of a loaded
    FastF1 session: laps, car telemetry (all drivers concatenated), measured
    car positions (`f1_backfill.positions`), weather, session results, and a
    derived stints/tyre-compound summary.

    Every table is normalized to a plain `pandas.DataFrame` (dropping
    FastF1's `Laps`/`Telemetry`/`SessionResults` subclasses) since nothing
    downstream should depend on FastF1-specific accessor behavior — this is
    a CLEAN TABLE fixture track, not a pass-through of FastF1's objects.
    """
    laps = pd.DataFrame(session.laps).reset_index(drop=True)
    return {
        "laps": laps,
        "car_telemetry": _concat_car_data(session.car_data),
        POSITIONS_TABLE: position_table(session.pos_data),
        "weather": pd.DataFrame(session.weather_data).reset_index(drop=True),
        "results": pd.DataFrame(session.results).reset_index(drop=True),
        "stints": _derive_stints(laps),
    }


def _concat_car_data(car_data: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for driver_number, telemetry in car_data.items():
        frame = pd.DataFrame(telemetry).reset_index(drop=True)
        frame["DriverNumber"] = driver_number
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


_STINT_GROUP_COLUMNS = ["Driver", "Stint"]
_STINT_REQUIRED_COLUMNS = {"Driver", "Stint", "Compound", "LapNumber"}


def _derive_stints(laps: pd.DataFrame) -> pd.DataFrame:
    """One row per (driver, stint): compound, first/last lap, lap count.

    FastF1's `laps` table already carries `Stint`/`Compound`/`TyreLife` per
    lap; this is just a convenience rollup so consumers don't have to
    re-derive it from `laps` themselves every time.
    """
    if laps.empty or not _STINT_REQUIRED_COLUMNS.issubset(laps.columns):
        return pd.DataFrame(
            columns=["Driver", "Stint", "Compound", "LapStart", "LapEnd", "LapCount"]
        )

    records: list[dict[str, object]] = []
    for (driver, stint), group in laps.groupby(_STINT_GROUP_COLUMNS, dropna=False):
        records.append(
            {
                "Driver": driver,
                "Stint": stint,
                "Compound": group["Compound"].iloc[0],
                "LapStart": group["LapNumber"].min(),
                "LapEnd": group["LapNumber"].max(),
                "LapCount": len(group),
            }
        )
    return pd.DataFrame(records)
