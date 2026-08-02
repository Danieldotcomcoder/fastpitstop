"""The Python-side mirror of the SQL schema: per-table column order and
idempotency keys.

This is the single source of truth the sinks build rows against — the SQL
migrations in `migrations/` MUST stay in lock-step with it (a test asserts
every table and column named here appears in the migration). Keeping the
column ORDER here means the COPY column list and the staging INSERT always
agree, and `MemorySink` can enforce the exact same idempotency the DB does
without a database present.

Idempotency (the "re-ingesting a recording twice changes nothing"
guarantee): every derived row carries the `(source_seq, event_index)` of
the `DomainEvent` it came from. `event_index` is the event's position
within the list its originating `RawRecord` emitted — deterministic,
because the pipeline is a pure function of the record stream. That pair
uniquely identifies any event in a session, which is why it is the
idempotency key on the high-volume / fan-out tables.

NOTE on the deviation from the prompt's literal `(session_key, kind,
source_seq)`: one raw record fans out into MANY same-`kind` events (a
`CarData.z` batch is one telemetry sample per driver; a `TimingData` delta
is one `GapUpdated` per driver), so `(kind, source_seq)` is NOT unique and
would silently drop rows. `(source_seq, event_index)` is the smallest key
that is genuinely unique per event; `kind` is then redundant (a given
seq+index maps to exactly one event of one kind). `laps`/`stints` instead
use their natural domain keys — one row per (driver, lap) / (driver,
stint) — which Phase 2 joins on anyway.
"""

from __future__ import annotations

# Session identity, repeated as the leading columns of every table.
SESSION_COLUMNS: tuple[str, ...] = ("session_year", "session_round", "session_type")

# table -> ordered column list, exactly matching the COPY / INSERT column
# list and the CREATE TABLE column set in migrations/001_initial.sql.
TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "events": (
        *SESSION_COLUMNS,
        "ts_utc",
        "source_seq",
        "event_index",
        "kind",
        "payload",
    ),
    "laps": (
        *SESSION_COLUMNS,
        "driver",
        "lap_number",
        "ts_utc",
        "source_seq",
        "event_index",
        "lap_time_s",
        "sector1_s",
        "sector2_s",
        "sector3_s",
        "is_personal_best",
        "compound",
        "tyre_age",
        "provenance",
    ),
    "car_telemetry": (
        *SESSION_COLUMNS,
        "driver",
        "ts_utc",
        "source_seq",
        "event_index",
        "speed",
        "rpm",
        "gear",
        "throttle",
        "brake",
        "drs",
        "provenance",
    ),
    "car_positions": (
        *SESSION_COLUMNS,
        "driver",
        "ts_utc",
        "source_seq",
        "event_index",
        "x",
        "y",
        "z",
        "status",
        "provenance",
    ),
    "stints": (
        *SESSION_COLUMNS,
        "driver",
        "stint_number",
        "ts_utc",
        "source_seq",
        "event_index",
        "compound",
        "start_lap",
        "tyre_age_at_start",
        "provenance",
    ),
    "pit_stops": (
        *SESSION_COLUMNS,
        "driver",
        "event",
        "lap_number",
        "ts_utc",
        "source_seq",
        "event_index",
        "provenance",
    ),
    "race_control": (
        *SESSION_COLUMNS,
        "ts_utc",
        "source_seq",
        "event_index",
        "category",
        "message",
        "flag",
        "scope",
        "driver",
        "lap",
        "provenance",
    ),
    "weather": (
        *SESSION_COLUMNS,
        "ts_utc",
        "source_seq",
        "event_index",
        "air_temp",
        "track_temp",
        "humidity",
        "pressure",
        "wind_speed",
        "wind_dir",
        "rainfall",
        "provenance",
    ),
    "team_radio": (
        *SESSION_COLUMNS,
        "driver",
        "audio_url",
        "captured_at",
        "ts_utc",
        "source_seq",
        "event_index",
        "provenance",
    ),
}

# table -> the columns whose tuple must be unique (matches the SQL PRIMARY
# KEY / UNIQUE constraint). Used by MemorySink to replicate ON CONFLICT DO
# NOTHING; PostgresSink relies on the DB constraint of the same shape.
IDENTITY_KEYS: dict[str, tuple[str, ...]] = {
    "events": (*SESSION_COLUMNS, "source_seq", "event_index"),
    "laps": (*SESSION_COLUMNS, "driver", "lap_number"),
    "car_telemetry": (*SESSION_COLUMNS, "source_seq", "event_index"),
    "car_positions": (*SESSION_COLUMNS, "source_seq", "event_index"),
    "stints": (*SESSION_COLUMNS, "driver", "stint_number"),
    "pit_stops": (*SESSION_COLUMNS, "source_seq", "event_index"),
    "race_control": (*SESSION_COLUMNS, "source_seq", "event_index"),
    "weather": (*SESSION_COLUMNS, "source_seq", "event_index"),
    "team_radio": (*SESSION_COLUMNS, "source_seq", "event_index"),
}

# Materialized current-state tables, upserted from `SessionState` (a
# projection of the log, never the only copy) rather than appended to.
SESSIONS_COLUMNS: tuple[str, ...] = (
    *SESSION_COLUMNS,
    "status",
    "current_lap",
    "track_status",
)
DRIVERS_COLUMNS: tuple[str, ...] = (
    *SESSION_COLUMNS,
    "number",
    "code",
    "team",
    "position",
    "last_lap_s",
    "best_lap_s",
    "current_compound",
    "tyre_age",
    "gap_to_leader_s",
    "laps_down",
    "interval_s",
    "in_pit",
    "stint_number",
    "laps_completed",
)
