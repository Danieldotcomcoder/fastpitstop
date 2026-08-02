# Phase 0 runbook

Everything an operator needs to run the Phase 0 replay harness: record a
live session, replay a recording for development, back-fill historical
data, and verify the whole thing still holds together. Phase 0 produces
nothing a user sees — its deliverable is "raw in, raw out, faithfully",
plus clean historical tables. Phase 1 (normalization into domain events)
is explicitly out of scope.

## Exit criteria

Phase 0 is done when every box is checked:

- [ ] Can record a live session to a valid recording dir (verified on ≥1 real weekend).
- [x] Can replay any recording at arbitrary speed with faithful timing/order.
- [x] `just verify-phase0` is green in CI.
- [ ] Historical 2023→now backfilled to parquet with a catalog.
- [x] docs/PHASE0.md lets someone else operate all of the above.

(The two unchecked boxes need, respectively, a real race weekend and one
long local `f1-backfill` run per season — the code paths for both are built
and tested; the boxes get ticked when they've been exercised for real.)

## Verification: `just verify-phase0`

```sh
just verify-phase0
```

Generates a small synthetic recording (`f1_replay.verify`), runs it
through `f1_replay.replay` at `speed=0`, and asserts the round-trip
invariant: **record stream out == record stream in** — same count, same
order, identical `seq`/`topic`/`payload`/timestamps, with the subscribe
snapshot delivered first and verbatim. That invariant IS the contract
between the recorder and the replayer (both depend on `f1_recording`; see
CLAUDE.md's hard invariants). If either side drifts, this fails loudly
naming the first mismatching record.

It runs as part of `just check`, so CI is red whenever the contract is
broken. The companion end-to-end smoke test —
`python/f1_replay/tests/test_phase0_smoke.py`, real recorder write → real
replay via the Python sink API → count/order assertions — is the canary
Phase 1 builds on.

## Recording a live session (`f1-record`)

`f1_ingest` talks to F1's real, unofficial SignalR feed, which only exists
during a live session — roughly 24 weekends a year. As a result:

- The connection layer is **not unit tested**. It can't be meaningfully
  faked without also faking away the thing that makes it risky (an
  unofficial, undocumented, live-only protocol). What *is* unit tested,
  with a canned message sequence and no socket, is `RecordingSession` — the
  part that turns feed events into a recording. See
  `python/f1_ingest/tests/test_recording_session.py`.
- Day-to-day development does **not** run against the live feed. It runs
  against recordings (`f1_replay`) and historical tables (`f1_backfill`).
  The live recorder only needs to work correctly during the ~24
  weekends/year it's actually exercised; everything built on top of it
  should be developed and tested the other 340+ days using those.

### Smoke-testing outside a race weekend

```sh
uv run f1-record --dry-run
```

This connects, asks the feed to subscribe, and reports whether a session is
currently live — then exits without writing anything. An empty/no-data
response is the **expected**, non-error outcome outside a race weekend, not
a bug. This is the only way to exercise the connection layer without a real
session.

`--dry-run` always skips authentication (`no_auth=True`) so it stays a
quick, non-interactive check — the real auth flow requires an F1TV
subscription and an interactive browser sign-in (see "Authentication"
below), which defeats the point of a smoke test. `--connect-timeout
SECONDS` controls how long it waits.

**Known issue (fastf1 3.8.3 + signalrcore 1.0.2, as of writing):**
`no_auth=True` currently raises `TypeError: access_token_factory is not
function` inside `signalrcore`'s `with_url` — fastf1's own `_run()` passes
`"access_token_factory": None` when `no_auth=True`, but this version of
`signalrcore` requires the key to be either absent or an actual callable,
not `None`. This affects FastF1's own `no_auth` mode too, not just ours.
`f1_ingest` catches this (and any other connection error) and treats it as
"not live" for `--dry-run`'s purposes, and as a disconnect worth
reconnecting for in the real recorder — so it doesn't crash — but it does
mean `--no-auth` currently can't actually reach the feed. Re-check this
against current `fastf1`/`signalrcore` releases; it may already be fixed
upstream.

### Authentication

Full live data requires signing in with an F1TV Access/Pro/Premium
subscription. `f1_ingest` reuses FastF1's own auth (`fastf1.internals.
f1auth`, via the inherited `SignalRClient._run()`) rather than
reimplementing it: the first time `f1-record` runs without `--no-auth` and
without a cached token, it prints a URL to open in a browser and blocks
(in the connection thread only) until sign-in completes; the token is then
cached by FastF1 itself (`platformdirs.user_data_dir("fastf1")`) for future
runs. `--no-auth` skips this and connects unauthenticated, which FastF1's
docs say "may only work for some sessions or may only return empty or
partial data" — see the known issue above for its current status with our
dependency versions.

### Recording a real session

```sh
uv run f1-record --out data/recordings/ --session-name 2024_bahrain_fp1
```

Recordings land as one directory per session under `data/recordings/`
(gitignored), containing `manifest.json`, `subscribe.json`, and
`stream.jsonl` — the format spec is `docs/DATA_CONTRACTS.md`.

- `--session-name` is optional; if omitted, the directory name is derived
  from the session's own `SessionInfo` (`<year>_<grand_prix>_<session_type>`).
- SIGINT/SIGTERM (Ctrl+C, or a normal `kill`) trigger a graceful shutdown:
  the current buffer is flushed and the recording directory is closed
  cleanly.
- If the connection drops mid-session, `f1-record` reconnects with
  exponential backoff (capped by `--max-backoff`, default 60s) and keeps
  appending to the *same* recording — see `docs/DATA_CONTRACTS.md`'s
  "Live recorder sentinel topics" section for how reconnects show up in
  the stream.

### Cross-validating against FastF1's own recorder

FastF1 ships its own live-timing recorder. Run it **in parallel**, on the
same machine or another, during a real session to cross-check our capture
(e.g. did we miss messages, get the topic list right, see the same
`SessionInfo`):

```sh
python -m fastf1.livetiming save fastf1_reference.jsonl
```

Compare topic coverage and rough message counts between
`fastf1_reference.jsonl` and our `stream.jsonl`. They won't be byte-for-byte
identical (different on-disk formats — see `fastf1.livetiming.client.
messages_from_raw` vs. `f1_recording`'s format), but the set of topics and
the approximate cadence of messages per topic should line up.

### What to check after a real capture

1. `manifest.json` — does `grand_prix`/`session_type`/`session_start_utc`
   match reality? (`f1_ingest/manifest.py` builds these from the raw
   `SessionInfo` topic; if F1 changes that topic's shape, this is the one
   place that needs updating — see that module's docstring.)
2. `subscribe.json` — does it contain a non-empty snapshot for every
   subscribed topic that had data at connection time?
3. `stream.jsonl` — spot-check a `CarData.z`/`Position.z` record decodes
   cleanly with `f1_recording.decode_z`.
4. If a reconnect happened, confirm the `_meta.reconnect` /
   `_meta.subscribe_snapshot` records appear at the right point in the
   stream and that recording continued afterward.

## Replaying a recording for development (`f1-replay`)

```sh
uv run f1-replay data/recordings/2024_bahrain_fp1                # real time
uv run f1-replay data/recordings/2024_bahrain_fp1 --speed 100    # 100x fast
uv run f1-replay data/recordings/2024_bahrain_fp1 --speed 0      # as fast as possible
uv run f1-replay data/recordings/2024_bahrain_fp1 --delay 45     # hold back 45s
```

Prints the recording back as JSONL on stdout, in the same relative timing a
live consumer would have seen it — the subscribe snapshot first (as a
synthetic `_meta.subscribe_snapshot` record), then every stream record in
seq order. Pipe it into whatever you're developing.

- `--speed` scales the *gaps* between messages: `1` = real time, `100` =
  100x, `0` = no sleeping at all (emit back-to-back). Useful for running a
  whole race in seconds during development.
- `--delay` shifts the entire stream later by a constant number of seconds
  (it does not compound per message). This is the Phase 3 "Delay Sync"
  primitive, built here so it has one implementation from day one.

From Python, use the sink API instead of the CLI — this is how Phase 1's
pipeline and all tests consume replays:

```python
from f1_replay.replayer import replay
from f1_replay.sinks import list_sink

records = []
replay(recording_dir, list_sink(records), speed=0)
```

See `docs/DATA_CONTRACTS.md`'s "Replay semantics" for the full timing model.

## Historical backfill (`f1-backfill`)

```sh
uv run f1-backfill --season 2024              # a whole season
uv run f1-backfill --season 2024 --round 5    # one round
uv run f1-backfill --season 2024 --refresh    # force re-pull
```

Acquires every session of the season from FastF1 (laps, car telemetry,
weather, results, stints — cached on disk in `data/fastf1_cache/`) and
OpenF1 (intervals, race control, team-radio URLs — rate-limited to the free
tier: 3 req/s, 30 req/min, with backoff on 429). Both sources are free and
unauthenticated; OpenF1 covers 2023 onward.

Output lands under `data/historical/` (gitignored) as Hive-style
partitioned parquet plus a catalog:

```
data/historical/
├── manifest.json                                  # the catalog
└── season=2024/round=01/session=Race/
    ├── laps.parquet ... team_radio.parquet
```

Being `key=value` partitioned, it's directly queryable without a database:

```sql
-- duckdb
SELECT * FROM read_parquet('data/historical/**/*.parquet', hive_partitioning=true);
```

The catalog (`manifest.json`) lists every acquired session with its
sources, per-table row counts, and acquisition timestamp. Its presence
check is what makes re-running **idempotent and resumable**: a session
already in the catalog is skipped with no network calls; a crash mid-season
loses nothing already acquired (the catalog is saved after every session);
`--refresh` is the only way to force a re-pull. Table schemas and catalog
fields are specced in `docs/DATA_CONTRACTS.md`'s "Historical backfill
tables".

To back-fill everything OpenF1 covers, run once per season:

```sh
uv run f1-backfill --season 2023
uv run f1-backfill --season 2024
uv run f1-backfill --season 2025
uv run f1-backfill --season 2026
```

(Expect the first run per season to take a while — FastF1 downloads full
telemetry per session; subsequent runs hit its on-disk cache. A session that
fails mid-season is reported at the end and retried on the next run, since
it never entered the catalog.)

## The raw-vs-historical boundary (read this before routing data around)

Phase 0 has **two separate fixture tracks**. They do not mix:

| | raw recordings | historical tables |
|---|---|---|
| produced by | `f1-record` (live SignalR feed) | `f1-backfill` (FastF1 + OpenF1) |
| contains | raw wire messages, verbatim | clean, processed tables |
| format | `f1_recording` dirs (jsonl) | partitioned parquet + catalog |
| consumed by | `f1_replay` → Phase 1 pipeline | Phase 2 analytics kernel, directly |
| lands in | `data/recordings/` | `data/historical/` |

**Do not try to route backfill through the replayer.** FastF1 and OpenF1
hand back processed, already-decoded data — not the SignalR wire messages
the recording format stores — so there is nothing faithful a replay of
them could reproduce. The replayer replays *recordings*; the backfill
feeds the *kernel*. The adapter that maps historical tables (and raw
recordings) onto the Phase 1 normalized-event schema is a **Phase 1
task** — the seam is documented in `docs/DATA_CONTRACTS.md` ("Historical
backfill tables"), and deliberately not built in Phase 0.

## What Phase 0 explicitly does not do

- No parsing of raw payloads into domain events (laps, stints, gaps) — the
  recorder stores messages verbatim, the replayer re-emits them verbatim.
- No database. TimescaleDB arrives in Phase 1.
- No normalization adapter over the historical tables.
- Nothing a user sees.

Raw in, raw out, faithfully. Stop there.
