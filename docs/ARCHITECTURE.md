# Architecture

Stub. This document will grow alongside the phases described in
[`CLAUDE.md`](../claude.md) — do not relitigate the phase map or tech
decisions here; extend them.

## Repo layout

```
python/           uv workspace (Python 3.12)
  f1_recording/    Phase 0 — the recording format contract (recorder <-> replayer)
  f1_ingest/        the live-feed connector: Phase 0 — connects to the SignalR
                     feed and records it raw; Phase 1 adds normalization ->
                     domain events -> TimescaleDB (same producer, per CLAUDE.md)
  f1_replay/        Phase 0 — replays a recording at arbitrary speed
  f1_backfill/       Phase 0 — historical data acquisition (FastF1 + OpenF1)
  f1_domain/         Phase 1 — the domain vocabulary: pure types, zero logic
  f1_parse/          Phase 1 — layer one (decode: raw payloads -> typed data)
                      and layer two (SessionStateMachine: deltas -> state ->
                      domain events)
  f1_store/          Phase 1 — persistence: the append-only DomainEvent log +
                      materialized state tables in TimescaleDB (the sink)
  f1_historical_adapter/  Phase 1 — the seam: f1_backfill parquet -> the same
                      DomainEvent stream the live pipeline emits
  f1_pipeline/       Phase 1 — the wiring: the one run(records, sink) path both
                      live and replay feed, the historical ingest path, the
                      f1-pipeline CLI, and the verify-phase1 harness
  f1_kernel/         Phase 2 — the analytics kernel: typed lap/stint/telemetry
                      frames over the DomainEvent stream, KernelParams, circuit
                      metadata, the clean-lap filter, and the backtest harness
  f1_api/            Phase 3.1 — the FastAPI read model: REST + a WebSocket
                      envelope stream over f1_kernel/f1_store, the only thing
                      the frontend talks to
  f1_radio/          Phase 4 — the team-radio corpus: acquires TeamRadioClip
                      audio into a local cache (4.1), then transcribes it
                      offline via local faster-whisper into radio_transcripts
                      (4.2, a table separate from team_radio). Embeddings/
                      search (4.3) and API/frontend exposure are next.
packages/, apps/   pnpm workspace (Phase 3 frontend)
  apps/web/          Phase 3.2 — the SvelteKit frontend: design system,
                      typed REST client generated from f1_api/openapi.json,
                      a WebSocket wrapper over /stream/{key}, one smoke route
                      (GET /sessions). Phase 3.3 (src/lib/delay-sync/) adds
                      the spoiler-safety gate + calibration UX + its own dev
                      harness (/delay-sync). Phase 3.4 (src/lib/track-map/)
                      is the first race view: a Canvas track map (outline,
                      cars, sector dominance) driven from Delay Sync's
                      visibleState, plus its own dev harness (/track-map).
                      Degradation/undercut panels are the other planned
                      race view, not built yet.
  packages/          empty — shared TS code once something needs sharing
                      across multiple apps
docs/               this file, plus DATA_CONTRACTS.md and PHASE0/1/2.md
data/               gitignored local data (recordings, historical, fastf1 cache)
```

## Current phase (0): Replay harness

Goal: record the live feed to disk, replay it at arbitrary speed, and acquire
historical data — so the rest of the stack can be developed against ~80 past
races on any weekday instead of only during live sessions.

- `f1_recording` owns the on-disk recording format. It is the contract
  between the recorder and the replayer; nothing else should invent its own
  format. Raw messages only — no domain parsing here (that's Phase 1).
- `f1_ingest` is the live-feed connector: it connects to F1's SignalR feed
  (mirroring FastF1's `fastf1.livetiming` connection approach — see
  `f1_ingest/client.py`) and records every message raw via `f1_recording`.
  It knows topic names but no domain semantics. See
  `docs/PHASE0.md` for the reality check on testing this (it only
  runs against the real feed live, ~24 weekends/year) and how to
  cross-validate it against FastF1's own recorder.
- `f1_replay` reads recordings produced by `f1_recording` and replays them
  through a pluggable sink (`replay()` for library use, `f1-replay` for a
  JSONL-on-stdout CLI), at arbitrary `--speed` and with a fixed `--delay`
  (the Phase 3 "Delay Sync" primitive) — see `docs/DATA_CONTRACTS.md`'s
  "Replay semantics" section for the timing model and how the initial
  subscribe snapshot is surfaced.
- `f1_backfill` produces clean historical tables (FastF1 + OpenF1) as
  partitioned parquet under `data/historical/` plus a `manifest.json`
  catalog, separate from the raw live recordings, with a documented seam
  for the Phase 1 historical -> normalized-event adapter — see
  `docs/DATA_CONTRACTS.md`'s "Historical backfill tables" section for the
  table schema and idempotency/`--refresh` behavior.

## Phase 1 (done): Ingest + store

Goal: normalize the raw stream into domain events and persist them. LIVE and
REPLAY feed the exact same pipeline (an iterator of `RawRecord`s — the
pipeline neither knows nor cares which), and the transform is a pure
function of the record stream: no wall clock, no randomness, no network.
See CLAUDE.md's "Phase 1 status" for the full invariants.

Parsing is layered, each layer separately testable:

```
RawRecord -> decoded payload -> state mutation -> domain event -> sink (DB)
             (f1_parse.decode)   (f1_parse.        (f1_domain      (next;
                                  state_machine)    vocabulary)     DB is a
                                                                    sink, not
                                                                    a dependency)
```

- `f1_domain` is the vocabulary — the `DomainEvent` discriminated union
  (discriminated on `kind`), identity types/enums, and the materialized
  `SessionState`/`DriverState` models. Pure pydantic types: zero logic, zero
  I/O, no dependency on any other workspace package. Every event carries
  `session_key`/`ts_utc`/`source_seq` (the originating `RawRecord.seq`) plus
  a `provenance` (`LIVE` default vs `HISTORICAL`, so Phase 2 can tell
  live-derived from adapter-derived events apart), and an `UnknownEvent`
  variant preserves anything unparsed verbatim.
- `f1_parse` layer one: `decode(record) -> DecodedMessage`, stateless,
  dispatching to one decoder module per topic family (timing, car_data,
  position, weather, track_status, race_control, team_radio, session_info,
  session_data, lap_count, driver_list). Timing topics come back as the
  partial deltas they are; `deep_merge` (with pinned null/empty/sparse-list
  semantics) is the utility layer two folds them with. `CarData.z`/
  `Position.z` inflate via `f1_recording.decode_z` and fan out to one sample
  per (entry, driver). Unknown topics and malformed payloads pass through
  verbatim — the pipeline never crashes on a message it doesn't recognize.
- `f1_parse.state_machine` is layer two: `SessionStateMachine.apply(record)
  -> list[DomainEvent]`, plus a materialized `state -> SessionState`
  projection and `seed(subscribe_snapshot)`. Where deltas become facts:
  lap completions (an observed increment plus a FRESH lap time), pit
  in/out, stint starts with compounds, gaps (lapped cars are a `LapGap`
  count, never a duration), positions (TimingData and DriverList
  deduplicated against one canonical value), track/session status, weather,
  race control, team radio (URLs resolved via SessionInfo's path). Emission
  is change-detected per driver per field, a monotonic-seq guard makes
  re-applied records inert, and seeding adopts the snapshot as the new
  baseline — the FIRST seed also publishes the running order and roster
  once (on the next real record, so replay stays deterministic), while the
  recorder's `_meta.*` reconnect re-seeds stay silent. Backtested
  end-to-end against a synthetic full-race recording
  (tests/race_sim.py) replayed through the real `f1_replay` path, with
  exact assertions: lap counts and times, pit laps, stint compounds, final
  classification, determinism (byte-identical event streams), idempotency.
- `f1_store` is the SINK (never a dependency of the transform, per
  CLAUDE.md — the normalizer runs and is fully testable with no DB). It
  persists what the pipeline emits two ways at once: the append-only
  `events` log (the source of truth, replayable via `replay_events`) and
  typed materialized tables Phase 2 queries directly (`laps`,
  `car_telemetry`/`car_positions` as compressed hypertables, `stints`,
  `pit_stops`, `race_control`, `weather`, `team_radio`) plus current-state
  (`sessions`, `drivers`). `EventSink` is the contract; `PostgresSink`
  writes via batched COPY-into-staging + `ON CONFLICT DO NOTHING` (so
  re-ingesting a recording is idempotent), while `NullSink`/`MemorySink`
  need no database. Schema is versioned SQL under `migrations/`.
- `f1_historical_adapter` closes the Phase 0 seam: it maps `f1_backfill`'s
  parquet tables onto the SAME `DomainEvent` stream the live pipeline
  emits (`events_from_parquet`), stamped `provenance=HISTORICAL`, so
  Phase 2's kernel runs one code path over both live and 2023-2025
  history. `compare_session` reports field-level agreement between a
  recording and the historical tables for the same session.
- `f1_pipeline` is the wiring and the single entry point. `run(records,
  sink, session_key)` is the one normalization path both live and replay
  feed (it never learns which); `run_events` is the historical front
  (adapter events straight to the sink); `Pipeline` is the push form the
  live socket drives one record at a time. The `f1-pipeline` CLI
  (`replay` / `ingest-historical` / `live`) wires each source to a Postgres
  sink and logs per-run metrics (records in, events by kind, unknown-topic
  count, rows written, wall time). `just verify-phase1` replays a
  checked-in recording end-to-end and asserts against ground truth plus
  determinism/idempotency — see docs/PHASE1.md.

## Phase 2 (done): Analytics kernel

Goal: derive predictive state the feed does not carry — the moat. Built as
PURE FUNCTIONS over typed frames: load data (historical parquet, the
TimescaleDB event log, or events in hand), then pass frames in. No analytic
touches a database, a network or a clock, so the same input always gives the
same output. See CLAUDE.md's "Phase 2 status" for the full invariants and
`docs/PHASE2.md` for the runbook.

```
DomainEvent ─► lap/stint/telemetry frames ─► clean_laps ─► analytic(frames, params)
 (Phase 1's    (f1_kernel.frames, pure)      (f1_kernel.    (pure; returns a value
  contract)                                   cleaning)      AND a confidence signal)
```

- `f1_kernel.schemas` is Phase 2's contract the way `DomainEvent` was Phase
  1's: three stable, documented polars schemas (`lap_frame`, `stint_frame`,
  `telemetry_frame`) that every analytic, backtest and later UI reads.
  Adding a column is safe; renaming or retyping one breaks everything
  written against it.
- `f1_kernel.sources` is the loading half — `MemorySource`, `HistoricalSource`
  (backfill parquet via `f1_historical_adapter`) and `StoreSource` (the event
  log via `f1_store.replay_events`) — all answering one `EventSource`
  protocol so the frame builders stay pure and source-agnostic. `kinds`/
  `drivers` filters are a pushdown, not a second code path.
- `f1_kernel.frames` holds the event→frame transforms. Notable derivations:
  the lap WINDOW `(start, end]` is the spine everything window-scoped is
  summarized over; gaps are summarized as a median with a sample count
  rather than one instant; telemetry distance is integrated from speed
  because F1 telemetry carries no distance channel.
- `f1_kernel.status` turns track status into a queryable timeline — from the
  feed's own `TrackStatusChanged` topic live, and reconstructed from
  race-control messages for historical sessions, which have no status
  stream at all.
- `f1_kernel.params` is `KernelParams`: every tunable constant, typed,
  frozen, JSON round-trippable, with a per-field provenance marker. Almost
  all are PROVISIONAL until a calibration prompt fits them.
- `f1_kernel.circuit` loads versioned per-circuit JSON — corners, DRS zones
  and lap distance DERIVED from real session data, pit loss the one value
  that stays provisional.
- `f1_kernel.cleaning` is the clean-lap filter: `clean_laps` for analytics,
  `annotate_laps` for a UI that must explain exclusions. It flags rather
  than silently drops, and reports when a judgement could not be made.
- `f1_kernel.backtest` runs an analytic over a corpus of sessions and
  tabulates the result — how every later analytic gets tested, per
  CLAUDE.md's "backtested against replays, not mocks".
- `f1_kernel.pit_loss` measures what a green-flag stop actually costs,
  per circuit, from the backfill (median + uncertainty per circuit;
  written into the circuit files by `tools/calibrate_pit_loss.py`).
- `f1_kernel.undercut` is the crossover predictor — undercut/overcut
  windows with confidence derived from the underlying fit reliability —
  and `f1_kernel.undercut_eval` its walk-forward backtest: predict at the
  lap before each real stop, score against what actually happened
  (committed report under `f1_kernel/reports/`).
- `f1_kernel.braking` is braking-point drift: `braking_points` finds each
  lap's sustained brake-application distance per corner from one driver's
  telemetry and the circuit's corner metadata; `braking_drift` fits how
  that point moves across a stint's tyre age, reusing `fit_degradation`'s
  `Reliability` verdict for the same reason it exists there.
- `f1_kernel.report` is the seam over all of the above: `analyze_session`
  chains clean -> fuel-correct -> degradation -> crossover -> braking-drift
  into one typed `KernelReport`; `analyze_session_from_frames` is the pure
  core ("pure over loaded frames"); `LiveKernelSession` is the live variant
  a growing event stream drives, one `push()` per update. `just
  verify-phase2` runs the kernel over the checked-in fixture and checks the
  release-gate exit criteria — see docs/PHASE2.md.

## Phase 3 (done): Frontend

Goal: the first thing a user sees — live and replayed races, spoiler-safe
by construction. See CLAUDE.md's "Phase 3 status" for the full invariants
and `docs/PHASE3.md` for the runbook (API, frontend, and the Playwright
verification harness alike).

```
f1_kernel / f1_store ─► f1_api (REST + /stream WebSocket) ─► apps/web (SvelteKit)
 (Phases 1-2, pure       (Phase 3.1: the read model —          (Phase 3.2 shell + design
  Python)                 the ONLY thing the frontend           system; 3.3 Delay Sync
                          talks to)                             gates it; 3.4 track map,
                                                                 3.5 kernel-analytics views
                                                                 and 3.6's timing tower are
                                                                 the race views; 3.6 also
                                                                 composes them into /race,
                                                                 the actual dashboard)
```

- `f1_api` (3.1) is the read model. `sessions.py` resolves a `SessionKey` to
  whichever `f1_kernel.EventSource` actually has it — historical backfill
  parquet (always available) or, when configured and reachable, the live
  TimescaleDB event log — so every route is agnostic about where a session
  lives, the same "DB is a sink, never a dependency" rule Phases 1-2 apply.
  `schemas.py` mirrors the kernel's own dataclasses/frame schemas
  field-for-field (the response contracts ARE the Phase 3 interface);
  `convert.py` reshapes kernel output into them with no logic of its own.
  `routes/sessions.py` is the REST surface (`/sessions`, lap/degradation/
  analysis/braking-point/circuit reads — `/circuit`, Phase 3.4, serves
  `f1_kernel.Circuit`'s corners/DRS zones/outline); `stream/` is
  `/stream/{key}`, which in `mode=replay` re-paces a session's own event
  stream back out as timed envelopes using `f1_replay`'s own timing model
  (made public from `f1_replay` for exactly this reuse) — the Phase 0
  replay moat applied to the WebSocket, not just historical REST reads.
  `openapi.json` is committed: the frontend's typed-client source once it
  starts.
- `apps/web` (3.2) is the SvelteKit scaffold: a design system (tokens for
  color/type/spacing, light/dark, four primitives — `Panel`, `Stat`,
  `ConfidenceBadge`, `LoadingState` — plus the unofficial-disclaimer
  `Footer`), a typed REST client generated from `f1_api`'s own
  `openapi.json`, and a dumb `SessionStream` wrapper over `/stream/{key}`.
  One smoke route proves the full path end-to-end against the checked-in
  fixture; every race view built on top of this scaffold (3.3-3.6) lives
  under its own route.
- Delay Sync (3.3) is the spoiler-safety gate, the killer feature —
  `apps/web/src/lib/delay-sync/`, a plain framework-agnostic TS module
  (`TimelineBuffer` gates every envelope on `event_time_utc <=
  playbackClockMs(wallNow, delaySeconds)`; `DelaySyncController` wires it
  to a derived `visibleState`, the ONLY thing a view may subscribe to)
  developed against `f1_api`'s `mode=replay` WebSocket, per CLAUDE.md's "no
  live race required" invariant — three thin Svelte components (slider,
  tap-to-sync, indicator) and a dev harness route (`/delay-sync`) sit on
  top. See docs/PHASE3.md's "Delay Sync" section for the full module map
  and how the zero-leak guarantee is tested.
- Track map (3.4) is the first race view: `f1_kernel.track_outline`
  derives a circuit's real 2D lap shape from position telemetry (a build
  step `tools/build_circuits.py` now also runs), served via `f1_api`'s new
  `GET /sessions/{key}/circuit`; `apps/web/src/lib/track-map/` is a
  framework-agnostic pure-TS core (per-driver position/dominance reducers,
  interpolation, Canvas geometry) with a thin `TrackMap.svelte` on top,
  driven from Delay Sync's `visibleState` only — see docs/PHASE3.md's
  "Track map" section for the full module map.
- Kernel-analytics views (3.5) are the other three race views —
  `apps/web/src/lib/kernel-views/` (framework-agnostic pure TS, the same
  split as Delay Sync/track map) plus `UndercutCountdown.svelte`,
  `GapStreamGraph.svelte`, `DegradationCurves.svelte` and
  `BrakingPointDrift.svelte`. The undercut countdown and gap stream read
  `visibleState` directly (a `kernel_snapshot` envelope and the raw
  `gap_updated` stream are already spoiler-safe by construction); the two
  REST-fetched views (whole-session/stint responses) get a second gate,
  `RevealTracker`, deciding WHEN an already-fetched fit may render rather
  than recomputing anything. See docs/PHASE3.md's "Kernel-analytics views"
  section for the full module map.
- The race dashboard (3.6) composes every view above into `/race`, the
  actual product: `$lib/timing-tower/` (the one view that didn't already
  exist — classification, gaps, tyres, folded from `visibleState` the same
  way every other reducer here is) plus the track map, undercut countdown
  and a switchable (tabbed) analytics panel, mobile-first with a
  `:has()`-gated wider grid at 900px+. Also the Phase 3 E2E verification
  harness (`apps/web/playwright.config.ts` + `apps/web/e2e/`, wired as
  `just verify-phase3`): a real browser against a real running `f1_api` +
  frontend, both pointed at the checked-in kernel fixture. Building it
  surfaced a real gap in 3.1's WebSocket layer — replayed envelopes carried
  their source event's own historical timestamp, which made Delay Sync's
  wall-clock-vs-`event_time_utc` gate a no-op for any non-live session —
  now fixed by remapping to the real wall-clock send instant
  (`f1_api.stream.replay`). See docs/PHASE3.md's "The race dashboard"
  section for the full module map and why.

## Phase 4 (done): Team radio corpus

Goal: a genuinely new dataset — searchable driver-pit radio — feeding
Phase 5's "why" engine. See CLAUDE.md's "Phase 4 status" for the full
invariants and `docs/PHASE4.md` for the runbook.

```
TeamRadioClip event ─► fetcher (cache audio) ─► transcribe_batch (faster-whisper) ─► radio_transcripts ─► index_batch (embed+context) ─► search ─► f1_api (4.4)
 (Phase 1)              f1_radio.fetcher/         f1_radio.transcription/               f1_store's DSN,     f1_radio.embeddings/context/    f1_radio.search   routes/radio.py,
                         cache/manifest (4.1)       transcribe_batch (4.2)                migration 002       index_batch, migration 003 (4.3)  (4.3)             stream/replay.py
```

- `f1_radio` acquisition (4.1) downloads each `TeamRadioClip`'s audio into a
  deterministically-keyed local cache, idempotent and resumable
  (`f1_radio.fetcher`/`cache`/`manifest`), and ships the SEPARATE
  `radio_transcripts` schema (`f1_store/migrations/002_radio_transcripts.sql`)
  transcription writes into.
- `f1_radio.transcription`/`transcribe_batch` (4.2) is local batch
  transcription via `faster-whisper` — no per-minute cost, offline/batch
  only (never in the live hot path). The hallucination guard is
  faster-whisper's own VAD pre-filter: silence/noise is stripped to zero
  segments before the decoder ever runs, so it is flagged rather than
  decoded into invented text — proven against a real model and checked-in
  SYNTHETIC audio fixtures (Windows-SAPI TTS + generated silence/noise,
  never real broadcast audio). `run_batch` is idempotent (skips clips that
  already have a row), resumable (skip state comes from the store's own
  contents), and safely parallelizable (`--workers N`; the real table's
  UNIQUE CONSTRAINT is the claim, so racing workers never double-write).
  `f1_radio.quality_report` is a pure function over a season's transcripts
  (confidence distribution, flagged clips, language breakdown) rendered as
  markdown. See docs/PHASE4.md's "Transcription" section for the full
  module map.
- `f1_radio` semantic index (4.3) makes the corpus SEARCHABLE:
  `f1_radio.embeddings` loads a local `sentence-transformers` model (no
  paid API); `f1_radio.context.SessionContext` derives race context (lap,
  position, track status) at each clip's `captured_at` from that session's
  own event stream, reusing `f1_kernel.status` rather than re-deriving
  SC/VSC reconstruction; `f1_radio.index_batch.run_index_batch` backfills
  both onto each clip's `radio_transcripts` row (idempotent, resumable,
  mirroring `transcribe_batch.run_batch`'s shape) via migration 003's new
  pgvector `embedding` column plus the context columns. `f1_radio.search`
  is the query side — `PostgresSearchIndex` (pgvector's `<=>` operator) and
  `MemorySearchIndex` (the same ranking in plain Python) both answer
  semantic/hybrid/structured queries (driver, session, lap, track status,
  date range, keyword). `f1-radio index --season Y` /
  `f1-radio search "query"` are the CLI. See docs/PHASE4.md's "Semantic
  index" section for the full module map.
- `f1_api` radio integration (4.4) serves the corpus through the read model:
  `GET /radio/search` (ranked, context-enriched), `GET /sessions/{key}/radio`
  (every clip for a session), `GET /radio/{clip_id}` (one clip's transcript +
  segments + context + audio URL). `f1_api.radio` is the new seam — real
  FastAPI `Depends` providers (unlike `sessions.py`'s plain functions),
  since the radio corpus has no DB-free real backing tests could exercise
  instead. `/stream/{key}` gained a `"radio_clip"` envelope kind
  (`f1_api.stream.replay.radio_clip_envelope`) enriching the `team_radio_clip`
  event already on that stream with its transcript, paced off `captured_at`
  (not `ts_utc`) through the exact same wall-clock remap every other
  envelope uses — exactly as spoiler-safe. See docs/PHASE4.md's "Served
  through f1_api" section for the full module map.
- Frontend + verification (4.5) is where the corpus becomes something a
  user sees: `apps/web/src/lib/radio/` (pure TS, the `RadioClipView` shape
  both a live envelope and a REST response convert into, plus the timeline
  lane's own `RadioLaneReducer`) and `RadioClipCard`/`RadioLane.svelte`
  (wired under `/race`'s gap-stream tab) and `routes/radio/+page.svelte`
  (the standalone corpus search product) sit on top. `just verify-phase4`
  runs a SEPARATE Playwright `f1_api` + frontend pair (its own
  `webServer` group, ports in `apps/web/e2e/ports.ts`) against a dedicated
  fixture via `f1_api.radio_fixture`'s new DB-free path
  (`F1_API_RADIO_FIXTURE`), since the Phase 3 fixture ships no
  `team_radio_clip` events at all. See docs/PHASE4.md's "Frontend" section
  for the full module map.

## Phase 5 (done): The why-engine

Goal: on a detected anomaly, fuse kernel state + radio + race control into
ONE grounded, sourced sentence — never a guess. See CLAUDE.md's "Phase 5
status" for the full invariants and `docs/PHASE5.md` for the runbook.

```
DomainEvent stream ─► kernel frames + KernelReport ─► detect_anomalies ─► Anomaly[] ─► assemble_evidence ─► EvidenceBundle ─► explain ─► Explanation
 (Phases 1-2, pure       f1_kernel.analyze_session         f1_why (5.1) — pure,   ranked by    f1_why.evidence (5.2) —  token-budgeted,  f1_why.narration (5.3) —
  Python)                                                   no LLM, no evidence   salience      pure, kernel state +   spoiler-safe      the ONLY LLM call, behind
                                                             bundle                              radio + race control   (<= anomaly_time) a grounding validator
```

- `f1_why` anomaly detector (5.1) is pure Python over `f1_kernel`'s own
  typed frames and `KernelReport` — detection only, per CLAUDE.md's own
  line for this prompt: no evidence bundle assembled, no LLM called here.
  `detect_anomalies(session_frames, kernel_report, params) ->
  list[Anomaly]` covers six `AnomalyKind`s: `PACE_LOSS`/`OFF_TRACK` (a
  robust outlier versus a driver's own RECENT clean-lap trend in the same
  stint — a lap's whole time, or one mini-sector's, the lock-up/off-track
  proxy this schema supports without needing telemetry); `POSITION_CHANGE`
  (a clean on-track swap, never a pit-cycle reshuffle); `PIT_ANOMALY`
  (reusing `f1_kernel.pit_loss.pit_loss_samples` against the
  calibrated/default norm); `DEG_CLIFF` (a threshold on
  `fit_degradation`'s own optional quadratic term); `UNDERCUT_RESOLVED`
  (reusing `f1_kernel.undercut_eval.evaluate_session_stops`'s walk-forward
  machinery to score how a real duel actually resolved). Every anomaly
  carries a `salience` — a shared `[0, 1)` ranking score built from each
  kind's own "how many seconds of race-significance" figure — so a
  consumer can rank/limit rather than drowning in every small wobble. See
  docs/PHASE5.md's "Anomaly detector" section for the full module map.
- `f1_why.evidence` (5.2) gathers everything relevant to one `Anomaly` at
  or before its own `anomaly_time` into a typed, token-budgeted
  `EvidenceBundle` — still no LLM. Kernel state (lap/tyre/compound context,
  a WALK-FORWARD-refit degradation trend — sliced to laps at or before the
  anomaly, never trusting a whole-session `KernelReport` verbatim, the
  same slice-then-refit discipline `f1_kernel.undercut.undercut_snapshot`
  already uses), radio clips (`f1_radio.search`, reused rather than a
  second query path) and race-control messages (read from the session's
  own raw event stream, which `SessionFrames`/`KernelReport` do not carry)
  are ranked by a `salience` distinct from each item's own trust
  `confidence`, then trimmed to fit the budget. `EvidenceBundle.
  low_evidence` is true unless a genuinely explanatory item (radio/race
  control, never kernel-state context alone) survived. See
  docs/PHASE5.md's "Evidence bundle" section for the full module map.
- `f1_why.narration` (5.3) is the ONLY place an LLM is used anywhere in
  this project. `NarrationModel` is a swappable Protocol — `ClaudeHaikuModel`
  (real, lazy-imported `anthropic`, structured-output `messages.parse`) or
  `MockNarrationModel` (deterministic, every test in this package uses it).
  `explain` calls the model at most once — never at all when
  `bundle.low_evidence` already guarantees the answer — then a structural
  grounding validator (never a second LLM call) rejects any citation of an
  id not in the bundle or any "explanation" with no citations at all;
  every rejection, and the model's own honest decline, normalizes to the
  same fallback text and `LOW` confidence. Confidence otherwise reflects
  the WORST cited item's own. `explain_cached` caches by anomaly id and
  never regenerates. See docs/PHASE5.md's "Grounded narration" section for
  the full module map.
- `f1_api` why-engine integration (5.4) serves anomalies and explanations
  through the same read model everything else uses: `GET /sessions/{key}/
  anomalies` (salience-ranked, no prose), `GET /anomalies/{id}/explanation`
  (grounded text + cited evidence with lazy-fetch hrefs). The WebSocket
  emits lightweight `explanation` envelopes paced on each anomaly's own
  `anomaly_time`, remapped through the same Delay Sync wall-clock gate as
  every other envelope — an explanation cannot appear before the user's
  playback clock reaches its moment. Historical sessions precompute and
  cache; live sessions generate on anomaly against the same cache. See
  docs/PHASE5.md's "Why-engine API + timeline" section.
- Frontend why UI + verification (5.5): `$lib/why` + `ExplanationLane` on
  `/race` (Delay-Synced `visibleState` only) and `/story` (salience-ranked
  narrative with expandable cited evidence). `just verify-phase5` boots
  `f1_api` with `F1_API_WHY_FIXTURE` (`CitingNarrationModel`) and asserts
  grounded + fallback explanations, spoiler-safety, and the story view.
  See docs/PHASE5.md's "Frontend UI + verification" section.

## Later phases

See `CLAUDE.md` for phase 6 (narrow vision). It gets its own section here
once it starts.