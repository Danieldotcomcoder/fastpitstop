"""f1_backfill: acquires and caches historical F1 sessions (2023-now) as
clean tables for the Phase 2 analytics kernel.

IMPORTANT BOUNDARY: this package does NOT produce raw SignalR recordings
and does NOT feed `f1_replay`. FastF1 and OpenF1 both give processed,
already-decoded data — not the raw wire messages `f1_recording` and
`f1_ingest` deal with. This is a SEPARATE fixture track from the replay
harness: it feeds Phase 2 (the analytics kernel) directly, with its own
clean, partitioned-parquet table schema. The adapter that would map these
historical tables onto the Phase 1 normalized-event schema (the same
schema the live pipeline emits) is a Phase 1 task — this package leaves
that seam documented (see `docs/DATA_CONTRACTS.md`) but does not build it.

Sources (see CLAUDE.md's "Data sources"):
- FastF1 (`f1_backfill.fastf1_source`, on-disk cache in
  `data/fastf1_cache/`): laps, car telemetry, weather, session results,
  stints/tyre compounds.
- OpenF1 REST (`f1_backfill.openf1`, free, no auth, 2023+): intervals/gaps,
  race control messages, team-radio recording URLs. Rate-limited to the
  free tier (3 req/s, 30 req/min) with backoff on 429.

Output: Hive-style partitioned parquet under `data/historical/` plus a
`manifest.json` catalog (see `f1_backfill.catalog`) that makes re-running
idempotent - already-acquired sessions are skipped unless `--refresh` is
passed.
"""

__all__: list[str] = []

__version__ = "0.0.1"
