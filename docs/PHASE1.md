# Phase 1 runbook

Everything an operator needs to run the Phase 1 pipeline: normalize a raw
recording (or the live feed, or historical parquet) into TimescaleDB, and
verify the whole thing still holds together. Phase 1 turns Phase 0's raw
records into a normalized, queryable domain model — one producer,
event-sourced, replay and live sharing a single pipeline. Phase 2 (the
analytics kernel) is explicitly out of scope.

## The one pipeline

```
RawRecord ─► SessionStateMachine ─► DomainEvent ─► EventSink ─► TimescaleDB
 (live or     (f1_parse:            (f1_domain     (f1_store)
  replay)      decode + fold)        vocabulary)
```

`f1_pipeline.run(records, sink, session_key=...)` is the single normalization
path. It takes an iterable of `RawRecord`s and does not know or care whether
they came from a live socket or a replayed recording — that is the
non-negotiable Phase 1 invariant (CLAUDE.md). The historical adapter is a
second front: `run_events(events, sink)` writes already-normalized events
straight to the sink (no state machine, no materialized state).

The DB is a **sink**, never a dependency of the transform: the pipeline runs
and is fully testable with `NullSink`/`MemorySink` and no database present.

## Exit criteria

Phase 1 is done when every box is checked. `just verify-phase1` asserts all
of them against a checked-in recording on every run:

- [x] A full recorded race replays into the store with zero crashes.
- [x] Final positions in the store match the race result (ground truth).
- [x] Lap counts and pit stops per driver match the result (ground truth).
- [x] Same recording replayed twice produces identical event streams
      (determinism — byte-identical serialized events).
- [x] Re-ingesting the same session leaves row counts unchanged (idempotency).
- [x] The historical adapter produces events for any 2023+ session
      (`f1_historical_adapter`, exercised by `f1-pipeline ingest-historical`).
- [x] `just verify-phase1` is green in CI.

`verify-phase1` runs green in CI with no database (it uses `MemorySink`,
which enforces the exact same identity/idempotency rules the DB does). When
`F1_STORE_RUN_DB_TESTS=1` and a DSN are set it ALSO replays into a throwaway
TimescaleDB and checks the persisted row counts — the literal "into a
throwaway DB" pass.

## Verification: `just verify-phase1`

```sh
just verify-phase1
```

Replays the checked-in synthetic race (`f1_pipeline/src/f1_pipeline/fixtures/
replay_race/`) end-to-end through the real pipeline into a `MemorySink`, then
asserts against the known-good snapshot (`fixtures/expected.json`):

- event counts by kind and materialized row counts (regression snapshot);
- **final classification, lap counts, and pit stops per driver against the
  recording's independent ground truth** (from `f1_parse`'s `race_sim`) — so
  this checks the pipeline against reality, not just against itself;
- determinism (a second replay is byte-identical) and idempotency
  (re-ingest leaves row counts unchanged).

It runs as part of `just check`, so CI is red whenever a Phase 1 exit
criterion breaks. Regenerate the fixture (e.g. after a deliberate schema or
event change) with:

```sh
uv run python python/f1_pipeline/fixtures/generate.py
```

## The store: bring up TimescaleDB

```sh
docker compose -f python/f1_store/docker-compose.yml up -d
export F1_STORE_DSN=postgresql://pitwall:pitwall@localhost:5432/pitwall
uv run f1-store-migrate          # apply the schema (idempotent)
```

`f1-pipeline` applies migrations automatically before each run, so
`f1-store-migrate` is only needed if you want to prepare the schema
separately. Connection comes from `F1_STORE_DSN` (or libpq `PG*` env vars, or
`--dsn`). Schema and table layout: `docs/DATA_CONTRACTS.md` ("Event store").

## Replaying a recording into the store (`f1-pipeline replay`)

```sh
uv run f1-pipeline replay data/recordings/2024_bahrain_race --round 1
uv run f1-pipeline replay data/recordings/2024_bahrain_race --round 1 --speed 0
```

- `--round` is **required**: the recording `manifest.json` carries the year
  and session type but not the championship round, and the `SessionKey` (which
  keys every row) needs it. `--year`/`--session-type` override the manifest if
  needed.
- `--split-sessions` instead files every record under the session the
  stream's own `SessionInfo` names, exactly as the live ingest does
  (`SessionRouter`, below). Use it for a recording the live worker made: one
  process runs all weekend, so its recording usually spans several sessions.
  `--round` is then optional (the feed's `Meeting.Number` is the round).
- `--speed` scales inter-message gaps (`0` = as fast as possible, the default
  for ingest; `1` = real time). `--delay` adds a constant offset. Both go
  through the same `f1_replay` timing model as `f1-replay`.
- On completion it prints the run metrics (see below).

## Ingesting historical data (`f1-pipeline ingest-historical`)

```sh
uv run f1-pipeline ingest-historical --season 2024
uv run f1-pipeline ingest-historical --season 2024 --round 1
uv run f1-pipeline ingest-historical --season 2024 --session-type race
uv run f1-pipeline ingest-historical --season 2024 --include-telemetry
```

Discovers every session under `data/historical/season=<Y>/` (produced by
`f1-backfill`), maps each onto the same `DomainEvent` stream via
`f1_historical_adapter`, and ingests it. Historical events are marked
`provenance=HISTORICAL` and carry a synthetic `source_seq` (see
DATA_CONTRACTS.md for what history can and cannot provide). `CarTelemetry` is
off by default (`--include-telemetry` opts in — it is large and resampled).
No materialized `sessions`/`drivers` state is written for historical (there is
no live state projection); the event log and typed tables are.

## Recording + ingesting live (`f1-pipeline live`)

```sh
uv run f1-pipeline live --no-auth
```

Connects to the live SignalR feed, records it raw to disk (Phase 0 durability,
via `f1_ingest`) AND feeds every raw record through the pipeline into the
store — one producer, raw to disk and normalized to the DB from a single
object. Like `f1-record`, it reconnects with backoff and shuts down cleanly on
SIGINT/SIGTERM, writing the materialized state and final metrics on exit.

**One process, a whole weekend.** Production runs this as a long-lived
service, and F1 does not reconnect it between sessions: it announces each
new session over the open socket with a complete `SessionInfo` keyframe
(new `Key`, `Name`, `Path`), then streams keyframes for every other topic
within ~10 seconds. `SessionRouter` re-derives the session from every
`SessionInfo` it sees (snapshot or feed message) and starts a fresh state
machine per session; a reconnect into the same session re-seeds silently.
Before this, the key was fixed at the first snapshot, and the 2026
Azerbaijan FP3, qualifying and race were all filed under "practice_2" (the
race never read as live). The session's `sessions.status` row is rewritten
on every status change, since that row decides liveness
(`f1_store.live_session_keys`). A store failure ends the process (exit 1)
so the container restarts onto a fresh connection instead of silently
dropping the rest of a race. `--year`/`--round`/`--session-type` override
what the feed says; passing all three pins the key and turns routing off.

**Reality check** (same as `f1-record`, see `docs/PHASE0.md`): the live feed
only exists during a real session (~24 weekends/year) and the socket layer is
not unit tested. Day-to-day development runs off `replay` and
`ingest-historical`, not `live`. The routing itself is unit tested against
payloads cut from the real 2026 Azerbaijan recording
(`tests/test_session_router.py`).

## Run metrics

Every run prints, and the CLIs log, a metrics summary:

- **records in**, **events out**, and **events by kind**;
- **rows written** per table;
- **unknown-topic count** (and which topics) — the operational early warning:
  the feed is unofficial and can change without notice, so a sudden spike in
  `unknown` events means a topic we stopped recognizing (or a new one) and the
  parser needs updating;
- **wall time** (measured strictly around the run — never inside the
  transform, so determinism holds).

## What Phase 1 explicitly does not do

- No analytics: no clean-lap filter, fuel correction, degradation fitting,
  undercut/overcut prediction, or braking-point drift. That is Phase 2, built
  on the tables this phase fills — not pulled forward.
- No UI. Nothing a user sees (Phase 3).
