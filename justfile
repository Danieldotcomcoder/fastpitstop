# Task runner for the F1 Companion monorepo. Run `just` to list recipes.
# Uses plain, shell-agnostic command lines so this works under sh (CI/macOS/
# Linux) and PowerShell (Windows) alike — only the shell used to invoke each
# line differs, per the `windows-shell` setting below.

set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

default:
    @just --list

# Install all Python and JS dependencies for the whole workspace, plus the
# Chromium build Playwright's E2E suite (verify-phase3) needs. A from-scratch
# CI image typically also needs `--with-deps` on this playwright install
# command for the OS-level libraries Chromium needs (apt/dnf packages) —
# omitted here since a normal dev machine already has them from everyday
# browser use; see docs/PHASE3.md.
setup:
    uv sync
    pnpm install
    pnpm --filter @pitwall/web exec playwright install chromium

# Everything CI runs: lint + typecheck + test + the phase contract checks.
check: lint typecheck test verify-phase0 verify-phase1 verify-phase2 verify-phase3 verify-phase4 verify-phase5

# Static checks only (ruff check + format check), no fixes applied.
lint:
    uv run ruff check .
    uv run ruff format --check .

# Auto-fix formatting and lint issues in place.
fmt:
    uv run ruff format .
    uv run ruff check --fix .

typecheck:
    uv run pyright

test:
    uv run pytest

# Phase 0 verification harness: generate a synthetic recording, replay it at
# speed=0, and assert the round-trip invariant (record stream out == record
# stream in, order + payloads identical). Fails loudly if the recorder <->
# replayer contract is ever broken. See docs/PHASE0.md.
verify-phase0:
    uv run python -m f1_replay.verify

# Phase 1 verification harness: replay a checked-in recording end-to-end
# through the real pipeline into a throwaway store, then assert against a
# known-good snapshot — event counts, final classification, lap counts and
# pit stops (vs ground truth), plus determinism and idempotency. Runs green
# in CI with no database (MemorySink); also checks a throwaway TimescaleDB
# when F1_STORE_RUN_DB_TESTS=1. See docs/PHASE1.md.
verify-phase1:
    uv run python -m f1_pipeline.verify

# Phase 2 verification harness: runs the unified kernel (`analyze_session`)
# over the checked-in synthetic fixture, checks the clean-lap filter against
# its ground truth, confirms the fuel coefficient/pit loss are CALIBRATED,
# and checks the committed degradation/undercut backtest reports still pass
# their own directional-sanity and calibration checks. Needs no data, no
# database and no network. See docs/PHASE2.md.
verify-phase2:
    uv run python -m f1_kernel.verify

# Phase 3 verification harness: Playwright (apps/web/playwright.config.ts)
# boots f1_api against the SAME checked-in synthetic historical fixture
# f1_kernel/f1_api's own test suites use, boots the frontend in replay mode,
# and runs a real headless Chromium through the dashboard's smoke +
# spoiler-safety E2E suite (apps/web/e2e/) — load a replayed session, assert
# the timing tower/undercut countdown/switchable analytics panel render real
# data, and assert (sampling the rendered sync-diagnostics readout, which
# mirrors TimelineBuffer's own state) that nothing releases ahead of the
# playback clock. Needs no data, no live session, no database — just a
# `just setup` (for Playwright's Chromium) beforehand. See docs/PHASE3.md.
# Scoped to its own spec files (not the whole apps/web/e2e/ directory) so it
# stays independent of verify-phase4's separate radio fixture/webServer pair.
verify-phase3:
    pnpm --filter @pitwall/web exec playwright test dashboard.spec.ts desk.spec.ts calls.spec.ts sync.spec.ts archive.spec.ts track-map-animation.spec.ts weekend.spec.ts circuits.spec.ts races.spec.ts standings.spec.ts

# Phase 4.5 verification harness: Playwright boots a SECOND, independent
# f1_api + frontend pair (apps/web/playwright.config.ts's own webServer
# entries) pointed at a dedicated radio fixture
# (python/f1_radio/tests/fixtures/e2e_historical +
# e2e_radio_fixture.json, via F1_API_RADIO_FIXTURE — f1_api.radio_fixture's
# DB-free path) rather than the Phase 3 fixture, which carries no
# team_radio_clip events at all. Asserts: a paraphrased query with no
# lexical overlap ranks the right clip first, enriched with race context;
# a driver filter excludes the closest-embedding clip when it belongs to
# someone else; the corpus search page works at a 390px mobile viewport;
# and, replaying the fixture session, every radio clip releases in the
# correct chronological order and never before its configured Delay Sync
# delay has elapsed. Needs no data, no live session, no real database, no
# network — just a `just setup` beforehand. See docs/PHASE4.md.
verify-phase4:
    pnpm --filter @pitwall/web exec playwright test radio.spec.ts

# Phase 5.5 verification harness: Playwright reuses the Phase 3 f1_api +
# frontend pair (apps/web/playwright.config.ts) against the checked-in
# kernel historical fixture, with F1_API_WHY_FIXTURE enabling
# f1_api.why_fixture's CitingNarrationModel so grounded explanations exist
# without a live Anthropic key. Asserts: a known anomaly gets a grounded,
# cited explanation; explanations appear only after the Delay Sync delay;
# nothing leaks ahead of the clock; an ungrounded/weak case shows the
# canonical fallback; the race story view ranks moments by salience with
# expandable cited evidence. Needs no data, no live session, no database,
# no network — just a `just setup` beforehand. See docs/PHASE5.md.
verify-phase5:
    pnpm --filter @pitwall/web exec playwright test why.spec.ts

# The release gate on REAL races (docs/ROADMAP.md W0): run before every
# deploy, after `just check`. Needs the backfill under data/historical/; each
# step skips itself without it (CI has no F1 data).
#   1. the story: every card borne out by the raw FastF1 tables, the
#      headline moments found, no unsupported number (f1_why story_eval);
#   2. the map: measured car dots on the drawn track (check_outline_fit);
#   3. the browser: five real races at phone and desktop size through the
#      production build: no console/request errors, no race information
#      before the delay, the released log never ahead of the clock, layout
#      shift and main-thread budgets, no filler story cards.
verify-real:
    uv run python python/f1_why/tools/story_eval.py --check
    uv run python deploy/check_outline_fit.py --gate 2024-1-race 2025-3-race
    pnpm --filter @pitwall/web exec playwright test --config playwright.real.config.ts

# Visual baselines (docs/ROADMAP.md W0): every screen at 390, 768 and 1440 px
# in both themes, against the screenshots in apps/web/e2e-visual/baselines.
# Part of the release gate. A difference fails it until someone has looked.
verify-visual:
    pnpm --filter @pitwall/web exec playwright test --config playwright.visual.config.ts

# Accept the current screens as the new baselines, after reviewing the diff
# images `verify-visual` left under apps/web/test-results.
visual-approve:
    pnpm --filter @pitwall/web exec playwright test --config playwright.visual.config.ts --update-snapshots

# Dev-only: refresh every committed Phase 2 calibration/backtest report
# (fuel coefficient, per-circuit pit loss, degradation, undercut) from
# data/historical/ in one pass. Needs a populated backfill; never run in
# CI. See docs/PHASE2.md's "Backtest suite" section.
backtest-phase2:
    uv run python python/f1_kernel/tools/run_backtest_suite.py

# ROADMAP W5, step one of two, on 2024 (development): why the undercut's
# medium calls score below low (with candidate confidence rules), and the
# rejoin call with pit loss measured on 2024 itself (in-sample: F1's archive
# answers 401 since October 2026, so 2023 cannot be fetched). Rules and
# thresholds are read here; 2025 stays held out. Needs the 2024 backfill;
# not in CI.
w5-diagnose:
    uv run python python/f1_kernel/tools/backtest_undercut.py --seasons 2024 --diagnose --out python/f1_kernel/reports/undercut_backtest_2024_diagnosis.md
    uv run python python/f1_kernel/tools/backtest_rejoin.py --seasons 2024 --pit-loss-in-sample --out python/f1_kernel/reports/rejoin_backtest_2024_dev.md

# ROADMAP W5, step two: the held-out 2025 check, run once the rules read
# from `w5-diagnose` are in the kernel. Pit loss for the rejoin call comes
# from 2023-2024 only. These two reports decide which calls ship live.
w5-holdout:
    uv run python python/f1_kernel/tools/backtest_undercut.py --seasons 2025
    uv run python python/f1_kernel/tools/backtest_rejoin.py --seasons 2025

# Phase 3.1: run the FastAPI read model locally (http://127.0.0.1:8000,
# docs at /docs). See docs/PHASE3.md.
serve-api:
    uv run f1-api --reload

# Regenerate python/f1_api/openapi.json after a response-model change and
# commit the result — test_openapi.py fails on drift. See docs/PHASE3.md.
export-openapi:
    uv run python python/f1_api/tools/export_openapi.py

# Phase 3.2: run the SvelteKit dev server (http://localhost:5173). Point it
# at a running f1_api via PUBLIC_F1_API_BASE_URL in apps/web/.env (defaults
# to http://127.0.0.1:8000) — see docs/PHASE3.md for `just serve-api`.
web-dev:
    pnpm --filter @pitwall/web dev

# Build the frontend for production (adapter-node output under apps/web/build/
# — a Node server, run via `node build/index.js`; see apps/web/Dockerfile).
web-build:
    pnpm --filter @pitwall/web build

# Typecheck (svelte-check) and lint (eslint) the frontend.
web-check:
    pnpm --filter @pitwall/web check
    pnpm --filter @pitwall/web lint

# Run the frontend's unit tests (vitest) — chiefly the Delay Sync core's
# spoiler-safety property tests (src/lib/delay-sync/), pure TS with no DOM.
web-test:
    pnpm --filter @pitwall/web test

# Regenerate the typed API client from the committed python/f1_api/openapi.json
# (openapi-typescript). Run after any f1_api response-model change — a
# backend contract change then surfaces as a TS type error at every call
# site that relied on the field that moved, per docs/PHASE3.md.
web-generate-client:
    pnpm --filter @pitwall/web generate-client

# ---------------------------------------------------------------------------
# Containers + deploy (see DEPLOY.md for the full runbook).
# ---------------------------------------------------------------------------

# Bring up the whole stack locally in containers: db + api + web, hot reload
# for both api and web (compose.dev.yml bind-mounts source), db published on
# 5432. Waits for the db to actually be healthy (`--wait`), applies
# migrations, then seeds one race from the same checked-in synthetic
# recording the Phase 1-5 test suites use, so a fresh clone has something to
# look at with zero manual steps. Idempotent — safe to re-run.
up:
    docker compose -f compose.yml -f compose.dev.yml up -d --build --wait
    uv run f1-store-migrate
    uv run f1-pipeline replay python/f1_pipeline/src/f1_pipeline/fixtures/replay_race --round 1

# Stop the local containerised stack (data survives — named volumes aren't
# removed; add `-v` by hand if you actually want to wipe it).
down:
    docker compose -f compose.yml -f compose.dev.yml down

# Apply pending migrations against whatever F1_STORE_DSN currently points
# at (dev by default, or prod over an SSH tunnel) — idempotent, safe to run
# on every deploy per f1_store.cli's own docstring.
migrate:
    uv run f1-store-migrate

# pg_dump the db service to backups/pitwall-<env>-<timestamp>.dump.
backup env="dev":
    uv run python python/f1_store/tools/backup.py --env {{env}}

# Restore a backup into a SCRATCH database (never the live one) and verify
# it actually works — an untested backup is not a backup. Example:
#   just restore backups/pitwall-dev-20260801T120000Z.dump pitwall_restore_test
restore file target-db env="dev":
    uv run python python/f1_store/tools/restore.py --file {{file}} --target-db {{target-db}} --env {{env}}

# Pre-flight for a live recording session — disk space, db reachability,
# volume writability. Run this an hour before every race weekend. (In prod,
# run it inside the worker container instead — see DEPLOY.md — so it checks
# the actual mounted volume/network, not the host's.)
record-check:
    uv run python python/f1_store/tools/record_check.py

# Build both images, push to GHCR, then pull+roll on the VPS over SSH.
# Requires GHCR_NAMESPACE, DEPLOY_HOST, DEPLOY_USER, DEPLOY_PATH (see
# .env.example, DEPLOY.md). A human runs this deliberately — deploy stays
# manual during the beta.
deploy:
    uv run python deploy/deploy.py

# Roll the VPS onto the image CI already built and pushed for THIS commit
# (.github/workflows/ci.yml build-and-push). Builds nothing locally --
# push to main, wait for CI to go green, then run this.
deploy-ci:
    uv run python deploy/deploy.py --no-build

# Deploy WITHOUT a registry: stream this checkout to the VM, build both
# images there, bring the stack up and migrate. No GHCR, no CI, no local
# docker build. See deploy/deploy_host.py.
deploy-host:
    uv run python deploy/deploy_host.py

# Redeploy the previous image tag on the VPS (writes nothing new — just
# points IMAGE_TAG back at deploy.py's own .last-deployed-sha).
rollback:
    uv run python deploy/rollback.py
