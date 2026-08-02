"""Phase 1 verification harness (`just verify-phase1`).

Replays a checked-in recording end-to-end through the REAL pipeline into a
throwaway store, then asserts against a known-good snapshot: event counts by
kind, materialized row counts, and — the ones that actually matter — final
classification, lap counts, and pit stops per driver against the recording's
ground truth. Also proves determinism (same recording twice -> identical
event stream) and idempotency (re-ingest -> unchanged row counts).

The default store is `MemorySink`, which enforces the exact same identity/
idempotency rules the DB does, so this runs green in CI with no database. If
`F1_STORE_RUN_DB_TESTS=1` (and a reachable DSN) is set, it ALSO replays into
a throwaway TimescaleDB and checks the persisted row counts match — the
literal "into a throwaway DB" check when a DB is available.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, cast

from f1_domain import SessionKey, SessionType
from f1_pipeline.pipeline import run
from f1_pipeline.replay_source import replay_records
from f1_recording import RawRecord
from f1_store import MemorySink

FIXTURE_DIR = Path(__file__).parent / "fixtures"
RECORDING_DIR = FIXTURE_DIR / "replay_race"
EXPECTED_PATH = FIXTURE_DIR / "expected.json"

# The fixture is a synthetic 2024 round-1 race (see fixtures/generate.py).
SESSION_KEY = SessionKey(year=2024, round=1, session_type=SessionType.RACE)


class VerificationError(AssertionError):
    """A Phase 1 exit criterion did not hold."""


def load_expected() -> dict[str, object]:
    return json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))


def _laps_per_driver(sink: MemorySink) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in sink.rows["laps"]:
        counts[str(row["driver"])] += 1
    return dict(counts)


def _pit_laps_per_driver(sink: MemorySink) -> dict[str, list[int]]:
    pits: dict[str, list[int]] = {}
    for row in sink.rows["pit_stops"]:
        if row.get("event") != "entry":
            continue
        driver = str(row["driver"])
        lap = row.get("lap_number")
        if isinstance(lap, int):
            pits.setdefault(driver, []).append(lap)
    return {driver: sorted(laps) for driver, laps in pits.items()}


def _final_positions(sink: MemorySink) -> dict[str, int]:
    if sink.state is None:
        return {}
    return {
        number: driver.position
        for number, driver in sink.state.drivers.items()
        if driver.position is not None
    }


def verify(*, recording_dir: Path = RECORDING_DIR) -> list[str]:
    """Run every Phase 1 exit-criterion check. Returns human-readable lines
    on success; raises `VerificationError` on the first failure."""
    expected = load_expected()
    failures: list[str] = []

    def check(name: str, actual: object, want: object) -> None:
        if actual != want:
            failures.append(f"{name}:\n    expected {want!r}\n    got      {actual!r}")

    # Materialize the record stream once so we can replay it repeatedly.
    records = list(replay_records(recording_dir, speed=0))

    # 1. Full race replays with zero crashes.
    primary = MemorySink()
    metrics = run(records, primary, session_key=SESSION_KEY)

    # 2/3. Event counts, row counts, classification, laps, pit stops.
    check("events_by_kind", metrics.events_by_kind, expected["events_by_kind"])
    check("row_counts", primary.row_counts(), expected["row_counts"])
    check("final_positions", _final_positions(primary), _as_int_map(expected["final_positions"]))
    check("laps_by_driver", _laps_per_driver(primary), _as_int_map(expected["laps_by_driver"]))
    check("pit_laps", _pit_laps_per_driver(primary), _as_list_map(expected["pit_laps"]))

    # 4. Determinism: a second run over the same records is byte-identical.
    second = MemorySink()
    run(records, second, session_key=SESSION_KEY)
    if _event_json(primary) != _event_json(second):
        failures.append("determinism: second replay produced a different event stream")

    # 5. Idempotency: re-ingesting into the same store changes no row counts.
    before = primary.row_counts()
    run(records, primary, session_key=SESSION_KEY)
    if primary.row_counts() != before:
        failures.append(
            f"idempotency: row counts changed on re-ingest ({before} -> {primary.row_counts()})"
        )

    if failures:
        raise VerificationError("Phase 1 verification failed:\n" + "\n".join(failures))

    lines = [
        f"PHASE 1 VERIFY: OK - {metrics.records_in} records -> {metrics.events_out} events "
        f"in {metrics.wall_seconds:.3f}s",
        f"  event kinds: {len(metrics.events_by_kind)}, "
        f"rows: {sum(primary.row_counts().values())}, "
        f"drivers classified: {len(_final_positions(primary))}",
        "  determinism OK, idempotency OK",
    ]
    if os.environ.get("F1_STORE_RUN_DB_TESTS") == "1":
        lines.append(_verify_against_postgres(records, expected))
    return lines


def _event_json(sink: MemorySink) -> list[str]:
    return [event.model_dump_json() for event in sink.events]


def _as_int_map(value: object) -> dict[str, int]:
    assert isinstance(value, dict)
    typed = cast("dict[str, Any]", value)
    return {str(k): int(v) for k, v in typed.items()}


def _as_list_map(value: object) -> dict[str, list[int]]:
    assert isinstance(value, dict)
    typed = cast("dict[str, Any]", value)
    return {str(k): [int(x) for x in v] for k, v in typed.items()}


def _verify_against_postgres(records: list[RawRecord], expected: dict[str, object]) -> str:
    """Optional literal "into a throwaway DB" pass, when a DB is configured."""
    import uuid

    import psycopg
    from psycopg import sql

    from f1_store import PostgresSink, apply_migrations
    from f1_store.config import DEFAULT_LOCAL_DSN

    want = _as_int_map(expected["row_counts"])
    admin_dsn = os.environ.get("F1_STORE_TEST_DSN", DEFAULT_LOCAL_DSN)
    name = f"pitwall_verify_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    dsn = admin_dsn.rsplit("/", 1)[0] + f"/{name}"
    try:
        counts: dict[str, int] = {}
        with psycopg.connect(dsn) as conn:
            apply_migrations(conn)
            with PostgresSink(conn) as sink:
                run(records, sink, session_key=SESSION_KEY)
            for table in want:
                with conn.cursor() as cur:
                    cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
                    row = cur.fetchone()
                    counts[table] = int(row[0]) if row else 0
        if counts != want:
            raise VerificationError(f"postgres row counts {counts} != expected {want}")
        return "  postgres throwaway-DB row counts OK"
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (name,)
            )
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))


def main() -> int:
    for line in verify():
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
