# Phase 2 runbook

Everything needed to work on the analytics kernel: load typed frames from
any source, tune parameters, filter laps down to the ones that are actually
evidence, and backtest an analytic over a corpus of races. Phase 2 produces
nothing a user sees — its deliverable is derived state that is *correct and
provably so*. The UI is Phase 3.

**Status: prompts 2.1-2.7 are built.** The data layer, parameters, circuit
metadata, backtest harness, clean-lap filter, fuel correction, degradation
fitting, per-circuit pit-loss calibration, undercut/overcut crossover
prediction, braking-point drift, the unified `analyze_session` API and
`just verify-phase2` all exist.

## Exit criteria

Phase 2's release gate, all asserted by `just verify-phase2` (green with no
data, no database and no network — see "Verification" below):

- [x] Kernel runs end-to-end over any 2023+ session with no crashes —
      `analyze_session` over the checked-in fixture, plus the opt-in
      `test_real_historical.py` suite over a real backfill.
- [x] Clean-lap filter removes SC/VSC/pit/dirty-air laps correctly on
      spot-checked races — checked against the fixture's own ground truth
      (`tests/fixtures/truth.json`) both here and in `test_cleaning.py`.
- [x] Fuel coefficient and per-circuit pit loss are CALIBRATED from data,
      not hardcoded — `fuel_coef_s_per_kg` and `default_pit_loss_s`
      (4 shipped circuit files carry their own calibrated value).
- [x] Degradation fits are directionally sane across the dataset (softer =
      higher deg) — `reports/degradation_backtest.md`'s directional check.
- [x] Every analytic emits a confidence/reliability signal —
      `DegradationFit.reliability`, `CrossoverResult.confidence`,
      `BrakingDrift.reliability`, structurally guaranteed by their types.
- [x] THE MILESTONE: the undercut predictor correctly calls the window on
      at least 5 replayed 2025 races — `reports/undercut_backtest.md`'s
      calibration check, currently over 10 held-out 2025 races.
- [x] `just verify-phase2` is green in CI.

What remains beyond this gate is calibration DEPTH, not correctness: most
braking params and several degradation/undercut params are still
PROVISIONAL/STRUCTURAL rather than CALIBRATED (see each section below).

## The shape of it

```
DomainEvent stream ─► frames ─► clean_laps ─► fuel_correct ─► fit_degradation ─► undercut_window
   (f1_kernel.sources)  (lap /   (f1_kernel.    (f1_kernel.      (f1_kernel.      (f1_kernel.
                        stint /   cleaning)      fuel)            degradation)     undercut; value
                        telemetry)                                                 + confidence)
```

Two rules that everything obeys, from CLAUDE.md:

- **Analytics are pure functions over frames.** You LOAD data, then PASS
  FRAMES IN. No analytic opens a database, a socket or a clock. Same input,
  same output, always — that is what makes any of it backtestable.
- **No magic numbers.** Every constant lives in `KernelParams` with a
  documented default and a stated provenance. Nothing reads a global.

## Loading frames

Three sources, one protocol (`f1_kernel.EventSource`), all producing the
same `DomainEvent` stream Phase 1 defined:

```python
from f1_domain import SessionKey, SessionType
from f1_kernel import HistoricalSource, MemorySource, StoreSource, lap_frame

key = SessionKey(year=2024, round=1, session_type=SessionType.RACE)

laps = lap_frame(HistoricalSource("data/historical"), key)   # backfill parquet
laps = lap_frame(MemorySource(events), key)                  # events you hold
laps = lap_frame(StoreSource(conn), key)                     # TimescaleDB log
```

The builders are `lap_frame`, `stint_frame`, `telemetry_frame(source, key,
driver)` and `session_frames` (laps + stints + circuit metadata in one
pass). Schemas are specced in `docs/DATA_CONTRACTS.md` ("Kernel frames") and
declared in `f1_kernel/schemas.py`; treat them like a database schema.

`f1_kernel.list_historical_sessions(root)` answers "what can I backtest
over?" by reading `f1-backfill`'s catalog. It returns an empty list — not an
error — when nothing has been backfilled yet.

### What each source can and cannot give you

| | historical parquet | store / live |
| --- | --- | --- |
| lap, stint, pit, position, gap data | yes | yes |
| track status | reconstructed from race control | the feed's own topic |
| telemetry | opt-in, resampled by FastF1 | only if recorded |
| timestamps | reconstructed, ~second-accurate across channels | the feed's own |

Filters (`kinds=`, `drivers=`) are a **pushdown**: the stream you get is the
full stream filtered the same way, just without paying to build the rest.
Loading a race's lap frame does not materialize its telemetry.

## Parameters

```python
from f1_kernel import KernelParams, describe_params

params = KernelParams()
tight = params.with_overrides(dirty_air_threshold_s=0.9)
for name, value, provenance, doc in describe_params(params):
    print(name, value, provenance)
```

Params are frozen and round-trip through JSON exactly, so a stored result
can always be reproduced. An unknown name or an out-of-range value raises —
a typo in a calibration run must never silently do nothing.

**Most defaults are still PROVISIONAL or STRUCTURAL.** `PROVISIONAL` means a
plausible published figure that this project has *not* measured;
`STRUCTURAL` means a definitional threshold or sample-size floor with
nothing to calibrate; `CALIBRATED` means fitted from our own 2023-2025
backfill, documented below. So far that's `fuel_coef_s_per_kg` (fully) and
`compound_offset_s` (partially — see "Degradation fitting" below). For
everything still PROVISIONAL: relative comparisons (same params on both
sides) are trustworthy, absolute outputs are order-of-magnitude.

## Circuit metadata

`python/f1_kernel/src/f1_kernel/circuits/*.json`, one file per circuit,
loaded by `load_circuit(circuit_id)` / `circuit_for_event(event_name)`.
Per CLAUDE.md it is calibrated where possible, never guessed:

- **corners** — FastF1's own circuit info: real distances along the lap.
- **lap distance** — median telemetry distance over the sampled racing laps.
- **DRS zones** — derived from telemetry: where the DRS channel actually
  reads open, across many laps and drivers, kept only where several
  independent samples agree. `detection_m` is null everywhere because
  detection points are timing-loop concepts no car channel carries.
- **pit loss** — CALIBRATED from our own backfill where a circuit has
  enough measured green-flag stops (see "Pit-loss calibration" below), with
  the uncertainty and stop count stored alongside
  (`pit_loss_uncertainty_s`, `pit_loss_n_stops`); provisional otherwise.
  `pit_loss_provenance` says which one a file is carrying.
- **outline** (Phase 3.4's track map) — the real 2D lap shape, derived by
  `f1_kernel.track_outline.derive_track_outline` from the SAME reference
  lap's own position telemetry (FastF1's `X`/`Y` columns — the channel
  `f1_domain.CarPosition` documents for the live feed). Left in the
  input's own raw coordinate units rather than centered/rescaled, so a
  live `car_position` sample plots directly alongside it with no invented
  transform; see that module's docstring for the full reasoning. `None`
  for a circuit this step has not been run against — but that was every
  shipped circuit's steady state for most of Phase 3, not a per-circuit
  edge case, and got fixed rather than left as an accepted gap: all six
  now carry a real outline (`tools/build_circuits.py`, run once
  `data/fastf1_cache/` had all six races' sessions cached). **Re-running
  `build_circuits.py` resets `pit_loss_*` to its PROVISIONAL placeholder**
  (it always writes that block) — `tools/calibrate_pit_loss.py --seasons
  2024 --write-circuits` must be re-run immediately after to restore the
  CALIBRATED values, the same two-step sequence used here.
- **pit_lane** (added 2026-09-25) — an OPEN polyline from pit entry to pit
  exit, the median path of the reference race's real pit stops
  (`f1_kernel.track_outline.derive_pit_lane`; each car's swerve into its
  own box averages out). `tools/build_pit_lanes.py` writes ONLY this key,
  so unlike `build_circuits.py` it never touches a calibrated pit loss.
  Without it the track map drew cars in the pits and their garages in
  empty space beside the track (a fifth to two thirds of all position
  samples in practice and qualifying).

Six 2024 circuits ship. Anything else returns None from `circuit_for_event`
and an analytic must fall back to `KernelParams.default_pit_loss_s` rather
than assume every venue is present.

Regenerate from the FastF1 cache (needs a populated `data/fastf1_cache/`),
now also deriving the outline from the same session:

```sh
uv run python python/f1_kernel/tools/build_circuits.py            # all
uv run python python/f1_kernel/tools/build_circuits.py bahrain    # one

# build_circuits.py always writes pit_loss_provenance="provisional" — it
# predates pit-loss calibration and doesn't know about it. Restore the
# calibrated values immediately after:
uv run python python/f1_kernel/tools/calibrate_pit_loss.py --seasons 2024 --write-circuits
```

## Pit-loss calibration

What a green-flag stop actually costs, measured per circuit from our own
data (`f1_kernel.pit_loss`, run by `tools/calibrate_pit_loss.py`):

```sh
uv run python python/f1_kernel/tools/calibrate_pit_loss.py --seasons 2024
uv run python python/f1_kernel/tools/calibrate_pit_loss.py --seasons 2024 --write-circuits
```

Per stop: `(in-lap + out-lap) - (median of the clean laps just before the
stop, same stint + median of the clean laps just after the out-lap, new
stint)`, in fuel-corrected time — the neighbour windows make compound
changes and tyre wear across the stop cancel to first order, which the
originally sketched single race-wide baseline could not. Stops under
SC/VSC/yellow/red, drive-throughs (no stint change) and double-stops are
excluded and COUNTED (a stop under a safety car has a completely different
cost — that asymmetry is the whole point of the undercut analytic). The
per-circuit value is the median across stops with a MAD-based standard
error on it.

**By construction the number contains the average out-lap warm-up** — the
out-lap is compared against warmed fresh-tyre pace, and lap times alone
cannot split pit-lane transit from warm-up. That is why
`KernelParams.undercut_out_lap_penalty_s` defaults to 0: raising it while
using a calibrated pit loss would double-count.

The 2024 run (`f1_kernel/calibration/pit_loss.json`, 410 green-flag stops
across 23 races) spans **18.8s (Spa) to 30.1s (Singapore)** with per-circuit
standard errors mostly 0.2-0.6s — matching paddock folklore in both
ordering and magnitude. Four of the six shipped circuit files cleared the
8-stop floor and are now CALIBRATED (bahrain 24.32, albert_park 21.09,
suzuka 22.38, monza 25.73); Jeddah (5 stops) and Monaco (3 — the 2024 race's
stops happened under a red flag, which the green-flag filter rightly
refuses) stay provisional. `KernelParams.default_pit_loss_s` (the
no-circuit-file fallback) is now the calibrated cross-circuit median,
23.03s. Circuits without a shipped file still get an estimate in the
calibration artifact, keyed by slugified event name.

## The clean-lap filter

The reason our numbers should be trustworthy where public tools' are not.

```python
from f1_kernel import annotate_laps, clean_laps, clean_lap_summary

good = clean_laps(laps, params)          # analytics use this
shown = annotate_laps(laps, params)      # a UI uses this: every lap + why
summary = clean_lap_summary(laps, params)  # per-driver counts per reason
```

Nothing is silently dropped: `annotate_laps` returns every row with a
boolean per reason plus an `exclusion_reasons` list, and `clean_laps` is
exactly that filtered to `is_clean`. Reasons: `is_lap1`, `is_in_lap`,
`is_out_lap`, `is_sc`, `is_vsc`, `is_yellow`, `is_red`, `is_dirty_air`,
`is_anomalous`. The full definitions live in `f1_kernel/cleaning.py`'s
module docstring; the three worth restating:

- **Dirty air** is defined as the MEDIAN car-ahead interval over the lap
  window being below the threshold — i.e. the driver was within it for at
  least half the lap. That is what "a meaningful portion" has to mean to be
  reproducible.
- **Anomalies** use median + MAD, never mean + stddev, and the bound is
  estimated over *candidate* laps only (those that survived every other
  filter). Otherwise safety-car laps inflate the spread until nothing looks
  anomalous — the filter would quietly stop working on exactly the races
  that need it.
- **Unknown is not excluded.** A session with no gap channel has unknown
  dirty-air state on every lap; excluding them all would leave nothing.
  `dirty_air_known` / `anomaly_known` report it instead.

Track status is derived, not read: `f1_kernel.status` builds a timeline from
the feed's `TrackStatusChanged` events when present, and otherwise
reconstructs one from race-control messages (historical sessions have no
track-status stream at all). It handles the cases real races actually
produce — a safety car ended by a red flag, a VSC upgraded to a full safety
car, sector yellows cleared one at a time.

## Fuel correction

Without this, "degradation" just measures the tank emptying.

```python
from f1_kernel import clean_laps, fuel_correct, lap_frame

good = clean_laps(lap_frame(source, key), params)
corrected = fuel_correct(good, params)   # + fuel_load_kg, lap_time_fuel_corrected_s
```

`f1_kernel.fuel` has two separate jobs:

- **The fuel model** (`estimate_fuel_load_kg`) — a near-linear burn from
  `params.start_fuel_kg` (110 kg, the regulation max) down towards empty.
  The burn RATE is derived from the race's own length
  (`start_fuel_kg / total_laps`) whenever it is known, so a 78-lap Monaco
  and a 53-lap Monza both target empty at their own finish line rather than
  sharing one constant; `fuel_burn_kg_per_lap` is the fallback when no race
  length is available at all. Both are documented approximations — real
  cars do not literally plan to arrive on empty.
- **The correction** (`fuel_correct`) — a plain affine shift of every clean
  lap to a common reference load (`params.fuel_reference_kg`, 0 kg by
  default), using `params.fuel_coef_s_per_kg`. Exactly reversible:
  `raw = corrected + fuel_coef * (fuel_load - reference)`.

**`fuel_coef_s_per_kg` is CALIBRATED**: 0.0456 s/kg, 95% CI [0.031, 0.060],
from a cross-circuit meta-regression over 780 hard/medium-compound stints
across 23 2024 races. A within-stint regression of lap time against
estimated fuel load is NOT identified — within one stint, estimated fuel
load is a noiseless linear function of lap number, and so is tyre age, so
the two are perfectly collinear (confirmed empirically: it recovers the
wrong sign over the real corpus). What breaks the collinearity is that the
fuel model's burn rate VARIES across circuits while a stint's tyre-age
slope does not, so the method instead regresses each stint's raw
(uncorrected) pace slope against that race's own burn rate — see
`f1_kernel.fuel.FuelStintEstimate`'s docstring for the full derivation.

Re-run the calibration (needs a populated `data/historical/`):

```sh
uv run python python/f1_kernel/tools/calibrate_fuel.py --seasons 2024
```

This writes `python/f1_kernel/calibration/fuel_coefficient.json` (the full
record: value, CI, every per-stint estimate) but does NOT touch
`KernelParams` itself — the literal default in `params.py` is updated BY
HAND after inspecting the script's output, the same review step
`tools/build_circuits.py`'s regenerated JSON goes through.

## Degradation fitting

What public strategy tools fake with a vision demo; this measures it from
lap data.

```python
from f1_kernel import fit_degradation

fits = fit_degradation(corrected, params)   # one DegradationFit per (driver, stint, compound)
```

Each fit is a robust (Theil-Sen) linear fit of `lap_time_fuel_corrected_s`
against `tyre_age`, plus an optional Huber-loss quadratic term
(`quadratic_term_s_per_lap2`) for compounds that fall off a cliff rather
than degrading linearly — attempted only with
`params.degradation_quadratic_min_laps`+ laps, kept separate from the
linear rate rather than blended into it. Every fit reports:

- `deg_rate_s_per_lap` plus its own 95% confidence interval (Theil-Sen's),
- `intercept_s` — fuel-corrected pace extrapolated to `tyre_age = 0`,
- `n_laps_used`, `r2`, and a **`reliability`** verdict:
  `thin` below `params.min_laps_for_degradation`, `unreliable` when the
  rate's CI is wider than `params.degradation_max_ci_width_s_per_lap` even
  with plenty of laps, `good` otherwise. A 4-lap fit is never presented as
  equal to a 15-lap one.

**Backtest** (`tools/backtest_degradation.py`, report committed at
`python/f1_kernel/reports/degradation_backtest.md`): fits every 2024 race,
checks that the MEDIAN degradation rate ranks soft above medium above hard
(PASS on the full 2024 corpus), and flags — rather than excludes — fits
outside a plausible band (2.8% of non-thin fits, well under the 5%
tolerance; almost entirely intermediate-tyre stints on a drying track,
where the track itself speeding up dominates any tyre effect).

**`compound_offset_s` is PARTIALLY CALIBRATED**: `calibrate_compound_offsets`
pairs, for each driver within one session, their mean fresh-tyre intercept
on a compound against their own mean intercept on soft (the reference),
cancelling car/driver pace. Over 2024 this identifies **HARD** cleanly
(0.36s slower than soft, 79 samples across 13 races — now the
`KernelParams` default) but fits **MEDIUM** as slightly NEGATIVE, which the
method's own docstring attributes to a real confound: track evolution,
temperature and traffic move independently of compound and do NOT cancel
between a driver's stints the way fuel cancels within one stint's own laps
(fuel correction already removes THAT trend). Shipping a number known to
be physically implausible would be worse than leaving it at its previous
PROVISIONAL default, so `MEDIUM` (and `INTERMEDIATE`/`WET`, never
calibrated against a dry reference at all) stay untouched. Re-run with:

```sh
uv run python python/f1_kernel/tools/calibrate_compound_offsets.py --seasons 2024
```

It prints an explicit `soft <= medium <= hard` plausibility check and
refuses to suggest writing in a compound that fails it.

## Undercut / overcut crossover

The payoff: not "here is a lap chart" but "here is what is about to
happen". `f1_kernel.undercut` projects both cars of a duel forward on
their fitted, fuel-corrected pace models and answers the pit wall's
question directly:

```python
from f1_kernel import fit_degradation, undercut_snapshot, undercut_window

fits = fit_degradation(corrected, params)
result = undercut_window(frames, "44", "1", fits, params)   # 44 chasing 1
result.window          # open / opens_later / closed / unknown
result.opens_in_laps   # the Phase 3 countdown: "undercut opens in 3 laps"
result.crossover_lap   # earliest defender stop that hands 44 the place
result.confidence      # high / medium / low, from the fits' own reliability
result.reasons         # why — never a bare number

live = undercut_snapshot(frames, params)   # every adjacent pair, one call per lap
```

- **`undercut_window(state, A, B, deg_fits, params)`**: A (behind) pits at
  `decision_lap + 1`; the defender's response lap is scanned.
  `crossover_lap` is the earliest response that still leaves A ahead once
  both have stopped, `projected_gap_if_covered_s` the margin if B covers
  immediately, and the window scan projects both cars forward to say when
  a currently-losing undercut OPENS. `overcut_window` is the exact mirror
  (B pits now, A stays out; `crossover_lap` becomes a deadline).
- **Equal pit losses cancel** in every after-both-stop margin — the
  undercut comes through on the pace difference over the stop stagger, not
  on the size of the pit loss. The calibrated pit loss instead sets
  `projected_rejoin_gap_s` (traffic exposure on rejoining) and is what a
  future safety-car-asymmetry model would perturb for one car.
- **Confidence is derived, not asserted**: both fits `good` and the margin
  sign-stable across the fits' own 95% CIs → `high`; a `thin` fit or a
  margin that flips inside the CIs → `medium`; an `unreliable` fit or a
  non-green decision lap → `low`; missing inputs → the window is `unknown`
  and `reasons` says exactly what was missing, never a silent guess.
- **`undercut_snapshot` is the per-lap live primitive**: it slices the lap
  frame to the decision lap, REFITS degradation on the slice (the
  walk-forward contract, enforced rather than trusted), pairs cars
  adjacent in race order within `params.undercut_max_gap_s`, and returns
  one structured `CrossoverResult` per pair. Phase 3 calls it after every
  completed lap and renders the countdown; nothing here draws anything.

**The backtest** (`f1_kernel.undercut_eval`, run by
`tools/backtest_undercut.py`, report committed at
`python/f1_kernel/reports/undercut_backtest.md`): for every real
green-flag stop in a race, reconstruct the duel (the car directly ahead
for the undercut case, directly behind for the overcut case), make the
prediction at the lap BEFORE the stop from data available up to that lap
only, and score the call against who was actually ahead once both cars
had stopped. Unscoreable pairs (SC stops, double-stacks, rivals who never
stopped again, neutralized sequences) are excluded and counted — the
exclusion ledger is part of the report. Confidence must mean something:
the report splits the hit rate by confidence level and fails its
calibration check if `high` does not beat `low`. On the full 2024 corpus
(628 evaluated duels, 429 scored calls) the walk-forward hit rate is
75.3% overall — **89.3% on high-confidence calls vs 69.7% on low**. The
committed report runs the same protocol on 2025, a season no parameter
was calibrated on: over the backfilled rounds (2-11 at generation time),
267 evaluated duels, 192 scored, 73.4% overall and **85.7% high vs 67.4%
low** — the confidence signal survives out-of-sample. Re-run the tool (or
`tools/run_backtest_suite.py`, see "Backtest suite" below) as more of 2025
lands.

```sh
uv run python python/f1_kernel/tools/backtest_undercut.py --seasons 2025
```

## Braking-point drift

The thing the project originally thought needed a camera, done from the
brake channel every lap instead: `f1_kernel.braking` finds where a driver
starts braking for each corner and tracks how that point moves across a
stint's tyre age.

```python
from f1_kernel import braking_drift, braking_points, stint_frame, telemetry_frame

telemetry = telemetry_frame(source, key, "44")
points = braking_points(telemetry, frames.circuit, params)   # one row per lap x corner
drift = braking_drift(points, stint_frame(source, key), params)  # one fit per driver/stint/corner
drift[0].drift_m_per_lap     # positive = braking EARLIER as the tyre ages
drift[0].reliability          # good / thin / unreliable, same verdict as fit_degradation
```

- **`braking_points(telemetry, circuit, params)`** takes ONE driver's
  `TELEMETRY_FRAME_SCHEMA` frame (telemetry stays driver-scoped throughout
  the kernel) and `circuit.corners`, and returns a FRAME: one row per lap
  the telemetry covers times every corner the circuit ships. For each
  corner it searches an approach window (`params.braking_approach_window_m`
  before the corner, clipped so it can never reach into the PREVIOUS
  corner's own zone — important for a chicane) for the track distance where
  the brake trace, binned onto `params.braking_distance_bin_m`, first stays
  above `params.brake_on_threshold_pct` for at least
  `params.braking_min_sustained_m` — long enough to be a real application,
  not a single noisy stab. Nothing is silently dropped: `known=False` means
  too few telemetry samples fell in the window to trust any verdict
  (`params.braking_min_samples_in_window`), which is a different claim from
  `known=True` with a null `brake_point_distance_m` (plenty of telemetry,
  genuinely no sustained braking event found).
- **`braking_drift(braking_frame, stint_frame, params)`** fits, per
  (driver, stint, corner), a robust (Theil-Sen) trend of
  `distance_before_corner_m` against tyre age — the exact method
  `fit_degradation` uses for lap time, tyre age derived the same way
  (`lap_number - start_lap + 1 + tyre_age_at_start`) so the two always agree
  on how old the tyre was. It reuses `fit_degradation`'s own `Reliability`
  verdict (`good`/`thin`/`unreliable`) rather than inventing a second
  taxonomy for what is structurally the same claim: a fit from a handful of
  laps, or one whose confidence interval is too wide, is never presented as
  equal to a clean 15-lap trend.

Braking-specific params (`brake_on_threshold_pct`,
`braking_distance_bin_m`, `braking_approach_window_m`,
`braking_min_sustained_m`, `braking_min_samples_in_window`,
`min_laps_for_braking_drift`, `braking_drift_max_ci_width_m_per_lap`) are
all STRUCTURAL/PROVISIONAL — debounce floors and search-window caps, not
yet calibrated against a measured spread of real driver behaviour. No
calibration prompt has run for this analytic yet.

## The unified kernel API

Every analytic above is independently callable and independently tested —
`f1_kernel.report.analyze_session` is the seam that chains them in the
documented order (clean -> fuel-correct -> degradation -> crossover ->
braking-drift) into one typed `KernelReport`, so a caller does not have to
re-derive the wiring `f1_kernel.undercut_eval` and every `tools/backtest_*.py`
script already hand-rolled for itself:

```python
from f1_domain import SessionKey, SessionType
from f1_kernel import HistoricalSource, KernelParams, analyze_session

key = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
report = analyze_session(HistoricalSource("data/historical"), key, KernelParams())

report.clean_laps               # annotate_laps' output, filtered to is_clean
report.degradation_fits         # one DegradationFit per (driver, stint, compound)
report.crossovers               # undercut_snapshot at the latest common lap
report.braking_drift            # empty unless braking_drivers= was passed
print(report.summary())
```

- **`analyze_session(source, session_key, params, *, event_name=None,
  braking_drivers=None, decision_lap=None, total_laps=None)`** loads the
  frames from `source` and runs the full kernel. Braking-point drift is the
  one stage that is OPT-IN (`braking_drivers=[...]`): it is the only stage
  that needs telemetry, and telemetry is large and resampled for historical
  sources (DATA_CONTRACTS.md), so pulling it for every driver by default
  would make a routine call expensive for no benefit to a caller that only
  wants lap-level analytics. `total_laps` is resolved once (the frame's own
  max lap number, unless overridden) and shared between the fuel model and
  the crossover horizon, so the two stages can never silently disagree
  about the race length — see `f1_kernel.fuel.fuel_correct`'s own docstring
  for why a mismatch there would bias the fuel model.
- **`analyze_session_from_frames(frames, params, *, telemetry=None, ...)`**
  is the PURE core underneath it — "pure over loaded frames," per the
  prompt that asked for this API: no I/O, no clock, no network, same
  `SessionFrames` in, an identical `KernelReport` out. `analyze_session` is
  the "load, then pass frames in" wrapper around it, the same relationship
  `f1_kernel.load` has to `f1_kernel.frames` everywhere else in this
  package. Every stage degrades to an empty, correctly-schemed result on
  thin/empty input rather than raising — the same discipline every
  individual analytic already follows — so this runs over every session in
  a backtest without special-casing the thin ones. `KernelReport.notes`
  surfaces anything worth knowing that is not itself a number: no circuit
  metadata, an empty stage, braking skipped because the event has no
  shipped circuit.
- **`LiveKernelSession`** is the "live variant": a small stateful
  accumulator — the one kernel object that is not frozen — that holds a
  growing `DomainEvent` stream for one session and recomputes a fresh
  `KernelReport` on every `push`, the shape Phase 3 drives after each
  completed lap. `push` never touches a clock, a socket or a database
  itself; it only accumulates the events it was handed and reruns the same
  pure pipeline over them, so CLAUDE.md's purity invariant holds at the
  level that matters (no wall-clock reads, no network inside the
  transform) even though the object itself has memory between calls. It
  deliberately recomputes from scratch each call rather than attempting a
  true incremental diff — a lap frame is small enough (at most a few
  thousand rows for a full race) that the added complexity would not earn
  its keep.

Every field on `KernelReport` that is itself an analytic's output already
carries that analytic's own confidence/reliability signal
(`DegradationFit.reliability`, `CrossoverResult.confidence`,
`BrakingDrift.reliability`) — the report assembles those, it does not
invent a new number on top of them.

## Backtesting

The test method for all of Phase 2:

```python
from f1_domain import SessionType
from f1_kernel import Backtest, HistoricalSource

bt = Backtest(HistoricalSource("data/historical"))
races = bt.sessions(session_types=[SessionType.RACE], seasons=[2024])
result = bt.run(my_analytic, races)

print(result.summary())
print(result.table)
result.raise_for_errors()          # opt into all-or-nothing
```

An analytic is any `(SessionFrames, KernelParams) -> rows` callable. One bad
session does not end a run — failures are collected in `result.errors` — and
`result.params` records the exact parameter set, because a result without
its parameters is not reproducible. `bt.compare({"old": a, "new": b})` runs
several analytics over the same races into one table with an `analytic`
column: the "did this change help?" view.

### Backtest suite: refreshing the committed reports

```sh
uv run python python/f1_kernel/tools/run_backtest_suite.py               # each tool's own default seasons
uv run python python/f1_kernel/tools/run_backtest_suite.py --seasons 2024 2025
just backtest-phase2                                                     # the same, no-args
```

One command instead of four: it runs `calibrate_fuel.py`,
`calibrate_pit_loss.py`, `backtest_degradation.py` and
`backtest_undercut.py` in sequence and refreshes every committed artifact —
`calibration/fuel_coefficient.json` + `reports/fuel_coefficient.md`,
`calibration/pit_loss.json` + `reports/pit_loss.md`,
`reports/degradation_backtest.md`, `reports/undercut_backtest.md`. It is
purely an orchestrator: each step is the same independently runnable tool
it always was, and nothing here writes into `KernelParams` or the shipped
`circuits/*.json` automatically — every calibrated number still goes
through the by-hand review step each tool's own docstring describes.

Left unset, `--seasons` is NOT forwarded, so each step keeps its own
default: 2023-2025 for fuel/pit-loss/degradation, but 2025 ONLY for the
undercut backtest, since that one is deliberately evaluated on a season no
parameter was calibrated on (see "Undercut / overcut crossover" above).
Passing `--seasons` explicitly forwards it to every step uniformly — do
that only when you mean to change what counts as held-out, e.g. while
calibrating on 2023-2024 only, ahead of a 2025-only backtest.

One step failing does not stop the others (a season with no qualifying
stints for the fuel regression, say); the exit code is nonzero if any step
failed. Dev-only, needs a populated `data/historical/`; never imported by
the kernel at runtime. `just verify-phase2` (below) is the other half of
this deliverable — it asserts the reports this refreshes still say what
they said last time, and needs no data at all.

## Tests

```sh
uv run pytest python/f1_kernel        # everything below
```

Three layers, deliberately:

1. **Checked-in synthetic fixture** (`python/f1_kernel/tests/fixtures/`) —
   two historical races in `f1_backfill`'s exact on-disk shape, with
   `truth.json` recording exactly what was built: which laps ran under the
   safety car, which lap is the planted anomaly, which driver sat in
   another's wake and for how long. Assertions are exact. Runs anywhere,
   with no data, no database and no network.

   Regenerate with:

   ```sh
   uv run python python/f1_kernel/fixtures/generate.py
   ```

   It is synthetic rather than a slice of a real race on purpose: `data/` is
   gitignored because F1's data does not belong in this repo.

2. **Live-provenance tests** (`test_live_provenance.py`) — the Phase 1
   recording replayed through the real pipeline into the kernel, proving the
   frames are genuinely source-agnostic and not quietly historical-only.

3. **Real-data tests** (`test_real_historical.py`) — the same code over
   actual `f1-backfill` output, skipped when `data/historical/` is absent.
   They assert known facts about specific 2024 races (Saudi Arabia's safety
   car on laps 7-9, Australia's VSC on lap 17) and sanity ranges, not exact
   numbers. This is what catches the things synthetic data cannot.

`test_report.py` covers the unified `analyze_session`/`KernelReport`/
`LiveKernelSession` API against the checked-in fixture: every stage present
in the documented order, graceful degradation on an empty session, opt-in
braking via `braking_drivers=`, and — the point of the live variant — that
`LiveKernelSession` fed the same events in two pushes converges on exactly
what a single batch `analyze_session` call produces. `test_verify.py`
guards `just verify-phase2` itself, including that a missing committed
report fails loudly rather than being silently skipped. Real-data pytest
pins (`test_undercut_eval.py`'s coin-flip test, `test_pit_loss.py`'s
Spa-vs-Bahrain ordering) sit alongside the fuel and degradation backtests
(`test_fuel.py`, `test_degradation.py`, and the committed reports/JSON
artifacts described above). `test_braking.py` covers braking-point drift
the same three-layer way — a synthetic end-to-end test (a planted per-lap
earlier-braking trend, driven through `braking_points` then
`braking_drift`, direction asserted on aggregate) plus an opt-in
real-backfill version over a driver's longest stint, skipped rather than
failed when the local backfill has too little reliable evidence to judge.

## Verification: `just verify-phase2`

```sh
just verify-phase2
```

Runs `analyze_session` over the checked-in fixture and asserts the exit
criteria above hold: no crash across both fixture races; the clean-lap
filter's SC/dirty-air/pit/yellow flags match `tests/fixtures/truth.json`
exactly; `fuel_coef_s_per_kg` and `default_pit_loss_s` carry `CALIBRATED`
provenance and at least one shipped circuit has its own calibrated pit
loss; and the two committed backtest reports
(`reports/degradation_backtest.md`, `reports/undercut_backtest.md`) still
say PASS on their own directional-sanity and calibration checks, with the
undercut corpus covering at least 5 held-out 2025 races.

The last two checks are deliberately checks AGAINST THE COMMITTED
ARTIFACTS, not a live recomputation — confirming those claims from scratch
needs the real 2023-2025 backfill this repo does not ship (`data/` is
gitignored). This mirrors the three-layer testing discipline above: the
checked-in synthetic fixture is what makes CI green with no data, and the
committed reports are how a claim that genuinely needs the full corpus
still gets checked on every run. Re-run `tools/run_backtest_suite.py`
against a populated `data/historical/` when the reports need refreshing
(a new season lands, a param changes) — `just verify-phase2` will then be
checking the fresh ones.

It runs as part of `just check`, so CI is red whenever a Phase 2 exit
criterion breaks — the last of the phase-wide contract checks alongside
`just verify-phase0` and `just verify-phase1`.

## What Phase 2 explicitly does not do

- No UI, no API, nothing a user sees (Phase 3). `undercut_snapshot` returns
  structured results for Phase 3 to render; it draws nothing.
- No writes. The kernel reads; `f1_store` owns persistence.
- No traffic model and no safety-car strategy: crossover projections are
  clean-air and green-flag by construction, and say so in their reasons.
- No vision, anywhere. `f1_kernel.braking` finds braking points from the
  telemetry brake channel only — Phase 6's camera stays narrowly scoped to
  wet-track state, client-side, on the user's own stream.
