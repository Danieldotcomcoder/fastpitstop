"""Minimal stub for the fastf1 module-level API f1_backfill uses.

fastf1 ships no type information of its own (see
`f1_ingest`'s `stubs/fastf1/livetiming/client.pyi` docstring for the
project's general approach to this). `get_session`, `get_event_schedule`,
and `Cache.enable_cache` deliberately return `Any` here rather than
modeling FastF1's full `Session`/`EventSchedule` surface: f1_backfill
treats those objects as untyped at this single boundary and immediately
normalizes/casts them into its own typed shapes
(`f1_backfill.fastf1_source.SessionLike`, plain `pandas.DataFrame`) — see
that module's docstring.
"""

from contextlib import AbstractContextManager
from typing import Any, Literal

__version__: str

class Cache:
    @staticmethod
    def enable_cache(
        cache_dir: str,
        ignore_version: bool = ...,
        force_renew: bool = ...,
        use_requests_cache: bool = ...,
    ) -> None: ...
    @classmethod
    def disabled(cls) -> AbstractContextManager[None]: ...

def get_session(
    year: int,
    gp: str | int,
    identifier: int | str | None = ...,
    *,
    backend: Literal["fastf1", "f1timing", "ergast"] | None = ...,
    force_ergast: bool = ...,
) -> Any: ...
def get_event_schedule(
    year: int,
    *,
    include_testing: bool = ...,
    backend: Literal["fastf1", "f1timing", "ergast"] | None = ...,
    force_ergast: bool = ...,
) -> Any: ...
