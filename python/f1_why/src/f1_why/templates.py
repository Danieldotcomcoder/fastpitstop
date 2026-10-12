"""`TemplateNarrationModel`: story sentences built from the evidence bundle's
own numbers, with no model call.

Why not the model: the free narration models wrote the same "fresher tyres"
sentence for every overtake, ran out of their 50-a-day allowance mid-race
and then showed "no clear cause" for everything. A template states what the
data says, in the same words every time it applies, at no cost, in no
time, and it cannot invent anything: every number in a sentence is read
from the bundle, and every item it uses is cited, so the grounding check in
`f1_why.narration.explain` applies to it exactly as it does to a model.

Each kind has one writer. A writer states the moment itself from the
anomaly's own numbers (always available, cited as `anomaly`), then adds at
most two causes, each only when an evidence item supports it, citing that
item: tyres from `lap_context`, a closing gap from `gap_trend`, an incident
from `race_control`. A moment with no supported cause is still described,
never explained by a guess.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from f1_domain import DriverRef
from f1_why.anomaly import Anomaly, AnomalyKind, DetailValue
from f1_why.evidence import EvidenceBundle, EvidenceItem, EvidenceKind
from f1_why.narration import ModelNarration

_ANOMALY = "anomaly"
_PARTICLES = frozenset({"de", "van", "von", "di", "da", "le", "la", "du", "del", "der", "dos"})
_COMPOUND_ORDER = {"soft": 0, "medium": 1, "hard": 2}
_NAMED_CAR = re.compile(r"\b(\d{1,2}) \([A-Z]{3}\)")
_INCIDENT = re.compile(r"(?:(TURN \d+) )?INCIDENT INVOLVING CARS? ", re.IGNORECASE)
_STOPPED = re.compile(r"CAR (\d{1,2}) \([A-Z]{3}\) STOPPED", re.IGNORECASE)
_STATUS_LABEL = {"sc": "Safety car", "vsc": "Virtual safety car", "red": "Red flag"}
_NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five"}


def surname(ref: DriverRef) -> str:
    """How a sentence names a driver. The live feed writes the surname in
    capitals ("Max VERSTAPPEN"); historical data does not ("Max
    Verstappen"), so then it is the last word, with any particle before it
    ("Nyck De Vries")."""
    full = (ref.full_name or "").split()
    capitals = [word for word in full if len(word) > 1 and word.isupper()]
    if capitals:
        return " ".join(word.capitalize() for word in capitals)
    if full:
        name = [full[-1]]
        for word in reversed(full[:-1]):
            if word.lower() not in _PARTICLES:
                break
            name.insert(0, word)
        return " ".join(name)
    return ref.code or f"car {ref.number}"


def _seconds(value: float) -> str:
    return f"{value:.1f} s"


def _laps_word(count: int) -> str:
    return _NUMBER_WORDS.get(count, str(count))


@dataclass
class _Writer:
    """One bundle being written up: names, typed access to numbers, and the
    ids of the items used so far."""

    bundle: EvidenceBundle
    cited: list[str] = field(default_factory=lambda: [_ANOMALY])

    @property
    def anomaly(self) -> Anomaly:
        return self.bundle.anomaly

    def name(self, number: DetailValue) -> str:
        if not isinstance(number, str) or not number:
            return "a car"
        for ref in self.bundle.roster:
            if ref.number == number:
                return surname(ref)
        return f"car {number}"

    def names(self, numbers: Sequence[str]) -> str:
        named = [self.name(number.strip()) for number in numbers if number.strip()]
        if len(named) <= 1:
            return "".join(named)
        return ", ".join(named[:-1]) + " and " + named[-1]

    def int_(self, key: str) -> int | None:
        value = self.anomaly.summary.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def float_(self, key: str) -> float | None:
        value = self.anomaly.summary.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return float(value)

    def str_(self, key: str) -> str | None:
        value = self.anomaly.summary.get(key)
        return value if isinstance(value, str) and value else None

    def item(self, evidence_id: str) -> EvidenceItem | None:
        return next((i for i in self.bundle.items if i.evidence_id == evidence_id), None)

    def items(self, kind: EvidenceKind) -> list[EvidenceItem]:
        return [i for i in self.bundle.items if i.kind is kind]

    def cite(self, item: EvidenceItem) -> None:
        if item.evidence_id not in self.cited:
            self.cited.append(item.evidence_id)


def _join(sentence: str, causes: Sequence[str]) -> str:
    """`sentence` with up to two cause clauses, ending in a full stop."""
    kept = [cause for cause in causes if cause][:2]
    if not kept:
        return sentence + "."
    return sentence + ", " + " and ".join(kept) + "."


def _clauses(parts: Sequence[str]) -> str:
    """Clauses joined by semicolons into one sentence, capitalized."""
    text = "; ".join(parts)
    return text[:1].upper() + text[1:] + "."


# ---------------------------------------------------------------------------
# causes, each from one evidence item
# ---------------------------------------------------------------------------


def _tyre_cause(w: _Writer, driver: str, rival: str) -> str | None:
    """The passing car's tyre advantage, when its lap context shows one, as
    a clause that stands on its own."""
    mine, theirs = w.item(f"lap_context:{driver}"), w.item(f"lap_context:{rival}")
    if mine is None or theirs is None:
        return None
    my_compound, their_compound = mine.data.get("compound"), theirs.data.get("compound")
    my_age, their_age = mine.data.get("tyre_age"), theirs.data.get("tyre_age")
    if isinstance(my_compound, str) and isinstance(their_compound, str):
        mine_rank = _COMPOUND_ORDER.get(my_compound.lower())
        theirs_rank = _COMPOUND_ORDER.get(their_compound.lower())
        if my_compound.lower() != their_compound.lower() and (
            mine_rank is None or theirs_rank is None or mine_rank < theirs_rank
        ):
            w.cite(mine)
            w.cite(theirs)
            return (
                f"{w.name(driver)} was on {my_compound.lower()}s, "
                f"{w.name(rival)} on {their_compound.lower()}s"
            )
    if isinstance(my_age, int) and isinstance(their_age, int) and their_age - my_age >= 5:
        w.cite(mine)
        w.cite(theirs)
        return f"{w.name(driver)}'s tyres were {their_age - my_age} laps fresher"
    return None


def _gap_cause(w: _Writer) -> str | None:
    """A pass set up over several laps: the gap at the line came down."""
    trend = next(iter(w.items(EvidenceKind.GAP_TREND)), None)
    if trend is None:
        return None
    first, last = trend.data.get("first_gap_s"), trend.data.get("last_gap_s")
    start, end = trend.data.get("from_lap"), trend.data.get("to_lap")
    if not isinstance(first, int | float) or not isinstance(last, int | float):
        return None
    if not (isinstance(start, int) and isinstance(end, int)) or end <= start:
        return None
    if first - last < 0.5 or first <= 0 or last < 0:
        return None
    w.cite(trend)
    laps = _laps_word(end - start + 1)
    return f"the gap had come down from {_seconds(first)} to {_seconds(last)} over {laps} laps"


def _incident_cars(item: EvidenceItem) -> tuple[str | None, list[str]] | None:
    """A race-control incident message's turn and the cars it names."""
    text = str(item.data.get("message") or "")
    match = _INCIDENT.search(text)
    if match is None:
        return None
    cars = _NAMED_CAR.findall(text[match.end() :].split(" - ")[0])
    if not cars:
        return None
    turn = match.group(1)
    return (turn.lower() if turn else None), list(dict.fromkeys(cars))


def _incident_cause(w: _Writer, about: Sequence[str]) -> str | None:
    """The first race-control incident naming one of `about`: "after an
    incident at turn 3 with Magnussen", never naming `about`'s own driver
    back at them."""
    for item in w.items(EvidenceKind.RACE_CONTROL):
        found = _incident_cars(item)
        if found is None:
            continue
        turn, cars = found
        if not set(about).intersection(cars):
            continue
        w.cite(item)
        where = f" at {turn}" if turn else ""
        others = [car for car in cars if car not in about]
        with_whom = f" with {w.names(others)}" if others else ""
        return f"after an incident{where}{with_whom}"
    for item in w.items(EvidenceKind.RACE_CONTROL):
        stopped = _STOPPED.search(str(item.data.get("message") or ""))
        if stopped is None:
            continue
        car = stopped.group(1)
        if car not in about:
            continue
        w.cite(item)
        if list(about) == [car]:
            return "after stopping on track"
        return f"after {w.name(car)} stopped on track"
    return None


def _pair_incident(w: _Writer, driver: str, rival: str) -> str | None:
    """Race control noting an incident between exactly these two cars."""
    for item in w.items(EvidenceKind.RACE_CONTROL):
        found = _incident_cars(item)
        if found is None:
            continue
        turn, cars = found
        if not {driver, rival} <= set(cars):
            continue
        w.cite(item)
        where = f" at {turn}" if turn else ""
        return f"race control noted an incident{where} between them"
    return None


def _incident_note(w: _Writer) -> str | None:
    """A neutral sentence for a track-wide moment: what race control noted,
    not a claim that it caused anything."""
    for item in w.items(EvidenceKind.RACE_CONTROL):
        found = _incident_cars(item)
        if found is None:
            continue
        turn, cars = found
        w.cite(item)
        where = f" at {turn}" if turn else ""
        return f"Race control noted an incident{where} involving {w.names(cars)}."
    return None


_TIME_PENALTY = re.compile(r"(\d+) SECOND TIME PENALTY FOR CAR (\d{1,2}) ", re.IGNORECASE)


def _penalty_served(w: _Writer, driver: str) -> str | None:
    """A time penalty race control gave `driver`, served at a pit stop:
    "5-second penalty"."""
    for item in w.items(EvidenceKind.RACE_CONTROL):
        match = _TIME_PENALTY.search(str(item.data.get("message") or ""))
        if match is None or match.group(2) != driver:
            continue
        w.cite(item)
        return f"{match.group(1)}-second penalty"
    return None


def _traffic_cause(w: _Writer, driver: str) -> str | None:
    context = w.item(f"lap_context:{driver}")
    if context is None:
        return None
    gap = context.data.get("gap_ahead_s")
    if isinstance(gap, int | float) and 0.1 <= gap < 1.0:
        w.cite(context)
        return f"while stuck {_seconds(gap)} behind the car ahead"
    return None


# ---------------------------------------------------------------------------
# one writer per kind
# ---------------------------------------------------------------------------


def _position_change(w: _Writer) -> str:
    a = w.anomaly
    driver = a.drivers[0]
    if w.anomaly.summary.get("gained") is False:
        passers = (w.str_("passed_by") or "").split(",")
        sentence = (
            f"{w.name(driver)} dropped from P{w.int_('from_position')} to "
            f"P{w.int_('to_position')} on lap {a.lap_number}, passed by {w.names(passers)}"
        )
        lap_loss = w.float_("lap_loss_s")
        losing = f"losing {_seconds(lap_loss)} on the lap" if lap_loss is not None else None
        return _join(sentence, [c for c in (losing, _incident_cause(w, [driver])) if c])

    rival = a.drivers[1] if len(a.drivers) > 1 else None
    passed = (w.str_("passed") or "").split(",")
    victims = w.names(passed) if len(passed) > 1 else w.name(rival)
    sentence = (
        f"{w.name(driver)} passed {victims} for P{w.int_('to_position')} on lap {a.lap_number}."
    )
    causes: list[str] = []
    if rival is not None:
        contact = _pair_incident(w, driver, rival)
        if contact:
            causes.append(contact)
    rival_loss = w.float_("rival_loss_s")
    if rival is not None and rival_loss is not None:
        sector = w.int_("rival_loss_sector")
        where = f" in sector {sector}" if sector else ""
        causes.append(f"{w.name(rival)} lost {_seconds(rival_loss)}{where} that lap")
    if rival is not None:
        tyres = _tyre_cause(w, driver, rival)
        if tyres:
            causes.append(tyres)
    gap = _gap_cause(w)
    if gap:
        causes.append(gap)
    if not causes:
        return sentence
    return sentence + " " + _clauses(causes[:2])


def _undercut(w: _Writer) -> str:
    attacker, defender = w.anomaly.drivers[0], w.anomaly.drivers[1]
    a_stop, d_stop = w.int_("attacker_stop_lap"), w.int_("defender_stop_lap")
    margin = w.float_("actual_margin_s")
    changed = w.anomaly.summary.get("order_changed") is True
    overcut = w.str_("undercut_kind") == "overcut"
    a_name, d_name = w.name(attacker), w.name(defender)
    lead = abs(margin) if margin is not None else None

    if a_stop is not None and d_stop is not None and d_stop != a_stop:
        apart = abs(d_stop - a_stop)
        laps = "a lap" if apart == 1 else f"{apart} laps"
        timing = (
            f"pitted on lap {a_stop}, {laps} {'later' if overcut else 'earlier'} than {d_name},"
        )
    elif a_stop is not None:
        timing = f"pitted on lap {a_stop}"
    else:
        timing = "pitted"
    gap = f" {_seconds(lead)}" if lead is not None else ""
    if changed:
        move = "overcut" if overcut else "undercut"
        sentence = f"{a_name} {move} {d_name}: {a_name} {timing} and came out{gap} ahead"
    else:
        # The data shows when each car stopped, not what either team meant
        # by it, so a stop that did not work is described, not called an
        # attempt.
        sentence = f"{a_name} {timing} but came out{gap} behind once both had stopped"

    confidence = w.str_("confidence")
    decision = w.int_("decision_lap")
    predicted = w.anomaly.summary.get("predicted_ahead")
    call = ""
    if confidence and decision and isinstance(predicted, bool):
        if predicted == changed:
            call = f" Our model called it on lap {decision} ({confidence} confidence)."
        elif predicted:
            call = f" Our model had expected {a_name} to come out ahead ({confidence} confidence)."
        else:
            call = f" Our model had not expected it to work ({confidence} confidence)."
    return sentence + "." + call


def _neutralization(w: _Writer) -> str:
    status = w.str_("status") or "sc"
    first, last = w.int_("start_lap"), w.int_("end_lap")
    label = _STATUS_LABEL.get(status, "Safety car")
    began_as = w.str_("opening_status") or status
    escalated = w.int_("worst_from_lap")
    retired = [d for d in (w.str_("retired") or "").split(",") if d]
    cause = f", after {w.names(retired)} retired" if retired else ""
    if began_as != status and escalated is not None:
        # A safety car that became a red flag is told in that order: the
        # 2024 São Paulo GP's lap-30 safety car was a red flag only from 32.
        opening = (
            f"{_STATUS_LABEL.get(began_as, 'Safety car')} from lap {first}{cause}, then a "
            f"{label.lower()} on lap {escalated}; racing resumed after lap {last}."
        )
    else:
        span = f"on lap {first}" if first == last else f"from lap {first} to lap {last}"
        opening = f"{label} {span}{cause}."
    if not retired:
        note = _incident_note(w)
        opening += f" {note}" if note else ""

    pitted = [d for d in (w.str_("pitted") or "").split(",") if d]
    before, after = w.str_("leader_before"), w.str_("leader_after")
    follow: list[str] = []
    if status != "red":
        named_a_stop = False
        if after and before == after and after in pitted:
            follow.append(f"{w.name(after)} pitted under it and kept the lead")
            named_a_stop = True
        elif after and before and after != before and before in pitted and after not in pitted:
            follow.append(f"{w.name(before)} pitted under it, handing the lead to {w.name(after)}")
            named_a_stop = True
        elif after and before and after != before:
            how = " by pitting under it" if after in pitted else ""
            follow.append(f"{w.name(after)} took the lead from {w.name(before)}{how}")
            named_a_stop = after in pitted
        others = [d for d in pitted if d not in (before, after)]
        also = "also " if named_a_stop else ""
        if others and len(others) <= 3:
            follow.append(f"{w.names(others)} {also}pitted")
        elif others:
            follow.append(f"{len(others)} {'other ' if named_a_stop else ''}cars pitted")
    elif after and before and after != before:
        follow.append(f"{w.name(after)} led after it, ahead of {w.name(before)}")
    gainer, gain = w.str_("best_gainer"), w.int_("best_gain")
    # A lap-1 neutralization measures gains against the grid: that is the
    # start card's story, not this one's.
    if gainer and gain and gain >= 2 and gainer != after and first != 1:
        follow.append(f"{w.name(gainer)} gained {gain} places")
    if not follow:
        return opening
    return opening + " " + _clauses(follow)


def _retirement(w: _Writer) -> str:
    driver = w.anomaly.drivers[0]
    position = w.int_("position")
    last = w.int_("last_completed_lap") or 0
    if last == 0:
        sentence = f"{w.name(driver)} retired on lap 1, from P{position} on the grid"
    elif w.anomaly.summary.get("in_pits") is True:
        sentence = (
            f"{w.name(driver)} retired from P{position} on lap {last + 1}, "
            f"having pitted on lap {last}"
        )
    else:
        sentence = f"{w.name(driver)} retired from P{position} on lap {last + 1}"
    cause = _incident_cause(w, [driver])
    return _join(sentence, [cause] if cause else [])


def _race_start(w: _Writer) -> str:
    leader, pole = w.str_("leader"), w.str_("pole")
    if leader and pole and leader != pole:
        sentence = f"{w.name(leader)} took the lead from pole-sitter {w.name(pole)} on lap 1"
    else:
        sentence = f"{w.name(leader)} kept the lead from pole on lap 1"

    def parse(key: str) -> list[tuple[str, int]]:
        pairs: list[tuple[str, int]] = []
        for part in (w.str_(key) or "").split(","):
            driver, _, places = part.partition(":")
            if driver and places.isdigit():
                pairs.append((driver, int(places)))
        return pairs

    def describe(pairs: list[tuple[str, int]], verb: str) -> str | None:
        if not pairs:
            return None
        head = f"{w.name(pairs[0][0])} {verb} {pairs[0][1]} places"
        rest = [f"{w.name(d)} {n}" for d, n in pairs[1:]]
        return head + (", " + ", ".join(rest) if rest else "")

    moves = [
        m for m in (describe(parse("gainers"), "gained"), describe(parse("losers"), "lost")) if m
    ]
    if not moves:
        return sentence + "."
    return sentence + ". " + _clauses(moves)


def _race_finish(w: _Writer) -> str:
    winner, second, third = w.str_("winner"), w.str_("second"), w.str_("third")
    grid = w.int_("winner_grid")
    margin = w.float_("margin_s")
    start = f" from P{grid} on the grid" if grid and grid > 1 else ""
    sentence = f"{w.name(winner)} took the chequered flag{start}"
    if second:
        gap = f"{_seconds(margin)} ahead of" if margin is not None and margin > 0 else "ahead of"
        sentence += f", {gap} {w.name(second)}"
    if third:
        sentence += f", with {w.name(third)} third"
    return sentence + "."


def _pace_loss(w: _Writer) -> str | None:
    """`None` when nothing in the bundle explains the loss: a lap that was
    slow for no reason the data can show is not a story, and saying so on
    a card is noise."""
    driver = w.anomaly.drivers[0]
    delta = w.float_("delta_s") or w.anomaly.magnitude
    lap = w.anomaly.lap_number
    if w.anomaly.kind is AnomalyKind.OFF_TRACK:
        sentence = (
            f"{w.name(driver)} lost {_seconds(delta)} in sector {w.int_('sector')} on lap {lap}"
        )
    else:
        sentence = f"{w.name(driver)} lost {_seconds(delta)} on lap {lap}"
        sector, sector_delta = w.int_("sector"), w.float_("sector_delta_s")
        if sector and sector_delta is not None and sector_delta >= 0.5 * delta:
            sentence += f", most of it in sector {sector}"
    # Traffic alone does not make a lost lap a story (being held up is
    # ordinary racing): it may accompany a race-control cause, never stand
    # in for one.
    incident = _incident_cause(w, [driver])
    if incident is None:
        return None
    return _join(sentence, [incident, _traffic_cause(w, driver) or ""])


def _deg_cliff(w: _Writer) -> str:
    driver = w.anomaly.drivers[0]
    compound = (w.str_("compound") or "tyre").lower()
    extra = w.float_("extra_time_s") or w.anomaly.magnitude
    age = w.int_("tyre_age_at_end")
    tail = w.int_("tail_laps")
    lap = w.anomaly.lap_number
    aged = f" ({age} laps old)" if age else ""
    laps = f"last {_laps_word(tail)} clean laps" if tail and tail > 1 else "latest clean lap"
    reference = w.int_("reference_laps")
    before = f"the {_laps_word(reference)} before them" if reference else "the laps before"
    sentence = (
        f"{w.name(driver)}'s {compound}s started to fade: the {laps} to lap {lap}{aged} ran "
        f"{_seconds(extra)} a lap slower than {before}"
    )
    if w.str_("fit_reliability") == "thin":
        sentence += " (from only a few laps)"
    return sentence + "."


def _pit_anomaly(w: _Writer) -> str:
    driver = w.anomaly.drivers[0]
    delta = w.float_("delta_s") or 0.0
    observed = w.float_("observed_pit_loss_s")
    lap = w.int_("in_lap") or w.anomaly.lap_number
    norm = " (against an estimated norm)" if w.str_("expected_provenance") != "calibrated" else ""
    if delta > 0:
        cost = f": {_seconds(observed)} lost to the stop" if observed is not None else ""
        penalty = _penalty_served(w, driver)
        if penalty is not None:
            return (
                f"{w.name(driver)} served a {penalty} at the stop on lap {lap}{cost}, "
                f"{_seconds(delta)} more than usual here{norm}."
            )
        return (
            f"Slow stop for {w.name(driver)} on lap {lap}{cost}, "
            f"{_seconds(delta)} more than usual here{norm}."
        )
    saved = _seconds(-delta)
    return f"Quick stop for {w.name(driver)} on lap {lap}: {saved} less than usual here{norm}."


_WRITERS: dict[AnomalyKind, Callable[[_Writer], str | None]] = {
    AnomalyKind.POSITION_CHANGE: _position_change,
    AnomalyKind.UNDERCUT_RESOLVED: _undercut,
    AnomalyKind.NEUTRALIZATION: _neutralization,
    AnomalyKind.RETIREMENT: _retirement,
    AnomalyKind.RACE_START: _race_start,
    AnomalyKind.RACE_FINISH: _race_finish,
    AnomalyKind.PACE_LOSS: _pace_loss,
    AnomalyKind.OFF_TRACK: _pace_loss,
    AnomalyKind.DEG_CLIFF: _deg_cliff,
    AnomalyKind.PIT_ANOMALY: _pit_anomaly,
}


class TemplateNarrationModel:
    """The default narrator: deterministic, free, instant. Satisfies
    `f1_why.narration.BundleNarrationModel`."""

    def narrate(self, bundle: EvidenceBundle) -> ModelNarration:
        writer = _WRITERS.get(bundle.anomaly.kind)
        w = _Writer(bundle)
        text = writer(w) if writer is not None else None
        if text is None:
            return ModelNarration(text="", cited_evidence_ids=(), no_clear_cause=True)
        return ModelNarration(text=text, cited_evidence_ids=tuple(w.cited), no_clear_cause=False)


__all__ = ["TemplateNarrationModel", "surname"]
