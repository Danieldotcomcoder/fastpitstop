-- f1_store schema, migration 001: the append-only DomainEvent log, the
-- typed derived tables Phase 2 queries, and the materialized current-state
-- tables. Applied by f1_store.migrations (tracked in schema_migrations).
--
-- Column sets and idempotency keys here MUST match f1_store/schema.py
-- (test_schema_matches_migration guards this). Session identity is the
-- (session_year, session_round, session_type) triple on every table, the
-- flattened f1_domain.SessionKey.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ---------------------------------------------------------------------------
-- events: the append-only log. The SOURCE OF TRUTH — every other table is
-- derivable from it (replay_events reads it back in order). Hypertable on
-- ts_utc; its unique constraint must include the partition column, so the
-- idempotency key is (session, source_seq, event_index, ts_utc).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    session_year   integer     NOT NULL,
    session_round  integer     NOT NULL,
    session_type   text        NOT NULL,
    ts_utc         timestamptz NOT NULL,
    source_seq     bigint      NOT NULL,
    event_index    integer     NOT NULL,
    kind           text        NOT NULL,
    payload        jsonb       NOT NULL,
    CONSTRAINT events_identity
        UNIQUE (session_year, session_round, session_type, source_seq, event_index, ts_utc)
);
SELECT create_hypertable('events', 'ts_utc', if_not_exists => TRUE, migrate_data => TRUE);
-- Phase 2 access patterns: replay/scan by (session, ts) and filter by kind.
CREATE INDEX IF NOT EXISTS events_session_ts_idx
    ON events (session_year, session_round, session_type, ts_utc);
CREATE INDEX IF NOT EXISTS events_session_kind_ts_idx
    ON events (session_year, session_round, session_type, kind, ts_utc);
CREATE INDEX IF NOT EXISTS events_session_seq_idx
    ON events (session_year, session_round, session_type, source_seq, event_index);

-- ---------------------------------------------------------------------------
-- laps: one row per completed lap, fully typed (NOT jsonb) — Phase 2 hammers
-- this. Natural key (session, driver, lap_number). Times are seconds.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS laps (
    session_year     integer NOT NULL,
    session_round    integer NOT NULL,
    session_type     text    NOT NULL,
    driver           text    NOT NULL,
    lap_number       integer NOT NULL,
    ts_utc           timestamptz,
    source_seq       bigint,
    event_index      integer,
    lap_time_s       double precision,
    sector1_s        double precision,
    sector2_s        double precision,
    sector3_s        double precision,
    is_personal_best boolean,
    compound         text,
    tyre_age         integer,
    provenance       text,
    CONSTRAINT laps_pk
        PRIMARY KEY (session_year, session_round, session_type, driver, lap_number)
);
CREATE INDEX IF NOT EXISTS laps_session_ts_idx
    ON laps (session_year, session_round, session_type, ts_utc);

-- ---------------------------------------------------------------------------
-- car_telemetry: the high-volume one (millions of rows/race). Hypertable +
-- compression on chunks older than a day.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS car_telemetry (
    session_year  integer     NOT NULL,
    session_round integer     NOT NULL,
    session_type  text        NOT NULL,
    driver        text        NOT NULL,
    ts_utc        timestamptz NOT NULL,
    source_seq    bigint      NOT NULL,
    event_index   integer     NOT NULL,
    speed         double precision,
    rpm           integer,
    gear          integer,
    throttle      double precision,
    brake         double precision,
    drs           integer,
    provenance    text,
    CONSTRAINT car_telemetry_identity
        UNIQUE (session_year, session_round, session_type, source_seq, event_index, ts_utc)
);
SELECT create_hypertable('car_telemetry', 'ts_utc', if_not_exists => TRUE, migrate_data => TRUE);
CREATE INDEX IF NOT EXISTS car_telemetry_session_driver_ts_idx
    ON car_telemetry (session_year, session_round, session_type, driver, ts_utc);
ALTER TABLE car_telemetry SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'session_year, session_round, session_type, driver',
    timescaledb.compress_orderby = 'ts_utc'
);
SELECT add_compression_policy('car_telemetry', INTERVAL '1 day', if_not_exists => TRUE);

-- ---------------------------------------------------------------------------
-- car_positions: the other high-volume channel, same treatment.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS car_positions (
    session_year  integer     NOT NULL,
    session_round integer     NOT NULL,
    session_type  text        NOT NULL,
    driver        text        NOT NULL,
    ts_utc        timestamptz NOT NULL,
    source_seq    bigint      NOT NULL,
    event_index   integer     NOT NULL,
    x             double precision,
    y             double precision,
    z             double precision,
    status        text,
    provenance    text,
    CONSTRAINT car_positions_identity
        UNIQUE (session_year, session_round, session_type, source_seq, event_index, ts_utc)
);
SELECT create_hypertable('car_positions', 'ts_utc', if_not_exists => TRUE, migrate_data => TRUE);
CREATE INDEX IF NOT EXISTS car_positions_session_driver_ts_idx
    ON car_positions (session_year, session_round, session_type, driver, ts_utc);
ALTER TABLE car_positions SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'session_year, session_round, session_type, driver',
    timescaledb.compress_orderby = 'ts_utc'
);
SELECT add_compression_policy('car_positions', INTERVAL '1 day', if_not_exists => TRUE);

-- ---------------------------------------------------------------------------
-- stints: one row per (driver, stint).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stints (
    session_year      integer NOT NULL,
    session_round     integer NOT NULL,
    session_type      text    NOT NULL,
    driver            text    NOT NULL,
    stint_number      integer NOT NULL,
    ts_utc            timestamptz,
    source_seq        bigint,
    event_index       integer,
    compound          text,
    start_lap         integer,
    tyre_age_at_start integer,
    provenance        text,
    CONSTRAINT stints_pk
        PRIMARY KEY (session_year, session_round, session_type, driver, stint_number)
);

-- ---------------------------------------------------------------------------
-- pit_stops: one row per pit entry/exit event (`event` = 'entry'|'exit').
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pit_stops (
    session_year  integer     NOT NULL,
    session_round integer     NOT NULL,
    session_type  text        NOT NULL,
    driver        text        NOT NULL,
    event         text        NOT NULL,
    lap_number    integer,
    ts_utc        timestamptz,
    source_seq    bigint      NOT NULL,
    event_index   integer     NOT NULL,
    provenance    text,
    CONSTRAINT pit_stops_identity
        UNIQUE (session_year, session_round, session_type, source_seq, event_index)
);
CREATE INDEX IF NOT EXISTS pit_stops_session_driver_idx
    ON pit_stops (session_year, session_round, session_type, driver, lap_number);

-- ---------------------------------------------------------------------------
-- race_control: flags, safety car, session-status messages.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS race_control (
    session_year  integer     NOT NULL,
    session_round integer     NOT NULL,
    session_type  text        NOT NULL,
    ts_utc        timestamptz NOT NULL,
    source_seq    bigint      NOT NULL,
    event_index   integer     NOT NULL,
    category      text,
    message       text,
    flag          text,
    scope         text,
    driver        text,
    lap           integer,
    provenance    text,
    CONSTRAINT race_control_identity
        UNIQUE (session_year, session_round, session_type, source_seq, event_index)
);
CREATE INDEX IF NOT EXISTS race_control_session_ts_idx
    ON race_control (session_year, session_round, session_type, ts_utc);

-- ---------------------------------------------------------------------------
-- weather: one row per weather sample (low-volume; plain table).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weather (
    session_year  integer     NOT NULL,
    session_round integer     NOT NULL,
    session_type  text        NOT NULL,
    ts_utc        timestamptz NOT NULL,
    source_seq    bigint      NOT NULL,
    event_index   integer     NOT NULL,
    air_temp      double precision,
    track_temp    double precision,
    humidity      double precision,
    pressure      double precision,
    wind_speed    double precision,
    wind_dir      double precision,
    rainfall      boolean,
    provenance    text,
    CONSTRAINT weather_identity
        UNIQUE (session_year, session_round, session_type, source_seq, event_index)
);
CREATE INDEX IF NOT EXISTS weather_session_ts_idx
    ON weather (session_year, session_round, session_type, ts_utc);

-- ---------------------------------------------------------------------------
-- team_radio: one row per radio clip (the URL; audio itself is Phase 4).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS team_radio (
    session_year  integer     NOT NULL,
    session_round integer     NOT NULL,
    session_type  text        NOT NULL,
    driver        text,
    audio_url     text,
    captured_at   timestamptz,
    ts_utc        timestamptz NOT NULL,
    source_seq    bigint      NOT NULL,
    event_index   integer     NOT NULL,
    provenance    text,
    CONSTRAINT team_radio_identity
        UNIQUE (session_year, session_round, session_type, source_seq, event_index)
);
CREATE INDEX IF NOT EXISTS team_radio_session_ts_idx
    ON team_radio (session_year, session_round, session_type, ts_utc);

-- ---------------------------------------------------------------------------
-- Materialized current-state (a projection of the log, upserted from
-- SessionState — never the only copy of anything).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    session_year  integer NOT NULL,
    session_round integer NOT NULL,
    session_type  text    NOT NULL,
    status        text,
    current_lap   integer,
    track_status  text,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT sessions_pk PRIMARY KEY (session_year, session_round, session_type)
);

CREATE TABLE IF NOT EXISTS drivers (
    session_year     integer NOT NULL,
    session_round    integer NOT NULL,
    session_type     text    NOT NULL,
    number           text    NOT NULL,
    code             text,
    team             text,
    position         integer,
    last_lap_s       double precision,
    best_lap_s       double precision,
    current_compound text,
    tyre_age         integer,
    gap_to_leader_s  double precision,
    laps_down        integer,
    interval_s       double precision,
    in_pit           boolean,
    stint_number     integer,
    laps_completed   integer,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT drivers_pk PRIMARY KEY (session_year, session_round, session_type, number)
);
