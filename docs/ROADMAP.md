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

- Predictions with published, backtested accuracy (85.7% on high-confidence
  undercut calls, over 2025 races the model never saw) and a confidence
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
  - qualifying and sprint sessions are not in the set yet, because
    `/sessions/{year}/{round}` always opens the race (W4).
- The watchdog is the `watchdog` service (`f1_api.watchdog`), tested
  against a real stalled stream. The post-session check is the existing
  `f1-historical-compare`, documented in DEPLOY.md; automating it is open.

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
- **Visual regression.** Screenshot baselines for every redesigned screen
  at 390, 768 and 1440 px, in both themes. A diff needs explicit approval.
- **Session watchdog.** During live sessions, a synthetic viewer on the VM
  emails an alert if the live stream stalls for more than 30 s.
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

**Not doing:** per-car labels on the map (removed at your request), F1 or
team logos, driver headshots.

**Done when:**
- every D5 screen matches its approved mockup;
- the screens pass W0 in both themes at all three widths;
- WCAG AA contrast is met throughout;
- a side-by-side review against the competitor screenshots is signed off by
  you.

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
- **Still open:** pit count beyond stints, the race control feed on every
  layout, qualifying, sprint, standings, mini-sectors, and the check
  against official timing.

**Done when** every tower field is checked against the official timing for
the W0 sessions, and qualifying shows the right drivers eliminated at each
Q break in a real qualifying replay.

### W5. Strategy radar and scorecard (signature)

Forward-looking calls, released through Delay Sync like everything else:

- *"LEC's undercut window opens in 2 laps."* (Exists today; 85.7% on
  high-confidence calls.)
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

### W8. Archive depth

- Backfill every 2026 round to date, plus qualifying and sprints for 2025
  and 2026, so the archive matches competitors' (2026 currently lists 3
  races).
- Archive cards with track thumbnails and no results on them.

**Done when** `/sessions` lists every completed 2025 and 2026 session and
each one opens and passes W0's smoke check.

### W9. SEO

- **S1. Search Console** (you, about 15 minutes):
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
- **S3. Grand Prix pages,** for example `/races/2026/singapore`: the
  schedule in the viewer's time zone, a countdown, the live link, then the
  replay and analysis after the race. Every race week brings a burst of
  "<GP> schedule / start time" searches.
- **S4. Guides:** "How to watch F1 without spoilers", "What is an
  undercut" (with a live example from our data), "Pit stop time loss at
  every circuit".
- **S5. Structured data:** `SportsEvent` with start dates for upcoming
  races (rich results), plus `BreadcrumbList`.
- **S6. A share image per Grand Prix** (track outline, name, date),
  generated at build time.
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
| **M0: now** | No deploys this weekend. You: S1 (Search Console and Bing). | Site verified, sitemap submitted |
| **M1: Trust** | W0 release gate; W1 Story 2.0; W2 mockups (D6 steps 1–2) | Story meets its eval targets; mockup direction chosen |
| **M2: Never empty, never thin** | W3 weekend hub; W4 completeness; W8 archive; W2 design system v2 components; S2 circuit pages; S3 Grand Prix pages | No empty states; tower checked against official timing; circuit and Grand Prix pages indexed |
| **M3: Feel** | W2 screen redesigns and motion; W7 phone glance mode and app; S6 share images | Screens match mockups and pass W0 at all sizes and themes |
| **M4: Signature** | W5 strategy radar and scorecard; W6 Sync 2.0 | Backtests committed; sync rehearsal passed |
| **M5: Reach** | S4 guides; S8 launch | Launch posts live; Search Console trend tracked |

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
