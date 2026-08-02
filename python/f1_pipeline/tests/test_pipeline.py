"""The one pipeline: metrics, determinism, idempotency, and the sink-
agnostic wiring — driven by the checked-in verification recording."""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import JsonValue

from f1_domain import DomainEvent
from f1_pipeline import Pipeline, run
from f1_pipeline.replay_source import replay_records
from f1_pipeline.verify import RECORDING_DIR, SESSION_KEY
from f1_recording import RawRecord
from f1_store import MemorySink, NullSink


def _rec(topic: str, payload: JsonValue, seq: int) -> RawRecord:
    return RawRecord(
        seq=seq, ts_wall=1.0e9 + seq, ts_mono=seq * 1_000_000, topic=topic, payload=payload
    )


def test_unknown_topics_are_counted_for_early_warning() -> None:
    sink = MemorySink()
    pipeline = Pipeline(SESSION_KEY, sink)
    pipeline.process(_rec("Heartbeat", {"beat": 1}, 1))
    pipeline.process(_rec("Heartbeat", {"beat": 2}, 2))
    metrics = pipeline.finalize()

    assert metrics.records_in == 2
    assert metrics.events_by_kind.get("unknown") == 2
    assert metrics.unknown_topics_total == 2
    assert metrics.unknown_topics_by_topic == {"Heartbeat": 2}


def test_replay_is_deterministic_and_idempotent() -> None:
    records = list(replay_records(RECORDING_DIR, speed=0))

    first = MemorySink()
    run(records, first, session_key=SESSION_KEY)
    second = MemorySink()
    run(records, second, session_key=SESSION_KEY)
    # Byte-identical event streams — the determinism contract.
    assert [e.model_dump_json() for e in first.events] == [
        e.model_dump_json() for e in second.events
    ]

    before = first.row_counts()
    run(records, first, session_key=SESSION_KEY)  # re-ingest into the same store
    assert first.row_counts() == before


def test_metrics_rows_written_match_the_sink() -> None:
    records = list(replay_records(RECORDING_DIR, speed=0))
    sink = MemorySink()
    metrics = run(records, sink, session_key=SESSION_KEY)
    assert metrics.rows_written == sink.written_rows
    # every event writes exactly one events-log row.
    assert metrics.rows_written["events"] == metrics.events_out
    assert metrics.records_in > metrics.events_out // 2  # sanity: real record count


def test_null_sink_still_produces_metrics_but_no_rows() -> None:
    metrics = run([_rec("Heartbeat", {"beat": 1}, 1)], NullSink(), session_key=SESSION_KEY)
    assert metrics.events_out == 1
    assert metrics.rows_written == {}


def test_wall_seconds_is_measured_around_the_run() -> None:
    ticks: Iterator[float] = iter([10.0, 12.5])
    events: list[DomainEvent] = []
    metrics = run([], MemorySink(), session_key=SESSION_KEY, clock=lambda: next(ticks))
    assert metrics.wall_seconds == 2.5
    assert not events  # nothing to process; just the timing contract
