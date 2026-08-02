# Phase 4 runbook

The team-radio corpus: acquire driver-pit audio, transcribe it locally, and
index it for search. Phase 4 produces a genuinely new dataset — nobody else
has searchable, context-enriched F1 team radio — and is the primary input
for Phase 5's "why" engine. Everything here lives under `python/f1_radio/`.

```
TeamRadioClip event ─► fetcher (cache audio) ─► transcribe_batch (faster-whisper) ─► radio_transcripts ─► index_batch (embed + context) ─► search ─► f1_api (REST + /stream) ─► apps/web
 (Phase 1, already       f1_radio.fetcher/cache/     f1_radio.transcription/             f1_store's own      f1_radio.embeddings/context/       f1_radio.search   routes/radio.py,      RadioLane +
  exists — audio_url,    manifest (4.1)               transcribe_batch (4.2)              DSN, migration 002   index_batch, migration 003 (4.3)   (4.3)             stream/replay.py (4.4)  /radio search (4.5)
  driver, captured_at)
```

## Exit criteria

- [x] `TeamRadioClip` audio can be fetched into a local, deterministically-keyed
      cache, idempotently and resumably (4.1 — `f1_radio.fetcher`).
- [x] `radio_transcripts` schema exists, separate from `team_radio`, applied
      by the existing `f1-store-migrate` mechanism (4.1 —
      `f1_store/migrations/002_radio_transcripts.sql`).
- [x] Batch transcription via local faster-whisper: full text, per-segment
      timestamps, detected language, and a confidence derived from
      segment-level probabilities; idempotent, resumable, safely
      parallelizable (4.2 — `f1_radio.transcription`/`transcribe_batch`).
- [x] Silence/noise never surfaces as hallucinated text — VAD-filtered to
      zero segments and flagged, not decoded (4.2, see "The hallucination
      guard" below). Proven against a REAL model and checked-in synthetic
      audio fixtures, not only a fake backend.
- [x] Non-English exchanges keep their own language, labeled, never forced
      through an English decode (4.2 — no `language=` override passed to
      the model, so faster-whisper auto-detects and transcribes in the
      source language).
- [x] A per-season quality report (count, confidence distribution, flagged
      low-confidence clips, language breakdown) is generatable, so a season's
      transcripts can be eyeballed rather than trusted blind (4.2 —
      `f1_radio.quality_report`, `f1-radio quality-report`).
- [x] Embeddings + semantic/hybrid search over the transcript corpus, with
      race-context enrichment (lap/position/track-status at `captured_at`) —
      4.3, see "Semantic index" below.
- [x] Served through `f1_api` (REST search + per-session listing + one-clip
      reads) and integrated into the `/stream/{key}` timeline behind Delay
      Sync — 4.4, see "Served through f1_api" below.
- [x] Live/replay radio lane is Delay-Sync gated; spoiler-safety holds for
      radio (4.5, see "Frontend" below).
- [x] Corpus search UI works with filters, on mobile (4.5).
- [x] `just verify-phase4` green in CI (4.5).

## Acquisition: `f1-radio fetch` (4.1)

```sh
uv run f1-radio fetch --season 2024
uv run f1-radio fetch --season 2024 --round 5
uv run f1-radio fetch --season 2024 --refresh
```

Reads every `TeamRadioClip` event for a season from `f1-backfill`'s catalog
(`f1_radio.discovery.season_team_radio_events`, via
`f1_historical_adapter.events_from_parquet` — the one existing parquet-to-
`DomainEvent` seam, reused rather than a second reader) and downloads each
clip's audio into `data/radio_audio/` (`f1_radio.cache.clip_cache_path`,
keyed deterministically by `(session, driver, captured_at)` — never content-
hashed, so a re-run always resolves to the same path with no lookup first).

`f1_radio.manifest.FetchManifest` is the durability layer: saved after
*every* clip, not just at the end, so an interrupted fetch is resumable and
a re-run skips anything already recorded (`--refresh` forces a re-pull).
Missing/dead URLs are recorded (`NO_URL`/`FAILED`) and the batch continues —
never a crash on one bad clip. Requests are rate-limited to OpenF1's own
free-tier budget (team-radio URLs are OpenF1-sourced) with backoff honoring
`Retry-After`.

## Transcription: `f1-radio transcribe` (4.2)

```sh
uv run f1-radio transcribe --season 2024
uv run f1-radio transcribe --season 2024 --round 5 --workers 4
uv run f1-radio transcribe --season 2024 --model-size medium --device cuda --compute-type float16
```

Requires `F1_STORE_DSN` (or `--dsn`) pointed at a migrated TimescaleDB —
`f1-store-migrate` (or any `f1-pipeline`/`f1-api` run) already applies
`002_radio_transcripts.sql`.

### The model (`f1_radio.transcription`)

`transcribe_clip(audio_path, backend)` wraps one `faster_whisper.WhisperModel.
transcribe()` call (`load_model` loads the real thing — local, CPU-friendly
by default (`small`/int8), no per-minute cost, per CLAUDE.md's Phase 4
budget invariant) into a `TranscriptionResult`: full `text`, per-segment
`TranscriptSegment` (start/end/text/confidence), detected `language` +
`language_probability`, an overall `confidence`, and `low_confidence`/
`no_speech_detected` flags.

**The hallucination guard.** F1 radio is short, noisy, jargon-heavy and
often clipped — exactly the conditions under which Whisper is known to
invent fluent-sounding text over silence. `vad_filter=True` (faster-
whisper's Silero-VAD pre-filter) strips silence/noise *before* it ever
reaches the decoder, so a genuinely silent or noise-only clip yields **zero
segments** rather than invented text — reported as `no_speech_detected=True`,
`text=""`, `confidence=0.0`, never as a transcript. For genuine speech,
`confidence` is faster-whisper's own per-segment `avg_logprob` (a natural-log
probability; `exp(...)` maps it back to ~[0, 1]), duration-weighted across
segments so a long confident segment isn't swamped by a short garbled one.
Below `DEFAULT_LOW_CONFIDENCE_THRESHOLD` (0.5, STRUCTURAL — not yet
calibrated against a measured corpus of known-good/known-bad F1 radio), a
result is flagged `low_confidence=True` rather than presented as certain —
the same "every value carries a quality signal" discipline the Phase 2
kernel already applies. Very short clips need no special case: sub-second
audio decodes directly, and a clip too short for VAD to find speech in falls
through the same zero-segments path as silence.

Language is never forced: no `language=` override is passed, so
faster-whisper auto-detects and transcribes IN that language (not
translated to English) — a French exchange stays French text, labeled
`language="fr"`.

Proven three ways (`f1_radio/tests/`):

- `test_transcription.py` — confidence derivation, the hallucination guard,
  duration-weighting math, all against a fake `WhisperBackend` (no model, no
  audio file needs to exist).
- `test_real_model.py` — the SAME claims against a REAL `tiny` faster-whisper
  model and checked-in SYNTHETIC audio fixtures
  (`tests/fixtures/audio/`: two Windows-SAPI text-to-speech clips, one
  silence clip, one white-noise clip — synthesized, never real F1 broadcast
  audio, clean of any broadcast-rights concern). Model-gated and skipped by
  default (downloads a real model, needs real CPU time) — the same
  "not exercisable in every environment" reality this project already
  accepts for the live feed (docs/PHASE0.md) and the database
  (`test_migrations.py`'s `F1_STORE_RUN_DB_TESTS`):

  ```sh
  F1_RADIO_RUN_MODEL_TESTS=1 uv run pytest python/f1_radio/tests/test_real_model.py
  ```
- `test_migrations.py` (DB-gated, `F1_STORE_RUN_DB_TESTS=1`) — the
  `radio_transcripts` schema and its unique constraint against a real
  TimescaleDB.

### The batch runner (`f1_radio.transcribe_batch`)

`run_batch` is the fully-injected core: given `TeamRadioClip` events, a
`FetchManifest` (which clips have cached audio) and store/model factories,
it transcribes every fetched-but-not-yet-transcribed clip. `run_batch_for_
season` wires season discovery + defaults on top, the same split
`f1_radio.fetcher.fetch_clips`/`f1_radio.cli._run_fetch` already establish.

- **Idempotent**: a clip already carrying a transcript row is skipped before
  any model work happens (one cheap `store.get(...)` lookup per clip).
- **Resumable**: skip decisions are read fresh from the store's current
  contents on every call — a crash partway through leaves every completed
  clip's row durably written, and a re-run picks up exactly where it left
  off. No separate progress file to go stale (unlike the fetch manifest,
  which needs one because a `FAILED`/`NO_URL` outcome has nowhere else to
  live — a transcript's "am I done" question is answered by the table
  itself).
- **Parallel, no double-processing**: `--workers N` transcribes N clips
  concurrently via a thread pool (`ctranslate2`'s inference releases the
  GIL, so this is genuine parallelism). The claim is the store's own write
  semantics: `store.insert(...)` is atomic against the real table's UNIQUE
  CONSTRAINT (`ON CONFLICT DO NOTHING`) — if two workers (in-process, or two
  separate `f1-radio transcribe` invocations racing the same database) both
  transcribe the same not-yet-claimed clip, only one row lands; the other's
  model work is wasted CPU, never a crash or a duplicate. A model instance
  is loaded once per worker thread (thread-local, the expensive resource)
  and reused across that thread's clips.

`f1_radio.store.TranscriptStore` is the seam that makes this testable with
no database: `PostgresTranscriptStore` for real use, `MemoryTranscriptStore`
for tests — the same "DB is a sink, fully testable with no database
present" split `f1_store` makes with `PostgresSink`/`MemorySink`.
`test_transcribe_batch.py` covers idempotency, resumability (a flaky-backend
simulated crash, then a clean second run), and parallel-worker safety (8
clips, 4 workers, exactly one row each, no duplicates) — all against fakes,
no real model, no real database.

## Quality report: `f1-radio quality-report` (4.2)

```sh
uv run f1-radio quality-report --season 2024
uv run f1-radio quality-report --season 2024 --out python/f1_radio/reports/quality_2024.md
```

`f1_radio.quality_report.build_quality_report` is a PURE function over an
already-loaded list of `TranscriptRecord`s (`list_transcripts_for_season` is
the loader half — "load, then pass data in," the same split every other
analytic in this project follows) — transcript count, no-speech count, a
confidence histogram, a language breakdown, model-version breakdown, and the
flagged low-confidence/no-speech clips, sorted worst-first.
`render_markdown` turns that into the same kind of `reports/*.md` document
`f1_kernel`'s calibration tools produce. No report is committed here yet —
unlike Phase 2's calibration reports, this needs a real season's worth of
fetched + transcribed audio (`data/` is gitignored and this repo ships none)
to mean anything; run the command above once a season has been fetched and
transcribed, and commit the output the same way `tools/run_backtest_suite.py`'s
reports are committed.

## Semantic index: `f1-radio index` + `f1-radio search` (4.3)

What makes the corpus a genuinely searchable DATASET rather than a pile of
text: a local sentence embedding per transcript, race context (lap,
position, track status) captured alongside it, and a search service over
both — semantic, hybrid (semantic + structured/keyword filters), or pure
structured filtering.

### Bring up pgvector

Migration 003 (`f1_store/migrations/003_radio_embeddings.sql`) runs
`CREATE EXTENSION vector` and adds `embedding`/`embedding_model_version`/
`lap_number`/`position`/`track_status`/`track_status_flags` columns
straight onto `radio_transcripts` — a genuinely separate store from
`team_radio` (CLAUDE.md's Phase 4 invariant), just extended rather than
duplicated. It needs an instance where the `vector` extension is actually
installed: `python/f1_store/docker-compose.yml` now uses the
`timescale/timescaledb-ha` image rather than the plain one, which bundles
pgvector alongside TimescaleDB. Apply it the same way as every other
migration:

```sh
docker compose -f python/f1_store/docker-compose.yml up -d
uv run f1-store-migrate
```

No ANN index (HNSW/IVFFlat) is created — a season's radio corpus is at most
a few thousand rows, small enough that pgvector's brute-force `<=>` scan is
already fast; see the migration's own docstring for why that is a
deliberate choice, not an oversight.

### Indexing: `f1-radio index`

```sh
uv run f1-radio index --season 2024
uv run f1-radio index --season 2024 --round 5 --model-name sentence-transformers/all-MiniLM-L6-v2
```

`f1_radio.embeddings.load_embedding_model` loads a real local
`sentence-transformers` model (CPU-friendly, no per-call API cost, per
CLAUDE.md's Phase 4 budget invariant) the same lazy-import-behind-a-Protocol
way `f1_radio.transcription.load_model` loads faster-whisper.
`embedding_model_version` (== the model name) is recorded on every indexed
row, the same "which model produced this" provenance
`f1_radio.transcription.model_version` already gives transcripts.

`f1_radio.context.SessionContext` is the race-context half: built ONCE per
session from that session's full `DomainEvent` stream (laps, positions,
track status — reusing `f1_kernel.status.track_status_timeline`/
`status_during`, the same SC/VSC/yellow reconstruction the clean-lap filter
already depends on for historical sessions with no track-status delta
stream), then answers every clip in that session — lap in progress,
classification position, track status — at the instant it was captured.
This is what makes a result meaningful ("this complaint was on lap 40
running P3 under a VSC") rather than a bare transcript, and what Phase 5's
why-engine will join against.

`f1_radio.index_batch.run_index_batch` backfills both (embedding + context)
onto each clip's existing `radio_transcripts` row via
`TranscriptStore.set_embedding` — **idempotent** (a clip whose stored
`embedding_model_version` already matches is skipped, the embedding model
never even loads if every pending clip turns out to be skipped/empty) and
**resumable** (skip decisions come from the store's own current contents on
every call, the same discipline `f1_radio.transcribe_batch.run_batch`
already established for 4.2). A clip with no transcript row yet
(`no_transcript`) or an empty transcript (`empty_text`, e.g. a genuinely
silent clip) is reported, never treated as an error.

### Search: `f1-radio search`

```sh
uv run f1-radio search "complaining about front tyres"
uv run f1-radio search "box this lap" --driver 44 --season 2024 --round 5
uv run f1-radio search "" --driver 1 --track-status sc --lap 20   # pure filter, no semantic ranking
```

`f1_radio.search.search_transcripts` embeds the query (skipped for a blank
query — a pure structured/keyword search) and delegates to a `SearchIndex`:
`PostgresSearchIndex` ranks server-side by pgvector's `<=>` cosine-distance
operator (`f1_radio.embeddings` stores unit-norm vectors, so `1 - distance`
reported as `similarity` is a true cosine similarity); `MemorySearchIndex`
does the identical ranking in plain Python over an already-loaded list of
`TranscriptRecord`s — the same Postgres/Memory split `f1_radio.store.
TranscriptStore` already makes, so the ranking and filter-composition logic
has a real unit test with no database. `SearchFilters` covers driver,
season/round/session type, lap number, track status, a `captured_at` date
range, and a literal keyword substring — composed as SQL `WHERE` clauses
(Postgres) or a Python predicate (Memory) applied BEFORE similarity
ranking, so a filter excludes a candidate even when its embedding is the
closest match. There is no `team` filter: a radio clip carries a driver
number, not a team, and this package has no driver→team roster to join
against. Each `SearchResult` carries the full `TranscriptRecord` (transcript
text, clip metadata, transcription confidence, and — once indexed — lap/
position/track-status) plus the query's own `similarity` score.

Not built here, deliberately (CLAUDE.md's Phase 4 invariant — "Search is
served through f1_api like everything else; the frontend never touches the
transcript store or an embedding model directly"): no HTTP endpoint, no UI.
`f1-radio search` is the same kind of direct-access predecessor
`f1-radio quality-report` was before any of this had an API — a later
prompt wraps `search_transcripts` in an `f1_api` route.

### Tests

```sh
uv run pytest python/f1_radio
F1_STORE_RUN_DB_TESTS=1 uv run pytest python/f1_radio/tests/test_migrations.py python/f1_radio/tests/test_search_postgres.py
F1_RADIO_RUN_MODEL_TESTS=1 uv run pytest python/f1_radio/tests/test_real_embeddings.py
```

`test_context.py` covers `SessionContext.at` against planted lap/position/
race-control events (no store, no database). `test_index_batch.py` covers
`run_index_batch`'s idempotency (re-indexing with the same model version
never re-calls the embedding backend), a different model version
re-embedding rather than skipping, and the no-transcript/empty-text/
failed-batch edge cases — all against `MemoryTranscriptStore` and a
`FakeEmbeddingBackend` (the same scripted-fake precedent
`FakeWhisperBackend` set for 4.2). `test_search.py` proves the semantic
claim at the unit level: a `FakeEmbeddingBackend` whose vectors the test
plants directly shows a paraphrased query with NO lexical overlap ranks the
intended clip first, and that structured filters exclude a candidate even
when its embedding is the closest match. `test_real_embeddings.py` is the
opt-in sibling proving the same paraphrase claim against a REAL local
model, mirroring `test_real_model.py`'s gating
(`F1_RADIO_RUN_MODEL_TESTS=1`, downloads a real model). `test_migrations.py`
gained DB-gated coverage of migration 003 itself (the pgvector column
round-trips with no custom psycopg type adapter registered —
`f1_radio.store.vector_literal`/`_parse_vector` handle it as plain text —
and pgvector's `<=>` operator is usable against it); `test_search_postgres.py`
is the DB-gated mirror of `test_search.py` against a real
`PostgresSearchIndex`.

## Served through f1_api (4.4)

The corpus becomes part of the product: three REST routes plus timeline
integration on `/stream/{key}`, matching every other kernel value's own
"the frontend never touches the store/model directly" rule.

```
GET /radio/search?q=&driver=&session=&from=&to=   ranked, context-enriched results
GET /sessions/{key}/radio                          every clip for one session, time-ordered
GET /radio/{clip_id}                                one clip: transcript, segments, context, audio URL
```

`f1_api.radio` is the new seam, analogous to `f1_api.sessions` but for a
corpus that (unlike historical/live sessions) has **no DB-free real
backing at all** — a transcript only ever lives in `radio_transcripts`.
`get_radio_search_index`/`get_radio_store`/`get_embedding_backend_factory`
are real FastAPI `Depends` providers (unlike `sessions.py`'s plain
`resolve_session`, called directly) precisely because tests need a lever to
swap in a `MemorySearchIndex`/`MemoryTranscriptStore`/fake embedding
backend — the same lever `main.py` already uses for `get_settings`.
`get_embedding_backend_factory` resolves to the loader FUNCTION, uncalled —
resolving it is always cheap; only a non-blank query with a configured
corpus actually calls it, so a blank-query search against an unconfigured
corpus never triggers a real model load. `clip_id_path`/`parse_clip_id`
encode `(session_key, driver, captured_at)` into one opaque, percent-encoded
path segment (`~`-joined) — the identity a transcript row is keyed on,
`radio_transcripts` carrying no own primary key column to reuse.

`RadioClipOut` joins a clip's identity + `audio_url` (from the originating
`TeamRadioClip` event — `TranscriptRecord` itself only ever carries a local
`audio_path`, not a URL) to whatever transcript/context exists yet;
`has_transcript=False` with every transcript field `None` is a normal
Phase 4.2 pipeline state (fetched, not yet transcribed), never an error.
`/radio/search` results never carry `audio_url` (resolving it would mean
re-reading every matched session's event stream just to answer a ranked
list); `/sessions/{key}/radio` and `/radio/{clip_id}` do, since both are
already scoped to one session's own event stream.

**Timeline integration** (`f1_api.stream.replay`): a `team_radio_clip` event
already rode `/stream/{key}` (it was in `DEFAULT_STREAM_KINDS` since Phase
3.1); `radio_clip_envelope` enriches it with the joined transcript/context,
under a new `"radio_clip"` kind distinct from the raw `DomainEvent.kind` so
a consumer never has to reach for a second REST call mid-race. The pacing
change this needed: a radio clip's own `elapsed_s` is measured from
`captured_at` (the moment the exchange actually happened), not `ts_utc`
(the moment our pipeline observed it) — `_event_pacing_ts` — because
`captured_at` is the instant CLAUDE.md's Phase 4 invariant ("a radio clip's
captured_at cannot surface ahead of the user's playback clock") is actually
about. It still goes through the exact same wall-clock remap every other
envelope does (`envelope_for_event`, shared by `replay_envelopes` and
`mode=live`'s polling loop) — never the raw historical timestamp — so it is
exactly as spoiler-safe as everything Phase 3.3's Delay Sync already gates.

### Tests

`python/f1_api/tests/test_radio.py` covers the REST surface entirely
against `Memory*` fakes (no database, mirroring `f1_radio`'s own
`test_search.py`): ranked/context-enriched results for a paraphrase with no
lexical overlap, structured filters (driver, session, blank-query pure
filter search with no embedding call), graceful `[]`/404/503 degradation
when the corpus or a specific clip is unavailable, and `clip_id` round-
tripping. `python/f1_api/tests/test_replay.py` extends the Phase 3 spoiler
suite: a synthetic `MemorySource` session (the checked-in kernel fixture
ships no `team_radio_clip` events at all) proves the enriched envelope's
kind, that pacing follows `captured_at` rather than `ts_utc`, transcript
enrichment when a `radio_store` is given, graceful degradation without one,
and that `mode=live`'s `envelope_for_event` gates on `captured_at` too.

## Frontend (4.5): the two radio surfaces + verification

The corpus becomes something a user actually sees. Two surfaces, both
framework-agnostic-core-plus-thin-Svelte, matching every other Phase 3 race
view's own split (Delay Sync, track map, kernel-analytics views):

- **`$lib/radio/`** (`apps/web/src/lib/radio/`) is the shared pure-TS core,
  zero Svelte imports: `view.ts` declares the ONE `RadioClipView` shape both
  surfaces render (a live `radio_clip` envelope and a REST `RadioClipOut`
  are different wire shapes; `radioClipViewFromEnvelope`/`radioClipViewFromApi`
  are the two converters, the same "define the canonical shape, convert into
  it" split `$lib/delay-sync/envelope.ts` uses for `Envelope` itself),
  `confidenceTier` (a three-tier good/thin/unreliable badge over the raw
  confidence float, `thin`'s floor matching `f1_radio.transcription.
  DEFAULT_LOW_CONFIDENCE_THRESHOLD` exactly so the frontend's "low
  confidence" marking means the same 0.5 boundary the backend's own quality
  signal already uses), and `trackStatusLabel`. `lane.ts`'s
  `RadioLaneReducer` is the timeline lane's own per-domain-event reducer —
  the same "fold `VisibleSessionState.events` into what one view needs" job
  `$lib/kernel-views/gap-stream.ts`/`$lib/timing-tower` already do for
  theirs — reading ONLY `radio_clip` envelopes, which need no separate
  spoiler gate at all: a clip's pacing is already gated on `captured_at`
  (`f1_api.stream.replay`'s `_event_pacing_ts`), so by the time one reaches
  `visibleState.events` it is provably not ahead of the user's playback
  clock, the same reasoning `GapStreamGraph`/`UndercutCountdown` give for
  skipping `RevealTracker`. `search-params.ts`'s `buildRadioSearchQuery`/
  `hasAnyRadioFilter` are the corpus search page's own pure logic (which
  filter fields get trimmed, which get omitted rather than sent blank) —
  kept out of the Svelte component specifically so it has a real unit test
  with no DOM. Found the hard way building this: `RadioLaneReducer.clips`
  handing back the SAME mutated-in-place array on every call made Svelte 5's
  `$derived.by` see "no change" even as new clips were pushed into it (the
  exact caveat `TimingTower.svelte`'s own comment already documents for
  mutating what a reducer hands BACK, as opposed to its own internal
  state) — fixed by returning a fresh array copy per call, cheap at a race's
  whole radio corpus.
- **`RadioClipCard.svelte`** (`apps/web/src/lib/components/`) is the one
  rendering of a clip both surfaces reuse — driver, captured-at time, lap/
  position/track-status context chips, the transcript (or "Transcription
  pending" for a fetched-not-yet-transcribed clip, never a blank), a
  `ConfidenceBadge` plus an explicit "Low-confidence transcript" note below
  `thin`, and click-to-play audio. Play is lazy two different ways
  depending on where the clip came from: the timeline lane's envelope
  already carries `audio_url`, so playing is just revealing an
  `<audio preload="none">`; a search result's `RadioClipOut.audio_url` is
  always `null` (that schema's own docstring: resolving it for every ranked
  result would mean re-reading every matched session's event stream), so
  `RadioClipOut` gained a `clip_id` field (`f1_api.radio.clip_id_path`'s own
  opaque segment) specifically so a card can resolve `GET /radio/{clip_id}`
  on click instead of reimplementing that encoding itself — the one new
  backend seam this prompt needed, a schema addition, not a new route.
- **`RadioLane.svelte`** is the live/replay timeline strip — a
  horizontally-scrollable filmstrip of `RadioClipCard`s, one per released
  `radio_clip` envelope, auto-scrolled to the newest — with its own explicit
  "Waiting for team radio…" empty state. Wired into `/race` directly below
  `GapStreamGraph` (the prompt's own "under the gap chart"), inside the
  existing switchable analytics panel's `gap` tab.
- **`routes/radio/+page.svelte`** is the standalone corpus search — the
  "this dataset exists" feature, built to read like a real search product
  rather than a debug form: a single prominent query box (with example
  queries to try when nothing's been searched yet), a collapsible filters
  panel (driver, session, a date range, track status), and results as
  `RadioClipCard`s with a similarity score bar. Debounced (350ms) rather
  than search-on-submit, with a request-id guard so a slow, superseded
  fetch can never overwrite a newer one's results. Explicit states
  throughout: idle (nothing searched — the example-query hint), loading,
  error, and no-results, distinct from each other rather than a single
  blank div for all four.

### Tests

```sh
just web-test                                  # $lib/radio's pure-TS core, no DOM
just verify-phase4                             # Playwright — see below
```

`$lib/radio`'s `view.test.ts`/`lane.test.ts`/`search-params.test.ts` cover
both envelope/API converters (including the not-yet-transcribed degrade
path), `confidenceTier`'s three-tier boundaries against
`DEFAULT_LOW_CONFIDENCE_THRESHOLD`, `RadioLaneReducer`'s cursor-based fold
and dedup, and every `buildRadioSearchQuery`/`hasAnyRadioFilter` blank-field
case — no DOM, matching every other `$lib` core's own testing discipline.

### Verification: `just verify-phase4`

```sh
just verify-phase4
```

A REAL browser against a REAL running `f1_api` + frontend pair — but a
SEPARATE pair from `just verify-phase3`'s (`apps/web/playwright.config.ts`
gained a second two-`webServer` group, ports declared once in
`apps/web/e2e/ports.ts` so the config and the spec file that needs an
absolute URL for this specific pair never drift), because the Phase 3
fixture ships no `team_radio_clip` events at all (docs/PHASE3.md) and a
running SvelteKit process reads `PUBLIC_F1_API_BASE_URL` once at boot, so
pointing at a second `f1_api` needs a second frontend process, not a
runtime switch. The API in this second pair sets `F1_API_HISTORICAL_ROOT`
to a DEDICATED fixture (`python/f1_radio/tests/fixtures/e2e_historical/`,
one session carrying only a `team_radio.parquet` table — deliberately NOT
added to the shared `f1_kernel` fixture, whose own `generate.py` fully wipes
and rebuilds that directory from its own `PLANS` and whose `test_sources.py`
pins an exact session count, either of which a hand-added session would
break or lose) plus `F1_API_RADIO_FIXTURE` pointing at
`e2e_radio_fixture.json` — `f1_api.radio_fixture`'s DB-free path (a
`MemorySearchIndex`/`MemoryTranscriptStore` seeded from the fixture, and a
`FixtureEmbeddingBackend` mapping a couple of hand-picked paraphrase queries
to vectors close to one clip's own planted embedding), reachable by a REAL
externally-driven process via one env var rather than only by a pytest
`app.dependency_overrides` lever. Both files are generated together by
`python/f1_radio/fixtures/generate_e2e.py` (`uv run python python/f1_radio/
fixtures/generate_e2e.py` to regenerate) from one shared list of four
planted clips across two drivers, so the parquet table and the JSON
fixture — and the embeddings inside it — can never drift apart. One clip is
deliberately LOW-confidence (a safety-car call, 0.35) so the low-confidence
marking has something real to assert against.

`apps/web/e2e/radio.spec.ts` asserts the three things this deliverable
asked for, plus the mobile/filters requirement:

- a paraphrased query with NO lexical overlap against its target
  transcript ranks the right clip first, enriched with its own lap/
  position context;
- a driver filter excludes the closest-embedding clip when it belongs to
  someone else — ranking happens WITHIN the filtered set, never falling
  back to the global best match;
- the search page works at a 390px mobile viewport;
- replaying the fixture session, every radio clip releases in the correct
  chronological order (`speed=0` gives every envelope in one connection the
  SAME remapped `event_time_utc` — see `docs/PHASE3.md`'s dashboard
  section for why that's deliberate — so ties keep arrival order, which is
  chronological `captured_at` order by construction) and — the spoiler-
  safety assertion — nothing appears before the configured Delay Sync delay
  has elapsed (sampled well before it, asserting zero rendered clips, then
  polled until it clearly has).

`just verify-phase3` was narrowed to name its own two spec files
explicitly (`dashboard.spec.ts track-map-animation.spec.ts`) rather than
running the whole `apps/web/e2e/` directory, so it stays independent of
this prompt's separate fixture/webServer pair; `just verify-phase4` runs
only `radio.spec.ts`. Both are wired into `just check`.

## What Phase 4 explicitly does not do yet

- No per-segment embeddings (finer-grained recall than one vector per whole
  transcript) — the deliverable called this out as optional, and it isn't
  built; a future pass could add a sibling table keyed the same natural way
  `radio_transcripts` already is.
- No calibration of `DEFAULT_LOW_CONFIDENCE_THRESHOLD` or VAD parameters
  against a measured corpus of real F1 radio — STRUCTURAL defaults, the same
  honesty `f1_kernel.braking`'s own PROVISIONAL params carry until a
  calibration prompt fits them.
- No ANN index (HNSW/IVFFlat) on the `embedding` column — a deliberate
  choice at this corpus size, not an oversight; see migration 003's own
  docstring.
