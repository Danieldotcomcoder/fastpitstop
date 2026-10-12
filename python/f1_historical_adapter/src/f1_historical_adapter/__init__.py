"""f1_historical_adapter: the Phase 1 seam from historical parquet to the
normalized domain-event stream.

`f1_backfill` produces clean FastF1/OpenF1 tables (a SEPARATE fixture track
from the raw recordings — see docs/DATA_CONTRACTS.md). This package maps
those tables onto the SAME `f1_domain.DomainEvent` stream the live pipeline
emits, so Phase 2's kernel runs one code path over both live and 2023-2025
history. Historical events are stamped `provenance=HISTORICAL` and carry a
synthetic `source_seq`; see `events_from_parquet` for the full list of what
history can and cannot provide.

`compare_session` / `compare_streams` are the comparison harness: run both
paths for one session and report field-level agreement (a report, not an
assertion — discrepancies between the two sources are expected);
`compare_event_streams` is the check run on every live session afterwards
(`f1_api.post_session`).
"""

from f1_historical_adapter.adapter import build_events, events_from_parquet
from f1_historical_adapter.compare import (
    Agreement,
    ComparisonReport,
    compare_event_streams,
    compare_session,
    compare_streams,
)
from f1_historical_adapter.reader import (
    SessionTables,
    load_session_tables,
    session_directory,
)

__all__ = [
    "Agreement",
    "ComparisonReport",
    "SessionTables",
    "build_events",
    "compare_event_streams",
    "compare_session",
    "compare_streams",
    "events_from_parquet",
    "load_session_tables",
    "session_directory",
]

__version__ = "0.0.1"
