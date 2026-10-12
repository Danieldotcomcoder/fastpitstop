# Data contracts

## Recording format (`f1_recording`)

The recording format is the CONTRACT between the recorder (writes) and the
replayer (reads) — see the "Hard architectural invariants" section of
[`CLAUDE.md`](../claude.md). Both depend on the `f1_recording` package;
nothing else should invent its own format. It stores **raw messages only** —
no F1 domain concepts (laps, stints, gaps). Parsing raw → domain events is a
Phase 1 concern, not this package's.

### Layout

A recording is a directory (one per session):

```
<recording-dir>/
├── manifest.json    # session metadata, written once at creation
├── subscribe.json   # the full-state snapshot captured at connection time
└── stream.jsonl      # append-only log, one JSON object per line, receive order
```

### `manifest.json`

A single JSON object, `f1_recording.Manifest`:

| field               | type       | notes                                                     |
| ------------------- | ---------- | ---------------------------------------------------------- |
| `format_version`    | int        | Currently `1`. Bump when the on-disk shape changes.        |
| `year`               | int        | Season year.                                                |
| `grand_prix`         | str        | e.g. `"Monaco Grand Prix"`.                                 |
| `session_type`       | str        | e.g. `"Race"`, `"Qualifying"`, `"FP1"`.                      |
| `session_start_utc`  | datetime   | ISO 8601, UTC.                                               |
| `recorder_version`   | str        | Version string of the recorder that produced this recording.|
| `topics`             | list[str]  | Topics subscribed to for this recording.                     |

### `subscribe.json`

The raw full-state message the feed sends on subscribe (before any
incremental updates). Stored verbatim as whatever JSON value the feed sent —
this package makes no assumptions about its shape.

### `stream.jsonl`

One `f1_recording.RawRecord` per line, in receive order:

| field      | type              | notes                                                                    |
| ---------- | ----------------- | ------------------------------------------------------------------------- |
| `seq`      | int                | Monotonic counter starting at 0, one per recording.                       |
| `ts_wall`  | float              | Unix epoch seconds at receive time. For human-readable alignment only — wall clock can jump (NTP, DST, etc). |
| `ts_mono`  | int                | Monotonic nanoseconds at receive time. Use this for precise inter-message timing on replay. |
| `topic`    | str                | e.g. `"TimingData"`, `"CarData.z"`, `"Position.z"`, `"TeamRadio"`, `"RaceControlMessages"`, `"WeatherData"`, `"TrackStatus"`, ... |
| `payload`  | JSON value or str  | The raw message value exactly as received. For compressed `.z` topics this is the base64 string, **not** decoded — the writer never decodes on write. |

### Compressed (`.z`) topics

Some topics (`CarData.z`, `Position.z`) arrive base64-encoded and
raw-deflate-compressed (no zlib header) to save bandwidth. `f1_recording`
never decodes these when writing — `payload` stays the raw base64 string.
`f1_recording.decode_z(payload)` is a **read-side only** convenience that
base64-decodes then `zlib.decompress(..., -zlib.MAX_WBITS)`-inflates the
result back into a JSON value.

### Crash resilience

`RecordingWriter.append()` fsyncs by default after every record (pass
`sync=False` to batch, then call `flush()` yourself). `RecordingReader`
tolerates a truncated/partial final line in `stream.jsonl` — the on-disk
trace of a crash mid-write — by silently dropping it; any malformed line
before the last one is treated as genuine corruption and raises.

## Live recorder sentinel topics (`f1_ingest`)

`f1_ingest`'s `RecordingSession` (the live recorder, Phase 0.3) appends two
synthetic records to `stream.jsonl` on every reconnect — sessions can span
reconnects, and these let the replayer (and a human) see exactly where and
how many times that happened. Both are prefixed distinctly from real F1
topics (always PascalCase, never leading `_`) so they're unmistakably
synthetic when reading a recording back:

| topic                        | payload                    | meaning                                                        |
| ----------------------------- | --------------------------- | ---------------------------------------------------------------- |
| `_meta.reconnect`              | `{"attempt": <int>}`         | Marks a reconnect; `attempt` counts up from 1.                    |
| `_meta.subscribe_snapshot`     | the fresh full-state dict    | The new subscribe completion result received on that reconnect.  |

The *original* `subscribe.json` on disk is never overwritten by a
reconnect — it always stays the state at the very first connection.

## Replay semantics (`f1_replay`)

`f1_replay.replay()` reads a recording via `RecordingReader` and re-emits it
through a `Callable[[RawRecord], None]` sink, in seq order, so a downstream
consumer cannot tell it apart from a live `f1_ingest` connection (modulo
speed) — see that package's module docstring for the full timing model.
Two things worth knowing if you consume it:

- The subscribe snapshot (`subscribe.json`) is always sent to the sink
  *first*, wrapped in a synthetic `RawRecord` with topic
  `_meta.subscribe_snapshot` (the same sentinel-naming convention as
  `f1_ingest`'s *resubscribe* snapshot on reconnect — see above). Its `seq`
  is a placeholder (`0`); rely on emission order, not `seq` comparison,
  to know it came first.
- `--speed` scales the gaps between messages (`0` = as-fast-as-possible,
  no sleeping at all); `--delay` adds one constant offset to the whole
  stream rather than compounding per message — this is the Phase 3 "Delay
  Sync" primitive, seeded here so it has one implementation from Phase 0
  onward.

## Historical backfill tables (`f1_backfill`)

**Boundary**: this is a separate fixture track from the recording format
above. It does not produce raw SignalR recordings and does not feed
`f1_replay` — FastF1/OpenF1 hand back already-processed data, not wire
messages. It feeds the Phase 2 analytics kernel directly. The Phase 1
adapter mapping these tables (and raw recordings) onto the normalized
domain-event schema is *not built yet* — this section documents the seam,
not the adapter.

### Layout

Hive-style partitioned parquet under `data/historical/`, plus one
`manifest.json` catalog:

```
data/historical/
├── manifest.json
└── season=<YYYY>/round=<NN>/session=<slug>/
    ├── laps.parquet
    ├── car_telemetry.parquet
    ├── car_positions.parquet
    ├── weather.parquet
    ├── results.parquet
    ├── stints.parquet
    ├── intervals.parquet        # present only if OpenF1 had a matching session
    ├── race_control.parquet     # ditto
    └── team_radio.parquet       # ditto
```

`session` is the slugified FastF1 session name (`Practice_1`, `Qualifying`,
`Race`, `Sprint`, ...). A table file is omitted entirely (not written as an
empty schema-less file) when there was nothing to write for it — most
commonly `intervals`/`race_control`/`team_radio` when `f1_backfill.openf1`
couldn't match the session (see below).

### Tables

| table            | source  | grain                        | notes                                                              |
| ---------------- | ------- | ----------------------------- | ------------------------------------------------------------------- |
| `laps`            | FastF1  | one row per driver per lap     | includes `Stint`/`Compound`/`TyreLife` per FastF1's own `Laps` frame. |
| `car_telemetry`   | FastF1  | one row per telemetry sample   | all drivers concatenated, with a `DriverNumber` column added.        |
| `car_positions`   | FastF1 (F1 static archive `Position.z`) | one row per car per position sample (~4 Hz) | `DriverNumber`, `Date` (tz-naive UTC, the feed's own timestamp), `X`/`Y`/`Z` (int32, the feed's ~1/10 m track-local units, the same units every circuit outline uses), `Status`. NOT resampled. `(0, 0, 0)` garage placeholders dropped. See below. |
| `weather`         | FastF1  | one row per weather sample     | as FastF1's `weather_data`.                                          |
| `results`         | FastF1  | one row per driver             | session classification/results.                                      |
| `stints`          | FastF1 (derived) | one row per driver per stint | rolled up from `laps`: `Compound`, `LapStart`, `LapEnd`, `LapCount`.   |
| `intervals`       | OpenF1  | one row per interval update    | gap to leader / car ahead. `/v1/intervals`.                          |
| `race_control`    | OpenF1  | one row per race control event | flags, safety car, session status. `/v1/race_control`.               |
| `team_radio`      | OpenF1  | one row per radio clip         | `recording_url` only — audio itself is Phase 4's job. `/v1/team_radio`. |

`car_positions` is the one table that also exists for sessions the catalog
does NOT list. Since 2025 the live feed only sends `Position.z` to an
F1TV-authenticated client, so a live-ingested session (served from the
store) has no positions; F1 publishes them in its static archive after the
session. `f1-backfill --season Y --round R --session Race --positions-only
--uncatalogued` files that table at the same partition path, where
`HistoricalSource.position_track` finds it by session key alone. Plain
`--positions-only` retrofits every catalogued session from FastF1's cache.

OpenF1 tables require mapping a FastF1 `(season, location, session name)`
onto OpenF1's own `session_key` (`OpenF1Client.find_session_key`, an exact
case-insensitive match on `location` + `session_name` via `/v1/sessions`).
If no match is found the session is still acquired with FastF1-only
tables — this is a normal, non-fatal outcome (OpenF1 may not have back-filled
that session yet), not a bug to chase.

### `manifest.json`

One JSON object, keyed by `<season>_<round two-digit>_<slugified session
type>` (`SessionKey.catalog_key`):

| field          | type           | notes                                                |
| -------------- | -------------- | ------------------------------------------------------ |
| `season`        | int             |                                                          |
| `round`         | int             |                                                          |
| `session_type`  | str             | e.g. `"Race"`, `"Practice 1"`.                           |
| `event_name`    | str             | e.g. `"Bahrain Grand Prix"`.                             |
| `sources`       | list[str]       | `["fastf1"]` or `["fastf1", "openf1"]`.                  |
| `tables`        | dict[str, int]  | table name -> row count, for whatever was written.       |
| `acquired_at`   | datetime        | UTC, when this session was last (re-)acquired.           |

Presence of a key in `manifest.json` is exactly what makes re-running
`f1-backfill --season Y` idempotent: a session already in the catalog is
skipped unless `--refresh` is passed.

## Normalized domain events (`f1_domain`)

The `DomainEvent` discriminated union (discriminated on `kind`) is the Phase 1
contract — what the normalizer emits, the `f1_store` log stores, and Phase 2
consumes. See `python/f1_domain/src/f1_domain/events.py` for the field-level
spec. Every event carries the envelope `session_key` / `ts_utc` /
`source_seq` / `provenance`:

- `source_seq` traces back to the originating `RawRecord.seq` for live/replay
  events. For historical events it is a SYNTHETIC dense ordinal (see below).
- `provenance` is `LIVE` (default; the live/replay pipeline never sets it) or
  `HISTORICAL` (set by `f1_historical_adapter`).

## Event store (`f1_store`)

Persistence for the domain events. The DB is a SINK, not a dependency of the
transform (CLAUDE.md), so `EventSink` has database-free implementations
(`NullSink`, `MemorySink`) alongside `PostgresSink`. Schema lives in versioned
SQL under `f1_store/migrations/`; the Python column/idempotency mirror is
`f1_store/schema.py` (a test keeps them in lock-step).

Two representations are stored, always both (materialized state is never the
only copy of anything):

- **`events`** — the append-only log, the source of truth: one row per
  `DomainEvent` (`kind` + full event as `jsonb`), a Timescale hypertable on
  `ts_utc`. `replay_events()` reads it back in emission order and revalidates
  each payload, reconstructing exactly what the pipeline emitted.
- **Typed tables** Phase 2 queries directly: `laps` (relational, not jsonb),
  `car_telemetry` / `car_positions` (hypertables, compressed after a day),
  `stints`, `pit_stops`, `race_control`, `weather`, `team_radio`; plus
  materialized current-state `sessions` / `drivers` upserted from
  `SessionState`.

**Idempotency**: re-ingesting a recording is safe and repeatable. Every
derived row carries the `(source_seq, event_index)` of the event it came from
— `event_index` being the event's position within the list its record
emitted (deterministic, since the pipeline is a pure function of the record
stream). That pair is the idempotency key on the high-volume/fan-out tables,
enforced with `ON CONFLICT DO NOTHING`; `laps`/`stints` use their natural
domain keys `(driver, lap_number)` / `(driver, stint_number)`. NOTE: this
refines the intuitive `(session, kind, source_seq)` key — one record fans out
into many same-`kind` events (a `CarData.z` batch is one sample per driver),
so `(kind, source_seq)` is not unique and would drop rows; `event_index` is
what makes the key genuinely per-event.

## Historical adapter (`f1_historical_adapter`)

Maps `f1_backfill`'s parquet tables onto the same `DomainEvent` stream the
live pipeline emits — the seam Phase 0 deliberately left open.
`events_from_parquet(historical_root, session_key)` yields events in
chronological order, stamped `provenance=HISTORICAL`.

What historical **can** provide: `LapCompleted`, `SectorCompleted`,
`PitEntry`/`PitExit`, `StintStarted`, `PositionChanged`, `GapUpdated`,
`WeatherSample`, `RaceControlMessage`, `TeamRadioClip`, `DriverListUpdated`,
and (opt-in via `include_telemetry`) `CarTelemetry`. For qualifying and
sprint qualifying, also `QualifyingPartChanged`, `QualifyingBestLap`,
`QualifyingKnockout` and `SessionClockUpdated`, rebuilt from race control's
`qualifying_phase`, the laps and the results
(`f1_historical_adapter.qualifying`). Live, the same four come from
`TimingData`'s `SessionPart`/`NoEntries`/`BestLapTimes`/`KnockedOut` and the
`ExtrapolatedClock` topic. Checked 2026-10-11 against the recorded 2026
Singapore sprint qualifying and qualifying: both paths knock out the same
cars as F1 at every break. Live, a part's best laps are what the timing
screen showed at the time; a lap deleted after the part ended stays in
them (four SQ1 times there), while the archive drops it when FastF1 marks
it deleted.

`LapCompleted` means what it means live: the car crossed the line. The
archive keeps an untimed row for the lap a car stopped on (2024 Miami's
Sargeant: 28 rows, 27 laps completed), which the live feed never counts,
so the adapter emits no `LapCompleted` for an untimed lap beyond the
results' `Laps` for that driver. That lap's `SectorCompleted`s and pit
entry or exit are real and still emitted. An untimed lap within the count
(a gap in the archive's timing) stays.

What historical **cannot** provide (the kernel must not assume it):

- `source_seq` is SYNTHETIC — a dense 0..N emission ordinal, not a wire seq
  (there is no raw record behind a historical event).
- Timestamps are RECONSTRUCTED: FastF1's per-sample tables carry only a
  session-relative `Time`, so absolute UTC is rebuilt from a derived session
  start (`LapStartDate - LapStartTime`). Cross-channel sub-second alignment
  is therefore approximate.
- No track/session-status delta stream. `CarTelemetry` exists but is
  FastF1's resampled/gap-filled telemetry, not the feed's raw delta
  channel — hence off by default. `CarPosition` exists (from
  `car_positions`) and IS the feed's own channel, also off by default
  because of its size (~640k per race); a consumer that only draws
  positions reads the columnar POSITION TRACK instead
  (`f1_kernel.positions`, `HistoricalSource.position_track`).
- Delta-level granularity is lost: history is per-lap/per-event facts, not
  the constant partial-update stream the live state machine folds.

**Comparison harness**: `compare_session(recording_dir, historical_root,
session_key)` runs BOTH paths for one session and reports field-level
agreement (lap times, pit laps, final positions). Discrepancies between the
two sources are expected — it is a report, not an assertion.

## Kernel frames (`f1_kernel`)

Phase 2's contract, the way `DomainEvent` is Phase 1's. Three polars frames
with STABLE, documented schemas that every analytic, backtest and (later)
UI query reads. The authoritative definition — column order, dtype and a
per-column note — is `python/f1_kernel/src/f1_kernel/schemas.py`; this
section is the summary and the rules.

**Treat them like a database schema.** Adding a column is safe and normal.
Renaming, retyping or removing one breaks every analytic written against
it, so do it deliberately, in one commit, with the analytics updated.
Column ORDER is part of the schema. Units are in the names (`_s` seconds,
`_m` metres, `_kph`, `_pct`), and everything nullable is genuinely
nullable — a lap with no gap data must stay distinguishable from a lap with
a zero gap.

| frame | grain | notes |
| --- | --- | --- |
| `lap_frame` | one row per completed lap | sorted by `(driver, lap_number)` |
| `stint_frame` | one row per (driver, stint) | sorted by `(driver, stint_number)` |
| `telemetry_frame` | one sample, ONE driver | sorted by `ts_utc` |

Derivations worth knowing before you trust a column:

- **The lap window** `(lap_start_utc, lap_end_utc]` is the spine. It is
  reconstructed as `end - lap_time`, falling back to the driver's previous
  lap end when the feed sent no lap time. Everything window-scoped (gaps,
  track status) is summarized over it.
- **`gap_ahead_s` is a MEDIAN**, not an instant — the representative gap
  over the lap — and `gap_ahead_samples` reports how much evidence stood
  behind it. Zero samples means unknown, not "a large gap".
- **`track_status`** is the most severe status overlapping the lap;
  **`track_status_flags`** lists every one of them, which is what lets a
  lap that ran partly yellow and partly under the safety car be flagged as
  both. Chequered ranks with green: it is an instant, not a restriction.
- **`stint_number` is null** when the source emitted no `StintStarted`
  events. Stints are never invented from pit stops. A stint with no
  `start_lap` (live: the set fitted before the feed counted a lap) covers
  the session from its start. Live, stints are numbered in the order the
  sets were fitted, and the feed can open a set twice before the start, so
  stops are the stints begun after lap 1, not the last stint number less
  one.
- **`distance_m` is integrated from the speed channel** — F1's car
  telemetry carries no distance channel — so it drifts slightly over a
  session, and a hole in the stream contributes no distance rather than
  inventing metres across the gap.
- **`provenance`** is `live` or `historical` per row. Same schema either
  way; historical has reconstructed timestamps and a reconstructed track
  status (see the historical-adapter caveats above).

`annotate_laps` returns the lap frame PLUS one boolean per exclusion reason,
`is_clean`, the `dirty_air_known` / `anomaly_known` quality signals, and an
`exclusion_reasons` list. `clean_laps` is exactly that filtered to
`is_clean`. Reason semantics are specced in `f1_kernel/cleaning.py` and
summarized in `docs/PHASE2.md`.
