# Phase 5 runbook

The why-engine: on a detected anomaly, fuse kernel state + radio + race
control into ONE grounded, sourced sentence — never a guess. Phase 5
splits into five stages: a DETECTOR (5.1: pure Python, deterministic,
deciding WHEN something is worth explaining), an EVIDENCE RETRIEVER (5.2:
pure Python, gathering everything relevant at or before the anomaly's own
time into one token-budgeted bundle), GROUNDED NARRATION (5.3: the only
place an LLM is used anywhere in this project, turning that bundle into
one cited sentence behind a structural grounding validator), the API +
timeline integration (5.4: served through `f1_api`, paced behind Delay
Sync), and the frontend + verification (5.5: inline "why" lane, race
story view, `just verify-phase5`). 5.1–5.3 live under `python/f1_why/`;
5.4–5.5 live in `python/f1_api/` + `apps/web/`.

```
DomainEvent stream ─► kernel frames + KernelReport ─► detect_anomalies ─► Anomaly[]
 (Phases 1-2, pure       f1_kernel.analyze_session         f1_why (5.1) — pure,   ranked by
  Python)                                                   no LLM, no evidence   salience
                                                             bundle
                                                                  │
                                                                  ▼
                                              assemble_evidence ─► EvidenceBundle ─► explain ─► Explanation
                                               f1_why.evidence (5.2) — pure,   token-budgeted,   f1_why.narration (5.3) —
                                               kernel state + radio +          spoiler-safe       the ONLY LLM call, behind
                                               race control, walk-forward      (<= anomaly_time)  a grounding validator
                                                                  │
                                                                  ▼
                                              f1_api REST + WS (5.4) ─► Delay Sync ─► UI (5.5)
                                              /anomalies, explanation      visibleState only    ExplanationLane +
                                              envelopes on anomaly_time                         /story narrative
```

## Pipeline (detect → retrieve → explain → cache)

1. **Detect** — `detect_anomalies(frames, kernel_report, AnomalyParams)` over
   the session's kernel frames. Pure, deterministic, no LLM. Returns
   salience-ranked `Anomaly`s.
2. **Retrieve** — `assemble_evidence(anomaly, EvidenceSources, EvidenceParams)`
   pulls kernel state + radio + race control at or before `anomaly_time`
   (walk-forward degradation refit; structural never-future enforcement).
3. **Explain** — `explain` / `explain_cached` calls the model at most once
   (skipped entirely when `bundle.low_evidence`), then the grounding
   validator. Rejections and honest declines normalize to
   `FALLBACK_TEXT`.
4. **Cache** — `ExplanationStore` keys by anomaly id; never regenerate on
   request. Historical replay precomputes the batch once
   (`precompute_session_explanations`); live explains newly-detected
   anomalies against the same store.

## Grounding guarantee

- Every claim in a substantive explanation cites an `evidence_id` that
  exists in the retrieved bundle; the validator rejects invented ids and
  citation-less prose.
- Evidence is time-boxed to `<= anomaly_time` (spoiler-safety AND causal
  honesty). Degradation context is refit walk-forward on that slice.
- `low_evidence` (no radio / race-control survivor) never calls the model
  — the UI shows the canonical "No clear cause found in the available
  evidence." string, never a guess.
- Confidence is the WORST cited item's trust signal; the UI shows it and
  lets the user expand the real cited sources.

## Model backends

`NarrationModel` has three real implementations plus the mocks.
`f1_api.why.get_narration_model` resolves them in this order:

| condition | backend |
| --- | --- |
| `F1_API_WHY_FIXTURE` set | `CitingNarrationModel` (E2E, no key) |
| `OPENROUTER_API_KEY` set | `f1_why.OpenRouterModel` |
| `ANTHROPIC_API_KEY` set | `ClaudeHaikuModel` |
| neither | a declining `MockNarrationModel` |

A backend that cannot be constructed (missing optional dependency, bad
key) logs and falls through to the declining mock rather than 500-ing
every why-engine route — the engine is an enrichment, never the reason
the rest of the API stops working.

### `f1_why.openrouter.OpenRouterModel`

OpenRouter is OpenAI-compatible, and this project runs it on
`minimax/minimax-m3:free` (`DEFAULT_OPENROUTER_MODEL`; override with
`OPENROUTER_MODEL`, or point `OPENROUTER_BASE_URL` at a compatible proxy).
Two guarantees differ from the Anthropic backend, both because a free-tier
chat model gives weaker promises than a structured-output API:

- **No structured-output requirement.** JSON is requested twice over — the
  `response_format` hint AND an explicit instruction appended to the system
  prompt — and a 4xx retries ONCE without `response_format`, since some
  free models reject the field outright. The reply is read by
  `parse_narration_json`, a pure function that handles a markdown fence,
  conversational text wrapped around the JSON, braces inside strings, and
  missing fields. Anything it cannot read becomes `no_clear_cause=True`,
  which `explain` normalizes to `FALLBACK_TEXT` — so a malformed answer
  degrades to "no clear cause found", never to an ungrounded claim.
- **It never raises into the caller.** A transport error, a rate limit, or
  an unavailable model returns a decline (recorded on `last_error`), so one
  failed call degrades a single anomaly instead of taking down the race
  stream it runs inside.

Tested in `python/f1_why/tests/test_openrouter.py` against a scripted
stand-in client — no network, no key: every parse deviation above, the
`response_format` retry, and each failure mode degrading rather than
raising.

## Cost notes

- Model: a small, cheap one — OpenRouter's free tier by default, or Claude
  Haiku (`claude-haiku-4-5`) — over a token-budgeted bundle.
- Calls: **bounded per session** by `Settings.max_explanations`
  (`F1_WHY_MAX_EXPLANATIONS`, default 12). A real 2024 race detects ~302
  anomalies; narrating all of them would be one call each. Detection stays
  uncapped, narration does not — `f1_api.why.most_salient` trims to the
  moments that actually mattered.
- Also skipped entirely for a low-evidence bundle, and cached per anomaly
  (`explain_cached` / `MemoryExplanationStore`).
- CI / `just verify-phase5`: `F1_API_WHY_FIXTURE=1` resolves
  `CitingNarrationModel` (no key, no network).

## Exit criteria

- [x] Anomaly detector: pure Python over kernel/state frames, covering
      `pace_loss`, `position_change`, `pit_anomaly`, `undercut_resolved`,
      `deg_cliff`, and an `off_track`/lock-up proxy — deterministic, and
      tested against a synthetic fixture with exact ground truth (5.1,
      `python/f1_why`).
- [x] Anomaly detection fires on the right moments in spot-checked
      historical races (5.1 fixture + kernel fixture via `f1_api` 5.4/5.5).
- [x] Evidence bundle: kernel state + radio + race control fused per
      anomaly, spoiler-safe (only evidence at or before the anomaly's own
      time — enforced structurally, not just per-item, and the
      degradation context is refit WALK-FORWARD rather than trusting a
      whole-session `KernelReport`) (5.2, `f1_why.evidence`).
- [x] Explanations are grounded: every claim cites retrieved evidence;
      ungrounded output is suppressed by the validator (5.3).
- [x] LLM narration: one grounded, sourced sentence per anomaly over the
      assembled evidence bundle, citing what it used; "no clear cause
      found" when there is no evidence, never a guess (5.3, `f1_why.narration`).
- [x] Weak evidence yields an honest "no clear cause", never an invention
      (5.3 + 5.5 UI + `just verify-phase5`).
- [x] Confidence propagates into the explanation (a thin fit or a
      low-confidence transcript is flagged, not hidden — 5.3 derives an
      `Explanation`'s confidence from the WORST cited evidence item's own).
- [x] Cost stays within budget: a small fast model (Claude Haiku), a call
      skipped entirely for a low-evidence bundle, cached per anomaly,
      never regenerated on request (5.3).
- [x] Explanations are Delay-Sync gated and use only evidence at/before
      the anomaly time (5.4 timeline + 5.5 lane over `visibleState` only).
- [x] Explanations are served through `f1_api` like everything else and
      pass the same Delay Sync gate before reaching a screen (5.4).
- [x] Confidence is shown; users can expand and view the real cited
      sources (5.5 `ExplanationCard`).
- [x] The LLM call sits behind one swappable/mockable interface
      (`NarrationModel`); the whole test suite runs with no live API key
      (5.3 — every test uses `MockNarrationModel` /
      `CitingNarrationModel`).
- [x] `just verify-phase5` green in CI (5.5).

## Anomaly detector: `detect_anomalies` (5.1)

```python
from f1_domain import SessionKey, SessionType
from f1_kernel import HistoricalSource, KernelParams, analyze_session
from f1_why import AnomalyParams, detect_anomalies

key = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
report = analyze_session(HistoricalSource("data/historical"), key, KernelParams())

anomalies = detect_anomalies(report.frames, report, AnomalyParams())
for anomaly in anomalies:          # already sorted, most salient first
    print(anomaly.kind, anomaly.lap_number, anomaly.drivers, anomaly.salience)
```

`detect_anomalies(session_frames, kernel_report, params)` is pure and
deterministic — no I/O, no clock, no network, same frames/report in, an
identical (and identically ordered) list out, the same guarantee every
`f1_kernel` analytic gives. `kernel_report` is ordinarily
`analyze_session_from_frames(session_frames, ...)`'s own output (or
`analyze_session`'s); the two arguments must describe the same session,
the same trust `f1_kernel.frames.SessionFrames` itself asks callers to
honour. `params` is `f1_why`'s OWN `AnomalyParams` — a typed, frozen,
overridable constant set mirroring `KernelParams`' shape (see "Parameters"
below) — while any kernel-level recomputation the detector needs (a fresh
fuel-correction pass, the pit-loss/undercut machinery) reuses
`kernel_report.params`, the exact `KernelParams` that already produced the
report, so nothing can silently disagree with it.

### The six `AnomalyKind`s

| kind | signal | `drivers` | `magnitude` unit |
| --- | --- | --- | --- |
| `pace_loss` | a lap's fuel-corrected time is a robust outlier, slower, versus the driver's own TRAILING recent-clean-lap trend in the same stint | one driver | seconds |
| `off_track` | the same robust-outlier model applied to one mini-sector (sector 1/2/3) instead of the whole lap — the lock-up/off-track proxy this schema supports without telemetry | one driver | seconds |
| `position_change` | a clean, one-for-one on-track swap (never a pit-cycle reshuffle) | `(gainer, loser)`, or one driver when no single rival is isolable | places |
| `pit_anomaly` | a green-flag stop's measured cost is well outside the calibrated/default norm for the circuit | one driver | seconds |
| `deg_cliff` | a stint's degradation fit shows a non-linear (quadratic) falloff large enough to matter | one driver | seconds (extra time implied by the last lap the fit used) |
| `undercut_resolved` | a real stop-pair duel this session's own walk-forward prediction spoke to has resolved | `(attacker, defender)` | seconds (the realized margin) |

`pace_loss`/`off_track` are deliberately NOT compared against the whole
stint's median: a stint with genuine (non-anomalous) degradation curvature
drifts slower over its own length, and a late lap compared against the
whole stint's average would misread ordinary late-stint pace as a false
positive — caught empirically while building this prompt's own synthetic
fixture (see "Tests" below). Comparing against a TRAILING window of the
driver's own recent clean laps (`AnomalyParams.lookback_laps`) instead
tracks that drift rather than fighting it — literally what CLAUDE.md's own
Phase 5 prompt asks for ("suddenly slower than their recent trend").

`pit_anomaly` and `undercut_resolved` add no new fitting or measurement of
their own: `pit_anomaly` calls `f1_kernel.pit_loss.pit_loss_samples`
verbatim (the same green-flag-only, fuel-corrected, neighbour-baselined
measurement the calibration tooling itself uses) and compares against
`KernelParams.pit_loss_for`'s own precedence chain (override > circuit
file > default); `undercut_resolved` calls
`f1_kernel.undercut_eval.evaluate_session_stops` verbatim (the walk-forward
backtest machinery Phase 2's own undercut milestone was scored with) and
reports every real, scoreable stop-pair duel's resolution. `deg_cliff`
reads `KernelReport.degradation_fits`' own optional
`quadratic_term_s_per_lap2` directly — no new fitting, just a threshold on
what `fit_degradation` already computed.

### `Anomaly`

```python
@dataclass(frozen=True, slots=True)
class Anomaly:
    kind: AnomalyKind
    lap_number: int | None
    anomaly_time: datetime          # UTC — a future Delay Sync gate compares against this
    drivers: tuple[str, ...]
    magnitude: float                # non-negative, unit per AnomalyKind
    salience: float                 # [0, 1) ranking score, comparable across kinds
    summary: dict[str, float | int | str | bool | None]  # machine-readable, never prose
    reasons: tuple[str, ...]        # caveats — mirrors CrossoverResult.reasons
```

`summary` is the "machine-readable summary of what changed" this prompt
asked for: the specific numbers the call was made from (observed/expected/
delta, confidence, compound, ...), flat and JSON-friendly, never a
narrated sentence — narrating is explicitly a LATER Phase 5 prompt's job,
not this one's ("DO NOT: assemble evidence bundles or call any LLM.
Detection only."). `anomalies_to_frame(anomalies)` flattens a list to one
row each (`ANOMALY_SCHEMA`) for a report or further aggregation, the same
role every Phase 2 analytic's own `*_to_frame` plays — `summary` is
JSON-encoded into one string column since its shape varies by kind.

### Salience

Every kind's own "how big was this" figure is converted to a
seconds-equivalent (already the case for five of six kinds; `position_change`
scales places by `AnomalyParams.position_change_seconds_per_place` and a
front-of-field bonus, `undercut_resolved` scales the realized margin by a
confidence weight — see `AnomalyParams`) and squashed through one shared,
monotonic, saturating curve:

```
salience = 1 - exp(-seconds_equivalent / AnomalyParams.salience_scale_s)
```

onto a common `[0, 1)` range, so a consumer can rank/limit across
DIFFERENT kinds without knowing each one's own unit — CLAUDE.md's own
"don't explain every 0.05s wobble — surface the ones that matter" framing.
`detect_anomalies` returns its list already sorted by `salience`
descending (ties broken by lap/kind/drivers for determinism), so a caller
gets a ready-to-render, most-important-first list without sorting itself.

Two deliberate confidence-honesty touches, per CLAUDE.md's Phase 5
invariant ("confidence propagates"): a `deg_cliff` riding on an
`UNRELIABLE` degradation fit gets its salience DERATED (never dropped —
the caveat still rides in `reasons`) by
`AnomalyParams.unreliable_fit_derate`; an `undercut_resolved` call gets an
EXTRA salience boost when it was a HIGH-confidence prediction that came
through WRONG (`AnomalyParams.undercut_resolved_upset_multiplier`) — a
genuine upset is more worth explaining than a routine confirmation.

### Parameters

```python
from f1_why import AnomalyParams

params = AnomalyParams()
tighter = params.with_overrides(outlier_mad_multiplier=2.5)
```

Frozen, JSON round-trippable (`AnomalyParams.model_validate_json(params.
model_dump_json()) == params`), and — like `KernelParams` — `with_overrides`
raises on an unknown name or an out-of-range value rather than silently
doing nothing. Deliberately its OWN type, not a reuse of `KernelParams`:
`outlier_mad_multiplier`/`outlier_floor_s`/`lookback_laps`/
`min_reference_laps` answer "is this lap worth explaining", a different
question from the clean-lap filter's own `outlier_mad_multiplier` ("should
this lap be excluded from fitting"), and the two should not silently move
together if one is retuned. Every default is a deliberately chosen round
number, not yet calibrated against a measured corpus — the same honesty
every not-yet-calibrated `KernelParams` field already carries; there is no
`data/`-backed calibration tool for this package yet (nothing here would
have one to calibrate against — see "What Phase 5 explicitly does not do
yet" below).

## Anomaly detector tests (5.1)

```sh
uv run pytest python/f1_why/tests/test_detect.py
```

(`uv run pytest python/f1_why` runs this alongside 5.2/5.3's own test
files, all three sharing the same `race_frames`/`race_report`/
`anomaly_params`/`anomalies` fixtures from `python/f1_why/tests/
conftest.py` — hoisted there specifically so 5.2/5.3 assemble evidence and
narrate over the exact same anomalies this section's own assertions
pin down, never a second invented set.)

One checked-in synthetic fixture (`python/f1_why/tests/race_fixture.py`),
built DIRECTLY as polars frames rather than via `DomainEvent`s/a state
machine (`SessionFrames` is already plain data, so there is no need to
simulate a raw feed to get it) — the same "exact ground truth, no data, no
network" discipline `f1_kernel`'s own checked-in fixture follows, just
constructed inline instead of as committed parquet. Six drivers, each
isolated in its own widely-separated wall-clock time band (thousands of
seconds apart, far more than `KernelParams.undercut_max_gap_s`) so
`evaluate_session_stops`' race-order pairing never mixes one driver's
planted story into another's:

- **"1"** — a lone 24-lap stint with a real (planted) quadratic
  degradation cliff, plus one lap-8 lock-up (the lap AND its sector 2
  spike hard; sector 1/3 stay untouched) — `pace_loss`/`off_track`/
  `deg_cliff`.
- **"2"/"3"** — a green-flag duel: "2" runs 2s ahead, pits first onto a
  compound with a large, HIDDEN pace advantage over "3", who stays out and
  pits later — `undercut_resolved`. The model cannot see a compound's pace
  change in advance (`f1_kernel.undercut`'s own documented limitation), so
  this is a genuine walk-forward test, not a rigged one.
- **"4"/"5"** — flat, boring pace with one clean on-track position swap,
  neither ever pitting — `position_change`.
- **"6"** — one pit stop with a deliberately blown-out cost —
  `pit_anomaly`; "2"/"3"'s own stops cost exactly the calibrated norm,
  proving routine stops are NOT flagged alongside the anomalous one.

Assertions: each kind detected at (or within a lap of) its planted
lap/driver; salience ordering is sane (the decisive duel resolution
outranks the single late-race place swap, and sorts first overall);
`detect_anomalies` is deterministic (same frames/report in, byte-identical
— down to the field — list out, including across a freshly rebuilt
`SessionFrames`); an empty session degrades to `[]` rather than crashing;
`anomalies_to_frame`/`AnomalyParams` round-trip correctly.

## Evidence bundle: `assemble_evidence` (5.2)

```python
from f1_why import EvidenceParams, EvidenceSources, assemble_evidence

sources = EvidenceSources(
    session_frames=report.frames,
    kernel_report=report,
    events=raw_domain_events,       # RaceControlMessage lives only here
    radio_index=my_search_index,     # f1_radio.search.SearchIndex, or None
)
bundle = assemble_evidence(anomalies[0], sources, EvidenceParams())

bundle.items          # ranked + trimmed EvidenceItems, anomaly's own fact first
bundle.low_evidence    # True -> 5.3 emits "no clear cause", never a guess
bundle.anomaly_id      # stable cache key 5.3's ExplanationStore keys against
```

Still no LLM — CLAUDE.md's own line for this prompt is retrieval and
ranking only. `assemble_evidence(anomaly, sources, params)` gathers four
kinds of evidence, all time-boxed to at or before `anomaly.anomaly_time`:

- **The anomaly's own numbers** (`ANOMALY_FACT`) — `Anomaly.summary`/
  `.reasons` verbatim, always exactly one item, and the one item
  `_trim_to_budget` never drops regardless of the token budget, since it
  is the thing everything else exists to explain.
- **Kernel state around the moment** — `LAP_CONTEXT` (compound, tyre age,
  gap-ahead, track status, classification position at the anomaly's own
  lap, per driver in `Anomaly.drivers`, straight from the already-loaded
  lap frame) and `DEGRADATION_FIT` (that driver's current-stint pace
  trend). Two things this deliberately does NOT do: undercut/crossover
  context is never re-derived for `UNDERCUT_RESOLVED` — `Anomaly.summary`
  already carries the full crossover snapshot the detector scored
  against, so restating it here would just be a second copy of the same
  numbers; and braking-point drift is not a kind at all — `OFF_TRACK`'s
  own signal is a timing SECTOR while `f1_kernel.braking`'s drift fits are
  keyed by CIRCUIT CORNER, a different granularity this schema has no
  mapping between, and the fit itself is computed over a stint's whole
  telemetry, which for a mid-stint anomaly routinely extends past the
  anomaly's own lap — re-deriving it walk-forward would need a telemetry
  frame this module has no input for, so rather than silently leak or
  bolt on telemetry loading no prompt asked for, it is simply not built
  (braking drift is also empty on almost every real `KernelReport` in the
  first place, since that stage is itself opt-in).
- **Radio clips** (`f1_radio.search`, Phase 4's own search layer, reused
  rather than a second query path) — a `driver`-scoped, blank-query (pure
  structured filter; there is no topic to search for) call per driver in
  `Anomaly.drivers` inside the lookback window. A driver's own radio
  channel already carries both directions of a pit-wall exchange (OpenF1's
  `team_radio` has no separate "engineer" feed — DATA_CONTRACTS.md), so
  this alone is "the driver's own + their engineer's" the deliverable
  asked for. A clip with no transcript yet (fetched but not transcribed)
  is skipped — nothing citable.
- **Race control messages** (`f1_domain.RaceControlMessage`) in the same
  window, read directly from the session's own raw event stream —
  `SessionFrames`/`KernelReport` carry no raw events, only derived
  frames, so `EvidenceSources.events` is a third input a caller supplies
  alongside them.

**Walk-forward, not whole-session.** The prompt's own instruction —
everything pulled is "time-boxed to <= anomaly_time (never future data)"
— is read as applying to the DATA behind an item, not just its reported
timestamp. `detect_anomalies` (5.1) reads `kernel_report.degradation_fits`
directly, and that report ordinarily covers the WHOLE session — trusting
it verbatim here would let a stint's later laps quietly inform "evidence"
about an earlier anomaly in that same stint. `_degradation_items` instead
slices `session_frames.laps` to `lap_number <= anomaly.lap_number` and
REFITS degradation on exactly that slice — the identical "slice, then
refit on the slice" recipe `f1_kernel.undercut.undercut_snapshot` already
uses for its own walk-forward contract — reusing the original report's own
`params`/`total_laps` so the refit can never silently disagree with the
computation the caller already trusted for detection.

**Ranking and trimming.** Every `EvidenceItem` carries a `salience`
(`_recency_salience`: a kind's own base weight — radio and race control
rank above kernel-state context — modulated by how close it sits to
`anomaly_time` within the lookback window) used ONLY to decide what
survives `EvidenceParams.token_budget`, and a separate `confidence` — a
normalized `[0, 1]` trust signal derived from whichever native taxonomy
already applies (`Reliability`, `Confidence`, a transcript's own
confidence float) — used by 5.3 to decide how much to trust what got
cited. The two are independent questions and must never be conflated.
`_trim_to_budget` sorts candidates by descending salience (the anomaly's
own fact pinned first, unconditionally kept) and greedily keeps whatever
still fits the remaining budget, SKIPPING (not stopping at) an item too
large to fit rather than letting one oversized high-salience item starve
every smaller one behind it. `_estimate_tokens` is a deliberate
approximation (character count over `EvidenceParams.chars_per_token`, not
a real tokenizer) — this package touches no LLM at all, so pulling in a
tokenizer dependency just to budget a prompt would be disproportionate.

**`EvidenceBundle.low_evidence`** is true unless a genuinely EXPLANATORY
item (`RADIO_CLIP`/`RACE_CONTROL`) survived — kernel-state context alone
is background ("on medium tyres, tyre age 8" does not explain a sudden
8-second loss on its own), never itself a cause. This is deliberately
uniform across every `AnomalyKind`, even `DEG_CLIFF`/`PIT_ANOMALY` whose
own numbers already read like a cause ("the tyre fell off a cliff") — the
conservative reading is that WHY it happened still has no corroboration
without external evidence, and CLAUDE.md's own honesty contract favours
under- over over-claiming.

Every source builder is trusted to respect the window it was given, but
the "never future" guarantee is additionally enforced ONCE, structurally,
at the very end of `assemble_evidence` — the same "don't trust upstream,
enforce at the boundary" discipline `TimelineBuffer.release` uses for
Delay Sync's own spoiler-safety gate — so a bug in one source builder can
never leak a future fact into a bundle.

### Tests

```sh
uv run pytest python/f1_why/tests/test_evidence.py
```

Against the SAME checked-in fixture 5.1 uses (`anomaly_params`/`anomalies`
are now shared `conftest.py` fixtures, hoisted out of `test_detect.py` so
5.2/5.3 assemble evidence over the exact same anomalies 5.1 asserts against,
not a second invented set):

- a property test — for EVERY one of the fixture's six anomalies, seed a
  shared pool of race-control "landmines" placed one second after (and
  one second before) THAT anomaly's own `anomaly_time`, then assert
  assembling evidence for every one of them never returns an item whose
  `event_time` exceeds its own boundary (a landmine just after anomaly A
  can legitimately appear in a LATER anomaly B's own bundle — the property
  is checked per-bundle, never globally, which is why one pair is planted
  per real anomaly rather than one global pair);
- a seeded radio clip for the fixture's planted lock-up (`PACE_LOSS`)
  retrieved and ranked as the highest-salience item right after the
  anomaly's own fact;
- trimming to a token budget computed (from the anomaly fact's own
  measured cost) to fit precisely two of five identically-sized planted
  clips, asserting the SURVIVORS are exactly the two closest-in-time
  (highest-salience) ones;
- `low_evidence` toggling from true (no radio corpus, no race control) to
  false the moment a single race-control message is added;
- a radio clip outside the lookback window, and one with no transcript
  yet, both correctly retrieved as evidence of nothing;
- the walk-forward degradation refit using materially FEWER laps than
  (and disagreeing with) the same driver/stint's whole-session fit — the
  regression test for the "walk-forward, not whole-session" contract above.

## Grounded narration: `explain` (5.3)

```python
from f1_why import (
    ClaudeHaikuModel,
    MemoryExplanationStore,
    NarrationParams,
    explain_cached,
)

store = MemoryExplanationStore()
explanation = explain_cached(bundle, ClaudeHaikuModel(), store, NarrationParams())

explanation.text                  # one grounded sentence, or FALLBACK_TEXT
explanation.cited_evidence_ids    # which EvidenceItems it actually used
explanation.confidence            # high / medium / low
explanation.grounded              # False whenever text is the fallback
```

The ONLY place an LLM is used anywhere in this project, and CLAUDE.md's own
line for this prompt is that it stays tightly constrained: the model
explains RETRIEVED evidence, it never asserts a fact that is not in the
bundle.

- **`NarrationModel`** is a narrow Protocol (`complete(prompt) ->
  ModelNarration`) — the same swappable-backend split `f1_radio`'s
  `WhisperBackend`/`EmbeddingBackend` already establish for exactly this
  reason. **`ClaudeHaikuModel`** is the real backend — Claude Haiku
  (`claude-haiku-4-5`), which CLAUDE.md's own Phase 5 budget invariant
  names explicitly rather than leaving to this project's usual model
  default — calling `messages.parse` with a structured `output_format`
  (`{text, cited_evidence_ids, no_clear_cause}`) so the response is typed,
  not parsed prose, and importing `anthropic` LAZILY inside its own
  constructor, the same "importing this module never requires the real,
  optional dependency" precedent `f1_radio.transcription.load_model`/
  `f1_radio.embeddings.load_embedding_model` already set for
  faster-whisper/sentence-transformers — no test needs it installed, let
  alone a live key. **`MockNarrationModel`** is a deterministic, scripted
  fake (mirroring `FakeWhisperBackend`'s own precedent — a plain scripted
  object, not a mock-library double) every test in this package uses; it
  records every prompt it was actually asked to complete (`.calls`) so a
  test can assert a call was SKIPPED, not merely infer it from the result.
- **`build_prompt`** is a pure function (`EvidenceBundle -> str`),
  independently reviewable with no model involved: it renders the anomaly
  plus every surviving evidence item under its own stable id, and
  instructs the model to cite only ids it actually used.
- **`explain`** calls the model AT MOST once — never at all when
  `bundle.low_evidence` already guarantees the answer, the literal
  reading of CLAUDE.md's "keep calls few and cheap" — then runs
  **`_validate_grounding`**: deliberately structural, deterministic checks
  (every cited id must actually be in the bundle; a substantive
  explanation must cite at least one id), NEVER a second model call —
  that would both violate "the ONLY place an LLM is used" and double the
  cost this phase is explicitly budgeted against. `_validate_grounding`
  does not re-check `low_evidence` itself: `explain` is its only caller
  and already returns before ever calling the model in that case, so the
  validator can never actually observe a low-evidence bundle with
  something to validate — checking it there anyway would be defending
  against a state this private function structurally cannot reach.
  EVERY rejection path — a failed grounding check, or the model's own
  honest `no_clear_cause` — normalizes to the SAME canonical
  `FALLBACK_TEXT` and `ExplanationConfidence.LOW`, never a paraphrase, so
  a caller/UI can rely on exact text rather than parsing varying
  LLM-authored "I'm not sure" prose.
- **Confidence propagates**: `_derive_confidence` takes the WORST
  `confidence` among the CITED items only — never the best, and never an
  uncited item's, however low — an explanation is only as trustworthy as
  its weakest cited source. `ExplanationConfidence` is deliberately its
  OWN enum (`high`/`medium`/`low`), not a reuse of
  `f1_kernel.undercut.Confidence`: same shape, a different question ("is
  this narration well-grounded" vs "is this crossover call trustworthy"),
  the identical "must not silently move together if one is retuned"
  reasoning `AnomalyParams` already gives for not reusing `KernelParams`.
- **`explain_cached`** + **`MemoryExplanationStore`** cache by
  `EvidenceBundle.anomaly_id` and never regenerate — the other half of
  "keep calls few and cheap". `ExplanationStore` is a narrow Protocol
  (`get`/`set`), the same `Memory*`-now-`Postgres*`-later split every
  store/index in this project already follows, so a future persistent
  cache is a new class, not a new call site.

### Tests

```sh
uv run pytest python/f1_why/tests/test_narration.py
```

Entirely against hand-built bundles and `MockNarrationModel` — no test
needs a live key, and narration is tested in isolation from evidence
ASSEMBLY (`test_evidence.py`'s own job):

- a clear-cause bundle (a radio clip plus a degradation fit) yields an
  explanation citing both evidence ids, with confidence set by the
  WEAKER (thin) of the two, never the stronger one;
- a low-evidence bundle short-circuits to `FALLBACK_TEXT` with the model
  never called at all (`model.calls == []`) — not merely a fallback
  result, the call itself is proven skipped;
- the model's own `no_clear_cause=True` normalizes to the identical
  fallback text (here the model IS called once, and honestly declines);
- the grounding validator independently rejects a citation of a
  nonexistent evidence id, and separately rejects a "substantive"
  explanation that cites nothing at all;
- `explain` is deterministic given a fixed mock;
- `explain_cached` never calls the model a second time for the same
  anomaly id, and a pre-populated cache entry is returned without the
  model being called even once;
- `build_prompt` is pure and deterministic, and cites every evidence id
  present in the bundle.

## Why-engine API + timeline (5.4)

```python
# REST — detection (no prose) and grounded narration + cited evidence
GET /sessions/{key}/anomalies
GET /anomalies/{id}/explanation

# WS — lightweight explanation envelopes, paced on anomaly_time
# kind="explanation", event_time_utc remapped through the same Delay Sync
# gate every other envelope uses. Client fetches full evidence detail via
# GET /anomalies/{id}/explanation (lazy, like telemetry/radio).
```

`f1_api.why` is the seam: `get_narration_model` / `get_explanation_store`
are FastAPI `Depends` providers (tests override with `MockNarrationModel`
+ a fresh `MemoryExplanationStore`; production uses `ClaudeHaikuModel`
when `ANTHROPIC_API_KEY` is set, else a declining mock so CI never needs
a live key). `anomaly_id_path` encodes `session_key` + `f1_why.anomaly_id`
the same opaque way `clip_id_path` does for radio. Historical replay
precomputes every explanation once (`precompute_session_explanations`)
and emits each at its `anomaly_time`; live generates on newly-detected
anomalies against the same `explain_cached` store.

```sh
uv run pytest python/f1_api/tests/test_why.py python/f1_api/tests/test_why_fixture.py
just export-openapi && just web-generate-client
```

## Frontend UI + verification (5.5)

```sh
just web-test                                  # $lib/why pure-TS core, no DOM
just verify-phase5                             # Playwright — see below
```

`$lib/why/` mirrors `$lib/radio/`: framework-agnostic pure TS
(`view.ts` converters + labels, `ExplanationLaneReducer` over
`visibleState.events` for `kind === 'explanation'`, `buildStoryMoments`
for the salience-ranked story merge) plus thin Svelte glue.

- **`ExplanationLane.svelte`** — timeline strip on `/race`, mounted under
  the timing tower. Reads ONLY Delay-Synced `visibleState` (no second
  spoiler gate). Waiting state until the first explanation releases.
- **`ExplanationCard.svelte`** — one-line text, `ConfidenceBadge`, honest
  fallback styling for ungrounded/`FALLBACK_TEXT` cases, expandable
  **Cited evidence** that lazy-fetches
  `GET /anomalies/{id}/explanation` so the user sees the real sources
  (race-control message, radio clip, deg fit) with links back to those
  REST resources.
- **`/story`** — race story view: REST
  `/sessions/{key}/anomalies` (already salience-ranked) + per-anomaly
  explanations. The shareable, demo-worthy narrative surface. Mobile-first.

### Verification: `just verify-phase5`

```sh
just verify-phase5
```

A REAL browser against the Phase 3 `f1_api` + frontend pair
(`apps/web/playwright.config.ts`), pointed at the checked-in kernel
historical fixture, with `F1_API_WHY_FIXTURE=1` so
`get_narration_model` resolves to `CitingNarrationModel`
(`f1_api.why_fixture`) — grounded, cited explanations without a live
Anthropic key. Low-evidence anomalies still short-circuit inside
`explain` to `FALLBACK_TEXT`, giving the suite both shapes to assert.

`apps/web/e2e/why.spec.ts` asserts:

- (a) a known anomaly gets a grounded, cited explanation (fixture
  race-control-backed `position_change`);
- (b) explanations appear at the right synced moment (`speed=0` + delay
  boundary, same sharp before/after as Phase 3/4);
- (c) no explanation leaks ahead of the clock (waiting state before the
  delay elapses);
- (d) an ungrounded/weak case shows the safe fallback;
- the race story view ranks moments and expands cited evidence;
- usable at a 390px mobile viewport.

`just verify-phase5` is wired into `just check`.

## What Phase 5 explicitly does not do yet

- No telemetry in the evidence bundle beyond what a `KernelReport` already
  computed (braking-point drift is deliberately not a kind at all — see
  `f1_why.evidence`'s own `EvidenceKind` docstring for the full reasoning).
  A future prompt that wants telemetry-derived evidence needs a new input
  on `EvidenceSources`, not a change to what's already there.
- No persistent `ExplanationStore` — `MemoryExplanationStore` is the
  process-level cache today, so a restart re-narrates the capped set; a Postgres-backed store (mirroring
  `f1_radio.store`'s own split) is a later prompt. Radio-index wiring
  through `f1_api.radio` is live for evidence assembly when a corpus is
  configured.
- No calibration tooling/report for `AnomalyParams`, `EvidenceParams`, or
  `NarrationParams` (contrast `f1_kernel/tools/calibrate_*.py`) — there is
  no measured corpus yet of which anomalies were genuinely worth
  surfacing, how far back a genuinely causal radio call sits, or what
  confidence floors actually track a human's own judgement of a good
  explanation, to calibrate any of the three against.
