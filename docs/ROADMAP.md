# Roadmap: from working to best in class

*Written 2026-10-10, the weekend of the 2026 Singapore Grand Prix.*

FastPitStop works. The goal now is to be one of the best F1 dashboards
anywhere, and to be visibly unique, exceptional and dependable. This
document is the plan for getting there. It is depth work inside the
finished phases (CLAUDE.md's phase map). It does not start Phase 6, and
every CLAUDE.md invariant still applies: spoiler safety, the API as the
frontend's only data source, confidence on every derived value, grounded
explanations, and nothing calibrated by guesswork.

## Where we stand

Compared on 2026-10-10 against f1dash.net, f1.harro.ch,
app.formula1dashboard.com, f1livedata.com and MultiViewer.

**What competitors already have and we don't**

- Delay or spoiler protection is common. f1dash.net has a millisecond delay
  control, f1.harro.ch has "Sync to TV", and formula1dashboard.com hides
  finished results behind "Spoiler Protection" ("Play from Start" is marked
  coming soon). Delay Sync on its own is not a differentiator any more.
- Dense timing towers: sector times, mini-sectors, best lap, pit count and
  stint history. Ours has five columns.
- A home page that knows what's happening. f1livedata.com opens on the next
  Grand Prix with a countdown, the track outline and a session picker. Ours
  is text, and `/live` shows one sentence between sessions.
- Weather, standings, a race calendar and qualifying-specific views.
- Phone navigation built for phones (f1.harro.ch uses a bottom tab bar).
  Ours is one long scroll, about 2,900 px tall on a 390 px phone.
- Strategy analysis is no longer rare. f1.harro.ch shows degradation
  curves, undercut analysis and a "PIT AI" column. MultiViewer has a race
  trace and AI radio transcription.

**What nobody else has**

- Predictions with published, backtested accuracy (85.3% on high-confidence
  undercut and overcut calls, over 2025 races the model never saw) and a confidence
  label on every number.
- A delay gate that covers everything: timing, predictions, radio and
  explanations, with the phone clock corrected against the server.
- Explanations that cite their evidence.
- A replay harness that makes every feature testable on real races on any
  weekday.

Nobody has turned these strengths into something a viewer can see and feel.
That is what this plan does.

## Positioning

**The only F1 second screen that tells you what's about to happen, proves
how often it's right, and stays in step with your TV.**

Competitors are pit-wall data tables. FastPitStop is the knowledgeable
friend watching the race with you.

## Decisions (2026-10-10)

| Decision | Outcome |
| --- | --- |
| F1 TV login for live car positions and telemetry | **No.** The live map stays estimated from lap timing, replays keep F1's recorded positions, and there is no live telemetry or DRS state. We don't revisit this. |
| How story mode writes its sentences | **From the data, by templates.** The free-tier model becomes optional polish. No paid credits for now. |
| Visual direction | **Keep our own identity, not the dark broadcast look every competitor shares, and make it much richer.** See W2. |
| Live team radio transcripts | **Parked.** It needs a change to a Phase 4 invariant ("transcription is offline/batch, never in the live hot path"). See "Parked". |

## Ground rules

1. **Nothing ships during a race weekend.** No deploys from the start of
   first practice until the race ends.
2. **Measure on real sessions.** A feature is done when it passes the W0
   release gate on real replayed races, not when one screenshot looks right.
3. **Mockups before UI code.** Every redesigned screen is first a clickable
   mockup built on real data shapes. Building starts after it's approved.
4. **Every item has a "done when."** If we can't measure it, it isn't done.
5. **One milestone at a time,** each shipped between race weekends.

---

## Workstreams

### W0. The release gate ("flawless" as a process)

**Status (2026-10-11): built; the post-session check is still manual.**
- `just verify-real` runs. Its first run caught real layout-shift defects:
  - the empty tower filling;
  - the map appearing late;
  - the loading column snapping to full width.

  CLS went from 0.41 to 0.02 on desktop, all fixed. It also showed that the
  Delay Sync clock offset could read the server as AHEAD under load; it is
  now a lower bound that can only release late.
- Two departures from the plan below, both measured:
  - the frame check is a main-thread budget (headless Chromium composites
    the map canvas in software, so frame intervals measure the test
    machine, not the app);
  - qualifying joined the set on 2026-10-12 (`e2e-real/qualifying.spec.ts`,
    2024 Miami, checked break by break), and so did a sprint (the 2024
    China sprint replayed at both sizes; it and the wet 2025 Miami sprint
    are also checked against official timing in `tower.spec.ts`). No sprint
    qualifying is in it: the backfill has no 2024 sprint qualifying
    classification to check its knockouts against. The 2026 Singapore one
    was checked against F1 on 2026-10-11, outside the gate.
- The watchdog is the `watchdog` service (`f1_api.watchdog`), tested
  against a real stalled stream.
- **Post-session check, automated (2026-10-12).** The `postsession` service
  (`f1_api.post_session`) runs after every live session, once it has been
  quiet two hours. It:
  - files the session from F1's archive in the backfill, only once that
    archive is complete; this also keeps the standings current;
  - compares what viewers saw with it: each lap's time, tyre and tyre age,
    plus each driver's laps, stops and final place;
  - writes a report to `data/post_session/`;
  - logs an ERROR line for each thing that disagrees (logs only, no
    email: your call, 2026-10-12).

  Laps are paired by number in races and by lap time elsewhere, since the
  live lap count runs one ahead in qualifying. The archive's stops are its
  tyre changes, not its stint slots.

  Replaying the recorded Singapore weekend through today's parser, the
  check reports nothing for the race, qualifying, sprint qualifying or
  practice. It reports two real tower errors in the sprint, and the
  red-flagged Bahrain race's genuine tyre and stop gaps.

Comes first because every other workstream ships through it.

- **Real-race replay check (`just verify-real`).** Replays a fixed set of
  real sessions from `data/historical/` through the real API and frontend
  in Playwright, at 1× and 16×, at phone and desktop sizes. The set covers
  a street circuit, a safety-car race, a wet race, a sprint weekend and a
  qualifying session. It skips when the backfill is absent, like the other
  opt-in suites. It fails on:
  - any console error or failed request;
  - any spoiler leak (released data ahead of the playback clock);
  - car dots off the drawn track (reusing `deploy/check_outline_fit.py`);
  - p95 frame time above 20 ms at 1× with 4× CPU throttling;
  - layout shift (CLS) above 0.05;
  - a final running order that differs from the official classification.
- **Visual regression.** *Built 2026-10-12:* `just verify-visual`, see
  the status above. Screenshot baselines for every redesigned screen
  at 390, 768 and 1440 px, in both themes. A diff needs explicit approval.
- **Session watchdog.** During live sessions, a synthetic viewer on the VM
  logs an alert if the live stream stalls for more than 30 s (logs only, no
  email).
- **Post-session data check.** After each session, laps, positions and pit
  stops are compared against F1's archive automatically.

**Done when:** `just verify-real` runs green on the fixed set, is wired
into the pre-deploy checklist in DEPLOY.md, and the watchdog has alerted on
a deliberately stalled rehearsal.

### W1. Story 2.0

**Status: done 2026-10-11** (not yet deployed). On ten races judged against
the raw FastF1 tables: before, 47 of 120 cards true (39%) and 2 of 35
headline moments found; after, 109 of 109 true and 35 of 35 found, with no
unsupported number in any sentence. Details: docs/PHASE5.md, "Story 2.0";
report: `python/f1_why/reports/story_eval.md`.

The current story feels redundant and inaccurate. Reading the code points to
four causes. They are still to be confirmed against real races; the first
step of this workstream is that measurement.

1. **Fake overtakes.** `_position_moves` (f1_why/detect.py) only skips laps
   where *that* driver pitted. When a car ahead pits or retires, every car
   behind "gains a place" and is reported as a move.
2. **One incident, two cards.** `PACE_LOSS` and `OFF_TRACK` share one
   outlier model, so a single lock-up lap produces both.
3. **Clustered verdicts.** `UNDERCUT_RESOLVED` moments bunch at the end of
   a pit cycle. `story_moments` caps each kind at a third of the story,
   which still allows four near-identical cards.
4. **Weak narration.** Free models run with reasoning off under a 50
   requests/day cap, and the evidence is mostly tyre age. Sentences converge
   on "fresher tyres", and an exhausted quota shows "No clear cause found".

**Plan**

- **Evaluation set first.** Ten real races (a safety-car race, a wet race,
  a street circuit, a processional race, a sprint), with the true moments
  marked by hand under `python/f1_why/tests/eval/`. Measure the current
  detector against it and commit the report, the same way the kernel's
  backtest reports are committed.
- **Detection fixes:**
  - A pass counts only when both cars were on track, neither was pitting or
    retiring, there was no safety car or VSC, and the gap closed on track.
    Position changes caused by others' stops or retirements are dropped.
  - Signals for the same driver and lap merge into one moment.
  - A pit cycle becomes one strategy moment naming who gained, by how much,
    and whether our undercut call was right.
  - Lap 1 and safety-car restarts each become one summary card ("Start:
    NOR +3, HAM −2").
- **A narrator built from the data.** A `TemplateNarrationModel` implements
  the existing `NarrationModel` protocol, so `explain()`, the grounding
  validator and the explanation cache stay unchanged. Each moment kind has
  sentence templates filled from its evidence bundle, citing the evidence
  ids it used. For example: "NOR passed LEC for P4 on lap 34. His mediums
  were 8 laps fresher and he closed 1.9 s in 3 laps." It needs no network,
  costs nothing and is accurate by construction. The OpenRouter chain stays
  available behind a setting, off by default.
- **A story with shape.** The story follows the race: start, battles,
  strategy, incidents, finish. It holds as few as 5 or as many as 15
  moments, depending on what actually happened, not a fixed 12.
- **Bump `NARRATION_VERSION`** so stored explanations regenerate from
  templates, which costs nothing.

**Done when**, on the evaluation set:
- at least 90% of shown moments are real (precision);
- at least 80% of the hand-marked headline moments appear (recall);
- an automated fact check finds every number in every sentence in the
  evidence bundle;
- no incident produces more than one card;
- the story renders with no model call.

**Live story fixes (2026-10-11).** The story judged live, as the live hub
does, replaying a race lap by lap. Every finding below came from that
replay, and none showed in replays, which judge with hindsight:
- **Pit stops as retirements:** 2024 Miami called Verstappen, Sainz and
  Piastri retired for pit stops. A running race now needs two laps of
  evidence.
- **Lapped backmarkers as retirements:** 2024 Monaco called six of them
  retired. The last-car rule now counts from the car's own deficit.
- **A lapped car named on a VSC card:** 2026 Singapore's VSC card named
  Bottas, three laps down and still running. A neutralization now names
  only the detector's own retirements.
- **Crashers never told:** 2024 Monaco's red-flag card went out before its
  four crashers were confirmed. Later retirements now get their own cards
  (`f1_why.as_told`).
- **Nothing after the winner's crossing:** that was never analysed live, so
  Singapore's Albon and the podium went untold. It now is, and the finish
  card waits for the podium.
- **The finish itself:** that exposed one more case. At 2024 Miami,
  Piastri passed Zhou on the last lap and crossed 0.2 s ahead, which made
  Zhou look retired. After the flag a car must now be overdue (a lap and a
  half of its own laps since its last timed crossing,
  `AnomalyParams.retirement_overdue_laps`), and a timed crossing after the
  winner's is a finish. "Timed" matters: the backfill gives a lap a car
  never finished a placeholder end time (2024 Australia's Russell), which
  would otherwise read as a finish.

On today's Singapore race, live now tells the start, the two VSCs,
Bortoleto, Colapinto, Stroll, Albon and the finish, and nothing false;
production's live story had a false Bottas card. Replays are unchanged
(story eval 109/109, 35/35).

### W2. Design system v2 and screen redesigns

The brief is "more from our design." We keep the identity: an editorial
serif display face, tabular monospace figures, and both the paper-light and
dark themes. What's missing is life, depth and richness.

**D1. Design principles**
- One hero per screen: the thing that matters most right now is the
  largest thing on it.
- Every card shows a picture of its data (a bar, a trend, a shape), not
  only text.
- Motion means something: it shows change and never decorates.
- Team colour is used deliberately, and teammates are always told apart by
  more than colour.
- Dense when you want it, glanceable by default.

**D2. Design system v2** (`$lib/styles/tokens.css` + components)
- New tokens:
  - motion (durations, easings);
  - elevation (layering);
  - status colours (green, yellow, SC/VSC, red flag, purple, personal best);
  - a data-visualisation palette with colour-blind-safe teammate
    distinction;
  - a display-numeral scale.
- New components:

  | Area | Components |
  | --- | --- |
  | Timing | `StatTile`, `DeltaChip` (animated ±), `PositionChange` |
  | Tyres and gaps | `TyreBadge` (age ring), `StintBar`, `GapBar`, `Sparkline` |
  | Race state | `FlagBanner`, `Countdown`, `TrackThumbnail` (from real outlines) |
  | Layout | `Tabs`, `BottomNav`, `Sheet` (phone modal), `Toast` (moments), `Skeleton` |

**D3. Signature visuals**
- **Live race ribbon:** every car's position lap by lap, growing in real
  time. It upgrades `RaceShapeChart` into a hero visual.
- **Battle card:** the two cars that matter now, with gap trend, tyre
  delta, the undercut line and the next call.
- **Strategy timeline:** each car's stints as bars, with predicted next
  stops shown as ghosts. It upgrades `StrategyBoard`.
- **Track hero:** the circuit outline drawing itself from real data, on the
  home page and the Grand Prix pages.

**D4. Motion language**
- Overtakes reorder tower rows smoothly.
- Times and gaps roll to their new values.
- Fastest laps flash purple.
- Flag changes sweep across the top.
- New moments arrive as toasts.
- Everything honours `prefers-reduced-motion`, and nothing animates in a way
  that suggests data we don't have (estimated positions stay labelled).

**D5. Screens to redesign**
- Home
- `/live` in all four states: off week, pre-session, live, post-session
- Grand Prix weekend page
- Race desk on desktop, with Focus and Full density modes
- Race desk on phone (see W7)
- Sessions archive (track thumbnails, spoiler-safe cards)
- Story
- Circuit pages
- About

**D6. Process**
1. Build 2–3 clickable mockup directions for the three key screens (home,
   race desk on desktop, race desk on phone), with real data shapes.
2. You pick one direction.
3. Build the components, then the screens.
4. Record visual-regression baselines (W0).

Status (2026-10-11): step 1 done. Three directions, each with home, race
desk desktop and race desk phone, all drawn from the real 2024 Miami race
at lap 33 (nothing after lap 33 shown):
- **A, broadcast glance:** dark theme, the battle as the hero, stint bars in
  the tower, the story down the side, bottom tabs on the phone.
- **B, race programme:** paper theme, print-style classification, the
  battle as a headline, a stints-so-far strategy timeline, the next call
  as a dark pit board.
- **C, strategy radar:** calls first (every adjacent pair, with
  confidence), the model's record beside them, the field as one line by
  gap, the story as a lap axis.

Step 2 done the same day: **A** chosen. Your note on it: the three looked
alike, and apart from the stint bars they showed nothing the app lacks
today. The new things the plan adds come from W3, W4 and W5's data, not
from restyling, so each redesigned screen should lead with those.

D2 built 2026-10-11 (`$lib/ui/`, gallery at `/design`, kept out of
search):
- **Tokens:** motion, elevation, track-status fills, teammate dash, display
  numerals.
- **Components:** `StatTile`, `DeltaChip`, `PositionChange`, `TyreBadge`,
  `StintBar`, `GapBar`, `Sparkline`, `FlagBanner`, `Countdown`,
  `TrackThumbnail`, `Tabs`, `BottomNav`, `Sheet`, `Toaster`, `Skeleton`.
- **Pure helpers, unit-tested:** stint bars never draw past the viewer's
  lap, and fill the laps run so far when the race distance is unknown.
- **Contrast:** checked by `contrast.test.ts`, which reads `tokens.css`:
  every text colour on every surface, and every banner and board ink on
  its fill, meets AA in both themes.
- **Browser checks:** both themes at 390 and 1440 px, with no overflow and
  no console errors; the sheet, toasts, keyboard tabs, 44 px targets and
  reduced motion all work.

No screen uses these components yet: the screens are W3, W4 and M3.

**Race desk on desktop (2026-10-14), per mockup A.** Shared by `/live`
and replays. What it adds that the desk lacked:
- **The battle leads** (`$lib/desk/BattleHero.svelte`): the two cars, the
  gap as they last crossed the line, its trend over eight laps with
  safety-car, VSC and red-flag laps shaded, and one sentence on it ("Down
  from 2.7 s on lap 26, most of it behind the safety car (shaded)"; "Same
  tyres, same age: this one is on pace alone" only when nothing was
  neutralised).
- **The race ribbon, "How the top ten got here"**
  (`RaceRibbon.svelte`): every current top-ten car's place at the end of
  each lap, pit stops marked, neutralised laps shaded, teammates dashed by
  car number. It runs to the race distance only when the feed announced
  it; otherwise it ends at the lap reached, so a replay never gives a
  shortened race away.
- **One line on the field** beside the map: who leads by how much, how
  many cars are on the lead lap and how spread out they are.
- **What could happen next** keeps the undercut board and quotes the
  model's held-out record (85.3% of 68 high-confidence calls, 10 races
  of 2025). `model-record.test.ts` checks it against the committed
  report. This race's own scorecard is W5, not built.
- **Layout:** three columns at 1320 px and up (tower; battle, ribbon,
  map; next call and the story), two below that.
- **Motion:**
  - tower rows slide to their new order on an overtake;
  - gaps roll to new values, and the battle's gap rolls once a lap;
  - a fastest lap flashes purple;
  - a flag change sweeps across the strip;
  - a newly released moment arrives as a toast. A late join's backlog is
    never toasted, and a burst at full replay speed toasts only the
    newest.

  All of it is off under reduced motion.

All of it folds the released log (`race-history.ts`), so it grows live
with no REST call and holds nothing past the viewer's clock. Replays carry
no track-status stream, so neutralised laps come from race control there,
read as `f1_kernel.status` reads it. A lap's gap is the one sent as the
car crossed the line, not later sector updates.

The phone glance now draws its gap trend from the same history (it read
kernel snapshots, which are sparse) on an axis from zero, and the ribbon
sits in its Strategy tab.

Checks:
- unit tests, including a property test that the ribbon never holds a
  lap the released log has not completed;
- `e2e/desk.spec.ts`, now in `verify-phase3`: nothing before the delay,
  the ribbon never ahead of the lap counter, the three columns at 1440,
  and no overflow at 390, 768, 1024 and 1440.

The race-desk visual baselines change and need re-approval.

Layout shift, measured here on a 16x replay with a 5 s delay, the first
25 s:

| Desk | 1440 px | 1440 px, Focus | 1024 px | 390 px |
| --- | --- | --- | --- | --- |
| Before W2 | 0.039 to 0.051 | n/a | n/a | n/a |
| After W2 | 0.022 to 0.047 | 0.005 | 0.010 | 0.010 |

The early part, about 0.04, is a load-time reflow of the sync strip.
The old desk has it too; on your desktop the whole figure measured 0.02
(W0).

To stay inside W0's 0.05 budget:
- every new block keeps its size from the first paint;
- the battle's gap sits in a column of fixed width;
- at 1320 px and up, the next call fills the slot beside the battle and
  the ribbon without sizing it, and scrolls on the rare lap it needs
  more room.

The ribbon and the trend are recomputed only when a place, a lap's gap
or a stop changes, not on every released event.

**Home (2026-10-14), per A-Home.** It already had the next race, its
countdown and sessions, the circuit drawing itself, and the last race
behind the shield. It gains the mockup's three points under them:
- never ahead of your TV;
- says what just happened;
- calls the strategy, with the model's held-out record.

One departure for honesty: the mockup's "and keeps score" reads "and
shows its record". A per-race scorecard is W5, not built yet.

The search text, recent races and questions stay below, unchanged. The
home visual baselines change.

**The other D5 screens (2026-10-15).** Mockup A drew none of them, so ten
new boards came first, on the same design canvas, in direction A and
from real figures (two rows under the M2 ones): `/live` off week,
pre-session and post-session (pre-session also at phone width), the
archive and story (also at phone width), about, and the desk's Focus
density. You asked me not to wait, so they were built before your
approval. Each leads with something the screen lacked:
- **`/live` between sessions** (`$lib/live`):
  - *Pre-session:* a countdown to the next session is the hero, then the
    weekend's earlier sessions offered from the start, and how to get set.
  - *Post-session:* the session just finished, offered from its start
    with the result hidden, then the next one.
  - *Off week:* the next Grand Prix.

  Every state ends with "Before the weekend": what a stop costs at the
  circuit, on the scale of every measured circuit; tyre wear per
  compound, thin fits drawn as outlines; and the lap. All of it comes
  from past races. `idleState` picks the screen from the calendar, the
  catalog and the clock (unit-tested). Pre-session weather is still not
  built: the feed sends none.
- **Archive:** one card per weekend, every session it holds in running
  order. Above them, the season as a strip of circuit outlines: rounds
  run, the next one marked, later ones faint, and a run round with no
  replay marked "no replay". The "include qualifying & practice" switch
  is gone; every session is a chip now.
- **Story:** held back to the lap the viewer says they have watched
  (remembered per session; `?upto=` in a link), and the heading says
  how far. It used to show the whole race the moment one was picked.
  Moments are grouped (start, on track, strategy, incidents, finish)
  under a lap axis that ends at the viewer's lap. It never shows the
  race's length, or how many moments are still hidden: either would
  hint at what is coming.
- **About:** led by four published numbers: the held-out undercut
  record, 410 pit stops measured, the fuel coefficient, and 110/110
  story cards. `published.test.ts` checks each against its committed
  report.
- **Desk density:** Focus or Full, from the masthead on screens 900 px
  and wider, remembered on the device. Focus keeps the battle, the next
  call, the story, the ribbon and the top ten. The map, analytics, radio
  and race control are left out, not just hidden.

Checks:
- `e2e/archive.spec.ts` (new, in `verify-phase3`);
- `weekend.spec.ts`, which now checks the pre-session screen;
- `why.spec.ts`, which now checks the story is held back first;
- a Focus test in `desk.spec.ts`;
- the test calendars gain the fixture's 2024 season.

Baselines that change: live, sessions, about and race desk. The story
page has none.

**Circuit and Grand Prix pages against their M2 mockups (2026-10-15).**
Both match. The one real difference: the Grand Prix page's stop-cost
card left out the DRS zones it already loaded. It now lists them. The
circuit page goes further than its mockup (numbered corners on the
outline, the evidence behind each tyre figure) and is unchanged. The
grand-prix baselines change.

**Not doing:** per-car labels on the map (removed at your request), F1 or
team logos, driver headshots.

**Done when:**
- every D5 screen matches its approved mockup;
- the screens pass W0 in both themes at all three widths;
- WCAG AA contrast is met throughout.

**Status (2026-10-17): done.** Mockups approved, `verify-visual` approved,
`verify-real` passing. You dropped the planned side-by-side review against
competitor screenshots: other dashboards are inspiration, not a gate.

### W3. Weekend hub: never empty

- **API:** `GET /schedule?season=` and `GET /schedule/next`, built from the
  FastF1 event schedule `f1_backfill/schedule.py` already reads and cached
  daily. They return session start times in UTC plus circuit-local time.
- **Home hero:**
  - the next Grand Prix, with a countdown in the viewer's time zone;
  - the track hero (D3) and the weekend's sessions;
  - the latest race behind the spoiler shield.
- **`/live`** gets a real screen in every state:

  | State | What it shows |
  | --- | --- |
  | Off week | The next Grand Prix |
  | Pre-session | Countdown and weather |
  | Live | The dashboard |
  | Post-session | "Watch from the start" (W6 catch-up), with the results behind the shield |

- **Weather strip:** from the `weather_sample` events already in the
  stream (air and track temperature, humidity, wind, rain).

**Done when** no route ever shows a bare "nothing here" message, checked
by a Playwright pass over every state with a stubbed schedule.

Status (2026-10-11), built:
- **Schedule API:** `GET /schedule?season=` and `GET /schedule/next`
  (`f1_api.schedule`). FastF1's calendar, cached a day; a failed refresh
  serves the last good copy, and production fetches it in the background at
  start. The next event is this weekend's round until three hours after
  the race start (the regulations' cap on a race), then the next one.
- **Circuits for calendar rounds:** `GET /circuits/{id}`. Each round finds
  its circuit through `circuit_for_schedule`, using FastF1's schedule
  location (`schedule_locations` in each circuit file,
  `tools/build_circuit_locations.py`). Every 2024 to 2026 round resolves,
  including 2026's moved races (Madrid, Sepang).
- **Home:** the next-race block is rendered on the server, so search
  engines read it. It shows a countdown to the next session, the weekend's
  sessions at the track and in your time, the circuit drawn from its
  outline, and the last race behind the spoiler shield.
- **`/live` between sessions:** the same block in place of a paragraph.
- **Weather strip:** in the race desk masthead (air, track, humidity, wind,
  rain), from released samples only, at a fixed width so its arrival never
  moves the page.
- **Proof:** `e2e/weekend.spec.ts` in `just verify-phase3`, on a stubbed
  calendar whose times are relative to now, so a weekend is always in
  progress.
- **Not built:** pre-session weather, since the feed sends none before a
  session starts.


### W4. Timing and race-state completeness

- **Timing tower:**
  - a gap/interval toggle;
  - best lap;
  - sector times (already carried by `LapCompleted.sector_times` and
    `SectorCompleted`);
  - pit count and a stint-history mini-bar;
  - positions gained since the start;
  - retired and DNF states.
- **Track status:** full-width banners for SC, VSC and red flag, from the
  existing track-status timeline.
- **Race control feed** on every layout, phone included.
- **Qualifying view:**
  - the knockout cut line and the current Q part;
  - eliminated drivers greyed out;
  - improving laps marked, plus provisional pole.
- **Sprint and sprint-qualifying** handled as their own session types.
- **Standings:** drivers and constructors, computed from backfilled results.
- **Stretch: mini-sectors.** These need the Phase 1 parser to decode
  `TimingData` segments. Decide after M2.
- **Live races lost lap 1 (found and fixed 2026-10-11).** A race taken
  from the live feed had no lap 1, so it got no start card. The recording
  of the 2026 Singapore GP showed why: at the end of lap 1 the feed sends
  `NumberOfLaps: 1` and no lap time (the first `LastLapTime` is lap 2's).
  The parser now emits a race's or sprint's lap 1, timed from the
  "Started" status it saw (both receive times, so the feed's latency
  cancels): all 22 Singapore drivers get a lap 1 of 1:42 to 1:55, and the
  live story opens with its start card. A worker that joins after the
  start invents nothing.

Status (2026-10-11):
- **Tower, built:**
  - "Timing" and "Sectors" tabs;
  - an Int/Gap switch;
  - places gained since the start;
  - a stint-history bar that stops at the viewer's lap;
  - S1 to S3 of the last lap, plus best lap, coloured purple and green;
  - retired cars marked OUT and moved to the bottom.
- **OUT rule:** judged from the released laps with the story's rule, made
  safe for a running race: a car behind two laps on, or the leader two laps
  past the car's own deficit. Replayed with no false marks on 2024 Miami
  (Sargeant), Monaco (the four lap-1 crashers) and São Paulo (Colapinto,
  Hülkenberg, Sainz). F1's own `Retired` flag is no substitute: in
  Singapore it came only for Albon, after the race had finished.
- **Flag banner:** the sticky sync strip is the full-width flag banner,
  tinted for yellow, SC, VSC and red. It occupies the same space on green,
  so a flag never moves the page.
- **Checked against official timing (2026-10-12):** `e2e-real/tower.spec.ts`
  (in `just verify-real`) replays each W0 race to the flag in the browser
  and compares every tower row with F1's classification and lap data
  (`deploy/official_timing.py`): place, laps completed, last lap, best lap,
  stops, finishing tyre and OUT. All five match. It found one real bug: the
  archive keeps an untimed row for the lap a car stopped on, and the
  adapter replayed it as a completed lap (2024 Miami's Sargeant showed 28
  laps of his 27; Monaco's lap-1 crashers showed 1). The live feed never
  counts that lap, so a replay disagreed with live. `f1_historical_adapter`
  now drops it when the classification says the car never completed it
  (its sectors and pit entry stay). The story's retirement rule had leaned
  on those rows, so it now finds a lap-1 crasher from the grid and places
  each retirement on the lap the car stopped on. That is what a live
  viewer gets too, and it is more accurate: 2024 Australia's Russell
  crashed on lap 57, not 58. The story check still gives 110/110 true
  cards and 35/35 headlines.
- **Live sessions checked too (2026-10-12):** the four races the store took
  in live (2026 rounds 14 to 17) against the archive's timing. Laps
  completed match for every car in all four. Round 17 (Singapore) matches
  every field. The rest is explained:
  - Hamilton's round-14 best lap: his first four laps are missing from the
    store (the weekend the worker filed the race under practice).
  - Round 15's last laps: a slow final lap is timed live and untimed in
    the archive.
  - Round 16's stop counts: the store lacked lap 1, before the parser
    timed it.
  - Round 16's tyres: a real bug. In that red-flagged race F1 rewrote the
    stint lists (moving stints down a slot and reusing slots), and the
    parser read the last slot as the tyre fitted. A stale slot left
    Piastri on "hards, 1 lap old" for his last ten laps, on mediums.
  
  The parser now takes the stint the feed last started or counted a lap
  on (`f1_parse.state_machine._stint_moves`), and a set started in a
  reused slot, or a used set (it opens at the laps it had already done),
  is a stint of its own. Stints are numbered in the order the sets were
  fitted. Tyre age is the feed's `TotalLaps`, which already counts a used
  set's earlier laps; adding `StartLaps` counted them twice (Russell's
  used softs read 7 laps old when they were 5). These changes were
  checked by replaying the recorded Bahrain and Singapore races through
  the live path against the archive's tyre on every lap:

  | Race | Laps on the wrong compound | Laps whose tyre age is off by more than 1 |
  | --- | --- | --- |
  | Bahrain | 32 → 2 | 439 → 136 |
  | Singapore | 0 → 0 | 328 → 0 |

  Both remaining Bahrain laps are ones on which the feed itself said
  softs until it corrected them.
- **Live stints in the kernel (2026-10-12):** every live lap of the 2026
  Singapore GP had no stint number, because a race's first set opens
  before the feed counts a lap and `f1_kernel.frames._stint_for_lap`
  skipped a stint with no start lap. Live degradation fits therefore
  merged stints on the same compound. A stint with no start lap now covers
  the race from the start.
- **Pit count beyond stints (2026-10-12):** the tower counts stops as the
  stints begun after the first lap (`timing-tower` `stopsMade`).
  - Before the start the live feed can open a car's set twice. Singapore
    did this for three cars, and counting stints showed them a stop down
    from lap 1. The stint bar now draws only the last set opened before
    the start.
  - Replayed live, Singapore's stop counts match official for all 22 cars
    throughout the race.
  - Bahrain's are off for 5 of 22 at the flag. Four of those had sets F1
    never named, opened in old slots with nothing to mark them (the
    archive has no compound for them either). The fifth is Piastri's
    stale slot.
  - The old count was off for 2 Bahrain cars at the flag, and for the
    three Singapore cars from lap 1.
- **Race control feed on every layout (2026-10-12):**
  `e2e-real/feed.spec.ts` replays the 2024 Bahrain GP at 390 px and
  1440 px. The "Radio & race control" panel is on the page, inside the
  screen's width, and lists race control's messages under its filter.
- **Sprints (2026-10-12):** a sprint replays like a race (`?session=sprint`)
  and its tower matches the official timing in every field. It is checked
  on the 2024 China sprint and on the wet 2025 Miami sprint, in which
  every car but one pitted. Writing the check found a flaw in the check
  itself: OUT means the car stopped, not that it was unclassified. The
  China sprint's Alonso stopped on lap 18 of 19 and was classified 20th.
  The 2024 Miami sprint cannot be a test: its archive gives every car two
  pre-race pit visits as laps 1 and 2, so its replay shows two stops each.
- **Still open:** mini-sectors (the stretch). Qualifying and standings are
  built (see the M2 screens below).

**Done when** every tower field is checked against the official timing for
the W0 sessions, and qualifying shows the right drivers eliminated at each
Q break in a real qualifying replay.

Mockups for M2's new screens (2026-10-11, direction A, on the design canvas,
approved the same day):
- **Qualifying:** 2024 Miami, Q2 with 1:15 to go. Who makes Q3, with the cut
  line; what each car in the drop zone needs to beat (1:28.323); a strip of
  every time against the cut; who is on a flying lap now; the Q1 knockouts
  greyed out.
  *Built 2026-10-11:* four new domain events (`QualifyingPartChanged`,
  `QualifyingBestLap`, `QualifyingKnockout`, `SessionClockUpdated`), parsed
  live from `TimingData` and `ExtrapolatedClock` and rebuilt for backfilled
  sessions from race control, laps and results; `$lib/qualifying` folds
  them into the board, which replaces the race tower, pit board and race
  analytics for qualifying and sprint qualifying. Replays open with
  `?session=qualifying`, and the masthead links a weekend's sessions. One
  departure from the mockup, for honesty: "On a flying lap now" is "On
  track now", with out laps marked, because nothing in the feed tells a
  push lap from a cool-down lap. The map draws only measured positions in
  qualifying (the timing screen cannot place the cars). Checked: the 2024
  Miami rebuild matches every official Q1/Q2/Q3 time and knockout
  (opt-in test); the recorded 2026 Singapore sprint qualifying (red flag
  included) and qualifying give the same knockouts as F1 on both paths;
  `e2e-real/qualifying.spec.ts` replays 2024 Miami in the browser and
  checks each break. Deployed 2026-10-12: the four 2026 qualifyings the
  store took in before the parser read Q parts (Azerbaijan, Bahrain at
  Sepang, Singapore and its sprint qualifying) are served from the
  backfill, which rebuilds them.
- **Standings:** 2024 after Miami, behind their own spoiler shield ("as of
  the last race you watched"). A points sparkline per driver, how the top
  six got here, constructors, and the points still available.
  *Built 2026-10-11:* `/standings` and `/standings/{year}` over
  `GET /standings?season=&after_round=` (`f1_api.standings`): the official
  `Points` of every backfilled Grand Prix and sprint, ties split by
  countback, constructors credited per race to the team each car raced
  for. It reproduces the 2024 championship exactly (opt-in test). The
  page asks only for points up to the viewer's chosen round (kept on the
  device); until they choose, a race from the last 7 days, or an undated
  latest round, stays out. A round counts once its Grand Prix is
  backfilled. Checked by `e2e/standings.spec.ts` in `verify-phase3`.
  2026-10-12: read as "completely wrong" because the page showed the table
  after round 16 (Singapore held back) and said so only in a small box. The
  heading now names the race it counts up to ("After Bahrain, round 16 ·
  16 of 17 races counted") and a notice names the held-back race with a
  one-click "Count Singapore". `deploy/check_standings.py` compares every
  round with Jolpica: all of 2025 and 2026 match on points and wins, and
  on places wherever the FIA countback decides them.
- **Circuit page (S2):** Marina Bay. Its outline with numbered corners and
  DRS zones, the measured pit loss with its uncertainty, and tyre wear by
  compound, with thin fits marked.
- **Grand Prix page (S3):** 2026 United States GP. A countdown, the
  sessions at the track and in your time, what a stop costs there, and how
  the 2024 race was run (strategy only, no results). The mockup proposes an
  "Add to calendar" button that is not built yet.

### W5. Strategy radar and scorecard (signature)

Forward-looking calls, released through Delay Sync like everything else:

- *"LEC's undercut window opens in 2 laps."* (Exists today; 85.3% on
  high-confidence calls in the 2025 report, 71.4% on undercut calls
  alone.)
- *"Box now and HAM rejoins P6, in traffic behind RUS."* Rejoin
  projection; the pit board already computes it.
- *"VER's pit window opens around lap 31."* New: the crossover of
  degradation against pit loss.
- *"NOR's mediums are near the cliff."* New, a v2 candidate, built on the
  degradation cliff signal.

Every call resolves as right or wrong once the outcome is known, and the
race shows a live scorecard ("Our calls today: 7/9"), split by confidence.
After the race a ledger page lists every call. Only resolved calls count.

Same discipline as the undercut backtest: **a call type ships only after a
held-out backtest on 2025 races shows calibrated hit rates (high beats
medium beats low), with the report committed.**

**Done when** v1 (undercut and rejoin calls) passes its backtest, the
scorecard reproduces the backtest numbers on replayed races, and no call
ever appears ahead of the playback clock (property test).

**Status (2026-10-16): all five steps built. The held-out check failed
for both call types, so the screens stay behind the gate. Waiting on
your decision.**

*Found first:* the committed 2025 undercut report fails W5's own bar.
Medium scores 65.8% (79 calls) and low 67.4% (43), so medium is below
low. Its check only compared high (85.7%) with low. The live desk also
shows only undercut calls, while that report mixes in overcut ones.
You chose option A: diagnose on 2024, change the rule only if 2024
agrees, then check 2025 once.

*Built:*
- **Diagnosis tools.** Each scored undercut pair records what set its
  confidence:
  - the worse of the two degradation fits;
  - the medium cap, plus the same check at the rival's real stop lap,
    where the call is actually scored;
  - the not-green cap.

  The report adds:
  - a W5 ordering check with 95% intervals;
  - the same check for undercut calls on their own;
  - hit rates along each of those inputs;
  - with `--diagnose`, the candidate rules.
- **Calls in the kernel** (`f1_kernel.calls`, `f1_kernel.rejoin`).
  - *What a call is:* what the desk said about a car the lap before it
    boxed: an undercut window, or a rejoin ("P6, behind RUS").
  - *How it resolves:* right, wrong, void (with the reason) or no call.
    Only right and wrong count, split by confidence.
  - *Same code as the backtest:* the ledger is the backtest's own
    evaluator run over the laps released so far, so at the flag the
    scorecard is the backtest on that race.
  - *Rejoin confidence:* clear air to the nearest car against the spread
    of one stop at that circuit.
- **Rejoin backtest** (`tools/backtest_rejoin.py`). Pit loss is measured
  only on the seasons before the one being tested.
- **Ship gate.** `CLEARED_CALL_KINDS` is empty until a held-out report
  passes; a test fails if the two disagree. `F1_API_CALL_KINDS` turns
  kinds on for development and the browser tests only.
- **Through Delay Sync.**
  - A `call` stream envelope is sent when a call locks and again when
    its status changes.
  - `GET /sessions/{key}/calls?upto=` serves the ledger page.
  - Every per-lap analytics update now carries each car's rejoin
    projection.
  - In the browser, `$lib/calls` folds the released calls into the
    scorecard.

*Checks:*
- **Kernel property test:** both fixture races and four scripted edge
  cases, at every lock and settle instant:
  - nothing is dated past the viewer's moment;
  - a settled call never changes in any field.

  It found and fixed a void whose reason changed between mid-race and
  the flag.
- **API property test:** no `call` envelope is stamped before the
  replayed crossing it is about.
- **Browser property test:** 200 seeded trials through the real
  `TimelineBuffer`.

*Step 4, mockups (2026-10-15):* five new boards on the design canvas,
row W5. They are drawn from the ledger the code produces on the
checked-in Bahrain replay at lap 16; the 2025 record is left as
placeholders until the held-out run exists.
- **Race desk:** where the focused car comes out if it boxes now, drawn
  against how much a stop varies here; today's score by confidence; the
  latest calls.
- **Phone:** the Strategy tab.
- **Ledger page:** held to the lap you have watched, like the story
  page, and never showing the race's length. Calls are grouped as
  counted, waiting and not counted, with the reason for each.
- **Ledger on a phone.**
- **The call card in each state.**

Drawing them found two bugs, both fixed:
- **Rejoin projection:** it covered a car already in the pits, or on its
  out-lap.
- **Undercut evaluator:** a rival that pitted on the decision lap itself
  (so it stopped first) was left waiting for a second stop. Scored rows
  are unchanged.

*Diagnosis on 2024 (2026-10-16),* `just w5-diagnose`, committed as
`reports/undercut_backtest_2024_diagnosis.md` and
`reports/rejoin_backtest_2024_dev.md`:
- **Undercut confidence: a thin fit is now low, not medium.** Neither
  rule the plan offered passed on 2024 (undercut calls only):
  - the old rule: high 84.3%, medium 60.2%, low 64.0%;
  - the sign check at the rival's real stop lap: high 88.2%, medium
    62.5%, low 64.0%. It also needs the real stop, which is not known
    live, so it could never run on the desk.

  The cause was the fit floor. A thin fit (too few laps; its interval is
  never checked) scored 58%, below an unreliable one (70%), yet it made
  a call medium. Now only two good fits earn medium or high, and medium
  means two good fits whose margin straddles zero. On 2024: high 84.3%,
  medium 67.9%, low 57.8% (all calls: 89.4%, 71.4%, 64.0%). Swapping
  the two weak tiers also passed, but it would claim an unreliable fit
  beats a thin one, against the ranking the rest of the product shows.
- **Rejoin: thresholds and void rule unchanged.** Pit loss is measured
  on 2024 itself (`--pit-loss-in-sample`), labelled in the report: F1's
  archive answers 401 since October 2026, so 2023 cannot be fetched.
  High 78.2%, medium 63.6%, low 46.8%; clear air rises steadily through
  the 1 and 2 sigma thresholds. `another_car_pitted_around_the_rejoin`
  voids 382 of 648 calls, but it is not too broad: scored anyway, those
  calls hit 21% when a projected neighbour pitted and about 47 to 59%
  when only a car it drops behind did, against 66% when nobody did.
  They test the other cars' stops, not the projection.

*Held-out check on 2025 (2026-10-16),* `just w5-holdout`, run once with
the rules above, committed as `reports/undercut_backtest.md` and
`reports/rejoin_backtest.md`. **Neither call type passed, so neither
ships;** `CLEARED_CALL_KINDS` stays empty.
- **Undercut calls:** high 71.4% (28), medium 66.7% (21), low 66.7%
  (42). Medium ties low. All calls together now pass (85.3%, 70.7%,
  64.5%; before the rule change medium was below low), and so do
  overcut calls (95.0%, 75.0%, 61.8%), but the desk shows undercuts.
- **Rejoin:** high 87.9% (33), medium 22.2% (9), low 51.0% (49).

The corpus is the 10 races of 2025 on disk (rounds 2 to 11), so the
middle buckets hold 9 to 21 calls with intervals 30 to 50 points wide.
It cannot grow: F1's static archive answers 401 to every request since
October 2026, from the desktop and from the VM, and `f1-backfill`
depends on it. 2025 is now spent; tuning on it again would no longer be
a held-out test. A fresh held-out season would be 2026 as recorded live
by our own pipeline, which no rule has been read on.

*Step 5, the screens (2026-10-16),* from the approved mockups. Built
and tested, and hidden in production: nothing shows until a call kind
is cleared.
- **The gate reaches the stream.** Each car's rejoin projection IS the
  rejoin call, so the per-lap snapshot carries projections only when
  rejoin calls are served, plus the kinds served (`call_kinds`), which
  is how the desk knows which panels to show. Before this, every
  snapshot carried every car's projection whatever the gate said.
- **Race desk:** "If Pérez boxes at the end of this lap: P5, 1.1 s
  behind Russell" leads "What could happen next", drawn against one and
  two times how much a stop varies here, with the kernel's own
  confidence and reason (`$lib/calls/RejoinStrip`). The undercut keeps
  its call and numbers under it, its two charts dropped so the board
  still fits. "Our calls today" and the latest calls sit under the
  board (`$lib/desk/CallsPanel`); without them the desk's layout is
  unchanged, area for area.
- **Phone:** the Strategy tab leads with the rejoin strip, the score
  and the latest calls.
- **Pit board:** its "Rejoins" figure is the kernel's projection when
  rejoin calls are served, its own tower estimate otherwise.
- **Ledger page** (`/sessions/{year}/{round}/calls?upto=`): held to the
  lap watched, sharing the story page's remembered lap, never showing
  the race's length; counted, waiting and not counted, each with its
  reason; how a call is scored. With no kind cleared it says so and
  shows the 2025 record. Kept out of search.
- **2025 record:** `$lib/calls/record.ts`, checked against both reports
  by `record.test.ts`; shown with a "not cleared" line for a kind that
  has not passed.
- **Checks:** `calls.spec.ts` in `just verify-phase3`, against a third
  server pair with `F1_API_CALL_KINDS=undercut,rejoin`: the gate (the
  default pair shows no calls); the desk at the flag; no call card ahead
  of the playback clock, sampled through a delayed replay; the ledger at
  lap 16 (3 counted, 3 waiting, 7 not counted, none past lap 16, no race
  length); the phone tab at 390 px. Twelve new visual baselines (desk
  with calls and the ledger, every width, both themes). Making them
  found that the desk's baseline could be taken before the replay had
  finished (a lull of 2 s); it now waits for the stream's end, and the
  desk baselines approved earlier on 2026-10-16 were retaken.

*Waiting on you:* what to do with W5 given the held-out result (a 2026
held-out run on our own live-recorded races, a different rule read from
2024, or shipping a call type with fewer levels). Until then the screens
stay behind the gate.

### W6. Sync 2.0 (signature)

Built as pure TypeScript in `$lib/delay-sync/`, with property tests like
the existing core:

- **Pause and resume with your TV.** The playback clock freezes and resumes
  exactly. A property test proves no leak across pauses.
- **Re-sync at any moment,** not just lights out ("tap when the leader
  crosses the line", anchored on the leader's `lap_completed`).
- **Catch-up mode.** Started late, or watching the recording later? Start
  from lights out, and a live session becomes an in-step replay. This builds
  on the LiveHub backlog, which already serves the race so far to a viewer
  who joins late.
- **Sync quality indicator:** how confident we are that the dashboard
  matches the picture.

**Done when** the property tests pass and a VM rehearsal proves pause,
resume and catch-up on a recorded race played as live.

**Status (2026-10-17): built; property tests and the VM rehearsal pass.**

*Built* (`$lib/delay-sync/`, pure TypeScript):
- **The controller owns the clock** (`clockMs`): wall time minus the
  delay, or frozen where a pause began. Resuming adds exactly the time
  spent paused to the delay, so the clock carries on from where it
  stopped. Every reader goes through it: the release gate, the strip's
  clock, the maps, moments, the qualifying clock.
- **Re-sync on the leader** (`resync.ts`): the leader's crossing of lap N
  is the first `lap_completed` for lap N, by the stream's own timestamps.
  A tap picks the crossing nearest the current delay, so a delay off by
  less than half a lap picks the right one.
- **Catch-up** (`catchUpFrom`): the lights out tap with no slider cap. The
  page has already shown the race to the old clock, which cannot be taken
  back, so it is rebuilt from the start on the new one. Live only, and
  only from the race's own start status.
- **Carrying a sync over** (`carry`): a reconnect, a new replay speed or a
  restart keeps the delay, how it was set, and a pause in progress.
  Before, each rebuilt the controller from the delay alone.
- **Sync quality** (`quality.ts`): paused; not synced; in sync (a tap in
  the last 20 minutes); check (set by hand, or a tap that has aged); feed
  behind your picture (live, nothing delivered for 20 s of the clock; a
  red flag goes quiet too, and the wording says so).
- **The server:** `/stream/{key}?mode=live&history=<s>` sends gap history
  in full that far back. A late joiner otherwise gets gaps older than 5
  minutes reduced to each driver's latest, which is exact for the
  2-minute slider but left a catch-up, or a phone back from a long pause,
  with no intervals for the early laps. The desk asks for how far behind
  live its clock really runs.
- **The strip:** Pause/Resume stays on a phone's folded line;
  "Leader crossed the line" (races and sprints); "Watching from the
  start" (live); the quality label, with its reason as the shield's
  tooltip on a wide screen and under the controls on a phone. On a wide
  screen the strip now wraps to two lines; on a phone Pause is the icon
  alone, so the folded strip stays one line at 390 px.

*Checks:*
- **Property test** (`sync2.test.ts`): 200 seeded schedules of pauses,
  resumes, slider moves and leader taps against an independent model of
  the viewer's TV: nothing released ahead of it, and a paused clock never
  moves on a plain tick. A clock that ignores the pause fails it.
- **Browser** (`sync.spec.ts`, in `just verify-phase3`): pause on a replay
  freezes the lap, the released log and the clock, and resume carries on;
  a leader tap re-syncs; on a phone pause stays on the folded strip.
- **Server:** a viewer asking for long history gets every gap.

*Rehearsal (2026-10-17),* the recorded 2026 Singapore race played as live
at 4x on the VM (`deploy/rehearse_live.py` into the real `f1-pipeline
live`, a scratch `pitwall_rehearsal` database, a second API container),
driven through an SSH tunnel by `apps/web/rehearsal/sync-w6.mjs`. All
eleven checks passed:
- **Pause:** caught up to live, then 30 s paused with the lap, the clock
  and the released log frozen and nothing past the pause released; resume
  grew the delay by the pause (30.9 s) and the race carried on, never
  ahead of the clock.
- **Catch-up:** a viewer at lap 13 pressed "Watching from the start": the
  page rebuilt from lights out 5 min 11 s behind live, "In sync", and by
  lap 4 of the catch-up showed intervals for 21 cars, never ahead of the
  clock. (311 s is only just past the 5-minute window: the full-history
  path is proven by the server test, the rehearsal proves the wiring.)

The first run found two things, both fixed: the stand-in replayed the
feed's own `Utc` fields unchanged, so lights out sat days before every
other event (it now moves them onto the replay's clock); and catch-up fell
back to the stream's first event, which on a live stream can be an hour
of pre-race messages earlier (it now needs the race's own start).

*Replay speed (2026-10-17, your request):* replays start at 1x, and
picking 4x or 16x carries the race on from where it is instead of
restarting it. The browser sends `{"type": "speed", "speed": N}` over the
open stream; the server's `ReplayPace` re-anchors the replay at the moment
of the race reached and paces on at the new rate (race events, car
positions and story cards alike), waking a wait in progress so the change
is immediate. Timestamps never go backwards, so Delay Sync and a pause are
unaffected. Restart still restarts. Checked by `test_replay_pace.py`
(1x to 16x mid-race on the fixture: in order, no restart, each rate held)
and `sync.spec.ts` (one connection, the lap and the released log only
grow).

*Runbook:* upload `python/` and `deploy/rehearse_live.py` to
`/root/rehearsal` on the VM; create `pitwall_rehearsal` in `pitwall-db-1`;
run the api image twice with `/root/rehearsal/python` mounted over
`/app/python` and `F1_STORE_DSN` on the scratch database: once as
`f1-api` on `127.0.0.1:18000` with `F1_API_WHY_FIXTURE=1`, once as
`python /app/deploy/rehearse_live.py --recording
/data/recordings/<folder> --speed 4` with the data volume read-only. A
recording is named after the session the worker connected in and holds
the ones after it (the Singapore race is in
`2026_Singapore_Grand_Prix_Qualifying`). Tunnel 18000, run `vite dev`
against it on 4340, run the driver. Remove the containers, the database
and `/root/rehearsal` afterwards.

### W7. Phone glance mode and installable app

- **Bottom tabs:** Race · Battle · Map · Strategy · Story. The default view
  is the race in three seconds: the battle that matters, the next calls and
  the latest moment.
- **Keep screen awake** (Wake Lock API), with a toggle.
- **Installable app:** extend the existing manifest with an offline shell.
- **Tablets:** a landscape layout.

**Done when:**
- everything is reachable one-handed at 390×844;
- tap targets are at least 44 px;
- largest contentful paint is under 2.5 s on a mid-range phone profile;
- it passes W0.

Status (2026-10-14): done and deployed. Every "done when" is met.
- **Tabs:** below 900 px the race desk shows one view at a time:
  - **Race:** the glance (`$lib/desk/PhoneGlance.svelte`, helpers in
    `glance.ts`): the focused battle with its gap trend, what could
    happen next, the top five (a tap makes a car the battle) and the
    latest explained moment;
  - **Battle:** the undercut watch;
  - **Map;**
  - **Strategy:** the full tower, the analytics and the weather;
  - **Story:** why it happened, plus radio and race control.

  The bar is fixed at the bottom of the screen. Qualifying keeps its
  single board.
- **Delay strip:** on a phone it folds to one line: the shield, the flag
  and Adjust. Adjust opens the delay, tap-to-sync, speed and the wake
  toggle.
- **Glance layout:** every block keeps its size from the first paint.
  Filling in as the race was released, it measured 0.25 layout shift.
- **Keep screen on:** `$lib/wake-lock.ts` takes the lock again whenever
  the page comes back, and the choice is remembered on the device. It is
  hidden where the browser has no Wake Lock API.
- **Installable:** `src/service-worker.ts` keeps the app's own code and
  static files, and serves `static/offline.html` for a page asked for
  offline. It never stores race data or API responses: a cached answer
  could hold more of a race than the viewer has reached. The manifest
  gained an `id` and shortcuts to Live, Sessions and Standings.
- **Tablets:**
  - landscape (1024 and 1180 px wide) uses the desk's own grid;
  - portrait (768 to 899 px) gets the tabs with a two-column glance.
- **Checks:**
  - `e2e-real` asserts the glance, its five rows, 44 px tabs, and that
    the tower sits behind Strategy on every phone run;
  - the visual baselines for the race desk were re-approved after review
    (allowance raised to 500 pixels: glyph noise once reached 384).
- **Load time** (largest contentful paint on Lighthouse's mobile profile:
  4x CPU, Slow 4G, 150 ms round trip):
  - Before: replay desk 3.0 to 3.7 s; `/live` 2.8 s. Under load, 4.3 to
    6.4 s.
  - After, measured 2026-10-14:
    - **On the exact profile** (a local production build, so the 150 ms
      is the whole round trip): replay desk 2.11 s median, `/live` 1.98 s.
    - **Against production** from a connection worse than the profile
      (380 ms round trip), five runs back to back: desk 2.33 to 2.49 s,
      `/live` 2.24 to 2.26 s.
  - What changed:
    - **First data in the page:** the replay page and `/live` get their
      first data from the web server inside the HTML (`+page.server.ts`),
      not from the API after the JavaScript runs.
    - **Stale while refreshing:** the web server's copies of the session
      list and the next race are served at once even when expired, and
      refreshed behind them (`$lib/server/sessions.ts`, `schedule.ts`), so
      HTML never waits on a busy API.
    - **Compression:** Caddy compresses responses (`encode zstd gzip`).
      The session catalog went from 58 KB to 2.5 KB.
    - **Phone panels:** a tab's panels are mounted the first time it is
      opened, not all of them up front behind CSS.
    - **Lazy desk on `/live`:** an idle `/live` no longer downloads the
      race desk's code (107 KB).
    - **API cache:** `/laps` and `/circuit` are computed once per finished
      session per process (`f1_api.response_cache`); a running session's
      are reused for 15 s. Requests arriving during the computation wait
      for that one computation. They had taken 15 to 18 s on every
      replay open, starving every other request.

### W8. Archive depth

- Backfill every 2026 round to date, plus qualifying and sprints for 2025
  and 2026, so the archive matches competitors' (2026 currently lists 3
  races).
- Archive cards with track thumbnails and no results on them.

**Done when** `/sessions` lists every completed 2025 and 2026 session and
each one opens and passes W0's smoke check.

Status (2026-10-12): the data is done. Production lists all 120 sessions
of 2025 and every 2026 round through Singapore (round 17), qualifying and
sprints included; the 2025 standings rebuilt from them match the official
championship exactly. Each backfilled session also carries its venue
(`f1-backfill --locations-only`). The VM's retry loop is gone: running a
season that still has rounds to come always "fails" on those rounds, which
had stopped 2025 from ever retrying. Archive cards (2026-10-12): each
shows its circuit's outline, fitted on the server by circuit name (all 26
circuits match), and no result: the "Winner" line is gone (the API never
filled it, but the card would have shown it), and session types read
"Practice 1" rather than `practice_1`. `e2e-real/archive.spec.ts` checks
both at 390 and 1440 px. Smoke check (2026-10-12): `deploy/smoke_sessions.py`
opens every session the archive lists against a running API (production by
default): its detail, laps, circuit and (races and sprints) story, and its
replay stream until the roster and a lap arrive. Over all 85 sessions of
2026, 83 passed. The two failures:
- **Slow story:** stories took 14 to 137 s, every request. A finished
  race's story is now built once per process (`STORY_CACHE_SIZE` in
  `f1_api.routes.why`). Measured on the VM: detection 9 s and evidence 5 s
  for a 20-moment race, about 4x this machine.
- **Late roster (open):** the 2026 Spanish GP (round 14) was taken in live
  by a worker that joined after the start. Its store copy has every lap,
  but its first driver list comes at lap 20, so a replay shows numbers
  instead of names until then. The backfill holds the whole race, so
  serving it from there (as for qualifying without Q parts) is the fix to
  decide.

Not run on 2024 and 2025, and the Spanish GP roster is not being fixed:
past sessions are test material, and live comes first (your call,
2026-10-12).

### W9. SEO

- **S1. Search Console** (you, about 15 minutes). **Done by you** (confirmed
  2026-10-12):
  1. Add a **Domain** property for `fastpitstop.online` and verify it with
     the DNS TXT record Google gives you.
  2. Submit `https://fastpitstop.online/sitemap.xml`.
  3. Request indexing for `/`, `/sessions`, `/about` and `/live`.
  4. In Bing Webmaster Tools, use **Import from Google Search Console**.
     That covers Bing, DuckDuckGo and ChatGPT search.
- **S2. Circuit pages built on data only we have,** for example
  `/circuits/marina-bay`: pit-stop time loss (labelled calibrated or
  provisional), DRS zones, corners, outline, typical degradation by
  compound and braking points. These are server-rendered and in the
  sitemap. Search target: "Singapore GP pit stop time loss".
  *Built 2026-10-11:* `/circuits` and `/circuits/{slug}` (`GET /circuits`,
  `GET /circuits/{id}`), server-rendered with the map drawn on the server,
  in the sitemap with `SportsActivityLocation` data. Tyre wear per compound
  is measured by `f1_kernel/tools/build_circuit_wear.py` (dry races,
  `good` fits only; 3-7 fits shown thin, under 3 as an outline). Since
  2026-10-12 it rests on every race of 2024 and 2025 (no backfill holds
  2023): 2025 measured on the VM with `--fits-out`, merged here with
  `--fits-in`. 53 compound figures rest on 8 or more stints.
  A provisional pit loss is labelled "not measured" and never ranked.
  Braking points are not on the page yet: there is no per-circuit
  aggregate of them. Checked by `e2e/circuits.spec.ts` in `verify-phase3`.
- **S3. Grand Prix pages,** for example `/races/2026/singapore`: the
  schedule in the viewer's time zone, a countdown, the live link, then the
  replay and analysis after the race. Every race week brings a burst of
  "<GP> schedule / start time" searches.
  *Built 2026-10-11:* `/races/{year}/{slug}` (slug from the event name:
  `singapore`, `united-states`, `sao-paulo`), server-rendered from
  `GET /schedule`: the countdown, each session at the track and in the
  viewer's time, what a stop costs at the circuit, and how the previous
  race there was run (stop counts and tyre plans, no results). "Add to
  calendar" is a `calendar.ics` beside the page (UTC times, nominal
  session lengths). `SportsEvent` data with every session as a sub-event;
  this season's and last season's pages are in the sitemap. The home
  page's next race links to it. Checked by `e2e/races.spec.ts` in
  `verify-phase3`.
- **Search fixes (2026-10-17):** searching the name found the site on
  Google and Bing, but Bing showed no logo and no description, and the
  site did not appear for "F1 dashboard". Checked on production:
  - **No logo on Bing:** `/favicon.ico` answered 404 (Bing asks for it by
    name) and the only icon was an SVG. Now a real `favicon.ico` (16, 32,
    48 px) and 48, 96 and 192 px PNGs declared with their sizes, all
    rendered from `static/favicon.svg` by `scripts/build-icons.mjs`.
  - **No logo in the structured data:** the home page now carries an
    `Organization` with its 512 px logo as the `WebSite`'s publisher.
    `PROFILES` in `$lib/seo` lists the site's public profiles for
    `sameAs`, empty until one exists.
  - **The exact phrase:** "F1 dashboard" now leads the title ("FastPitStop
    — F1 Dashboard: Live Timing, Tyres & Strategy"), the H1 and the first
    paragraph of the home page, and its description.
  - **Bing hears of new pages at once:** IndexNow on every deploy and every
    6 hours (DEPLOY.md, "IndexNow").

  Ranking for a broad phrase like "F1 dashboard" rests mostly on what the
  code cannot do: links and mentions from F1 and tech sites (S8), and
  time. Search Console's Performance report, filtered to queries
  containing "dashboard", says which: impressions at positions 30 to 80
  mean relevant but lacking authority (links help); none at all means not
  yet considered for the phrase.
- **Analytics (2026-10-12):** Google Analytics 4 (`G-BVQJWG5QPX`) in
  `app.html`, so every page carries it, loaded only on `fastpitstop.online`
  itself so local runs and the browser tests send nothing. In-app page
  changes are counted by GA4's enhanced measurement (checked on production:
  the home page and a click through to /circuits both arrive as page
  views). No consent banner yet; that is your call.
- **S4. Guides:** "How to watch F1 without spoilers", "What is an
  undercut" (with a live example from our data), "Pit stop time loss at
  every circuit".
- **S5. Structured data:** `SportsEvent` with start dates for upcoming
  races (rich results), plus `BreadcrumbList`.
- **S6. A share image per Grand Prix** (track outline, name, date),
  generated at build time.
  *Built 2026-10-15:*
  - Every Grand Prix page shares its own 1200x630 card at
    `/races/{year}/{slug}/og.png`: the circuit drawn from its outline,
    the round, name, venue and dates, and the unofficial mark. Its
    `og:image` and `twitter:image` point at it; other pages keep the site
    image.
  - The card is built from the calendar and the circuit file only, so it
    can hold no result (`$lib/og/card.ts`, unit-tested, S7 wording
    included).
  - One departure from "at build time": the calendar and outlines come
    from the API at runtime and the image build has no API. So a card is
    rendered on first request (resvg, about 0.1 s) and cached per process
    and for a day by clients, the same way `calendar.ics` is served.
  - It is drawn with the site's own faces, vendored as TTF with their SIL
    OFL licences (`$lib/server/og/fonts`), so it renders the same on any
    machine.
  - Checked by `e2e/races.spec.ts`: the page links its card, which is a
    1200x630 PNG.
- **S7. Spoiler rule as a test.** No title, description or server-rendered
  text may contain a session's result.
- **S8. Launch, after M3:** a "Show HN" post on the engineering story,
  r/F1Technical, r/formula1 within its rules, and GitHub topics.

**Done when:**
- the site is verified in Search Console and Bing;
- every circuit and Grand Prix page is indexed;
- PageSpeed Insights scores 90+ on mobile for home, circuit and Grand Prix
  pages;
- monthly impressions and clicks are tracked from Search Console.

---

## Milestones

Each milestone ships between race weekends and passes W0 before deploy.

| Milestone | Contents | Exit |
| --- | --- | --- |
| **M0: now** (done) | No deploys this weekend. You: S1 (Search Console and Bing). | Site verified, sitemap submitted |
| **M1: Trust** | W0 release gate; W1 Story 2.0; W2 mockups (D6 steps 1–2) | Story meets its eval targets; mockup direction chosen |
| **M2: Never empty, never thin** | W3 weekend hub; W4 completeness; W8 archive; W2 design system v2 components; S2 circuit pages; S3 Grand Prix pages | No empty states; tower checked against official timing; circuit and Grand Prix pages indexed |
| **M3: Feel** (done) | W2 screen redesigns and motion; W7 phone glance mode and app; S6 share images | Screens match mockups and pass W0 at all sizes and themes |
| **M4: Signature** | W5 strategy radar and scorecard; W6 Sync 2.0 | Backtests committed; sync rehearsal passed |
| **M5: Reach** | S4 guides; S8 launch | Launch posts live; Search Console trend tracked |

### Status (2026-10-12)

- **M0:** done.
- **M1:** done, all of W0 included. The last part, visual baselines,
  was built 2026-10-12:
  - **What:** `just verify-visual` covers ten screens at 390, 768 and
    1440 px in both themes (60 screenshots). They are the production build
    over the checked-in fixture, with a fixed-date calendar, the browser's
    clock frozen and the time zone set to UTC. The race desk is taken at
    the fixture race's last lap, its map and clock strip masked.
  - **Approving:** a difference fails it until `just visual-approve`.
  - **Sensitivity, measured:** identical runs differ by at most 98
    pixels, all inside display-font glyphs, so 300 are allowed. 2 px
    more padding on the archive's cards moved about 5,500 pixels on every
    screen, and all six failed.
  - **Release gate:** part of it (DEPLOY.md).
  
  The post-session check was finished 2026-10-12 (see W0). The one live
  gap its first runs found is fixed. In the Singapore sprint the feed
  cleared `TyresNotChanged` during safety-car drive-throughs and set it
  again seconds later, so the tower counted stops Leclerc and #5 never
  made. A stop is now counted once the new set has done a lap with the
  flag still clear: it shows one lap later, and the tyre itself switches
  at once.

  Replayed live, every session of the Singapore weekend now matches F1's
  archive in the check: race, sprint, qualifying, sprint qualifying and
  practice.
- **M2:** every content item is built, deployed and checked:
  - W3 weekend hub;
  - W4 completeness: the tower against official timing, sprints,
    qualifying, standings, stops, race control on every layout;
  - W8 archive;
  - W2 components;
  - S2 circuit pages;
  - S3 Grand Prix pages.
  
  Mini-sectors were a stretch, to decide after M2. Of the exit, "no empty
  states" and "tower checked against official timing" are met. "Circuit and
  Grand Prix pages indexed" is Google's to report, in Search Console's
  Pages report.
- **M3:**
  - W7 is done (see W7's status).
  - W2's screen redesigns: done 2026-10-17 (see W2).
  - S6 share images: built 2026-10-15 (see S6).
- **M4:** W5 built 2026-10-16 (see W5), all five steps; neither call
  type passed the 2025 held-out check, so its screens stay behind the
  gate. On hold by your decision (2026-10-17). W6 built 2026-10-17; its
  property tests and VM rehearsal pass (see W6).
- **M5:** not started.

## Parked (needs a decision)

- **Live team radio with transcripts, released through Delay Sync.** Phase 4
  says transcription is offline/batch and never in the live hot path. A
  separate worker transcribing clips during a session, off the hot path,
  would keep the spirit of that rule but changes its letter. Revisit
  after M4.
- **Mini-sectors** (W4 stretch): a Phase 1 parser addition.
- **Paid narration credits:** unnecessary once W1 ships its template
  narrator.

## Out of scope

- F1 TV login, live car telemetry and live DRS state (decided 2026-10-10).
- Phase 6 (vision).
- F1 or team logos, driver headshots, broadcast video.
- User accounts.

## Risks

| Risk | Mitigation |
| --- | --- |
| Free narration models get withdrawn or capped | W1 makes narration template-first; the model is optional |
| F1's unofficial feed changes | The pipeline's unknown-topic counter, plus the W0 post-session data check |
| A big race overloads the single VM | Load-test the shared LiveHub with 1,000 simulated viewers before M3's launch |
| Design work sprawls | Mockups first; only approved screens get built |
| SEO pages leak results | The S7 test fails the build |
