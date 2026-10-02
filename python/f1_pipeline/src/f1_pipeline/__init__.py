"""f1_pipeline: the Phase 1 wiring — one normalization path, three fronts.

`run(records, sink, session_key)` is the single pipeline both LIVE and
REPLAY feed (CLAUDE.md's non-negotiable invariant: they share one path and
it never learns which is which). `run_events` is the historical front —
`f1_historical_adapter`'s already-normalized events straight to the sink.
`Pipeline` is the push form the live socket drives one record at a time;
`SessionRouter` (and `run_routed`, its pull form) splits a stream that spans
several sessions into one `Pipeline` each, by the stream's own SessionInfo.

The `f1-pipeline` CLI wires the real sources to a Postgres sink:
`f1-pipeline replay <recording>`, `f1-pipeline ingest-historical --season Y`,
`f1-pipeline live`. `f1_pipeline.verify` is the Phase 1 verification harness
(`just verify-phase1`).
"""

from f1_pipeline.metrics import MetricsCollector, RunMetrics
from f1_pipeline.pipeline import Pipeline, SessionRouter, run, run_events, run_routed
from f1_pipeline.replay_source import replay_records
from f1_pipeline.session import (
    discover_historical_sessions,
    session_key,
    session_type_from_name,
)

__all__ = [
    "MetricsCollector",
    "Pipeline",
    "RunMetrics",
    "SessionRouter",
    "discover_historical_sessions",
    "replay_records",
    "run",
    "run_events",
    "run_routed",
    "session_key",
    "session_type_from_name",
]

__version__ = "0.0.1"
