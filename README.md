# FastPitStop

*Working name: pitwall. Unofficial, not associated with Formula 1.*

FastPitStop is a Formula 1 companion for the second screen. Most F1
dashboards show you the timing feed and stop there. FastPitStop also works
out things the feed doesn't tell you, like how fast each tyre is wearing,
whether an undercut will work, and why a driver suddenly lost two seconds.
It holds all of it back to match your delayed TV broadcast, so the app
never gives away something you haven't seen yet.

It is in beta at [fastpitstop.online](https://fastpitstop.online).

## What it does

**Delay Sync.** A TV or streaming broadcast runs anywhere from a few
seconds to a minute behind the real race. You set your delay with a slider,
or tap once when the lights go out, and every number, position, chart and
prediction waits until your picture has reached that moment. The app treats
this as a hard rule and tests for it. If anything ever shows ahead of your
clock, that is a failing test, not a small visual bug.

**Analysis on top of the timing.** The race dashboard has:

- A **timing tower** with positions, gaps, tyre compound and tyre age, pit
  stops and personal bests.
- An **undercut / overcut countdown**. For the closest battles on track, it
  predicts whether pitting now would get a driver ahead, and on which lap.
  It shows the margin and a confidence band, not just one number.
- A **race positions chart**, lap by lap, with pit stops and safety-car
  periods marked.
- **Tyre performance**: a degradation curve for each stint, fitted from
  laps corrected for fuel, with a verdict on how far to trust each fit.
- **Braking-point drift**: for each corner, how a driver's braking point
  moves as the tyres and brakes wear, taken from the brake channel.
- A **track map** that draws F1's own recorded car positions when you
  replay a race. In a live session, where those positions aren't
  available, it estimates them from lap timing and says so.
- A **"why" lane and race story**. When something notable happens (a pass,
  a slow stop, a sudden loss of pace, a tyre falling off a cliff, an
  undercut working or failing), the app writes one sentence explaining it.
  Every sentence cites the evidence behind it, and the evidence is only
  what was known at that moment.

**Replay past races as if they were live.** Races from 2023 onward can be
replayed at any speed through the same dashboard, with the same spoiler
protection, as a live session.

## Principles

- **Never spoil the race.** Timing values, kernel predictions, radio clips
  and explanations all pass through the same Delay Sync gate before they
  reach the screen.
- **Be honest about confidence.** Every derived number comes with a signal
  saying how much to trust it (for a fit: good, thin or unreliable; for a
  prediction: high, medium or low). A fit from four messy laps looks
  different on screen from one based on fifteen clean laps.
- **Measure, don't guess.** Constants such as fuel effect per kg, pit-lane
  time loss and compound pace offsets are fitted from real race data. Each
  one records where its value came from, and anything not yet calibrated
  says so.
- **One code path.** Live timing, recorded replays and historical data all
  become the same stream of events and go through the same pipeline.
  Because the analytics give the same output for the same input, they can
  be tested against past races.
- **Explain, don't make things up.** The language model only describes
  evidence the app has already gathered. It has to cite that evidence, and
  a check rejects any sentence that doesn't. If there isn't enough
  evidence, the app says "no clear cause found" rather than guessing.

## How it's built

```
F1 live timing (SignalR)   FastF1 + OpenF1 history
          │                          │
          ▼                          ▼
   Replay harness  ──────►  Normalized domain events  ──►  TimescaleDB
   (record / replay)        (one pipeline for live,        (event log +
                             replay and history)            query tables)
                                     │
                                     ▼
                        Analytics kernel (pure Python)
             clean laps · fuel correction · degradation · pit loss
             undercut/overcut · braking drift
                                     │
          Team radio corpus ─────────┤
      (transcribed, searchable)      ▼
                              "Why" engine
              detect anomaly → gather evidence → one cited sentence
                                     │
                                     ▼
                     Read-model API (REST + WebSocket)
                                     │
                                     ▼
                   SvelteKit frontend, gated by Delay Sync
```

The project was built in phases, each with its own exit criteria and
verification step:

| Phase | What it built | Status |
| --- | --- | --- |
| 0 | **Replay harness.** Records the live feed exactly as received, replays it at any speed, and downloads historical data. This lets development happen against past races on any weekday instead of only on race weekends. | Done |
| 1 | **Ingest and store.** Turns the raw feed into typed domain events (laps, stints, pit stops, gaps, race control, radio) and stores them in TimescaleDB, both as an append-only log and as tables for querying. | Done |
| 2 | **Analytics kernel.** Pure, deterministic analytics tested against past races. This is the part that sets the project apart. | Done |
| 3 | **Frontend.** Delay Sync, the race dashboard and the track map. Mobile-first and drawn on Canvas. | Done |
| 4 | **Team radio corpus.** Downloads driver–pit radio audio, transcribes it on our own machine with faster-whisper, adds race context, and makes it searchable by meaning with pgvector. | Done (UI hidden until a full transcribed season is loaded) |
| 5 | **"Why" engine.** Finds anomalies, gathers the evidence for each one, and has a small language model write one grounded, cited sentence. | Done |
| 6 | **Wet-track vision.** Would run in a browser extension on the user's own stream, so no video frame ever reaches our servers. | Not started |

### Results so far

- **Undercut predictor:** tested on 10 races from 2025 that the model was
  never tuned on. Predictions are made the lap before each real stop, using
  only data available up to that lap. High-confidence calls were right
  **85.7%** of the time and low-confidence calls **67.4%**, so the
  confidence levels track real accuracy.
- **Pit-lane time loss:** measured from 410 green-flag stops across the
  2024 season. It ranges from about 18.8 s at Spa to 30.1 s at Singapore.
- **Fuel effect:** 0.0456 s per kg, fitted from 780 stints across 23 races
  from 2024.

### What's still open

- Live sessions reach the dashboard by polling the database. That path can
  only be fully tested during a real live session.
- The live feed doesn't give car positions to an unauthenticated client.
  During a live session the map estimates positions from lap timing, and
  the recorded positions are imported from F1's archive after the session.
- Many braking, degradation and anomaly parameters still use provisional
  defaults and haven't yet been fitted to measured data.

## Tech stack

- **Backend, ingest and analytics:** Python 3.12 (uv workspace), polars,
  FastAPI
- **Storage:** TimescaleDB (Postgres) with pgvector
- **Frontend:** SvelteKit and TypeScript (pnpm workspace), Canvas
- **Radio:** faster-whisper and sentence-transformers, both running on our
  own machine
- **Narration:** a small, cheap language model (OpenRouter's free tier, or
  Claude Haiku) behind one interface that can be swapped out or mocked
- **Hosting:** Docker Compose on a single server, with Caddy in front

## Repository layout

| Path | Contents |
| --- | --- |
| `python/f1_recording`, `f1_ingest`, `f1_replay`, `f1_backfill` | Phase 0: the recording format, live recorder, replayer and historical backfill |
| `python/f1_domain`, `f1_parse`, `f1_store`, `f1_historical_adapter`, `f1_pipeline` | Phase 1: domain types, feed parsing and state machine, the TimescaleDB store, the historical adapter, and the pipeline that connects them |
| `python/f1_kernel` | Phase 2: the analytics kernel, circuit metadata, and calibration and backtest reports |
| `python/f1_api` | The read-model API. It is the only thing the frontend talks to |
| `python/f1_radio` | Phase 4: the team radio corpus |
| `python/f1_why` | Phase 5: anomaly detection, evidence gathering and grounded narration |
| `apps/web` | The SvelteKit frontend |
| `docs/` | Architecture, data contracts, and one detailed document per phase |

## Further reading

- [`claude.md`](./claude.md): the full project brief, phase map and
  architectural rules
- [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md): how the packages fit
  together
- [`docs/DATA_CONTRACTS.md`](./docs/DATA_CONTRACTS.md): recording format,
  domain events and kernel data schemas
- `docs/PHASE0.md` to `docs/PHASE5.md`: each phase's design, exit
  criteria and verification

## Legal

This is an unofficial fan project and is not associated with Formula 1,
the FIA or any team. It uses no F1 or team logos and never stores broadcast
video. Timing data comes from F1's public live-timing feed and from the
community-run FastF1 and OpenF1 projects.
