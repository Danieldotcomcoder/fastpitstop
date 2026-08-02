"""Materialization + the database-free sinks: derive_rows, event_index
assignment, and MemorySink's idempotency — the same guarantee the DB gives,
proven without a database."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from store_data import EXPECTED_ROW_COUNTS, KEY

from f1_domain import DOMAIN_EVENT_ADAPTER, DomainEvent, LapCompleted, Provenance
from f1_store import MemorySink, NullSink, TableRow, assign_event_indices, derive_rows


def _tables(rows: list[TableRow]) -> set[str]:
    return {row.table for row in rows}


def test_every_event_writes_exactly_one_events_row(sample_events: list[DomainEvent]) -> None:
    for event, index in assign_event_indices(sample_events):
        rows = derive_rows(event, index)
        events_rows = [r for r in rows if r.table == "events"]
        assert len(events_rows) == 1
        assert events_rows[0].values["kind"] == event.kind


def test_lap_completed_writes_a_typed_lap_row() -> None:
    lap = LapCompleted(
        session_key=KEY,
        ts_utc=datetime(2024, 3, 2, 15, 0, tzinfo=UTC),
        source_seq=8,
        driver="44",
        lap_number=5,
        lap_time=None,
    )
    rows = derive_rows(lap, 0)
    assert _tables(rows) == {"events", "laps"}
    lap_row = next(r for r in rows if r.table == "laps")
    assert lap_row.values["driver"] == "44"
    assert lap_row.values["lap_number"] == 5
    assert lap_row.values["provenance"] == "live"


def test_events_row_payload_round_trips(sample_events: list[DomainEvent]) -> None:
    for event, index in assign_event_indices(sample_events):
        events_row = next(r for r in derive_rows(event, index) if r.table == "events")
        # Jsonb wraps the JSON-mode dump; validating it back reconstructs the
        # exact event (this is the log's source-of-truth property).
        payload = events_row.values["payload"].obj  # type: ignore[attr-defined]
        assert DOMAIN_EVENT_ADAPTER.validate_python(payload) == event


def test_event_index_resets_per_source_seq(sample_events: list[DomainEvent]) -> None:
    paired = assign_event_indices(sample_events)
    # seq 5 fans out to two telemetry samples -> indices 0 and 1.
    seq5 = [index for event, index in paired if event.source_seq == 5]
    assert seq5 == [0, 1]
    # every other seq here is a single event -> index 0.
    assert all(
        index == 0 for event, index in paired if event.source_seq != 5 and event.source_seq != 6
    )


def test_memory_sink_row_counts(sample_events: list[DomainEvent]) -> None:
    sink = MemorySink()
    sink.write_events(sample_events)
    assert sink.row_counts() == EXPECTED_ROW_COUNTS


def test_memory_sink_is_idempotent(sample_events: list[DomainEvent]) -> None:
    sink = MemorySink()
    sink.write_events(sample_events)
    once = sink.row_counts()
    sink.write_events(sample_events)  # re-ingest the identical stream
    assert sink.row_counts() == once


def test_null_sink_drains_a_generator_without_error(sample_events: list[DomainEvent]) -> None:
    sink = NullSink()
    drained = 0

    def generate() -> Iterator[DomainEvent]:
        nonlocal drained
        for event in sample_events:
            drained += 1
            yield event

    sink.write_events(generate())
    sink.flush()
    sink.close()
    # The point of NullSink is a no-op that still consumes the stream, so a
    # pipeline run only for its events behaves identically to a real sink.
    assert drained == len(sample_events)


def test_provenance_flows_into_rows() -> None:
    lap = LapCompleted(
        session_key=KEY,
        ts_utc=datetime(2024, 3, 2, 15, 0, tzinfo=UTC),
        source_seq=8,
        driver="44",
        lap_number=5,
        provenance=Provenance.HISTORICAL,
    )
    rows = derive_rows(lap, 0)
    assert next(r for r in rows if r.table == "laps").values["provenance"] == "historical"
