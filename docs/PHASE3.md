# Phase 3 runbook

Everything an operator needs to run and test Phase 3's pieces: the `f1_api`
FastAPI read model (3.1) — REST + a WebSocket envelope stream bridging the
pure-Python kernel/store (Phases 1-2) to the frontend — the SvelteKit shell
and design system (3.2), Delay Sync, the spoiler-safety gate (3.3), the
Canvas track map (3.4), the kernel-analytics views — undercut countdown,
gap stream, degradation curves, braking-point drift (3.5) — and the
composed race dashboard plus the Phase 3 E2E verification harness (3.6).
Phase 3's own remaining gaps (`mode=live` against a real live session, no
push-based live delivery) are called out in their own sections below.

## Exit criteria

Phase 3 as a whole (CLAUDE.md's "Phase 3 status"), tracked here as each
piece lands:

- [x] A read-model API exists: REST reads plus a WebSocket envelope stream
      over the analytics kernel and event store, consumable without
      touching TimescaleDB or importing the kernel directly (3.1, below).
- [x] `mode=replay` streams a full historical session end-to-end with
      monotonic `event_time_utc`, reusing (not reinventing) `f1_replay`'s
      timing model — the "no live race required" dev story extended to
      the WebSocket, not just REST reads. As of 3.6, that `event_time_utc`
      is also REMAPPED to real wall-clock time (see 3.6's own section) —
      without that, a replayed 2023-2025 session's timestamps stay in the
      actual historical past forever, which would make Delay Sync's
      playback-clock gate release everything instantly regardless of the
      chosen delay.
- [x] `openapi.json` is committed and kept in lock-step with the pydantic
      response models (a dedicated test fails on drift).
- [x] The frontend (SvelteKit + Canvas/WebGL, mobile-first) exists as a
      full race dashboard — the shell, design system and typed data path
      (3.2), Delay Sync (3.3), the Canvas track map (3.4), the
      kernel-analytics views (3.5) and the composed dashboard (3.6) are
      all built and verified end-to-end against the checked-in fixture,
      by both `just web-test` (vitest, pure logic) and `just verify-phase3`
      (Playwright, a real browser against a real running app).
- [x] A full historical race replays end-to-end in the browser via the API,
      no crashes — `just verify-phase3`'s `dashboard.spec.ts`.
- [~] Track map shows 20 cars on the real circuit shape, smooth on a mobile
      profile — true of the RENDERING PIPELINE (proven by the `/track-map`
      synthetic-preview E2E test and 90 vitest cases). The outline half is
      now also true for real: all six shipped circuits carry a real outline
      (`tools/build_circuits.py`, run against the populated FastF1 cache —
      see docs/PHASE2.md's circuit-metadata section). What's still missing
      is car MOVEMENT: `f1_backfill`'s historical parquet carries no
      `car_position` table at all (DATA_CONTRACTS.md), so no historical
      session anywhere can ever drive real car markers — the race
      dashboard's map panel renders a real track shape today but with no
      cars on it, its documented graceful-degradation state for the
      position-data half specifically. That remainder needs a
      live-ingested session, not a data-availability gap this repo can
      close on its own. See 3.4's and 3.6's own sections.
- [x] Undercut countdown, degradation curves, gap stream, and braking-point
      drift all render with confidence encoding — 3.5, exercised end-to-end
      by the dashboard and `just verify-phase3`.
- [x] Delay Sync: manual + auto calibration work (3.3's slider/tap-to-sync,
      reused as-is on the dashboard), and the spoiler-safety property test
      (vitest, 200 randomized trials) AND an E2E assertion against the real
      running app (`just verify-phase3`'s spoiler-safety test, sampling the
      dashboard's own rendered sync-diagnostics readout) both pass — zero
      leaks.
- [x] Usable and legible at 390px width — the dashboard is mobile-first by
      default; a `:has()`-gated wider grid only ever applies at 900px+.
      Verified manually in a 390×844 browser viewport (see 3.6).
- [x] "Unofficial · not associated with Formula 1" disclaimer shipped in
      the UI (`Footer`, 3.2, rendered on every page via the root layout);
      no F1/team marks.
- [x] `just verify-phase3` green in CI (3.6) — wired into `just check`
      alongside `verify-phase0`/`verify-phase1`/`verify-phase2`.
- [ ] `mode=live` exercised against a real live-ingested session — today it
      is implemented (DB-polling) but, like `f1_ingest`'s own connection
      layer (docs/PHASE0.md), only genuinely exercisable during a live
      session, not unit-tested.

## The read model (`f1_api`)

```
f1_kernel / f1_store ──► f1_api ──► frontend (apps/web, below)
 (Phases 1-2, pure         │
  Python, no HTTP)         │
                    REST: /sessions*
                    WS:   /stream/{key}
```

`f1_api` adds no analytics of its own — every number it returns is a
kernel value, reshaped (`f1_api/schemas.py` mirrors the kernel's own
dataclasses/frame schemas field-for-field; `f1_api/convert.py` reshapes,
never recomputes). It is the ONE place that touches TimescaleDB and
imports the kernel directly, precisely so nothing else has to: the
frontend (and anything after it) talks to `f1_api` only.

### Running it

```sh
uv run f1-api                      # http://127.0.0.1:8000, uvicorn under the hood
uv run f1-api --port 8080 --reload
```

Or directly with uvicorn (equivalent, what a real deployment would use):

```sh
uv run uvicorn f1_api.main:app --reload
```

Interactive docs land at `/docs` (Swagger UI) and `/redoc`; the machine-
readable contract is the committed `python/f1_api/openapi.json`.

### Configuration (all optional, all environment variables)

| variable                   | default            | meaning                                                        |
| --------------------------- | ------------------ | ---------------------------------------------------------------- |
| `F1_API_HISTORICAL_ROOT`    | `data/historical`   | where `f1-backfill` output lives (DATA_CONTRACTS.md).             |
| `F1_STORE_DSN`               | unset               | `f1_store`'s own DSN env var, reused as-is. Unset = no live sessions, not an error (mirrors `f1_kernel`'s own "DB is a sink, never a dependency"). |
| `F1_API_CORS_ORIGINS`       | `*`                 | comma-separated allowed origins for local frontend dev.           |

### REST

All read-only, all under one `SessionKey` path segment
(`f1_api.sessions.session_key_path`/`parse_session_key`:
`<year>-<round>-<session_type>`, e.g. `2024-1-race`):

```
GET /sessions                            list every session either source can serve
GET /sessions/{key}                      metadata + classification
GET /sessions/{key}/laps                 annotate_laps' output (every lap, clean or not)
GET /sessions/{key}/degradation          DegradationFit[] with reliability
GET /sessions/{key}/analysis             the full KernelReport (see below)
GET /sessions/{key}/circuit              corners, DRS zones and the track outline (3.4)
GET /sessions/{key}/telemetry/{driver}   the braking-point frame (not raw telemetry)
GET /health
```

`GET .../analysis` deliberately omits the per-lap frames `GET .../laps`
already serves (`KernelReportOut`'s own docstring has the full reasoning);
everything else the kernel's `analyze_session` produces is there,
including an opt-in `?braking_drivers=44&braking_drivers=1` query param for
the one stage that needs telemetry (expensive, so off by default — the
same opt-in `analyze_session` itself uses).

A session that exists in neither the historical backfill nor (if
configured) the live store 404s; a malformed `{key}` 422s — never a 500.

### WebSocket: `/stream/{key}`

```
/stream/{key}?mode=replay&speed=100    # the one to actually develop against
/stream/{key}?mode=live                # polls the store; needs a live-ingested session
/stream/{key}?mode=replay&speed=100&include_telemetry=true   # opt in to car_telemetry
```

**`car_telemetry` is excluded by default** (`f1_api.stream.replay.
DEFAULT_STREAM_KINDS` — every kind except it). Found the hard way: a real
backfilled race's resampled FastF1 telemetry across ~20 drivers is one to
two orders of magnitude more envelopes than every other kind combined, and
`f1_kernel.HistoricalSource.events()` includes it whenever `kinds` isn't
narrowed. Streaming that unfiltered over the socket pegged the browser tab
(each released batch was also getting re-copied into `visibleState.events`
once per envelope — see Delay Sync's section below for that half of the
fix) and, once the tab stopped keeping up, produced the repeating
`socket.send() raised exception.` server log. `include_telemetry=true` is
the on-demand escape hatch — no shipped view needs it today
(`BrakingPointDrift` reads the reduced braking-point frame via REST
`/telemetry/{driver}` instead), and there is still no backpressure on the
send loop if a future caller opts in and can't keep up — see "what Phase 3
does not do yet" below.

Every message is one envelope: `{event_time_utc, kind, payload}`.
`event_time_utc` is never stripped — it is what Delay Sync (3.3) gates
on. `kind` is either a `DomainEvent.kind` (the payload is that event,
verbatim) or `"kernel_snapshot"` (the payload is `{decision_lap,
crossovers: CrossoverResult[]}`, each crossover carrying its own
`confidence`).

`mode=replay` re-paces the session's own event stream using
`f1_replay.pacing_offset_s`/`sleep_until` (made public from `f1_replay`
specifically for this reuse — see that package's `replayer.py`
docstring) — the exact elapsed/speed/delay formula `f1_replay.replay`
uses for raw recordings, applied here to an already-normalized
`DomainEvent` stream instead. `speed=0` runs as fast as possible (useful
for tests and for scrubbing a whole race in seconds); this still takes a
few seconds of real CPU time for a full race, because a `kernel_snapshot`
recompute is genuinely not free — see `f1_api.stream.replay.LapSnapshotFeed`
for why it is batched once per race lap rather than once per raw event.

**`event_time_utc` is REMAPPED to real wall-clock time, not the source
event's own historical `ts_utc`** (added in 3.6, after Delay Sync's own
playback-clock gate was actually exercised against a real replayed
session for the first time in the E2E suite below, and turned out to
release everything instantly regardless of delay — see the "why" in
`f1_api.stream.replay`'s own module docstring). Each envelope's
`event_time_utc` is set to the real UTC instant it is actually SENT
(`start_wall_utc + pacing_offset_s(...)`, the exact same offset
`sleep_until` just blocked for) rather than the originating
`DomainEvent.ts_utc` — so a 2023-2025 backfill session replayed today
behaves, from a `event_time_utc`-vs-wall-clock perspective, exactly like
a genuinely live one, which is what lets `playbackClockMs(wall_now,
delay)` mean the same thing for both. `mode=live` is unaffected: its
events already carry a genuinely-live `ts_utc`, so `domain_event_envelope`
stays a straight passthrough there (`override_ts` is only ever passed
from the replay loop).

### Regenerating `openapi.json`

```sh
uv run python python/f1_api/tools/export_openapi.py
```

Run this after any response-model change and commit the result —
`test_openapi.py` fails the moment the committed file drifts from what the
app actually generates.

### Tests

```sh
uv run pytest python/f1_api
```

Four suites, all against the SAME checked-in synthetic historical fixture
`f1_kernel`'s own tests use (`python/f1_kernel/tests/fixtures/historical`
— reused via `python/f1_api/tests/api_fixture.py` rather than a second
invented fixture race), so this needs no `data/`, no database and no
network:

- `test_rest.py` — every REST endpoint against that fixture, including the
  404/422 error paths and the braking-point endpoint's graceful-empty-list
  behavior on an unknown driver.
- `test_stream_replay.py` — the WebSocket `mode=replay` path end-to-end:
  monotonic `event_time_utc`, well-formed envelopes, `kernel_snapshot`
  envelopes present and carrying a real `confidence`. `mode=live` is not
  tested here for the same reason `f1_ingest`'s connection layer isn't
  (docs/PHASE0.md): it needs a live session to mean anything.
- `test_replay.py` (3.6) — `replay_envelopes`' wall-clock timestamp remap
  in isolation, with a fake monotonic clock AND a fake wall clock injected
  (so the whole suite runs instantly regardless of how much simulated race
  time it covers): every envelope's `event_time_utc` tracks the injected
  wall clock rather than the fixture's real, fixed 2024-03-02 anchor;
  `domain_event_envelope` without `override_ts` still passes `ts_utc`
  through untouched (the `mode=live` guarantee); remapped timestamps scale
  by `speed` exactly like the send-pacing they share an offset with; a
  `kernel_snapshot` envelope carries the same remapped instant as the
  domain event that triggered it.
- `test_openapi.py` — the committed `openapi.json` matches what
  `create_app()` generates right now, and every documented route exists.

## The frontend scaffold (`apps/web`)

```
f1_api ──► apps/web (SvelteKit + TypeScript, pnpm workspace)
           ├─ src/lib/styles/tokens.css   design tokens (color/type/spacing, light+dark)
           ├─ src/lib/components/         Panel, Stat, ConfidenceBadge, LoadingState, Footer,
           │                              + one thin view per race view (3.3-3.6 below)
           ├─ src/lib/api/client.ts       typed REST client, generated from openapi.json
           ├─ src/lib/api/stream.ts       dumb SessionStream wrapper over /stream/{key}
           ├─ src/routes/+page.svelte     smoke route: GET /sessions, listed
           ├─ src/routes/delay-sync/      Delay Sync's own dev harness (3.3)
           ├─ src/routes/track-map/       the track map's own dev harness (3.4)
           ├─ src/routes/analytics/       the kernel-analytics views' own dev harness (3.5)
           └─ src/routes/race/            the composed race dashboard (3.6) — the product
```

This scaffold (3.2) is the shell, the design system and the data path
everything else renders through — no race view of its own; `/race` (3.6,
its own section below) is that. Each dev-harness route above proves ONE
piece end-to-end in isolation before 3.6 composes them.

### Design direction

Deliberately NOT dark-mode-first with team colors and a slanted sport
font — every public F1 dashboard already looks like that (per this
prompt's own brief: "the pretty track map is table stakes — it is NOT the
product"). The direction here reads closer to a data-journalism
instrument than a gamer HUD: a paper-toned light theme by default
(`prefers-color-scheme`-driven dark mode, overridable via the header's
theme toggle and persisted to `localStorage`), a serif display face for
headings, and tabular monospace figures (`.tabular`/`.font-mono`, `.stat`'s
value) for anything timed — lap times, gaps, countdowns — so columns of
numbers actually line up the way they would on a timing screen. The one
accent color is a muted copper, used sparingly; never a team color, never
neon. All of it lives as CSS custom properties in
`src/lib/styles/tokens.css`, so later work (the actual race views) styles
against tokens, never hardcoded values.

Four primitives, matching this prompt's scope exactly — no more:
`Panel` (a bordered surface with an optional title/subtitle), `Stat` (a
label + tabular value + unit, with an optional confidence badge — `null`
renders an em dash, never a blank or a zero, per the kernel's own "unknown
is not excluded" discipline), `ConfidenceBadge` (color AND text both carry
the tier, never color alone — and already handles both taxonomies the
kernel emits, `Reliability`'s good/thin/unreliable and `Confidence`'s
high/medium/low, so callers never remap one onto the other), and
`LoadingState` (an accessible `role="status"` spinner). `Footer` renders
the "unofficial · not associated with Formula 1" marker CLAUDE.md's legal
posture requires, on every page via the root layout.

### Running it

```sh
just web-dev             # http://localhost:5173, points at PUBLIC_F1_API_BASE_URL
just web-build           # static production build (apps/web/build/)
just web-check           # svelte-check (typecheck) + eslint
just web-generate-client # regenerate the typed client from python/f1_api/openapi.json
just web-test            # vitest — Delay Sync, track map, kernel-views, timing tower (all pure TS)
```

`apps/web/.env.example` documents `PUBLIC_F1_API_BASE_URL` (defaults to
`http://127.0.0.1:8000` if unset, via `$env/dynamic/public` — chosen over
`$env/static/public` specifically so `just web-dev` works with zero setup
rather than failing the build when no `.env` exists). Point it at a
running `f1_api` (`just serve-api`, optionally against the checked-in
kernel fixture: `F1_API_HISTORICAL_ROOT=python/f1_kernel/tests/fixtures/historical`)
to see the smoke route list real sessions.

The app builds with `@sveltejs/adapter-static` in SPA-fallback mode
(`ssr = false` set once, root-wide, in `+layout.ts`) — per Phase 3's own
invariant that the frontend only ever talks to `f1_api` over fetch/
WebSocket, there is nothing here a Node server would need to render.

### The typed client: how a contract break actually surfaces

`src/lib/api/client.ts` wraps `openapi-fetch` around types generated by
`openapi-typescript` from the committed `python/f1_api/openapi.json`
(`pnpm generate-client`, wired as `just web-generate-client`) — never
hand-typed, so a backend response-model change surfaces as a compile-time
error at every call site relying on the field that moved, not a silent
`undefined` at runtime. Verified directly while building this: temporarily
renaming `SessionSummaryOut.event_name` in `f1_api.schemas`, regenerating
both `openapi.json` and the TS client, and confirming `svelte-check` fails
exactly at `+page.svelte`'s `session.event_name` reference before
reverting both files.

`src/lib/api/stream.ts`'s `SessionStream` is hand-typed rather than
generated: FastAPI does not emit WebSocket routes into `openapi.json` (no
`/stream/{key}` path appears in the committed file), so there is no
generated shape to reuse. It mirrors `f1_api.schemas.StreamEnvelope`
field-for-field by hand and stays deliberately dumb — connect, parse,
republish envelopes to subscribers, no buffering or reordering. Delay Sync
(holding events back to match the user's playback clock) is Phase 3.3's
job, built on top of this wrapper, not inside it.

### Tests

No component/e2e test runner for `apps/web` as a whole — `just web-check`
(svelte-check + eslint) plus the manual browser check every UI-affecting
change should get (mobile viewport ~390px first, per the mobile-first
invariant) is that surface. Delay Sync (3.3, below) is the one exception:
its core is plain TS with no DOM dependency, so it gets a real unit-test
suite (`just web-test`, vitest).

## Delay Sync (3.2's sibling — the spoiler-safety gate)

The killer feature: CLAUDE.md's Phase 3 invariant ("nothing whose
event-time is ahead of `now − user_delay` may ever reach the screen") as
one small, framework-agnostic TS module —
`apps/web/src/lib/delay-sync/` — not buried inside a Svelte component, per
the prompt that built it. Every file there is plain TypeScript with zero
Svelte/SvelteKit import; the Svelte layer (three small components) sits
strictly on top of it.

```
SessionStream (dumb, 3.2) ─► DelaySyncController.ingest ─► TimelineBuffer
                                       │                     (sorted by
                                       ▼                      event_time_utc)
                              .tick(nowMs) releases envelopes
                              at or before playbackClockMs(nowMs, delaySeconds)
                                       │
                                       ▼
                              visibleState (VisibleSessionState) ◄── the ONLY
                                       │                             thing a
                                       ▼                             view may
                                    views                            subscribe to
```

- **`envelope.ts`** defines `Envelope` — structurally identical to
  `f1_api.schemas.StreamEnvelope` / `$lib/api/stream`'s `StreamEnvelope`,
  defined again rather than imported so this module never depends on the
  API layer (TS structural typing makes a real `StreamEnvelope` satisfy it
  with no conversion) — plus the `kernel_snapshot` sentinel and
  `eventTimeMs`, the one place `event_time_utc` gets parsed.
- **`clock.ts`**'s `playbackClockMs(wallNowMs, delaySeconds)` is the
  entire gate's arithmetic: wall time minus delay, delay in SECONDS
  (matching `f1_replay`'s own `--delay` and `f1_api`'s `pacing_offset_s` —
  one unit for a stream offset everywhere in this project).
- **`timeline-buffer.ts`**'s `TimelineBuffer` is the guarantee itself:
  `ingest` inserts into a buffer kept sorted by `event_time_utc` (so an
  out-of-order arrival still releases in the right order and never
  early); `release(playbackClockMs)` hands back — and only ever hands
  back, exactly once each — envelopes at or before that clock. Pure with
  respect to the clock value given.
- **`visible-state.ts`**'s `applyReleasedEnvelope`/`applyReleasedEnvelopes`
  is a pure reducer that runs ONLY over what `release` already returned —
  there is no other constructor path to `VisibleSessionState`, the one
  thing any view may subscribe to. It stays deliberately generic (an
  ordered event log plus a `latestSnapshot` pointer for `kernel_snapshot`
  envelopes) rather than modeling full per-driver race state — that
  reducer is the actual race views' job, not built yet. `applyReleasedEnvelopes`
  folds a whole released batch into ONE new `events` array (one spread),
  not `.reduce(applyReleasedEnvelope, state)` (one spread PER envelope in
  the batch) — found the hard way running `/race` against a real
  backfilled race at `speed=20`: a single tick can release thousands of
  envelopes against an `events` array already tens of thousands long, and
  the reduce form re-copies that whole array once per released envelope,
  which is what pegged the browser tab and ran memory away (the WebSocket
  section above covers the other half of that fix — excluding
  `car_telemetry` from the stream by default in the first place).
- **`store.ts`**'s `Store` is a small hand-rolled observable, not
  `svelte/store` — keeps the whole module importable/testable with zero
  Svelte runtime, while its `subscribe(run)` (call immediately with the
  current value, return an unsubscriber) still satisfies Svelte's own
  store contract, so `$store` auto-subscription works unmodified in any
  `.svelte` file.
- **`controller.ts`**'s `DelaySyncController` is the seam a view actually
  talks to: `ingest`/`tick` drive the buffer and fold releases into
  `visibleState`; `setDelaySeconds` (the slider) and `calibrateFromTap`
  (`calibration.ts`'s `delaySecondsFromTap` — "tap when the lights go
  out": delay = tap wall-time minus the known event's own
  `event_time_utc`) both clamp into `[minDelaySeconds, maxDelaySeconds]`
  and set `delayState.synced = true` — the "so the user trusts it" half
  of the always-visible indicator.
- Three Svelte components (`src/lib/components/`) sit on top, each a thin
  view over the controller with no logic of its own: `DelaySlider` (a
  0-120s range input), `TapToSync` (disabled until a caller-supplied
  `anchorEventTimeUtc` is known), `DelayIndicator` (current delay +
  synced/unsynced). `routes/delay-sync/+page.svelte` is Delay Sync's own
  dev harness — connects a real `SessionStream` in `mode=replay`, feeds it
  through the controller, ticks on a 250ms interval, and renders the three
  components plus the raw `visibleState` counters — proving
  `SessionStream -> ingest -> tick -> visibleState` end-to-end against a
  real replayed session, the same way `/` proves the REST path. It is
  explicitly NOT a race view: no track map, no degradation/undercut panel.

### Tests: where spoiler-safety is actually proven

```sh
just web-test          # pnpm --filter @pitwall/web test (vitest run)
```

All of `src/lib/delay-sync/` is pure TS with no DOM, so the whole suite
runs headless with no browser and no backend:

- a seeded-PRNG property test (`timeline-buffer.test.ts`) across 200
  randomized trials — random envelope counts, random arrival order,
  random non-monotonic clock-tick schedules — asserting the zero-leak
  guarantee: no envelope `release` ever hands back has an `event_time_utc`
  after the clock it was released at;
- a dedicated `controller.test.ts` case proving a high-confidence
  `kernel_snapshot` with a future `event_time_utc` stays withheld right up
  to (never before) its own boundary, and appears the instant the
  playback clock reaches it;
- `calibration.test.ts` checks `delaySecondsFromTap` against a simulated
  tap yields the correct offset;
- determinism tests (buffer- and controller-level) confirming a fixed
  ingest sequence plus a fixed clock schedule always produces the same
  released envelopes / visible state.

Manually verified end-to-end in a browser too: `f1-api` against the
checked-in kernel fixture, `just web-dev`, `/delay-sync` — selecting a
session connects the real replay WebSocket, the visible-event/caught-up
counters climb as envelopes release, and dragging the delay slider
updates the indicator from "Unsynced" to "Synced" immediately.

## Track map (3.4 — the first race view)

Table stakes visually, per CLAUDE.md's own brief ("the pretty track map is
table stakes — it is NOT the product. Do not prioritize it"): the
differentiation is entirely in painting real, derived data onto a real
shape, not in the map existing at all.

### The outline build step (backend)

`f1_kernel.track_outline.derive_track_outline` is a pure transform: given
one lap's worth of ordered (x, y) position samples, it dedupes, closes the
loop, and resamples evenly by arc length into a `TrackOutline`. It
deliberately does **not** center or rescale the shape — `points` stays in
the SAME raw coordinate units the input arrived in (the feed's own ~1/10 m
units per `f1_domain.CarPosition`), so a LIVE `car_position` sample from
the same circuit plots directly alongside it with no extra transform.
Fitting the shape to a screen (aspect-preserving scale-to-canvas) is a
display concern the frontend owns (`$lib/track-map/geometry.
fitOutlineToCanvas`), computed once per render and reused for the outline
and every car marker so the two can never drift apart.

Position is a channel this project only ever observes LIVE or via FastF1's
own matching telemetry — `f1_backfill`'s historical parquet carries none of
it (DATA_CONTRACTS.md), so an outline can never be derived from
`HistoricalSource`. `tools/build_circuits.py` gained the calling half: it
already loads a real FastF1 session for corners/DRS zones, so deriving the
outline from that SAME session's fastest lap's own position telemetry
(FastF1's `X`/`Y` columns) is one more step over data already in hand, not
a new dependency. `Circuit.outline` caches the result per circuit,
`None` until that tool next runs against a populated FastF1 cache — no
shipped circuit has one today (PROVISIONAL by omission, the same honesty
`docs/PHASE2.md`'s other not-yet-calibrated circuit fields already carry).

### The read model

`GET /sessions/{key}/circuit` (`f1_api.schemas.CircuitOut`) is the one new
backend seam this prompt needed — corners, DRS zones and the outline
(`TrackOutlineOut`, `null` until built), mirroring `f1_kernel.Circuit`
field-for-field. Returns `null` (never a 404) when the session's event
matches no shipped circuit, the same degrade-gracefully rule
`f1_kernel.circuit_for_event` already documents. Car positions themselves
need no new endpoint: `car_position` is already a `DomainEvent` kind, so it
already passes through `/stream/{key}` verbatim — the moment a session's
own event stream carries it, the track map renders it, with no protocol
change.

### The frontend (`apps/web/src/lib/track-map/`)

Framework-agnostic pure TypeScript, the same split Delay Sync (3.3)
established — zero Svelte/SvelteKit imports in the core, a thin Svelte
component on top:

- **`car-state.ts`** folds `VisibleSessionState.events` into per-driver
  `CarMarker`s (position, team, compound) — the per-domain-event reducer
  `$lib/delay-sync/visible-state.ts` explicitly left to "the actual race
  views' job." Reads `car_position`, `driver_list_updated` and
  `lap_completed`/`stint_started` (for compound); mutates in place for one
  reason: a race has ~20 cars and this runs once per released envelope over
  a whole session, and nothing needs referential-equality change detection
  on the result (the renderer re-reads it every animation frame anyway).
- **`interpolation.ts`** smooths motion between position samples. Delay
  Sync only ever hands a view samples already due, never a future one, so
  the render clock is usually AT OR PAST the newest known sample, not
  neatly between two — this lerps between the two most recent raw samples
  and CLAMPS at the newest once the clock passes it, holding position there
  (rather than extrapolating a car off its own track) until the next
  sample arrives. A hold that goes on suspiciously long is flagged `stale`
  so a renderer can fade the marker instead of pretending it's still live.
- **`arc-length.ts`** projects a car's (x, y) onto the outline as a lap
  fraction (nearest-vertex distance, cheap over a ~200-point polyline) —
  the primitive both the renderer's outline-fitting and dominance tracking
  are built on.
- **`dominance.ts`**'s `DominanceTracker` is the "shape" feature: it
  measures each driver's transit time through `DEFAULT_SEGMENT_COUNT`
  equal arc-length segments purely from consecutive `car_position` samples
  — no telemetry/distance channel needed, since our domain events carry no
  spatial sector-boundary data (`SectorCompleted` is a time split, not a
  place on track). A segment's record is replaced only by a STRICTLY
  faster transit (session "purple" semantics), colored by that driver's
  team.
- **`frame.ts`**'s `buildDrawFrame` resolves car-state, interpolation,
  dominance and team-color lookup (`team-colors.ts`, a static name-substring
  map — colors only, never logos, per CLAUDE.md's legal posture) into a
  plain `DrawFrame` with no canvas dependency, so **`renderer.ts`**'s
  `TrackMapRenderer` stays "draw what you're told": a single
  full-clear-and-redraw Canvas2D pass per frame (one ~200-point outline,
  up to `DEFAULT_SEGMENT_COUNT` dominance arcs, ~20 car markers — cheap
  enough that dirty-region tracking would cost more than it saves at this
  shape count).
- **`TrackMap.svelte`** is the thin glue: canvas sizing via
  `ResizeObserver` + device pixel ratio, an `requestAnimationFrame` render
  loop driven off the SAME playback clock (`playbackClockMs`) Delay Sync
  itself releases envelopes against, and `prefers-reduced-motion` — under
  reduced motion the interpolation tween is skipped (cars snap to each
  raw sample) and the component redraws only when `visibleState` actually
  changes rather than continuously ticking. It reads `visibleState` only,
  same invariant every other view follows.

### Tests

```sh
just web-test    # includes 90 track-map vitest cases, no DOM
```

Pure-logic coverage with no DOM: arc-length projection/round-tripping,
interpolation clamping and staleness, the car-state reducer (position
updates, driver-list merges, compound updates, malformed-payload
tolerance), `DominanceTracker` (no time from a single sample, a strictly
faster transit taking over a segment, a slower one never doing so,
out-of-order samples ignored, lap-boundary wraparound, `reset`), the
`RaceStateReducer`'s cursor (only newly-appended envelopes are processed,
never a full rescan) and `buildDrawFrame`. `python/f1_kernel/tests/
test_track_outline.py` covers the backend half the same way — synthetic
point clouds (a rectangle, a circle) standing in for a real position
trace, since a real one needs a populated FastF1 cache this repo does not
ship (the same three-layer discipline `docs/PHASE2.md` documents for
every other kernel derivation).

### Dev harness (`/track-map`)

The same role `/delay-sync` plays for Delay Sync: proves `SessionStream ->
DelaySyncController -> visibleState -> TrackMap` end-to-end in a browser.
Unlike Delay Sync's own harness, no environment this was built in has a
real live/store-backed session with `car_position` data — historical
(backfill) sessions never carry it at all (DATA_CONTRACTS.md) — so the
harness defaults to a "synthetic preview": three cars looping a stand-in
oval at different lap times through the exact same pipeline a real session
would use, clearly labeled in the UI as synthetic. All six shipped circuits
now carry a real outline (see "What's not covered yet" below), so
selecting a real session on one of those draws the real track shape (with
no cars on it — no historical session carries `car_position`, the
remaining gap); an event this repo has no shipped circuit for still proves
the graceful-degradation path — "no track outline available for this
circuit yet" — rather than a blank or broken canvas. As of 3.6, the
synthetic preview is
also what `just verify-phase3`'s `track-map-animation.spec.ts` drives to
prove the rendering pipeline genuinely animates (see 3.6's own section for
why a REAL session can't be used for that assertion). `geometry.ts`'s
`outlineFromApi` (the REST-response-to-`TrackOutline` conversion this
harness needs) moved into `$lib/track-map` itself in 3.6 so the race
dashboard could reuse the exact same conversion rather than a second copy.

### What's not covered yet

- Every shipped circuit now has a real `outline` (`tools/build_circuits.py`'s
  outline step, run against a populated FastF1 cache — see
  docs/PHASE2.md's circuit-metadata section for the pit-loss-calibration
  gotcha re-running that tool carries). This was flagged as an accepted
  "documented graceful-degradation state" for most of Phase 3 — every
  circuit, every race, no outline ever rendered — which on reflection was
  the map feature being dark everywhere, not a per-circuit edge case, so
  it got fixed rather than left as steady state. The graceful-degradation
  path itself is still real and still covered (an event with no shipped
  circuit file at all, e.g. a season this repo hasn't backfilled).
- (Superseded 2026-09-24.) This originally noted that no session had real
  `car_position` data. Replays now do: the backfill keeps F1's measured
  positions (`car_positions`, from the static archive's `Position.z`), the
  WebSocket sends them as one `car_positions` frame per feed instant (never
  one envelope per sample; see `f1_api.stream.replay`), Delay Sync keeps
  the released frames in a bounded `positionFrames` window, and the race
  desk's map (`DeskMap`) draws them through `MeasuredPositions`. The live
  feed still withholds positions from an unauthenticated client, so a live
  session is estimated until its positions are imported after the session
  (`f1-backfill --positions-only --uncatalogued`).

## Kernel-analytics views (3.5)

The other three race views besides the track map: Phase 2's payoff
analytics, rendered. Same split as Delay Sync and the track map —
framework-agnostic pure TS core (`apps/web/src/lib/kernel-views/`) plus
thin Svelte components (`apps/web/src/lib/components/`) — and the same
rule: every view reads `controller.visibleState` (or a REST fetch gated by
it), never a raw stream.

- **`UndercutCountdown.svelte`** needs no REST call and no separate
  spoiler gate at all: `kernel_snapshot` envelopes (Phase 3.1's
  `LapSnapshotFeed`, one per newly-completed lap that produced a
  crossover) already arrive through `/stream/{key}` and are released by
  Delay Sync like any other envelope, so `visibleState.latestSnapshot` IS
  the current projection. Renders the focused battle's headline
  (open/opens-in-N/closed/unknown), a margin-if-covered/rejoin-gap/
  decision-lap/pit-loss stat row, a small SVG confidence-interval bar (the
  95% CI band on the covered margin, point estimate, and a parity line —
  never a bare number), and `reasons` when confidence is anything but
  high. A `<select>` switches the focused battle when more than one duel
  is live.
- **`GapStreamGraph.svelte`** over `$lib/kernel-views/gap-stream.ts`'s
  `GapStreamReducer`: folds the raw `gap_updated` stream (not `/laps`'
  once-per-lap MEDIAN) into a per-driver time series, so the chart
  actually flows rather than stair-stepping. Canvas-rendered
  (`gap-stream-renderer.ts`) — a full race's `gap_updated` cadence reaches
  the low thousands of samples per driver across ~20 cars, the same
  "Canvas beats SVG at this point count" tradeoff `renderer.ts` makes for
  the track map. Bands are thick translucent team-colored strokes (never a
  literal stacked streamgraph — gap-to-leader per driver is not a
  decomposition of a whole), so parallel/crossing runs read as shapes.
  Click a legend chip to highlight one driver's band.
- **`DegradationCurves.svelte`** and **`BrakingPointDrift.svelte`** are
  the two REST-fetched views (`/degradation` and `/telemetry/{driver}` +
  `/analysis?braking_drivers=`) — the one place this prompt's own "views
  read `visibleState` only" rule needs a second gate, because those
  responses describe a WHOLE session/stint up front. `$lib/kernel-views/
  reveal.ts`'s `RevealTracker` folds `visibleState.events` (envelopes
  Delay Sync has ALREADY released) into "which stints have provably
  finished" (`isStintRevealed`: the driver has been seen starting a LATER
  stint, or the session itself finished) and "which laps have provably
  completed" (`isLapRevealed`, precise per lap for braking rows) — a fit
  or a lap's own row only renders once its own reveal check says yes.
  Never recomputes an analytic; only decides WHEN an already-computed one
  may be shown. Both render as SVG (a couple of fitted lines/CI bands, or
  one corner's points for one driver — small point counts, the opposite
  tradeoff from the gap-stream graph) with a `ConfidenceBadge` per fit and
  dashed strokes for anything below `good` reliability.
- `$lib/kernel-views/linear-fit.ts` (`sampleCurve`/`findCrossover`) and
  `iso-duration.ts` (`parseIsoDurationSeconds`, the ISO-8601 duration
  parser every WS-sourced `timedelta` field needs — the REST layer already
  normalizes these to plain floats via `f1_api.convert`, but the
  WebSocket serializes `DomainEvent`s verbatim) are the shared math/parsing
  underneath the components above; `$lib/timing-tower` (3.6) reuses
  `parseIsoDurationSeconds` rather than a second copy.

### Tests

```sh
just web-test    # kernel-views vitest cases, no DOM
```

Pure-logic coverage with no DOM: the gap-stream reducer (cursor-based
fold, ISO-duration parsing, lapped-vs-unknown gap handling), `RevealTracker`
(stint/lap reveal transitions, session-finished via terminal status or a
CHEQUERED flag), `linear-fit`'s curve sampling and crossover-finding math,
and `iso-duration`'s parser against pydantic's actual output shapes.

### Dev harness (`/analytics`)

The same role as every other harness: proves `SessionStream ->
DelaySyncController.ingest -> tick -> visibleState -> view` end-to-end for
all four views at once, mobile-first (a single stacked column — no
side-by-side desktop layout, unlike 3.6's dashboard below). Runs a chosen
session at a fixed `speed=20` replay.

## The race dashboard (3.6)

Everything above this section is a proven-in-isolation piece; this is
where they get composed into the actual product — the first screen this
project's own "what this is" (CLAUDE.md) describes, and the Phase 3 E2E
verification harness alongside it.

### The timing tower (`$lib/timing-tower/`)

The one race view that did not already exist: the anchor of any timing
screen (classification position, gap, interval, last lap, tyre), and the
per-domain-event reducer `$lib/delay-sync/visible-state.ts`'s own
docstring explicitly deferred to "the actual race views' job" — same
split as `$lib/track-map/car-state.ts` and `$lib/kernel-views/gap-stream.ts`
before it. `TimingTowerReducer` folds `position_changed`, `gap_updated`,
`lap_completed`, `stint_started`, `pit_entry`/`pit_exit` and
`driver_list_updated` into one `DriverRow` per driver (mutating in place —
the same ~20-drivers-re-read-every-render reasoning every other reducer in
this codebase gives); a row NEVER regresses a known field back to unknown
on a sparser later update (e.g. a `lap_completed` that omits `compound`
does not blank out the last known compound). `sortedRows` orders by
classification position ascending, with drivers the feed hasn't placed yet
sorted after every classified one — never treated as P0. `TimingTower.svelte`
renders it as a horizontally-scrollable table (mobile-first: columns never
wrap, the table scrolls instead) with a colored compound badge, a tyre-age
number, an in-pit row highlight + `PIT` badge, and a personal-best lap
highlighted. Tested with no DOM (`reducer.test.ts`): every event kind's
fold, the "never regress a known field" rule, position-based sort order
including the unclassified-drivers-last rule, and the reducer's
cursor-based fold discipline.

### The dashboard page (`/race`)

Composes, for a chosen session: the timing tower, the undercut/overcut
countdown, the track map, and a SWITCHABLE analytics panel (tabs over gap
stream / degradation / braking — only the active tab's component is
mounted, so switching away tears down its REST fetch/canvas loop rather
than running all three at once) — plus the session/speed picker, Delay
Sync's slider/indicator/tap-to-sync (reused as-is from 3.3), and a "sync
diagnostics" panel (visible-event count, caught-up-to time, and the raw
epoch-ms `event_time_utc`-vs-playback-clock pair a machine can read
directly — see the E2E spoiler-safety test below). Every panel reads
`controller.visibleState` (or a REST fetch gated by `RevealTracker`) only
— composing already-proven-safe views adds no new spoiler-safety surface.

Mobile-first, per CLAUDE.md's own Phase 3 invariant: the DOM order (and
the only layout below 900px) is tower → undercut → map → analytics. At
900px+, `.grid`'s `grid-template-areas` promotes this to a two-column
layout (tower spanning two rows on the left; undercut and map stacked on
the right; analytics spanning the full width beneath) — a `.wide-dashboard`
class on the page's own root element is the ONLY thing that changes,
via `main:has(> :global(.wide-dashboard))` in the root layout, so every
other route keeps its narrower 640px reading column untouched.

**Session picker + speed, deep-linkable.** Unlike every other harness
(which hardcode `speed=20`), this is the one place speed is a real picker
(1x / 5x / 20x / 50x / max), because CLAUDE.md's own ask for this prompt
("so the whole app is demoable on a weekday") wants a genuinely adjustable
demo. The chosen session/speed round-trip into the URL (`?key=&speed=`,
`goto(..., {replaceState: true})` — no history spam) via SvelteKit's
`resolve()` (the pathname is the literal `/race`, never read back off
`window.location`, specifically so `resolve()` can validate it against a
real route at compile time), so a specific demo is a shareable link — and
so `just verify-phase3` can drive this page directly by URL rather than
waiting on the async-populated `<select>`s and clicking through them. A
`delay` param (deep-link-only; the slider is the ongoing UX) is read once
at mount for the same reason.

**A native `<select>`'s `value` ATTRIBUTE does not select anything.**
Worth calling out because it cost real debugging time: `<select
value={x}>` (as opposed to Svelte's `bind:value`) only sets an HTML
attribute, which a `<select>` element does not consult to decide its own
selection — only the `.value` PROPERTY (or an `<option selected>`) does.
Both pickers on this page set it explicitly and imperatively instead, via
`bind:this` + a small `$effect`; the session picker's effect additionally
reads `sessions.length` so it re-applies once the async-fetched `<option>`s
actually exist, picking up a URL-driven key set before they render.

### `event_time_utc` had to be fixed for this to mean anything

Building the E2E suite below is what surfaced a real gap, now fixed (see
the WebSocket section above for the mechanism): `playbackClockMs` compares
an envelope's `event_time_utc` against real wall-clock time. A replayed
2023-2025 (or synthetic-fixture, fixed at 2024-03-02) session's
`ts_utc` values are always in the actual past relative to whenever anyone
actually replays them — comparing that against real "now" would release
every envelope the instant it is ingested, no matter the configured
delay, making the delay slider on this very page a placebo against any
historical session. `f1_api.stream.replay.replay_envelopes` now remaps
`event_time_utc` to the real wall-clock instant each envelope is actually
sent, so replay behaves indistinguishably from live for exactly the
`event_time_utc`-vs-wall-clock comparison Delay Sync depends on.

### Verification: `just verify-phase3`

```sh
just verify-phase3                                    # what CI runs
pnpm --filter @pitwall/web exec playwright test        # equivalent, direct
pnpm --filter @pitwall/web exec playwright test --ui   # interactive debugging
```

`apps/web/playwright.config.ts` declares TWO `webServer` entries — Playwright
starts both, polls each until it answers, and tears both down after the
run (or reuses whatever's already listening outside CI):

- `uv run f1-api --port 8834`, `F1_API_HISTORICAL_ROOT` pointed at the SAME
  checked-in synthetic historical fixture `f1_kernel`/`f1_api`'s own test
  suites use — no `data/`, no live session, no database;
- `vite dev --port 4319 --host 127.0.0.1 --strictPort` (the dev server, not
  a production build — verify-phase3 stays fast, matching `verify-phase0`/
  `verify-phase1`/`verify-phase2`'s own "no data, no network" speed), with
  `PUBLIC_F1_API_BASE_URL` pointed at the API above.

Both entries' `cwd`/env are resolved relative to the REPO ROOT, not the
config file's own directory (`apps/web/playwright.config.ts` sits TWO
levels below it — an off-by-one here silently breaks `F1_API_
HISTORICAL_ROOT`'s relative path while `uv run` itself still starts fine,
since `uv` separately searches upward for the workspace; this cost real
debugging time and is now a comment in the config).

`apps/web/e2e/`, all against the checked-in fixture (`SC_SESSION_KEY =
"2024-1-race"`, round 1, safety car + telemetry; `GREEN_SESSION_KEY =
"2024-2-race"`, round 2, green-flag, no telemetry — mirroring
`api_fixture.py` exactly):

- `dashboard.spec.ts` — the picker and disclaimer render before any
  session is chosen; a deep-linked session (`speed=0`, `delay=`
  `E2E_DELAY_SECONDS`) drives the timing tower, undercut countdown and map
  panel with real data; the green-flag session's countdown panel shows its
  own waiting state rather than crashing; the switchable analytics panel
  actually switches (and un-mounts the inactive tab); and — the spoiler-
  safety assertion — the page's own "sync diagnostics" readout is sampled
  well BEFORE the configured delay elapses (nothing may have released
  yet) and again via `expect.poll` once it clearly has, asserting AT EACH
  SAMPLE that the last released event's `event_time_utc` never exceeds the
  playback clock. `speed=0` plus a short fixed delay is deliberate: since
  the remap above gives every envelope in a `speed=0` stream the SAME
  `event_time_utc` (the connection instant), the whole batch releases in
  one sharp, fast, non-flaky step once the delay elapses, rather than this
  test depending on the fixture race's own simulated duration.
- `track-map-animation.spec.ts` — the one assertion that cannot be made
  against a real replayed session anywhere in this repo (no historical
  session ever carries `car_position` — DATA_CONTRACTS.md): drives
  `/track-map`'s existing, clearly-labeled synthetic preview (3.4) and
  diffs two canvas screenshots ~1s apart, asserting the pixels actually
  changed. The race dashboard's own map panel is asserted present and
  correctly showing its graceful-degradation state in `dashboard.spec.ts`,
  never asserted to animate — reusing the synthetic preview here, rather
  than adding a second synthetic-data path on the real dashboard, keeps
  the product honest: a real session never shows fabricated movement.

Two smaller, incidental fixes this surfaced, both defensive/idempotent and
covered by `f1_api`'s existing pytest suite passing unchanged: `vite.config.ts`
now pre-bundles `openapi-fetch` (`optimizeDeps.include`) so the FIRST route
to newly reach a not-yet-optimized dependency in a fresh dev-server process
doesn't trigger a mid-request full-reload; `f1_api.stream.websocket.stream`
now catches the `RuntimeError` `websocket.accept()` raises when a client
disconnects between the handshake starting and `accept()` actually running
— the same race `_safe_close` already guards on the way OUT, just not
previously guarded on the way IN.

## What Phase 3 explicitly does not do yet

- `mode=live` is implemented (DB-polling) but only genuinely exercisable
  during a real live-ingested session, the same reality `f1_ingest`'s own
  connection layer lives with (docs/PHASE0.md) — not unit-tested here.
- No push-based live delivery (`LISTEN`/`NOTIFY`) — `mode=live` polls.
  Correct, not the last word on efficiency; revisit once a real live
  session exists to develop the push path against.
- No backpressure on `/stream/{key}`'s send loop (`_stream_replay`'s
  producer thread, `_stream_live`'s poll loop). Excluding `car_telemetry`
  by default (see "WebSocket" above) brings a real historical race back
  into the few-thousand-event range this was originally sized for, but a
  client that still can't keep up with the default stream — or a caller
  that opts into `include_telemetry=true` — can still stall consuming the
  queue and, downstream, `websocket.send_json` itself. Logged as a real
  gap, not fixed here: needs either a bounded queue with drop/slow-consumer
  handling or a client-side ack, and neither has a session to develop
  against yet that would actually stress it beyond the fixture.
- No real `car_position` data anywhere in this repo (a data-availability
  gap, not an architecture one — see the Track map section above) and no
  shipped circuit has a real outline yet — so the race dashboard's map
  panel has never been exercised against real telemetry, live or replayed.
- No auth/accounts (CLAUDE.md's explicit instruction for these prompts).


## Session dashboard UI refresh

Opening `/sessions/[year]/[round]` starts the race's normal paced replay (20×,
with speed/restart and Delay Sync controls). It does not render final REST
classification or link elsewhere to start a second replay. The same page uses
`mode=live` for live-backed sessions. All timing and headline metrics come from
released events. REST lap history is filtered through `RevealTracker` before
position charts or stint bars receive it. Tyre fits and braking trends retain
their completed-stint reveal gates; pit projections use released kernel snapshots.

The timing rail sits beside a switchable analysis stage: positions, tyre
performance, pit strategy and braking. All known drivers are included by default.
Projection details expand on demand. Braking lists only drivers with a revealed
measured trend, automatically opens their trace, and distinguishes missing data
from a stint that has not yet finished on the playback clock.

`DriverRef.full_name` is additive and optional. Names flow from DriverList and
historical results to the API and timing tower. Supplied names/codes take priority;
legacy number-only responses can use explicit 2024–2026 season fallbacks, never
the current calendar year's roster. Unknown numbers remain unknown. The 2026
fallback uses F1's published driver-number list. `features.ts` gates the Story
and Radio surfaces; Story is now ON (see "The race story" below), Radio stays
off until a transcribed corpus exists to serve.

`session-review.spec.ts` verifies lap-by-lap progression, no final classification
at entry, reset on restart, all-driver names, mobile overflow, measured braking
and a single missing-data state. `dashboard.spec.ts` retains Delay Sync checks.


## One dashboard for replay and live

`$lib/session-detail/RaceWorkspace.svelte` is the race dashboard, and BOTH
`/sessions/[year]/[round]` (a replay) and `/live` (a session in progress)
render it. The routes only resolve *which* session — the URL for one, a
15s poll of `/sessions` for `provenance === 'live'` for the other — then
hand the same `SessionSummaryOut` to the same component.

This is what makes "test against a replay" mean something. The two pages
previously composed the same panels into two different arrangements, kept
in step by hand, so a fix to one silently missed the other. The only
differences that remain are the ones that genuinely exist: a live session
has no playback speed to choose and nothing to restart (`isLive` drops
those controls and opens a `mode: 'live'` stream), and `/live` hides the
back-link to the session list.

## The stream had a two-minute cold start

Building this pass surfaced the single biggest reason the dashboard looked
broken: **`/stream/{key}` took ~121 seconds to send its first envelope** on
a real race. `replay_envelopes` called `precompute_session_explanations`
INLINE before yielding anything, and a 2024 race detects ~302 anomalies at
~0.19 s of evidence assembly each. The timing tower, the tyre-performance
panel and every other view sat empty for two minutes and then, at
`speed>=20`, missed most of the race.

Two changes, both in `f1_api`:

- **The batch is capped.** `Settings.max_explanations`
  (`F1_WHY_MAX_EXPLANATIONS`, default 12) narrates only the most salient
  moments. `f1_api.why.most_salient` is the one place that trims.
  DETECTION stays uncapped — `GET /sessions/{key}/anomalies` still returns
  everything, now with an optional `?limit=`.
- **The batch no longer blocks the stream.** `f1_api.why.ExplanationSchedule`
  is a thread-safe hand-off; `_stream_replay` starts a worker that builds
  the batch against its OWN `resolve_session` source (never sharing a
  `StoreSource`'s database connection across threads) while events are
  already flowing. `replay_envelopes` picks the batch up whenever it lands.

Time to first envelope on 2024 round 1 went from **121.6 s to 2.8 s**.
Spoiler-safety is unchanged: an explanation arriving LATE is always safe
(it is still paced to at-or-after its own `anomaly_time`, through the same
wall-clock remap as every other envelope); only arriving early would leak,
and nothing here can cause that.

`GET /sessions/{key}/story` exists for the same reason: the story page used
to list anomalies and then fetch one explanation per row, each of which
re-ran `analyze_session` + `detect_anomalies` server-side. One route now
detects once and returns ranked, narrated moments.

## The race story

`features.SHOW_RACE_STORY` is on. Three surfaces:

- **The `Race story` tab** on the race workspace
  (`$lib/session-detail/RaceStoryPanel.svelte`) — one `GET /sessions/{key}/story`
  call, gated by `RevealTracker.isLapRevealed` so a moment appears only
  once the lap it happened on has been completed on the playback clock. A
  story describes the whole race up front, so without that gate opening
  the tab would hand the user the ending; the panel says how many later
  moments are still hidden.
- **The `Why` lane** in the timing rail (`ExplanationLane`) — live
  `explanation` envelopes, newest first. Needs no second gate: the
  envelopes are paced on `anomaly_time` already.
- **`/story`** — the standalone, shareable ranked narrative.

Narration backends are resolved in `f1_api.why.get_narration_model`:
`F1_API_WHY_FIXTURE` → the citing fixture model, else `OPENROUTER_API_KEY`
→ `f1_why.OpenRouterModel` (`OPENROUTER_MODEL`, default
`minimax/minimax-m3:free`), else `ANTHROPIC_API_KEY` → `ClaudeHaikuModel`,
else a declining mock. With no key at all, every explanation is the honest
canonical fallback — never a guess, and no test or CI run needs a key. See
docs/PHASE5.md for the OpenRouter backend's own guarantees.

## Charts

- **Race positions** (`RaceShapeChart`) is the lap-by-lap position chart,
  with gap-to-leader as a second mode in the dropdown. It is team-coloured
  with the second car of each team dashed (`teammateDashOrder`), labels
  each line with the driver's code at the right-hand edge in running order,
  marks every pit stop with a compound-coloured diamond, shades safety-car
  and VSC laps (`trackStatusBands`, taken across the whole field so a
  driver who was in the pits cannot hide a neutralisation), and reads out
  the running order at any lap on hover. The previous version cycled a
  10-colour palette across 20 drivers, so two drivers shared a colour and
  no line could be identified without hovering.
- **Braking** (`BrakingOverview` + `$lib/session-detail/braking.ts`) is now
  a corner map for ONE driver: median brake-application distance per
  corner with its lap-to-lap spread, the fitted trend, and a per-lap trace
  on expand. It previously requested `/analysis?braking_drivers=` for the
  whole field — a telemetry load per driver, minutes of server work — and
  never finished rendering. `driftDirection` reports "steady" whenever a
  fit's own confidence interval spans zero rather than reading a direction
  off the point estimate.
- **Tyre performance** needed no code change: it was empty only because
  nothing streamed for two minutes, so `RevealTracker` never saw a stint
  end.

## Layout

The rail (timing tower → undercut countdown → why lane) sizes to its
content rather than taking a fixed fraction of the width, and sticks on
tall viewports with the TOWER capped and internally scrolled — capping the
rail itself clipped the panels below it. The undercut countdown moved out
of the analysis tabs (where it was duplicated across two of them) into the
rail, since "what happens next" should stay on screen whichever deeper
read is open.

Below 640px `AppHeader` pins the Delay Sync pill to the bottom of the
viewport. The app shell now reserves room for it (it was covering the
bottom of the timing tower) and the workspace's own duplicate sync button
stands down at that width.
