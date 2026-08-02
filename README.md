# F1 Companion (pitwall)

An unofficial Formula 1 companion app. It goes beyond rendering the live feed:
the differentiator is a tested analytics kernel, novel datasets, and a race
that doesn't get spoiled by delay-synced playback.

See [`CLAUDE.md`](./CLAUDE.md) for the full project brief, tech decisions, and
phase map. Phases 0-3 (replay harness, ingest/store, the analytics kernel,
the frontend) are done bar `mode=live` (wiring the dashboard to an actual
live session); Phase 4 (the team-radio corpus) is current — audio acquisition
and local batch transcription exist, with embeddings/search next.

## Repo layout

- `python/` — uv workspace of Python packages: the Phase 0 replay harness
  (`f1_recording`, `f1_ingest`, `f1_replay`, `f1_backfill`), Phase 1's
  ingest/store (`f1_domain`, `f1_parse`, `f1_store`, `f1_historical_adapter`,
  `f1_pipeline`), Phase 2's analytics kernel (`f1_kernel`), Phase 3's read
  model (`f1_api`), and Phase 4's team-radio corpus (`f1_radio`). See
  `docs/ARCHITECTURE.md`.
- `apps/web` — the SvelteKit frontend (Phase 3): the race dashboard
  (`/race`), Delay Sync, the Canvas track map, and one dev-harness route per
  race view. `packages/` stays empty until something needs sharing across
  more than one frontend app.
- `docs/` — architecture notes, the data-contract spec, and one runbook per
  phase (`PHASE0.md`-`PHASE4.md`).
- `data/` — gitignored local data (recordings, historical tables, FastF1
  cache). Never committed.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python 3.12, managed automatically by uv)
- [pnpm](https://pnpm.io/)
- [just](https://github.com/casey/just)

## Getting started

```sh
just setup   # uv sync + pnpm install + Playwright's Chromium build
just check   # lint + typecheck + test + every phase's verification (same as CI)
```

Other tasks: `just fmt` (auto-format), `just lint`, `just typecheck`,
`just test`, `just verify-phase0` … `verify-phase5` (each phase's own
contract check, all part of `just check`).
Run `just` with no arguments to list every recipe, including the frontend
ones (`web-dev`, `web-build`, `web-check`, `web-test`).

To run the whole stack in containers (`just up`) or deploy it to a VPS
(`just deploy`), see [`DEPLOY.md`](./DEPLOY.md).

## Try the dashboard

No live race (and no `data/historical/` backfill) required — it runs
against the same checked-in synthetic fixture the test suites use:

```sh
F1_API_HISTORICAL_ROOT=python/f1_kernel/tests/fixtures/historical uv run f1-api &
just web-dev
```

Open `http://localhost:5173/race`, pick a session, and pick a replay speed
— the whole dashboard (timing tower, undercut/overcut countdown, track map,
degradation/gap-stream/braking analytics) runs in replay-as-live mode, held
back from spoiling by the Delay Sync slider. See `docs/PHASE3.md` for the
full runbook, and `docs/PHASE0.md`/`PHASE1.md`/`PHASE2.md` for the moat
underneath it.

## Phase 0 — definition of done

Phase 0 (the replay harness) is done when every box below is checked. The
operator runbook for all of it is [`docs/PHASE0.md`](./docs/PHASE0.md).

- [ ] Can record a live session to a valid recording dir (verified on ≥1
      real weekend) — `uv run f1-record`.
- [x] Can replay any recording at arbitrary speed with faithful
      timing/order — `uv run f1-replay <dir> --speed N --delay S`.
- [x] `just verify-phase0` is green in CI (round-trip invariant: record
      stream out == record stream in).
- [ ] Historical 2023→now backfilled to parquet with a catalog —
      `uv run f1-backfill --season <YYYY>`, one run per season.
- [x] `docs/PHASE0.md` lets someone else operate all of the above.
