<div align="center">

# FastPitStop

**A spoiler-safe Formula 1 second-screen companion that predicts as well as displays.**

### [🏁 Open the app: fastpitstop.online](https://fastpitstop.online/)

[Production](https://fastpitstop.online/) ·
[Getting started](#getting-started) ·
[Architecture](docs/ARCHITECTURE.md) ·
[Deploy guide](DEPLOY.md)

[![Production: fastpitstop.online](https://img.shields.io/badge/production-fastpitstop.online-e10600)](https://fastpitstop.online/)
![Status: beta](https://img.shields.io/badge/status-beta-orange)
![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![SvelteKit + TypeScript](https://img.shields.io/badge/SvelteKit-TypeScript-FF3E00?logo=svelte&logoColor=white)
![TimescaleDB + pgvector](https://img.shields.io/badge/TimescaleDB-pgvector-FDB515)

*Unofficial fan project. Not associated with Formula 1.*

</div>

---

Most F1 dashboards show you the timing feed and stop there. FastPitStop also
works out things the feed doesn't tell you, like how fast each tyre is
wearing, whether an undercut will work, and why a driver suddenly lost two
seconds. It holds all of it back to match your delayed TV broadcast, so the
app never gives away something you haven't seen yet.

## Try it

The production app is live at **<https://fastpitstop.online/>**, currently in
public beta. Nothing to install: open it on your phone next to the TV, pick a
live session or replay a past race, and set your broadcast delay.

Want to run it yourself instead? See [Getting started](#getting-started).

## Features

### Delay Sync

A TV or streaming broadcast runs anywhere from a few seconds to a minute
behind the real race. You set your delay with a slider, or tap once when the
lights go out, and every number, position, chart and prediction waits until
your picture has reached that moment. This is a hard rule with tests behind
it: if anything ever shows ahead of your clock, that is a failing test, not a
cosmetic bug.

### Analysis on top of the timing

- **Timing tower:** positions, gaps, tyre compound and age, pit stops and
  personal bests.
- **Undercut / overcut countdown:** for the closest battles on track, it
  predicts whether pitting now would get a driver ahead, and on which lap,
  with a margin and a confidence band instead of one bare number.
- **Race positions chart:** lap by lap, with pit stops and safety-car periods
  marked.
- **Tyre performance:** a degradation curve for each stint, fitted from
  fuel-corrected laps, with a verdict on how far to trust each fit.
- **Braking-point drift:** for each corner, how a driver's braking point
  moves as tyres and brakes wear, taken from the brake channel.
- **Track map:** draws F1's own recorded car positions when you replay a
  race. In a live session, where those positions aren't available, it
  estimates them from lap timing and says so.
- **"Why" lane and race story:** when something notable happens (a pass, a
  slow stop, a sudden loss of pace, a tyre falling off a cliff, an undercut
  working or failing), the app writes one sentence explaining it. Every
  sentence cites its evidence, and the evidence is only what was known at
  that moment.

### Replay past races as if they were live

Races from 2023 onward can be replayed at any speed through the same
dashboard, with the same spoiler protection as a live session.

## Design principles

- **Never spoil the race.** Timing values, kernel predictions, radio clips
  and explanations all pass through the same Delay Sync gate before they
  reach the screen.
- **Be honest about confidence.** Every derived number says how much to trust
  it (a fit is *good*, *thin* or *unreliable*; a prediction is *high*,
  *medium* or *low*). A fit from four messy laps looks different on screen
  from one based on fifteen clean laps.
- **Measure, don't guess.** Constants such as fuel effect per kg, pit-lane
  time loss and compound pace offsets are fitted from real race data. Each
  one records where its value came from, and anything not yet calibrated
  says so.
- **One code path.** Live timing, recorded replays and historical data all
  become the same stream of events and go through the same pipeline. The
  analytics are deterministic, so they can be tested against past races.
- **Explain, don't make things up.** The language model only describes
  evidence the app has already gathered. It must cite that evidence, and a
  check rejects any sentence that doesn't. With too little evidence, the app
  says "no clear cause found" rather than guessing.

## Getting started

### Prerequisites

| Tool | Version | Used for |
| --- | --- | --- |
| [Python](https://www.python.org/) | 3.12+ | Ingest, store, analytics kernel, API |
| [uv](https://docs.astral.sh/uv/) | latest | Python workspace and dependencies |
| [Node.js](https://nodejs.org/) | 22 | Frontend |
| [pnpm](https://pnpm.io/) | 10 | JS workspace and dependencies |
| [just](https://github.com/casey/just) | latest | Task runner (`just` lists every recipe) |
| [Docker](https://docs.docker.com/get-docker/) | with Compose v2 | Optional: only for the containerised stack |

### Install

```sh
git clone https://github.com/Danieldotcomcoder/fastpitstop.git
cd fastpitstop
just setup   # uv sync + pnpm install + Playwright's Chromium for the E2E suites
```

### Option A: run the full stack in containers

```sh
cp .env.example .env   # Compose reads it; the defaults work for local dev
just up                # db + api + web with hot reload, migrations, one seeded race
```

- Frontend: <http://localhost:5173>
- API: <http://localhost:8000> (OpenAPI docs at `/docs`)
- Database: `postgresql://pitwall:pitwall@localhost:5432/pitwall`

`just up` seeds one race from the synthetic recording the test suites use,
so a fresh clone has something to look at straight away. `just down` stops
the stack and keeps its data.

### Option B: run the API and frontend directly, on real data

No database is needed for this path. When `F1_STORE_DSN` is unset, the API
serves sessions straight from the historical backfill on disk.

```sh
# 1. Download one race from FastF1 + OpenF1 into data/historical/ (takes a few minutes)
uv run f1-backfill --season 2024 --round 5

# 2. Start the API on http://127.0.0.1:8000
just serve-api

# 3. In a second terminal, start the frontend on http://localhost:5173
just web-dev
```

Leave out `--round` to download a whole season. Downloads are cached and
resumable, and the OpenF1 calls stay within its free-tier rate limit.

### Configuration

Every variable is documented in [`.env.example`](.env.example) (stack-wide)
and [`apps/web/.env.example`](apps/web/.env.example) (frontend). The ones you
are most likely to touch:

| Variable | Default | Purpose |
| --- | --- | --- |
| `F1_STORE_DSN` | unset | TimescaleDB connection string. Unset means historical sessions only, no live data. |
| `F1_API_HISTORICAL_ROOT` | `data/historical` | Where the API looks for backfilled races. |
| `PUBLIC_F1_API_BASE_URL` | `http://127.0.0.1:8000` | Absolute URL the frontend uses to reach the API. |
| `OPENROUTER_API_KEY` | unset | Narration for the "why" engine via OpenRouter (free tier works). |
| `ANTHROPIC_API_KEY` | unset | Narration via Claude Haiku, used when no OpenRouter key is set. |

With neither narration key set, explanations fall back to "No clear cause
found in the available evidence." Nothing in development or testing needs a
live API key.

## Development

### Common tasks

| Command | What it does |
| --- | --- |
| `just check` | Everything CI runs: lint, typecheck, tests and every phase verification |
| `just lint` / `just fmt` | Ruff lint and format check / apply fixes |
| `just typecheck` | Pyright |
| `just test` | Python test suite (pytest) |
| `just web-check` | svelte-check and ESLint for the frontend |
| `just web-test` | Frontend unit tests (Vitest), including the Delay Sync spoiler-safety property tests |
| `just verify-phase0` … `just verify-phase5` | Each phase's release gate (see the phase docs) |
| `just export-openapi` then `just web-generate-client` | Regenerate the API contract and the typed TS client after changing a response model |
| `just backtest-phase2` | Refresh the calibration and backtest reports from your local backfill |

### Testing

The test suites run green with **no downloaded data, no database, no network
and no API key**. They use checked-in synthetic fixtures with exact ground
truth. Analytics are tested against replayed races, not mocks.

Some suites are opt-in and skip themselves unless enabled:

- Tests over real races in `data/historical/` run when that backfill is
  present.
- `F1_STORE_RUN_DB_TESTS=1` runs the store tests against a real TimescaleDB.
- `F1_RADIO_RUN_MODEL_TESTS=1` runs the radio tests against the real local
  speech and embedding models.

The phase verifications for the frontend (`verify-phase3` to
`verify-phase5`) use Playwright to boot a real API and frontend and drive
Chromium through the dashboard, including spoiler-safety checks on the
rendered page.

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
| 0 | **Replay harness.** Records the live feed exactly as received, replays it at any speed, and downloads historical data, so development can happen against past races on any weekday. | Done |
| 1 | **Ingest and store.** Turns the raw feed into typed domain events (laps, stints, pit stops, gaps, race control, radio) and stores them in TimescaleDB, as an append-only log and as query tables. | Done |
| 2 | **Analytics kernel.** Pure, deterministic analytics backtested against past races. | Done |
| 3 | **Frontend.** Delay Sync, the race dashboard and the track map. Mobile-first and drawn on Canvas. | Done |
| 4 | **Team radio corpus.** Downloads driver–pit radio, transcribes it locally with faster-whisper, adds race context, and makes it searchable by meaning with pgvector. | Done (UI hidden until a full transcribed season is loaded) |
| 5 | **"Why" engine.** Finds anomalies, gathers the evidence for each, and has a small language model write one grounded, cited sentence. | Done |
| 6 | **Wet-track vision.** Would run in a browser extension on the user's own stream, so no video frame ever reaches our servers. | Not started |

### Results so far

- **Undercut predictor:** tested on 10 races from 2025 that the model was
  never tuned on. Predictions are made the lap before each real stop, using
  only data available up to that lap. High-confidence calls were right
  **85.3%** of the time and low-confidence calls **64.5%**, so the
  confidence levels track real accuracy
  ([report](python/f1_kernel/reports/undercut_backtest.md)).
- **Pit-lane time loss:** measured from 410 green-flag stops across the 2024
  season, from about 18.8 s at Spa to 30.1 s at Singapore
  ([report](python/f1_kernel/reports/pit_loss.md)).
- **Fuel effect:** 0.0456 s per kg, fitted from 780 stints across 23 races
  from 2024 ([report](python/f1_kernel/reports/fuel_coefficient.md)).

### Known limitations

- Live sessions reach the dashboard by polling the database. That path can
  only be fully tested during a real live session.
- The live feed doesn't give car positions to an unauthenticated client.
  During a live session the map estimates positions from lap timing; the
  recorded positions are imported from F1's archive after the session.
- Many braking, degradation and anomaly parameters still use provisional
  defaults and haven't yet been fitted to measured data.

### Tech stack

- **Backend, ingest and analytics:** Python 3.12 (uv workspace), polars,
  FastAPI
- **Storage:** TimescaleDB (Postgres) with pgvector
- **Frontend:** SvelteKit and TypeScript (pnpm workspace), Canvas
- **Radio:** faster-whisper and sentence-transformers, both running locally
- **Narration:** a small, cheap language model (OpenRouter's free tier, or
  Claude Haiku) behind one interface that can be swapped out or mocked
- **Hosting:** Docker Compose on a single server, with Caddy in front

## Repository layout

| Path | Contents |
| --- | --- |
| `python/f1_recording`, `f1_ingest`, `f1_replay`, `f1_backfill` | Phase 0: the recording format, live recorder, replayer and historical backfill |
| `python/f1_domain`, `f1_parse`, `f1_store`, `f1_historical_adapter`, `f1_pipeline` | Phase 1: domain types, feed parsing and state machine, the TimescaleDB store, the historical adapter, and the pipeline that connects them |
| `python/f1_kernel` | Phase 2: the analytics kernel, circuit metadata, and calibration and backtest reports |
| `python/f1_api` | The read-model API, the only thing the frontend talks to |
| `python/f1_radio` | Phase 4: the team radio corpus |
| `python/f1_why` | Phase 5: anomaly detection, evidence gathering and grounded narration |
| `apps/web` | The SvelteKit frontend |
| `deploy/` | Deployment scripts and the Caddy config |
| `docs/` | Architecture, data contracts, and one detailed document per phase |

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): how the packages fit
  together
- [`docs/DATA_CONTRACTS.md`](docs/DATA_CONTRACTS.md): recording format,
  domain events and kernel data schemas
- [`docs/PHASE0.md`](docs/PHASE0.md) to [`docs/PHASE5.md`](docs/PHASE5.md):
  each phase's design, exit criteria and runbook
- [`DEPLOY.md`](DEPLOY.md): running the stack in containers and deploying it
  to a single server
- [`claude.md`](claude.md): the full project brief, phase map and
  architectural rules

## Data sources and acknowledgements

- **F1 live timing:** F1's public, unofficial SignalR timing feed. The
  connection follows [FastF1](https://github.com/theOehrly/Fast-F1)'s
  `livetiming` module.
- **[FastF1](https://github.com/theOehrly/Fast-F1):** historical laps,
  telemetry, weather and car positions (2023 onward).
- **[OpenF1](https://openf1.org):** intervals, race control messages and
  team radio URLs.

Thanks to the maintainers of FastF1 and OpenF1. This project would not
exist without their work.

## Contributing

Bug reports and ideas are welcome through
[GitHub Issues](https://github.com/Danieldotcomcoder/fastpitstop/issues),
including anything you run into on the production site at
[fastpitstop.online](https://fastpitstop.online/).
If you open a pull request:

1. Run `just check` (and `just web-check` / `just web-test` for frontend
   changes) and make sure it passes.
2. Keep Python fully typed and clean under Ruff and Pyright. The
   [pre-commit](https://pre-commit.com/) hooks in `.pre-commit-config.yaml`
   run both for you.
3. Back analytics changes with a test over a replayed race, not a mock.
4. If you change an API response model, regenerate `openapi.json` and the
   typed client (`just export-openapi`, `just web-generate-client`) and
   commit both.

## License

This repository does not include a license yet, so all rights are reserved
by default.

## Disclaimer

FastPitStop is an unofficial fan project and is not associated in any way
with the Formula 1 companies, the FIA or any team. F1, FORMULA ONE,
FORMULA 1, FIA FORMULA ONE WORLD CHAMPIONSHIP, GRAND PRIX and related marks
are trade marks of Formula One Licensing B.V. The app uses no F1 or team
logos and never stores broadcast video.
