"""pandas-scalar -> python/domain-scalar coercion.

FastF1/OpenF1 frames are full of `NaT`, `NaN`, numpy scalars, and pandas
`Timedelta`/`Timestamp`. The domain models want plain `int`/`float`/`str`/
`timedelta`/`datetime` or `None`. These helpers do that conversion in one
place so `None`-vs-`NaN` handling is consistent everywhere.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from f1_domain import Compound

_COMPOUNDS: dict[str, Compound] = {
    "SOFT": Compound.SOFT,
    "MEDIUM": Compound.MEDIUM,
    "HARD": Compound.HARD,
    "INTERMEDIATE": Compound.INTERMEDIATE,
    "WET": Compound.WET,
}


def opt_float(value: object) -> float | None:
    if value is None or pd.isna(value):  # type: ignore[arg-type]
        return None
    return float(value)  # type: ignore[arg-type]


def opt_int(value: object) -> int | None:
    if value is None or pd.isna(value):  # type: ignore[arg-type]
        return None
    return int(value)  # type: ignore[arg-type]


def opt_str(value: object) -> str | None:
    if value is None or (not isinstance(value, str) and pd.isna(value)):  # type: ignore[arg-type]
        return None
    text = str(value).strip()
    return text or None


def opt_bool(value: object) -> bool | None:
    if value is None or pd.isna(value):  # type: ignore[arg-type]
        return None
    return bool(value)


def opt_timedelta(value: object) -> timedelta | None:
    """A pandas `Timedelta`/`NaT` -> python `timedelta`/`None`."""
    if value is None or pd.isna(value):  # type: ignore[arg-type]
        return None
    return pd.Timedelta(value).to_pytimedelta()  # type: ignore[arg-type]


def to_utc(value: object) -> datetime | None:
    """A pandas `Timestamp`/ISO string/`NaT` -> tz-aware UTC `datetime`/None.
    Tz-naive inputs are assumed to already be UTC (FastF1's convention)."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):  # type: ignore[arg-type]
        return None
    ts = pd.Timestamp(value)  # type: ignore[arg-type]
    if pd.isna(ts):
        return None
    ts = ts.tz_localize(UTC) if ts.tzinfo is None else ts.tz_convert(UTC)
    return ts.to_pydatetime()


def driver_number(value: object) -> str | None:
    """Racing number as the domain's string key. OpenF1 sends it as an int
    or float (`22`, `22.0`); normalize to `"22"`."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):  # type: ignore[arg-type]
        return None
    if isinstance(value, str):
        return value.strip() or None
    return str(int(value))  # type: ignore[arg-type]


def compound(value: object) -> Compound | None:
    text = opt_str(value)
    if text is None:
        return None
    return _COMPOUNDS.get(text.upper(), Compound.UNKNOWN)
