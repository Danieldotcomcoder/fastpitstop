# Project: F1 Companion (working name: pitwall)

## What this is
A Formula 1 companion app that goes BEYOND displaying API data. Every public F1
dashboard stops at "render the feed." Our differentiator is a tested analytics
KERNEL that derives predictive state, plus novel datasets nobody else has, plus
a race that doesn't get spoiled. The pretty track map is table stakes — it is NOT
the product. Do not prioritize it.

The moat is: (a) a deterministic replay harness so we can develop against ~80
past races on any weekday instead of only during the ~24 live sessions per year,
and (b) an analytics kernel unit-tested against those replays.

## Phase map (built strictly in order; don't pull work forward)
Phases 0-5 are DONE and Phase 6 is deliberately not started. Work now is
DEPTH inside a finished phase (calibration, UI, live wiring), not a new
phase — so "don't pull work forward" still applies to Phase 6.
- Phase 0 (DONE): Replay harness. Record the live feed to disk, replay it at
  arbitrary speed, and acquire historical data. Produces NOTHING a user sees.
  Exit criteria + runbook: docs/PHASE0.md. `just verify-phase0` guards the
  recorder<->replayer contract in CI.
- Phase 1 (DONE): Ingest + store. Normalize the raw stream into domain events;
  persist to TimescaleDB. One producer, event-sourced, replay and live share one
  pipeline. See "Phase 1 status" below for the invariants.
- Phase 2 (DONE): Analytics kernel (the moat). Clean-lap filter, fuel
  correction, degradation fitting, undercut/overcut crossover prediction,
  braking-point drift. Pure functions, no UI, backtested against replays.
  Exit criteria + runbook: docs/PHASE2.md. `just verify-phase2` guards the
  release gate in CI.
- Phase 3 (DONE): Frontend. SvelteKit + Canvas, mobile-first. Killer feature = Delay Sync
  (hold data back to match the user's delayed TV broadcast so we never spoil).
- Phase 4: Team radio corpus. Transcribe driver-pit audio (faster-whisper),
  index it, make it searchable. A genuinely new dataset.
- Phase 5 (DONE): "Why" engine. On anomaly, fuse telemetry + radio + race
  control and ask an LLM for one grounded sentence. Exit criteria + runbook:
  docs/PHASE5.md. `just verify-phase5` guards the UI + Delay Sync contract.
- Phase 6: Vision, narrow. Wet-track state only. Runs client-side in a browser
  extension on the user's OWN licensed stream; no frame ever hits our servers.

## Tech decisions (do not relitigate)
- Ingest/backend/ML: Python 3.12, managed by uv (uv workspace).
- Frontend (later): TypeScript, SvelteKit, pnpm workspace. Canvas/WebGL for the map.
- Store (Phase 1): TimescaleDB (Postgres). No Kafka — we have one producer.
- Monorepo: polyglot. Python packages under python/*, TS under packages/* & apps/*.
- Task runner: justfile.

## Data sources (all free for our current needs)
- Live: F1's SignalR Live Timing feed (unofficial). Reference FastF1's
  `fastf1.livetiming` module for the connection details — do NOT reverse-engineer
  the SignalR negotiation from scratch, and verify topic names against FastF1's
  current source since the stream is unofficial and can change.
- Historical (2023+): FastF1 (cached) for laps/telemetry/weather; OpenF1 REST for
  intervals, race control, team-radio URLs. Free, no auth. OpenF1 free tier is
  3 req/s, 30 req/min — respect it.

## Hard architectural invariants
- The recording format is the CONTRACT between the recorder and the replayer.
  Both depend on the `f1_recording` package. Never let two components invent
  their own format.
- Phase 0 stores RAW messages only. Parsing raw → domain events is Phase 1. Do
  not decode into domain concepts in the recorder.
- Historical backfill produces CLEAN TABLES, not raw SignalR. It feeds the kernel
  directly (Phase 2). The adapter mapping historical → normalized-event schema is
  a Phase 1 task. Leave a documented seam; don't build it in Phase 0.

## Legal posture
Unofficial fan project. We run our own SignalR client. Any future vision runs
client-side on the user's own stream. State "unofficial, not associated with F1"
in the UI later. Don't build anything that ingests/stores broadcast video server-side.

## Conventions
- Full type hints; ruff + pyright clean. Tests with pytest.
- The core test discipline: kernel and pipeline logic is tested against recorded
  replay fixtures, not mocks. A feature isn't done until it's backtested on a replay.




## Phase 1 status: DONE

Phase 0 is complete: raw recording, faithful replay, historical parquet.
Phase 1 turns raw SignalR messages into a normalized, queryable domain model.

Phase 1 packages (under python/, in dependency order):
- f1_domain (1.1): the vocabulary — pure pydantic types and enums, zero logic,
  zero I/O. The `DomainEvent` discriminated union is to Phase 1 what
  f1_recording was to Phase 0: the contract everything later speaks.
- f1_parse (1.2): layer one — decode raw SignalR payloads into typed
  intermediate data. Stateless, one message in, structured data out. Depends
  on f1_recording (RawRecord, decode_z); emits NO domain events.
- f1_parse.state_machine (1.3): layer two — SessionStateMachine consumes the
  record stream via decode, folds deltas into SessionState, and emits
  DomainEvents on genuine transitions only. Idempotent per record and per
  value (the feed re-sends constantly); seeding from subscribe snapshots adopts them as the
  baseline; the FIRST seed also publishes the running order and roster once
  (on the next real record, keeping replay deterministic), while mid-session
  reconnect re-seeds stay silent. Backtested
  against a synthetic full-race recording driven through the real f1_replay
  path with exact ground truth (f1_parse/tests/race_sim.py); when the first
  real recording lands, add a sibling suite pinned to that race's results.
- f1_store (1.4): the TimescaleDB sink — append-only `events` log (source of
  truth) + typed materialized tables (laps, car_telemetry/car_positions
  hypertables, stints, pit_stops, race_control, weather, team_radio) +
  current-state (sessions, drivers). `EventSink` protocol with PostgresSink
  (batched COPY, idempotent via ON CONFLICT), Null/Memory sinks for tests.
  The DB is a sink, fully testable with no database present.
- f1_historical_adapter (1.5): the seam from Phase 0 — maps f1_backfill
  parquet onto the SAME DomainEvent stream the live pipeline emits
  (`events_from_parquet`, `provenance=HISTORICAL`), plus a recording-vs-
  historical comparison harness. One kernel code path over live + history.
- f1_pipeline (1.6): the wiring + verification. `run(records, sink,
  session_key)` is the one path both live and replay feed; `run_events` is
  the historical front; `Pipeline` is the push form for the live socket. The
  `f1-pipeline` CLI (replay / ingest-historical / live) wires each source to
  Postgres and logs per-run metrics (records in, events by kind, unknown-topic
  count = feed-drift early warning, rows written, wall time). `just
  verify-phase1` replays a checked-in race end-to-end and asserts against
  ground truth + determinism + idempotency (docs/PHASE1.md).
- (next) Phase 1 is functionally complete; remaining exit boxes need a real
  race weekend (live capture) and one long f1-backfill run per season. Then
  Phase 2 (the analytics kernel) builds on the tables f1_store fills.

Phase 1 invariants:
- LIVE and REPLAY feed the exact same pipeline. The pipeline takes an iterator of
  RawRecords and does not know or care where they came from. This is non-negotiable —
  if replay and live diverge, the whole Phase 0 investment is wasted.
- The pipeline is a PURE FUNCTION of the record stream: same recording in, byte-identical
  events out, every time. No wall-clock reads, no randomness, no network inside the
  transform. Determinism is what makes Phase 2 backtestable.
- Parsing is layered and each layer is separately testable:
    RawRecord -> decoded payload -> state mutation -> domain event
- The DB is a SINK, not a dependency of the transform. The normalizer must run and be
  fully testable with no database present.
- Store BOTH: the append-only event log (source of truth, replayable) and materialized
  current-state tables (fast to query). Never let materialized state be the only copy.


## Phase 2 status: DONE

Phase 1 is complete: normalized events + materialized state in TimescaleDB, one
pipeline for live/replay/historical. Phase 2 is the ANALYTICS KERNEL — the moat.
Everything public stops at "display the feed"; this derives predictive state.

Phase 2 invariants:
- The kernel is PURE FUNCTIONS over typed lap/stint frames. No DB access, no I/O,
  no network inside any analytic. You LOAD data (from f1_store or the historical
  adapter) then PASS frames in. Determinism is mandatory — same input, same output.
- Every analytic runs identically on live-derived and historical-derived data.
  Backtesting against 2023-2025 is the primary test method, not mocks.
- No hardcoded magic numbers. Fuel coefficient, pit loss, dirty-air threshold,
  compound offsets — all CALIBRATED from historical data and exposed as typed,
  overridable parameters with documented defaults and provenance.
- Every analytic returns a value AND a confidence/quality signal. A degradation
  fit from 4 dirty laps is not the same as one from 15 clean laps, and the output
  must say so. Downstream (UI, why-engine) decides whether to trust it.
- Use polars for the kernel dataframes (typed, fast). No pandas in kernel code.

Circuit metadata (pit loss, corner definitions, DRS zones) lives in a versioned
data file under python/f1_kernel/circuits/, calibrated where possible, never guessed.

Phase 2 packages (under python/, in dependency order):
- f1_kernel (2.1): the foundation — the data layer and contract every later
  analytic consumes. `schemas` declares three STABLE polars frame schemas
  (lap/stint/telemetry); `sources` loads the same DomainEvent stream from
  memory, backfill parquet or the TimescaleDB log behind one `EventSource`
  protocol (kind/driver filters are a pushdown, never a second code path);
  `frames` holds the pure event->frame transforms; `status` derives a track
  status timeline (the feed's own topic live, reconstructed from race
  control for historical); `params` is `KernelParams`, every tunable
  constant typed, frozen and provenance-marked; `circuit` loads per-circuit
  JSON whose corners/DRS zones/lap distance are DERIVED from real session
  data (only pit loss stays PROVISIONAL); `backtest` runs an analytic over a
  corpus and tabulates. Runbook: docs/PHASE2.md, schemas: DATA_CONTRACTS.md.
- f1_kernel.cleaning (2.2): the clean-lap filter. `annotate_laps` flags every
  lap with a boolean per reason (lap 1, in/out lap, SC/VSC/yellow/red, dirty
  air, robust outlier) plus `exclusion_reasons`; `clean_laps` is that
  filtered to `is_clean`. Nothing is silently dropped, and "unknown" (no gap
  channel, too few laps to estimate a bound) is reported rather than
  treated as clean.
- f1_kernel.fuel (2.3): fuel correction. `estimate_fuel_load_kg` is the fuel
  model (near-linear burn, race-length-derived burn rate); `fuel_correct`
  adds `lap_time_fuel_corrected_s`, a pure affine shift back to
  `params.fuel_reference_kg`. `fuel_coef_s_per_kg` is CALIBRATED — 0.0456
  s/kg from a cross-circuit meta-regression over 780 stints across 23 2024
  races (`tools/calibrate_fuel.py`; a naive within-stint regression against
  estimated fuel load is NOT identified, see `FuelStintEstimate`'s
  docstring for why and what breaks the collinearity).
- f1_kernel.degradation (2.4): degradation fitting. `fit_degradation` is a
  robust (Theil-Sen) fit of fuel-corrected lap time vs tyre age per
  (driver, stint, compound), with an optional Huber quadratic term and a
  `reliability` verdict (`good`/`thin`/`unreliable`) so a 4-lap fit is
  never shown as equal to a 15-lap one. `calibrate_compound_offsets` fits
  `compound_offset_s` the same paired-delta way; the 2024 backfill
  identifies HARD (now CALIBRATED, 0.36s) but flags MEDIUM as confounded by
  session-level drift that does not cancel between stints the way it
  cancels within one, so MEDIUM stays at its PROVISIONAL default rather
  than ship an implausible sign — see docs/PHASE2.md and
  `calibrate_compound_offsets`'s docstring for the full reasoning.
- f1_kernel.pit_loss + f1_kernel.undercut (2.5): the payoff. `pit_loss`
  measures what a green-flag stop actually costs — per stop,
  (in+out) - clean-neighbour baselines, fuel-corrected; median per circuit
  with MAD-based uncertainty; SC/drive-through/double-stops excluded and
  counted. 2024 gives 410 stops across 23 races, 18.8s (Spa) to 30.1s
  (Singapore); four shipped circuit files are now CALIBRATED and
  `default_pit_loss_s` is the cross-circuit median (23.03). `undercut`
  projects a duel's two fitted pace models over the stop stagger:
  `undercut_window` (pit now; scan the defender's response) returns
  crossover_lap, margins, window open/opens_in_N/closed/unknown and a
  confidence DERIVED from fit reliability + CI sign-stability;
  `overcut_window` is the mirror (deadline); `undercut_snapshot` is the
  per-lap live primitive (slice, REFIT on the slice, pair adjacent cars)
  Phase 3 renders as a countdown. Equal pit losses cancel in the
  after-both-stop margin (documented invariant + test) — pit loss instead
  drives the rejoin gap. Backtest (`undercut_eval` + committed report):
  predict at the lap BEFORE each real stop from sliced data only, score
  against who emerged ahead; 2024: 89.3% on high-confidence calls vs
  69.7% on low (calibration check PASS); 2025 held-out report committed
  (267 evaluated duels, 192 scored, 73.4% overall, 85.7% high vs 67.4%
  low over 10 backfilled rounds at generation time — re-run
  `tools/run_backtest_suite.py` as more of 2025 lands).
- f1_kernel.braking (2.6): braking-point drift — the thing the project
  originally thought needed computer vision, done from the brake channel
  every lap instead. `braking_points` takes one driver's telemetry frame
  plus circuit corner metadata and returns a frame (one row per lap x
  corner) of the sustained brake-application distance on approach to each
  corner: the trace is binned onto `params.braking_distance_bin_m` and only
  counts once it holds above `params.brake_on_threshold_pct` for
  `params.braking_min_sustained_m` (debounced against a stab), searched
  within `params.braking_approach_window_m` of the corner but never past
  the PREVIOUS corner's own distance (so a chicane's two zones can't bleed
  into each other); `known=False` reports too little telemetry to judge,
  distinct from `known=True` with no detection at all. `braking_drift` then
  fits, per (driver, stint, corner), a robust (Theil-Sen) trend of that
  distance against tyre age — the same method and the same reused
  `Reliability` verdict (`good`/`thin`/`unreliable`) `fit_degradation` uses
  for lap time, with tyre age derived identically
  (`lap_number - start_lap + 1 + tyre_age_at_start`) so the two analytics
  never disagree on stint age. A positive `drift_m_per_lap` is braking
  creeping EARLIER as tyres/brakes fade, the expected direction on a
  high-deg stint. All braking params are STRUCTURAL/PROVISIONAL — debounce
  floors and search-window caps, not yet calibrated against a measured
  spread of real driver behaviour. Tested three ways (`test_braking.py`):
  a checked-in synthetic end-to-end test (a planted per-lap earlier-braking
  trend driven through both functions, direction asserted on aggregate),
  corner-window unit tests (a chicane's zones must not leak into each
  other; detection lands near a real shipped circuit's corner), and an
  opt-in real-backfill version over a driver's longest stint that skips
  rather than fails when local telemetry gives too little reliable
  evidence to judge.
- f1_kernel.report (2.7): the kernel API + backtest suite + verification —
  the release milestone. `analyze_session(source, session_key, params)`
  chains every analytic in the documented order (clean -> fuel-correct ->
  degradation -> crossover -> braking-drift) into one typed `KernelReport`,
  so a caller no longer has to re-derive the wiring `f1_kernel.undercut_eval`
  and every `tools/backtest_*.py` script had hand-rolled for itself;
  `analyze_session_from_frames` is the pure core underneath it ("pure over
  loaded frames," per this prompt), and `LiveKernelSession` is the live
  variant — a growing `DomainEvent` stream, one `push()` per lap update,
  each call a pure recomputation over everything accumulated so far. Every
  `KernelReport` field that is itself an analytic's output carries that
  analytic's own confidence/reliability signal; the report assembles
  those, it invents no new number. `tools/run_backtest_suite.py` runs the
  fuel/pit-loss/degradation/undercut calibration-and-backtest tools in one
  pass and refreshes every committed report (`reports/*.md`,
  `calibration/*.json`); left to each tool's own default seasons, so
  fuel/pit-loss/degradation stay 2023-2025 while the undercut backtest
  stays 2025-only — the season it deliberately holds out. `just
  verify-phase2` runs `analyze_session` over the checked-in fixture and
  asserts the release-gate exit criteria: no crash, the clean-lap filter
  against the fixture's ground truth, CALIBRATED fuel/pit-loss provenance,
  and the two committed backtest reports still passing their own
  directional-sanity and calibration checks — checking the COMMITTED
  reports rather than recomputing them live is what keeps this green with
  no data, the same three-layer discipline the rest of Phase 2 follows.
  Runs green in CI with no data, no database and no network.
- (next) Phase 2's release gate is met (see docs/PHASE2.md's "Exit
  criteria", all boxes checked). What remains is calibration DEPTH, not
  correctness: most braking params and several degradation/undercut
  params are still PROVISIONAL/STRUCTURAL rather than CALIBRATED.

Testing discipline for Phase 2 (three layers, all of them required):
- a CHECKED-IN synthetic historical fixture with exact ground truth
  (python/f1_kernel/tests/fixtures/) so CI is green with no data, no DB and
  no network — `data/` is gitignored and real F1 data stays out of the repo;
- a LIVE-provenance suite driving the Phase 1 recording through the real
  pipeline into the kernel, so the frames are provably source-agnostic;
- OPT-IN real-data tests over `data/historical/` that assert known facts
  about specific 2024 races, skipped when the backfill is absent.


## Phase 3 status: DONE

Phases 0-2 complete: recording/replay, normalized store, calibrated analytics kernel
(undercut predictor cleared its milestone). Phase 3 is the FRONTEND — the first thing a
user sees. It renders live and replayed races and must never spoil the race the user is
watching on a delayed broadcast.

Phase 3 invariants:
- The frontend consumes the API ONLY. It never touches TimescaleDB or imports the kernel
  directly. The API is a read model; the kernel stays pure Python.
- Live and replay drive the SAME frontend through the SAME API shape. Dev happens against
  replayed historical sessions — no live race required, exactly as Phase 0 intended.
- SPOILER-SAFETY IS AN INVARIANT. Every timing value, position, AND derived kernel snapshot
  (undercut countdown, deg state) is gated behind the user's playback clock. Nothing whose
  event-time is ahead of (now − user_delay) may ever reach the screen. This is testable
  pure logic — treat a spoiler leak as a failing test, not a cosmetic bug.
- Anything with 20 moving cars renders on Canvas/WebGL, not SVG/DOM. Mobile-first: most
  users second-screen on a phone next to the TV.
- Unofficial fan project. The UI states "unofficial · not associated with Formula 1" and
  uses no F1/team marks or broadcast video. Team colors are fine; logos are not.
- Frontend stack: SvelteKit + TypeScript, pnpm workspace (scaffolded empty in Phase 0).
  Follow the frontend-design skill for visual system and tokens.
- Every kernel value shown carries its confidence/reliability from Phase 2. A "thin" fit is
  visually distinct from a "good" one — never present false precision.

Phase 3 packages (under python/ and packages/, in dependency order):
- f1_api (3.1): the read model — the ONLY thing the frontend talks to,
  bridging pure-Python `f1_kernel`/`f1_store` to the (not yet built)
  SvelteKit frontend. `sessions` resolves a `SessionKey` to whichever
  backing source has it — `HistoricalSource` (backfill parquet, always
  available) or, when `F1_STORE_DSN` is configured and reachable,
  `StoreSource` (live/in-progress sessions win over a historical entry at
  the same key) — so every route stays agnostic about where a session
  actually lives, the same "DB is a sink, never a dependency" discipline
  Phase 1/2 already apply. REST (`routes/sessions.py`): `GET /sessions`,
  `/sessions/{key}`, `/laps` (`annotate_laps`, nothing silently dropped),
  `/degradation`, `/analysis` (the full `KernelReport`, minus the per-lap
  frames `/laps` already serves — see `KernelReportOut`'s own docstring for
  why), `/telemetry/{driver}` (the braking-point frame, not raw telemetry —
  Phase 2.6's novel dataset, degrading to `[]` rather than 404ing on thin
  data, matching `braking_points`' own discipline). `schemas.py` mirrors
  every kernel dataclass/frame field-for-field ("get the response contracts
  right first"); `convert.py` reshapes kernel output into them and computes
  nothing of its own. The WebSocket (`stream/`): `/stream/{key}?mode=replay
  &speed=N` re-paces a session's own `DomainEvent` stream back out as
  `{event_time_utc, kind, payload}` envelopes using `f1_replay`'s OWN timing
  model (`pacing_offset_s`/`sleep_until`, made public from `f1_replay` for
  exactly this reuse) — the replay-harness moat applied to the WebSocket,
  not just the REST reads. `LapSnapshotFeed` drives a `LiveKernelSession`
  batched ONCE PER RACE LAP (never once per raw event — a lap fans out into
  a `lap_completed` per driver plus dozens of telemetry samples, and
  `push`'s whole-kernel recompute is only cheap at the lap cadence its own
  docs describe) and emits a `kernel_snapshot` envelope carrying
  `undercut_snapshot`'s crossovers whenever a fresh lap produced one.
  `mode=live` polls the store the same way instead — thinner by design,
  matching docs/PHASE0.md's own reasoning for why the live connection layer
  stays outside the unit-tested surface. `openapi.json` is committed
  (`tools/export_openapi.py` regenerates it) — the frontend's typed-client
  source and Phase 3's actual interface contract. Tested three ways: every
  REST route and the WS replay path run against the SAME checked-in
  synthetic historical fixture `f1_kernel`'s own tests use (one fixture
  race, not a second one invented here), plus a schema-drift test that
  fails whenever the committed `openapi.json` stops matching the live
  models.
- apps/web (3.2): the frontend scaffold — SvelteKit + TypeScript, added to
  the pnpm workspace (empty since Phase 0), plus the design system and the
  typed data path everything later renders through. Deliberately NOT
  dark-mode + team-colors + F1-font (the templated look every public F1
  dashboard already has): a paper-toned light theme by default with a
  `prefers-color-scheme`/manual-override dark mode, a serif display face for
  an editorial feel, and tabular monospace figures for anything timed —
  "instrument panel," not gamer HUD — all as CSS custom properties in
  `src/lib/styles/tokens.css`. Four primitives (`Panel`, `Stat`,
  `ConfidenceBadge`, `LoadingState`) plus the `Footer` disclaimer marker are
  the only components; `ConfidenceBadge` already handles both taxonomies the
  kernel emits (`Reliability`'s good/thin/unreliable and `Confidence`'s
  high/medium/low) since Phase 3's own invariant ("a thin fit is visually
  distinct from a good one") has to hold the moment any kernel value
  reaches a screen. The typed REST client (`src/lib/api/client.ts`) wraps
  `openapi-fetch` around types GENERATED from the committed
  `python/f1_api/openapi.json` (`pnpm generate-client` /
  `just web-generate-client`) rather than hand-typed, so a backend
  response-model change surfaces as a TS error at every affected call site —
  verified by temporarily renaming a field in `f1_api.schemas`, regenerating
  both files, and confirming `svelte-check` fails at the exact call site,
  then reverting. `src/lib/api/stream.ts` is a thin `SessionStream` wrapper
  over `/stream/{key}` — connects, parses the `{event_time_utc, kind,
  payload}` envelopes, republishes them to subscribers — and is deliberately
  dumb (no buffering/ordering) since Delay Sync is 3.3's job, built on top of
  this, not inside it; hand-typed rather than generated because FastAPI does
  not emit WebSocket routes into `openapi.json`. The one smoke route (`/`)
  fetches `GET /sessions` and lists them, proving the full path (SvelteKit ->
  typed client -> f1_api -> store) end-to-end against the checked-in kernel
  fixture. `just web-dev` / `web-build` / `web-check` / `web-generate-client` /
  `web-test` are the five tasks; `adapter-static` in SPA-fallback mode
  (`ssr = false` root-wide) since the frontend only ever talks to `f1_api`
  over fetch/WebSocket and never needs a Node server of its own.
- Delay Sync (3.3): the killer feature — CLAUDE.md's spoiler-safety
  invariant as one small, framework-agnostic TS module
  (`apps/web/src/lib/delay-sync/`), not buried in a Svelte component.
  `TimelineBuffer` is the whole guarantee in one method: `ingest` keeps
  every arriving envelope sorted by `event_time_utc`; `release(playbackClockMs)`
  hands back — and only ever hands back, exactly once each — the ones at or
  before that clock, idempotent and pure with respect to the clock it's
  given. `playbackClockMs(wallNowMs, delaySeconds)` is the one line of
  arithmetic behind "wall time minus user delay" (delay in SECONDS,
  matching `f1_replay`'s own `--delay` and `f1_api`'s `pacing_offset_s` —
  the same unit everywhere in this project). `visible-state.ts`'s
  `applyReleasedEnvelope`/`applyReleasedEnvelopes` is a pure reducer that
  ONLY ever runs over what `release` already returned — there is no
  constructor path from raw/pending envelopes to `VisibleSessionState`,
  which is the one thing any view is allowed to subscribe to; it stays
  deliberately generic (an ordered event log plus a `latestSnapshot`
  pointer for `kernel_snapshot` envelopes) rather than modeling full race
  state, since that per-domain-event reducer is the actual race views'
  job, still not built. `store.ts`'s `Store` is a hand-rolled observable
  (not `svelte/store`) so the whole module stays importable and testable
  with zero Svelte runtime — its `subscribe` still satisfies Svelte's own
  store contract, so `$store` auto-subscription works unmodified wherever
  a `.svelte` file wants it. `DelaySyncController` (`controller.ts`) is the
  seam a view actually talks to: `ingest`/`tick` drive the buffer and fold
  releases into `visibleState`; `setDelaySeconds` (the slider) and
  `calibrateFromTap` (`calibration.ts`'s `delaySecondsFromTap` — "tap when
  the lights go out," delay = tap wall-time minus the known event's own
  `event_time_utc`) both clamp into `[minDelaySeconds, maxDelaySeconds]`
  and mark `delayState.synced = true`, the "so the user trusts it"
  indicator's other half. Three Svelte components sit on top —
  `DelaySlider`, `TapToSync`, `DelayIndicator` — each a thin view over
  `controller.delayState`/`controller.setDelaySeconds`, no logic of their
  own; `routes/delay-sync/+page.svelte` is Delay Sync's own dev harness
  (proves `SessionStream` -> `controller.ingest` -> `tick` ->
  `visibleState` end-to-end against a real replayed session, the same way
  `/` proves the REST path), not a race view — no track map, no
  degradation/undercut panel. Tested as pure logic with **no DOM**
  (`vitest`, wired as `just web-test`): a seeded-PRNG property test across
  200 randomized trials (random envelope counts, random arrival order,
  random non-monotonic clock schedules) asserting zero leaks — no
  released envelope's `event_time_utc` ever exceeds the clock it was
  released at; a dedicated test that a high-confidence `kernel_snapshot`
  with a future `event_time_utc` is withheld right up to (but never
  before) its own boundary; `calibrateFromTap`'s math against a simulated
  tap; and buffer/controller determinism given a fixed clock sequence.
- Track map (3.4): the first race view — table stakes visually (per this
  file's own "do not prioritize it"), so the differentiation is entirely in
  the DATA painted onto it, not in the map existing. `f1_kernel.
  track_outline.derive_track_outline` is the build-step half: a pure
  transform from one lap's REAL (x, y) position samples (the feed's
  `car_position` channel, or FastF1's matching telemetry — never a
  hand-drawn SVG) into a `TrackOutline` — evenly resampled by arc length,
  but deliberately left in the SAME raw coordinate units the input arrived
  in rather than centered/rescaled, so a live `car_position` sample plots
  directly alongside it with no invented transform; fitting the shape to a
  screen is the frontend's job, done once per render and shared between the
  outline and every car marker. `tools/build_circuits.py` gained the
  calling half, deriving it from the same FastF1 reference lap already
  sampled for corners/DRS zones; `Circuit.outline` caches the result,
  `None` until that tool next runs against a populated FastF1 cache. As of
  2026-09-23 every venue on the calendar ships one (24 from 2024 reference
  races, Madring from the 2026 race itself, which FastF1 has no corner list
  for yet, so it ships without corners rather than guessed ones). Each file
  also records `feed_circuit_names` (the feed's `Meeting.Circuit.ShortName`),
  and `circuit_for_session` prefers it over the Grand Prix name, which is
  not an identity: the "Spanish Grand Prix" moved from Barcelona to Madrid
  in 2026. Live sessions carry it via the `SessionInfoUpdated` event. `f1_api` gained
  `GET /sessions/{key}/circuit` (`CircuitOut`, mirroring corners/DRS
  zones/outline field-for-field) — the one new backend seam this prompt
  needed, since the frontend cannot import the kernel directly. On the
  frontend, `apps/web/src/lib/track-map/` is framework-agnostic pure TS
  (matching Delay Sync's own split): `car-state.ts` folds `visibleState`'s
  event log into per-driver markers (position, team, compound) — the
  per-domain-event reducer Delay Sync's own `visible-state.ts` explicitly
  left to "the actual race views' job"; `interpolation.ts` smooths motion
  between position samples (lerp, then hold at the latest known point
  rather than extrapolate a car off its own track); `arc-length.ts`
  projects a car's (x, y) onto the outline as a lap fraction; `dominance.ts`
  is the "shape" feature — a `DominanceTracker` that measures each driver's
  transit time through N equal arc-length segments purely from consecutive
  position samples (no telemetry/distance channel needed, since our domain
  events carry no spatial sector-boundary data) and keeps whichever driver
  currently holds the fastest ("purple") time per segment, colored by team;
  `frame.ts`'s `buildDrawFrame` resolves all of the above plus team colors
  into a plain drawable shape with NO canvas dependency, so
  `renderer.ts`'s `TrackMapRenderer` stays "draw what you're told" — a
  single full-clear-and-redraw Canvas2D pass (cheap enough at this shape
  count that dirty-region tracking would cost more than it saves).
  `TrackMap.svelte` is the thin glue: canvas sizing via `ResizeObserver`,
  an rAF render loop, and `prefers-reduced-motion` (skip the interpolation
  tween and redraw only when new data actually arrives). Tested as pure
  logic with no DOM (90 vitest cases covering arc-length projection,
  interpolation clamping/staleness, the car-state reducer, dominance
  crossover/wraparound math, team-color lookup and frame-building) plus a
  real dev harness (`/track-map`) proving the whole pipeline in a browser —
  since no environment here has a real live/store-backed session with
  position data (historical/backfill sessions never carry `car_position` at
  all, per DATA_CONTRACTS.md), that harness's default "synthetic preview"
  mode drives three cars around a stand-in oval through the SAME
  `DelaySyncController`/`TrackMap` pipeline a real session would use,
  clearly labeled as synthetic; selecting a real session instead proves the
  documented graceful-degradation state ("no track outline yet") rather
  than a blank or broken canvas.
- Kernel-analytics views (3.5): the other three race views, matching Track
  map's own split — framework-agnostic pure TS
  (`apps/web/src/lib/kernel-views/`) plus thin Svelte components. Two need
  no REST call and no second spoiler gate at all: `UndercutCountdown.svelte`
  reads `visibleState.latestSnapshot` directly (the WS's own
  `kernel_snapshot` envelope, already released through Delay Sync like any
  other), rendering the focused battle's window state, a margin/rejoin-gap/
  decision-lap/pit-loss stat row, and a small SVG confidence-interval bar
  (95% band, point estimate, a parity line — never a bare number);
  `GapStreamGraph.svelte` over `gap-stream.ts`'s `GapStreamReducer` folds
  the raw `gap_updated` stream (not `/laps`' once-per-lap median) into
  per-driver time series, Canvas-rendered (`gap-stream-renderer.ts`) as
  thick team-colored bands rather than a literal stacked streamgraph (each
  driver's gap is independent, not a decomposition of a whole) since a full
  race reaches the low thousands of samples per driver. The other two —
  `DegradationCurves.svelte` (`/degradation`) and `BrakingPointDrift.svelte`
  (`/telemetry/{driver}` + `/analysis?braking_drivers=`) — describe a WHOLE
  session/stint up front, so they need a second gate: `reveal.ts`'s
  `RevealTracker` folds `visibleState.events` (already delay-gated) into
  "which stints have provably finished" (the driver started a LATER stint,
  or the session itself did) and "which laps have provably completed," and
  a fit/row only renders once its own check says yes — deciding WHEN an
  already-fetched value may show, never recomputing anything. Both render
  as SVG with a `ConfidenceBadge` per fit and dashed strokes below `good`
  reliability. `linear-fit.ts` (curve sampling/crossover math) and
  `iso-duration.ts` (`parseIsoDurationSeconds`, needed because the
  WebSocket serializes `DomainEvent`s verbatim while REST already
  normalizes `timedelta` fields to floats) are the shared primitives
  underneath. `routes/analytics/+page.svelte` is the dev harness — all four
  views against one replayed session, mobile-first stacked. Tested as pure
  logic with no DOM: the gap-stream reducer, `RevealTracker`'s stint/lap
  reveal transitions, `linear-fit`'s math, `iso-duration`'s parser.
- Dashboard assembly + verification (3.6): composes every view above into
  `/race` — the actual product, not another isolated dev harness. The one
  new view is `$lib/timing-tower/`: `TimingTowerReducer` folds
  `position_changed`/`gap_updated`/`lap_completed`/`stint_started`/
  `pit_entry`/`pit_exit`/`driver_list_updated` into one `DriverRow` per
  driver (never regressing a known field back to unknown on a sparser
  later update), `sortedRows` orders by classification position with
  undecided drivers sorted after every classified one, and
  `TimingTower.svelte` renders it as a scrollable table with a compound
  badge, tyre age, an in-pit highlight and a personal-best highlight.
  `/race` composes it with the undercut countdown, the track map, and a
  SWITCHABLE (tabbed) analytics panel — only the active tab mounts, so
  switching away tears down its own REST fetch/canvas loop — plus the
  session/speed picker (the one harness where speed is a real control, not
  hardcoded, since this prompt's own "demoable on a weekday" needs it) and
  Delay Sync's controls reused as-is. Mobile-first by DOM order (tower ->
  undercut -> map -> analytics, the only layout below 900px); a
  `.wide-dashboard` class opts into a wider CSS-grid layout above that via
  `main:has(> :global(.wide-dashboard))` in the root layout, so every other
  route's narrower reading column is untouched. Session/speed round-trip
  into the URL (`resolve('/race?...')`, `replaceState`) so a demo is
  linkable and Playwright can drive it directly. Building the Playwright
  suite surfaced a real Phase 3.1 gap: `f1_api.stream.replay` forwarded a
  replayed event's own historical `ts_utc` verbatim as `event_time_utc`,
  which — compared against real wall-clock time by Delay Sync's own gate —
  released every envelope instantly regardless of the chosen delay for any
  non-live session. Fixed by remapping `event_time_utc` to the real
  wall-clock instant each envelope is actually sent (the same offset
  `sleep_until` already paces by), with `domain_event_envelope` staying a
  straight passthrough for genuinely-live events (`mode=live`). Verified by
  `just verify-phase3` (`apps/web/playwright.config.ts` + `apps/web/e2e/`):
  Playwright boots a real `f1_api` and a real `vite dev` against the SAME
  checked-in kernel fixture and drives a real Chromium through the
  dashboard — a full session loads with no crash; the tower/undercut/map
  panels render real data; the analytics tabs actually switch; and, the
  spoiler-safety assertion, the dashboard's own rendered sync-diagnostics
  readout is sampled before AND after the configured delay elapses,
  asserting the released event log is never ahead of the playback clock at
  either sample. The track map's own animation can't be proven against any
  real session in this repo (no historical session ever carries
  `car_position` — DATA_CONTRACTS.md), so a separate spec instead drives
  `/track-map`'s existing synthetic preview and diffs two canvas
  screenshots — keeping the real dashboard honest (a real session never
  shows fabricated movement) rather than adding a second synthetic-data
  path to it. `just verify-phase3` is wired into `just check`.
- Race desk (2026-09-23): `RaceWorkspace` (the one component `/live` and
  `/sessions/[year]/[round]` both render) is the supplied design
  (pitwall-race-dashboard.html) on real data. `$lib/desk/` holds its pure
  helpers (focus pair, pit-board view model over the kernel crossover,
  snapshot history for the sparkline, lap records, race feed; unit-tested)
  and its panels; `$lib/styles/desk.css` is the design's stylesheet scoped
  under `.desk-page`. Every panel reads Delay Sync's released state; whole-
  session REST reads stay behind the reveal tracker, so braking drift shows
  the latest COMPLETED stint, not the design's "current stint". The map
  places cars from lap timing when the feed has no measured positions,
  scaled by replay speed, and says which it is doing.
- Measured positions (2026-09-24): replays now draw F1's own car positions
  (the static archive's `Position.z`, ~4 Hz per car) instead of estimating.
  `f1_backfill` keeps them as `car_positions` (retrofit: `f1-backfill
  --positions-only`); `f1_kernel.positions` serves them as a columnar
  POSITION TRACK (a race is ~640k samples, never one event each); the WS
  sends one `car_positions` frame per feed instant, paced through Delay
  Sync like everything else; Delay Sync keeps released frames in a bounded
  `positionFrames` window (not the event log); `MeasuredPositions` draws a
  moment behind the clock and interpolates between real samples. The live
  feed withholds positions from our unauthenticated client, so live
  sessions stay estimated; the `positions` service (`f1-pipeline
  archive-positions --watch`) imports them from F1's archive after each
  session, and the replay shifts them onto the session's own clock
  (`session_clock_offset`: 2.4 s on the 2026 Spanish GP, whose timing was
  stamped on receipt). 2026-09-25, measured rather than eyeballed: each
  circuit file carries a `pit_lane` (median of real stops,
  `tools/build_pit_lanes.py`), drawn under the racing line; the stream
  drops samples ~150 m from both lines (`near_lines`: a placeholder every
  car reported 1.5 km away in 2025 Jeddah FP1); `MeasuredPositions`
  smooths each car's samples in time (F1 stamps them loosely, which made
  cars surge and stall), draws a FIXED distance behind the clock for the
  speed, and never glides across a gap; `DeskMap` draws on the animation
  frame's own timestamp. Geometry is checked per session
  (`f1_api.geometry.session_positions`, shared by the stream and
  `/circuit`): when the circuit file's lines miss the session's own
  positions by more than 8 m at the 90th percentile, both lines are derived from the
  session itself (fastest clean laps, its own stops) — 2025 Suzuka's frame
  differs from 2024's by 13-30 m, beyond any rigid fit.
  `deploy/check_outline_fit.py` measures dots against the drawn lines per
  session.
- (next) Phase 3's one remaining gap is the live SignalR->API wiring —
  `mode=live` only polls today and is exercisable only during a real live
  session, the same reality `f1_ingest`'s own connection layer lives with.
  Not pulled forward by this prompt, per CLAUDE.md's own "build strictly in
  order." Phase 4 (team radio corpus) is next.


  ## Phase 4 status: DONE

Phases 0-3 complete: replay harness, normalized store, calibrated kernel, spoiler-safe
frontend. Phase 4 builds the TEAM RADIO CORPUS — transcribe driver-pit audio, index it,
make it searchable. It's a genuinely new dataset and the primary input for Phase 5's
why-engine.

Phase 4 invariants:
- Transcription runs OFFLINE/BATCH over historical audio, cached to disk. It is NOT in the
  live hot path. Clips are transcribed once and stored; never re-transcribed on request.
- The TeamRadioClip event (driver, audio_url, captured_at) already exists from Phase 1 —
  Phase 4 consumes it, adding transcripts + embeddings as a SEPARATE store keyed to it.
- Transcripts are DERIVED DATA with quality signals (confidence, language). A low-confidence
  transcript is labeled, never presented as certain — same honesty contract as the kernel.
- Search is served through f1_api like everything else; the frontend never touches the
  transcript store or an embedding model directly.
- Radio, aligned to the timeline, passes the SAME Delay Sync gate as everything else. A radio
  clip's captured_at cannot surface ahead of the user's playback clock. No spoilers via audio.
- Everything runs on the ~$40/mo budget: local faster-whisper (no per-minute API cost), local
  embeddings, a vector index that runs on the existing Postgres (pgvector) — no new paid service.
- Audio is fetched from the URLs already in the feed; we store transcripts and embeddings, and
  cache audio only as needed for processing. Stay within the unofficial-fan-use posture.

Exit criteria + runbook: docs/PHASE4.md.

Phase 4 packages (under python/f1_radio, in dependency order):
- f1_radio acquisition (4.1): `discovery` reads a season's `TeamRadioClip`
  events straight from `f1-backfill`'s own catalog via
  `f1_historical_adapter.events_from_parquet` (the one existing seam, not a
  second parquet reader); `cache`/`fetcher`/`manifest` download each clip's
  audio into a deterministically-keyed local cache
  (`data/radio_audio/<season>/<round>_<type>/<driver>_<timestamp>.<ext>`),
  idempotent and resumable (the manifest is saved after every clip, not
  only at the end) and rate-limited to OpenF1's own free-tier budget;
  `domain.radio_clip` joins one event to its cached-audio path and (once
  4.2 exists) its transcript. `f1_store/migrations/002_radio_transcripts.sql`
  is the SEPARATE `radio_transcripts` table this phase writes into, applied
  by the existing `f1-store-migrate` mechanism. `f1-radio fetch --season Y`.
- f1_radio.transcription + transcribe_batch (4.2): local batch
  transcription via `faster-whisper` (CPU-friendly, no per-minute cost).
  `transcribe_clip` wraps one model call into full text, per-segment
  timestamps/confidence, detected language, and an overall confidence
  derived from segment-level `avg_logprob`; the hallucination guard is
  faster-whisper's own VAD pre-filter (`vad_filter=True`) stripping
  silence/noise to zero segments BEFORE the decoder ever sees them, so a
  silent/noisy clip is flagged `no_speech_detected` rather than decoded into
  invented text — proven against a REAL model and checked-in SYNTHETIC audio
  fixtures (Windows-SAPI TTS speech clips + generated silence/noise, never
  real F1 broadcast audio), not only a fake backend. Language is never
  forced to English — auto-detected and transcribed in the source language,
  labeled. `transcribe_batch.run_batch` is idempotent (skips clips that
  already have a transcript row), resumable (skip state comes from the
  store's own current contents, not run-local bookkeeping) and safely
  parallelizable (`--workers N`, a thread pool; the claim is the store's own
  write — `INSERT ... ON CONFLICT DO NOTHING` against the real UNIQUE
  CONSTRAINT, or `MemoryTranscriptStore`'s lock in tests — so two workers
  racing the same clip never double-write, only waste redundant model time).
  `f1_radio.store` gained `TranscriptStore` (`PostgresTranscriptStore` /
  `MemoryTranscriptStore`) — the same "DB is a sink, fully testable with no
  database present" split `f1_store` itself makes. `quality_report` is a
  pure function over an already-loaded season's transcripts (count,
  confidence distribution, flagged low-confidence clips, language
  breakdown) rendered as the same kind of `reports/*.md` document
  `f1_kernel`'s calibration tools produce — not committed yet, since it
  needs a real fetched-and-transcribed season this repo doesn't ship.
  `f1-radio transcribe --season Y [--workers N]`,
  `f1-radio quality-report --season Y`.
- f1_radio semantic index (4.3): local embeddings + a searchable pgvector
  index over the transcript corpus, enriched with race context via a join
  back to Phase 1's state. `f1_radio.embeddings` loads a local
  `sentence-transformers` model (no paid API, per this project's budget
  invariant) the same lazy-import-behind-a-Protocol way `transcription.py`
  loads faster-whisper; `embedding_model_version` is recorded per row, the
  same provenance discipline `transcription.model_version` already gives
  transcripts. `f1_radio.context.SessionContext` is the race-context
  half — built ONCE per session from its full `DomainEvent` stream and
  reusing `f1_kernel.status.track_status_timeline`/`status_during` (the
  same SC/VSC/yellow reconstruction the clean-lap filter depends on for
  historical sessions) — answering every clip's lap-in-progress,
  classification position, and track status at `captured_at`.
  `f1_radio.index_batch.run_index_batch` backfills both onto each clip's
  existing `radio_transcripts` row via a new `TranscriptStore.
  set_embedding`, mirroring `transcribe_batch.run_batch`'s idempotent
  (skip a clip whose stored `embedding_model_version` already matches) and
  resumable (skip decisions read fresh from the store every call) shape;
  `f1_store/migrations/003_radio_embeddings.sql` adds the embedding +
  context columns straight onto `radio_transcripts` (still the one
  SEPARATE store from `team_radio` this phase's invariant asks for, just
  extended rather than duplicated) and needs a pgvector-capable Postgres
  (`docker-compose.yml` now uses the `timescaledb-ha` image). `f1_radio.
  search` is the query side: `search_transcripts` embeds a query (skipped
  for a blank one — pure structured/keyword search) and delegates to a
  `SearchIndex` — `PostgresSearchIndex` ranks server-side by pgvector's
  `<=>` operator, `MemorySearchIndex` does the identical ranking in plain
  Python, the same Postgres/Memory split `TranscriptStore` already makes,
  so the semantic-ranking and filter-composition logic (driver, session,
  lap, track status, date range, keyword — no `team` filter, since a clip
  carries a driver number and this package has no team roster) has a real
  unit test with no database. `f1-radio index --season Y` /
  `f1-radio search "query" [--driver ...]` are the CLI. Tested three ways
  matching this project's own discipline: a `FakeEmbeddingBackend` whose
  vectors the test plants directly proves a paraphrase with NO lexical
  overlap ranks first and that filters exclude a closer-but-wrong-driver
  match; an opt-in real-local-model test (`F1_RADIO_RUN_MODEL_TESTS=1`,
  same gate `test_real_model.py` already set for faster-whisper) proves
  the same claim against a genuine model; DB-gated tests
  (`F1_STORE_RUN_DB_TESTS=1`) prove the pgvector column round-trips with
  no custom psycopg type adapter (`f1_radio.store.vector_literal` writes/
  reads it as plain text) and that `<=>` is usable against it for real.
  No HTTP endpoint and no UI yet — deliberately out of scope per this
  phase's own invariant that search is served through `f1_api` like
  everything else, not touched directly by a frontend.
- f1_api radio integration (4.4): the corpus joins the product.
  `GET /radio/search?q=&driver=&session=&from=&to=` returns ranked,
  context-enriched results (`f1_radio.search_transcripts` reshaped, nothing
  recomputed); `GET /sessions/{key}/radio` lists every clip for one session,
  time-ordered, joined to its transcript if one exists yet; `GET
  /radio/{clip_id}` returns one clip's transcript, segments, context, and
  playable `audio_url`. `f1_api.radio` is the new seam — unlike
  `f1_api.sessions`' plain functions (testable because `HistoricalSource`
  needs no database), `get_radio_search_index`/`get_radio_store`/
  `get_embedding_backend_factory` are real FastAPI `Depends` providers,
  because the radio corpus has NO DB-free real backing at all; tests swap in
  a `MemorySearchIndex`/`MemoryTranscriptStore`/fake embedding backend via
  `app.dependency_overrides`, the same lever `main.py` already uses for
  `get_settings`. `get_embedding_backend_factory` resolves to the loader
  FUNCTION, uncalled, so resolving the dependency is always cheap — only a
  non-blank query against a configured corpus actually loads the real
  `sentence-transformers` model. `clip_id_path`/`parse_clip_id` encode
  `(session_key, driver, captured_at)` into one opaque, percent-encoded,
  `~`-joined path segment, since `radio_transcripts` has no own primary key
  to reuse. `RadioClipOut` joins a clip's `audio_url` (from the originating
  `TeamRadioClip` event — a transcript row itself only ever carries a local
  `audio_path`) to whatever transcript exists; `has_transcript=False` with
  every transcript field `None` is the normal "fetched, not yet transcribed"
  state, never an error. Timeline integration
  (`f1_api.stream.replay.radio_clip_envelope`) enriches the `team_radio_clip`
  event already riding `/stream/{key}` (it was in `DEFAULT_STREAM_KINDS`
  since 3.1) with its joined transcript, under a new `"radio_clip"` kind;
  the one deliberate pacing change is that a radio clip's `elapsed_s` is
  measured from `captured_at` (the moment the exchange actually happened),
  not `ts_utc` (the moment the pipeline observed it) — `_event_pacing_ts` —
  since `captured_at` is the instant this phase's own spoiler-safety
  invariant is about; it still goes through the exact same wall-clock remap
  every other envelope uses (`envelope_for_event`, shared by
  `replay_envelopes` and `mode=live`'s polling loop), so it is exactly as
  spoiler-safe. Tested against `Memory*` fakes with no database (REST) and a
  synthetic `MemorySource` session (streaming/pacing) — the checked-in
  kernel fixture ships no `team_radio_clip` events at all, the same "no
  DB-free real corpus" reality this module's own docstring documents.
- f1_api radio_fixture + frontend radio UI (4.5): the corpus becomes
  something a user actually sees, and its own Phase 4 exit gate closes.
  `apps/web/src/lib/radio/` is the shared pure-TS core two surfaces both
  render from — `view.ts`'s `RadioClipView` is the one shape a live
  `radio_clip` envelope and a REST `RadioClipOut` both convert into
  (`radioClipViewFromEnvelope`/`radioClipViewFromApi`), `confidenceTier`'s
  three-tier badge floor matches `f1_radio.transcription.
  DEFAULT_LOW_CONFIDENCE_THRESHOLD` exactly, `lane.ts`'s `RadioLaneReducer`
  folds `radio_clip` envelopes (already spoiler-safe — a clip's own pacing
  is gated on `captured_at`, so no separate reveal gate is needed, the same
  reasoning the gap-stream/undercut views already give for skipping
  `RevealTracker`), and `search-params.ts` is the corpus search page's own
  testable filter-building logic. `RadioClipCard.svelte` is the one
  rendering both surfaces reuse (context chips, confidence badge,
  low-confidence note, lazy click-to-play); `RadioSearchResultOut`'s
  `RadioClipOut` gained a `clip_id` field (`f1_api.radio.clip_id_path`'s own
  opaque segment) so a search result — whose `audio_url` is always `null`
  by that schema's own design — can resolve `GET /radio/{clip_id}` on
  click, the one backend seam this prompt needed. `RadioLane.svelte` is the
  live/replay timeline strip, wired into `/race` directly under
  `GapStreamGraph`; `routes/radio/+page.svelte` is the standalone corpus
  search — a real search product (prominent query box, example queries,
  collapsible driver/session/date/track-status filters, a similarity score
  bar), debounced with a request-id guard against a superseded fetch
  clobbering a newer one, with idle/loading/error/no-results states kept
  visibly distinct. `just verify-phase4` (Playwright) boots a SECOND,
  independent `f1_api` + frontend pair against a dedicated fixture
  (`python/f1_radio/tests/fixtures/e2e_historical` +
  `e2e_radio_fixture.json`, generated together by
  `f1_radio/fixtures/generate_e2e.py` so the parquet table and its
  embeddings can never drift apart) via `f1_api.radio_fixture`'s new
  DB-free path (`F1_API_RADIO_FIXTURE`, a `MemorySearchIndex`/
  `MemoryTranscriptStore`/`FixtureEmbeddingBackend` reachable by a real
  externally-driven process, not just a pytest override) — asserting a
  paraphrase with no lexical overlap ranks first, a driver filter excludes
  the closest-embedding clip when it's someone else's, the search page
  works at 390px, and replayed radio clips release in the correct
  chronological order with zero spoiler leaks. `just verify-phase3` was
  narrowed to name its own two spec files so it stays independent of this
  prompt's separate fixture/webServer pair. Found the hard way building
  this: `RadioLaneReducer.clips` handing back the same mutated-in-place
  array made Svelte 5's `$derived.by` see "no change" even as clips were
  pushed into it — the exact caveat `TimingTower.svelte` already documents,
  fixed the same way (a fresh array copy per call).
- (next) Per-segment embeddings (finer recall than one vector per whole
  transcript) were called out as optional and are not built. Phase 4's own
  exit criteria are now all met — Phase 5 (the "why" engine, fusing
  telemetry + radio + race control) is next, not pulled forward by this
  prompt, per CLAUDE.md's own "build strictly in order."


## Phase 5 status: DONE

Phases 0-4 complete: replay harness, normalized store, calibrated kernel, spoiler-safe
frontend, searchable radio corpus. Phase 5 is the WHY-ENGINE — on a detected anomaly, fuse
telemetry + kernel state + radio + race control into ONE grounded, sourced sentence.

Phase 5 invariants:
- GROUNDING IS EVERYTHING. The LLM explains RETRIEVED evidence; it never asserts facts not
  in the evidence bundle. Every explanation cites the specific events/clips it used. No
  evidence -> no explanation ("no clear cause found"), never a guess.
- The anomaly DETECTOR is pure Python over kernel/state frames (deterministic, testable). The
  LLM is only the final narration step over an already-assembled evidence bundle.
- Explanations pass the SAME Delay Sync gate. An explanation about lap 40 cannot surface before
  the user's clock reaches lap 40, and it may only use evidence at or before the anomaly time —
  no future data leaks into a "why". This is both spoiler-safety AND causal honesty.
- Confidence propagates. If the evidence is a thin deg fit or a low-confidence transcript, the
  explanation says so. Reuse the honesty contract from Phases 2 and 4.
- Cost stays within budget: use a small fast model (Claude Haiku) over a tight retrieved bundle,
  a few calls per race weekend. Cache explanations per anomaly; never re-generate on request.
- The LLM call is isolated behind one interface so it's swappable/mockable; all tests run
  against a mock model. Never require a live API key to run the test suite.

Exit criteria + runbook: docs/PHASE5.md.

Phase 5 packages (under python/f1_why, in dependency order):
- f1_why anomaly detector (5.1): the first Phase 5 package — pure Python
  over `f1_kernel`'s own typed frames and its `KernelReport`, deciding
  WHEN something is worth a future "why" (detection only, per this
  prompt's own instruction: no evidence bundle, no LLM call here).
  `detect_anomalies(session_frames, kernel_report, params) ->
  list[Anomaly]` is the one deliverable; `Anomaly` (`kind`, `lap_number`,
  `anomaly_time` — a UTC instant a future Delay Sync gate will compare
  against, `drivers`, `magnitude`, `salience`, a `summary` dict of the
  specific numbers behind the call — flat and machine-readable, never
  prose, and `reasons`) is the vocabulary, mirroring `CrossoverResult`'s
  own "structured result, no rendering" discipline. Six `AnomalyKind`s:
  `PACE_LOSS`/`OFF_TRACK` share one robust (median/MAD) outlier model
  applied to a TRAILING window of a driver's own recent clean laps within
  the same stint — deliberately never the whole stint's median: a stint
  with genuine (non-anomalous) degradation curvature drifts slower over
  its own length, and a whole-stint comparison would misread ordinary
  late-stint pace as a false positive, caught empirically building this
  prompt's own synthetic fixture. One runs over the whole lap
  (fuel-corrected); the other, per mini-sector (raw — the kernel has no
  per-sector fuel model), the closest lock-up/off-track proxy this schema
  supports without the expensive, opt-in telemetry a real braking-point
  drift needs. `POSITION_CHANGE` walks each driver's own `position` column
  lap-over-lap, excluding any lap either side touched by that driver's own
  pit stop, and pairs a clean one-for-one on-track swap into one `Anomaly`
  naming `(gainer, loser)`. `PIT_ANOMALY` reuses
  `f1_kernel.pit_loss.pit_loss_samples` verbatim and flags a stop far from
  `KernelParams.pit_loss_for`'s own calibrated/default norm. `DEG_CLIFF`
  reads `KernelReport.degradation_fits`' own optional quadratic term
  directly — no new fitting, just a threshold on what `fit_degradation`
  already computed, expressed as the extra seconds the curvature implies
  by the stint's last lap. `UNDERCUT_RESOLVED` reuses
  `f1_kernel.undercut_eval.evaluate_session_stops` (the walk-forward
  backtest machinery, unchanged) to find every real stop-pair duel this
  session's own prediction spoke to and reports how it resolved, weighting
  salience up for a HIGH-confidence call that came through wrong (an
  upset) via `AnomalyParams.undercut_resolved_upset_multiplier`. Every
  kind's own "how big" figure is converted to a shared seconds-equivalent
  and squashed through one saturating curve (`salience = 1 -
  exp(-seconds_equivalent / AnomalyParams.salience_scale_s)`) onto a
  common `[0, 1)` ranking scale, so a consumer can rank/limit across kinds
  without knowing each one's own unit — this prompt's own "don't explain
  every 0.05s wobble" framing. `AnomalyParams` is this package's own
  typed, frozen, overridable constant set — mirroring `KernelParams`'s
  shape, not reusing it: a pace-loss threshold answers a different
  question than the clean-lap filter's own outlier bound, and should not
  silently move if that one is retuned. Every default is a deliberately
  chosen round number, not yet calibrated against a measured corpus — the
  same honesty every not-yet-calibrated `KernelParams` field already
  carries. Tested against ONE checked-in synthetic six-driver race built
  directly as polars frames (`tests/race_fixture.py` — no
  `DomainEvent`/state-machine simulation needed, since `SessionFrames` is
  already plain data), each driver isolated in its own widely-separated
  wall-clock time band so `evaluate_session_stops`' race-order pairing
  never mixes one driver's planted story into another's: a lock-up lap
  detected at the right lap for both `PACE_LOSS` and `OFF_TRACK`, a clean
  swap detected for `POSITION_CHANGE`, a blown-out stop for `PIT_ANOMALY`
  (and confirmed NOT flagging the duel's own routine-cost stops), a
  genuine quadratic cliff for `DEG_CLIFF`, and a real (the undercut model
  cannot foresee a compound's hidden pace change) duel resolution for
  `UNDERCUT_RESOLVED` — plus salience-ordering (the resolved duel outranks
  a single late-race place swap) and determinism assertions.
- f1_why evidence retriever (5.2): given one `Anomaly`, gathers everything
  relevant AT OR BEFORE its own `anomaly_time` into one typed, self-contained
  `EvidenceBundle` — still no LLM, per this prompt's own line. Five
  `EvidenceKind`s: `ANOMALY_FACT` (the detector's own `summary`/`reasons`,
  always exactly one item, never dropped by trimming), `LAP_CONTEXT`
  (compound/tyre-age/gap/track-status at the anomaly's own lap, per
  driver), `DEGRADATION_FIT`, `RACE_CONTROL` (`f1_domain.RaceControlMessage`
  in a lookback window, read from a raw event stream `EvidenceSources`
  carries alongside the frames/report `f1_kernel` already produced), and
  `RADIO_CLIP` (`f1_radio.search`, Phase 4's own search layer, reused
  rather than a second query path — a `driver`-scoped blank-query filter
  search already returns both halves of a pit-wall exchange, since OpenF1's
  `team_radio` carries no separate "engineer" feed). The prompt's own
  instruction that everything pulled is "time-boxed to <= anomaly_time" is
  read literally, not just per-item-timestamp: `_degradation_items`
  re-slices the lap frame to `lap_number <= anomaly.lap_number` and REFITS
  degradation on exactly that slice — the identical "slice, then refit on
  the slice" walk-forward recipe `f1_kernel.undercut.undercut_snapshot`
  already uses — rather than trusting the caller's (ordinarily whole-
  session) `KernelReport.degradation_fits` verbatim, which would let a
  stint's later laps quietly inform "evidence" about an earlier anomaly in
  that same stint. Braking-point drift is deliberately NOT a kind at all:
  it is keyed by circuit corner while `OFF_TRACK`'s own signal is a timing
  sector, a mapping this schema does not have, and re-deriving it
  walk-forward would need a telemetry frame this module has no input for —
  documented as a reasoned omission rather than silently leaking or
  bolting on telemetry loading no prompt asked for. Every `EvidenceItem`
  carries a `salience` (relevance, used only to trim to
  `EvidenceParams.token_budget` — a small, approximate char-count
  heuristic, not a real tokenizer) kept strictly separate from a
  `confidence` (trust, `[0, 1]`, derived from whichever native taxonomy
  applies — `Reliability`, `Confidence`, a transcript's own float — never
  a new number invented on top of them); `_trim_to_budget` greedily fills
  by descending salience (skipping, not stopping at, an item too big to
  fit) and the final "never future" guarantee is re-enforced once,
  structurally, at the end of `assemble_evidence` regardless of whether
  every source builder already respected its own window — the same
  "don't trust upstream, enforce at the boundary" discipline
  `TimelineBuffer.release` uses for Delay Sync. `EvidenceBundle.low_evidence`
  is true when nothing beyond the anomaly's own fact survived; anything
  measured (lap context, a degradation fit, relevant race control, radio)
  goes to the narrator, which must cite what it uses and may decline.
  (Originally only radio/race control counted, with kernel context treated
  as background; revised 2026-09-23 because with the radio corpus empty the
  only thing clearing that bar was unrelated blue flags, while the tyre-age
  facts that actually explain a pass were excluded — see evidence.py's
  module docstring.) Race control is kept only when it names one of the
  anomaly's drivers or names nobody, and the bundle carries a driver roster
  (`roster`, identity not evidence) so a sentence names people. Tested against the SAME checked-in fixture
  5.1 uses (via a shared `anomalies` fixture now hoisted into
  `conftest.py`): a property test that no item in any of the fixture's six
  anomalies' bundles is ever timestamped after that anomaly's own
  boundary (seeded with deliberately future- and past-dated race-control
  "landmines" per anomaly); a seeded radio clip retrieved and ranked
  highest among supporting evidence; trimming keeping exactly the
  highest-salience items under a token budget computed to fit precisely
  two of five identically-sized planted clips; `low_evidence` toggling on
  a race-control message; and the walk-forward degradation refit using
  materially fewer laps (and disagreeing with) the same stint's
  whole-session fit.
- f1_why grounded narration (5.3): the ONLY place an LLM is used anywhere
  in this project, and tightly constrained per this prompt's own line.
  `NarrationModel` is a narrow Protocol (`complete(prompt) -> ModelNarration`),
  the same swappable-backend split `f1_radio`'s `WhisperBackend`/
  `EmbeddingBackend` already establish; `ClaudeHaikuModel` is the real
  backend — Claude Haiku (`claude-haiku-4-5`), per CLAUDE.md's own Phase 5
  budget invariant, naming the model explicitly rather than defaulting to
  this project's usual choice — calling `messages.parse` with a structured
  `output_format` so the response is a typed `{text, cited_evidence_ids,
  no_clear_cause}` rather than parsed prose, and importing `anthropic`
  LAZILY inside its own constructor so no test needs it installed, let
  alone a live key; `MockNarrationModel` is a deterministic scripted fake
  (mirroring `FakeWhisperBackend`'s own precedent) that every test in this
  package uses instead, and records every prompt it was actually asked to
  complete so a test can assert a call was skipped, not merely infer it
  from the result. `build_prompt` is a pure function (`EvidenceBundle ->
  str`) rendering the anomaly plus every surviving evidence item under its
  own stable id; `explain` calls the model AT MOST once — never at all
  when `bundle.low_evidence` already guarantees the answer, the literal
  reading of "keep calls few and cheap" — then runs `_validate_grounding`:
  deliberately structural/deterministic checks (every cited id must exist
  in the bundle; a substantive explanation must cite at least one),
  NEVER a second model call, since that would both break "the ONLY place
  an LLM is used" and double the budgeted cost. Every rejection path — a
  failed check, or the model's own honest `no_clear_cause` — normalizes to
  the SAME canonical `FALLBACK_TEXT` and `ExplanationConfidence.LOW`,
  never a paraphrase, so a caller can rely on exact text. Confidence
  otherwise propagates from the WORST (never the best, never an uncited
  item's) confidence among the evidence the model actually cited —
  `ExplanationConfidence` is deliberately its OWN enum, not a reuse of
  `f1_kernel.undercut.Confidence`, the same "same shape, different
  question, must not silently move together" reasoning `AnomalyParams`
  already gives for not reusing `KernelParams`. `explain_cached` +
  `MemoryExplanationStore` cache by anomaly id and never regenerate, the
  other half of the budget invariant. Tested entirely against
  `MockNarrationModel` — no test needs a live key: a clear-cause bundle
  (a radio clip plus a degradation fit) citing both ids with confidence
  set by the weaker (thin) one; a low-evidence bundle short-circuiting to
  the fallback with ZERO model calls made; the model's own decline
  normalizing to the same fallback; the grounding validator independently
  rejecting a citation of a nonexistent id and an explanation with no
  citations at all; determinism given a fixed mock; and the cache
  returning a stored explanation without the model being called a second
  time.
- f1_api why-engine integration (5.4): explanations join the product the
  same way radio did in 4.4. `f1_api.why` is the DI seam —
  `get_narration_model` / `get_explanation_store` are real FastAPI
  `Depends` providers (tests override with `MockNarrationModel` + a
  fresh `MemoryExplanationStore`; production resolves to
  `ClaudeHaikuModel` when `ANTHROPIC_API_KEY` is set, else a declining
  mock so CI never needs a live key), and `anomaly_id_path` encodes
  `session_key` + `f1_why.anomaly_id` into one opaque path segment the
  same way `clip_id_path` does for radio. REST:
  `GET /sessions/{key}/anomalies` returns salience-ranked `AnomalyOut`s
  (detection only — machine-readable `summary`, never prose);
  `GET /anomalies/{id}/explanation` returns the grounded `ExplanationOut`
  plus cited evidence with lazy-fetch `href`s (radio → `/radio/{clip_id}`,
  degradation/laps → the existing session routes). Timeline integration
  (`f1_api.stream.replay.explanation_envelope`) emits lightweight
  `explanation` envelopes paced on each anomaly's own `anomaly_time` —
  historical sessions precompute the batch via
  `precompute_session_explanations` / `explain_cached`, live sessions
  generate on newly-detected anomalies against the same cache — and the
  remapped `event_time_utc` passes the exact same Delay Sync gate
  everything else does, so an explanation cannot appear before the
  user's playback clock reaches its moment. Payload stays text +
  evidence ids/links; the client fetches full evidence detail lazily
  (the telemetry/radio lesson again). `openapi.json` regenerated; the
  frontend typed client (`pnpm generate-client`) compiles against it.
  No frontend UI in 5.4 — API + timeline plumbing only. Tested against the
  checked-in kernel fixture (REST: ranked anomalies + grounded cited
  explanations for the fixture's race-control-backed position changes;
  property: no explanation envelope releases ahead of a seeded playback
  clock, and no explanation cites evidence dated after its
  `anomaly_time`) with no live API key.
- Frontend why UI + verification (5.5): explanations become something a
  user actually sees, and Phase 5's own exit gate closes.
  `apps/web/src/lib/why/` is the shared pure-TS core — `view.ts`'s
  `ExplanationView` is the one shape a live `explanation` envelope and a
  REST `ExplanationOut` both convert into, `lane.ts`'s
  `ExplanationLaneReducer` folds `explanation` envelopes (already
  spoiler-safe — paced on `anomaly_time`, so no separate reveal gate),
  and `story.ts`'s `buildStoryMoments` merges salience-ranked anomalies
  with fetched explanations for the narrative surface.
  `ExplanationCard.svelte` is the one rendering both surfaces reuse
  (confidence badge, honest fallback styling, expandable CITED EVIDENCE
  that lazy-fetches `GET /anomalies/{id}/explanation` so the user sees
  the real sources with links back to those REST resources).
  `ExplanationLane.svelte` is the live/replay timeline strip, wired into
  `/race` under the timing tower; `routes/story/+page.svelte` is the
  salience-ranked race story — the shareable, demo-worthy surface.
  `F1_API_WHY_FIXTURE` (`f1_api.why_fixture.CitingNarrationModel`) is the
  DB-free / no-API-key path a real Playwright process needs, the same
  env-var seam `F1_API_RADIO_FIXTURE` already gives Phase 4.
  `just verify-phase5` (Playwright) reuses the Phase 3 `f1_api` + frontend
  pair against the checked-in kernel fixture with that env set —
  asserting (a) a known anomaly gets a grounded, cited explanation,
  (b) explanations appear at the right synced moment, (c) no explanation
  leaks ahead of the clock, (d) an ungrounded/weak case shows the safe
  fallback, plus the story view and a 390px mobile check. Wired into
  `just check`. Runbook + grounding guarantee + cost notes + exit
  criteria: docs/PHASE5.md.
- Narration hardening (2026-09-23): the OpenRouter chain runs with
  reasoning OFF (`REASONING_OFF`) — every free model is a reasoning model
  and spent its whole token budget thinking, and a truncated or JSON-less
  reply is now a provider failure, never a "no clear cause" verdict about
  the race. A process-wide `RequestBudget` keeps the key under the free
  tier's 20 requests/minute; `PostgresExplanationStore` (migration 005,
  keyed by `NARRATION_VERSION`) persists explanations so each moment is
  paid for once ever (the free tier also caps at 50 requests/day), and
  outages are never cached. `f1_api.why.story_moments` caps any one
  anomaly kind at a third of a narrated story.
- (next) Phase 5's own exit criteria are now all met. Remaining gaps are
  depth, not product surface: telemetry-derived evidence kinds, and `AnomalyParams`/`EvidenceParams`/
  `NarrationParams` calibration against a measured corpus. Phase 6
  (vision, wet-track state only, client-side) is next, not pulled
  forward by this prompt, per CLAUDE.md's own "build strictly in order."