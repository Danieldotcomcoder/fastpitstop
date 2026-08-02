"""The historical front (`run_events`): already-normalized events straight
to the sink, no state machine, no materialized state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from f1_domain import DomainEvent, LapCompleted, Provenance, SessionKey, SessionType
from f1_pipeline import run_events
from f1_store import MemorySink

_KEY = SessionKey(year=2023, round=5, session_type=SessionType.RACE)
_TS = datetime(2023, 5, 7, 14, 0, 0, tzinfo=UTC)


def _events(count: int) -> list[DomainEvent]:
    # Historical events: dense synthetic source_seq, provenance=HISTORICAL.
    return [
        LapCompleted(
            session_key=_KEY,
            ts_utc=_TS + timedelta(seconds=seq),
            source_seq=seq,
            provenance=Provenance.HISTORICAL,
            driver="1",
            lap_number=seq + 1,
            lap_time=timedelta(seconds=90 + seq),
        )
        for seq in range(count)
    ]


def test_run_events_writes_log_and_typed_rows() -> None:
    events = _events(5)
    sink = MemorySink()
    metrics = run_events(events, sink, chunk_size=2)  # chunk < count to exercise chunking

    assert metrics.events_out == 5
    assert metrics.records_in == 0  # historical has no raw records
    assert metrics.events_by_kind == {"lap_completed": 5}
    assert sink.row_counts() == {"events": 5, "laps": 5}
    # No materialized SessionState for historical (no state machine).
    assert sink.state is None


def test_run_events_is_idempotent() -> None:
    events = _events(4)
    sink = MemorySink()
    run_events(events, sink)
    before = sink.row_counts()
    run_events(events, sink)  # re-ingest the same events
    assert sink.row_counts() == before


def test_run_events_metrics_rows_written() -> None:
    sink = MemorySink()
    metrics = run_events(_events(3), sink)
    assert metrics.rows_written == sink.written_rows
    assert metrics.rows_written["events"] == 3
    assert metrics.rows_written["laps"] == 3
