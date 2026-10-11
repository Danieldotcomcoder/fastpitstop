"""`select_story`: which detected moments a viewer actually sees.

`detect_anomalies` is generous by design: a race yields a hundred or more
anomalies, most of them midfield wobbles. The story is the handful worth a
card, and it has to read like the race did. Three steps:

1. **Merge**, so one incident is one card. A pace loss and the sector loss
   inside it are the same mistake; a car that lost several places in a
   lap and the lap time it lost doing so are the same incident.
2. **Keep** what passes each kind's own rule (`StoryParams`). The rules
   look at the moment itself and nothing after it, so a live race and its
   replay pick exactly the same moments: there is no "top twelve of the
   whole race", which a live race cannot know until it is over.
3. **Deduplicate** by `story_key`, one card per real-world event. A live
   race re-detects every lap, and a degradation cliff found on lap 30 is
   found again on lap 31 anchored to lap 31; both are the same stint.

Pure and deterministic, like the detector it reads.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from pydantic import BaseModel, ConfigDict, Field

from f1_why.anomaly import Anomaly, AnomalyKind
from f1_why.evidence import anomaly_id


class StoryParams(BaseModel):
    """What makes a moment worth a card. Every default is a judgement, not a
    measurement, tuned against the hand-checked races in
    `tests/eval/` — the same honesty `AnomalyParams` carries."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pass_max_position: int = Field(
        default=6,
        ge=1,
        description="An overtake for this place or better is always a card.",
    )
    pass_extended_max_position: int = Field(
        default=10,
        ge=1,
        description=(
            "Further back, down to this place, an overtake is a card only when "
            "it gained `pass_extended_min_places` or more in one lap."
        ),
    )
    pass_extended_min_places: int = Field(default=2, ge=1)
    undercut_max_position: int = Field(
        default=10,
        ge=1,
        description=(
            "A pit-stop duel that changed the order is a card when the "
            "better-placed car ran this high or better."
        ),
    )
    undercut_max_gap_s: float = Field(
        default=3.0,
        gt=0,
        description=(
            "The cars must have been this close when the call was made to "
            "count as a duel. The kernel pairs any two cars within 30s, which "
            "made cards of cars on unrelated strategies: in the 2025 Spanish "
            "GP, ten of them."
        ),
    )
    undercut_max_stop_offset_laps: int = Field(
        default=6,
        ge=1,
        description=(
            "And their stops this few laps apart: a car that stops 20 laps "
            "before another is on a different strategy, not undercutting it."
        ),
    )
    undercut_upset_max_position: int = Field(
        default=6,
        ge=1,
        description=(
            "A confident call that failed with the order unchanged is a card "
            "only this near the front: the model's misses further back are "
            "its own business, not the race's story."
        ),
    )
    incident_max_position: int = Field(
        default=10,
        ge=1,
        description=(
            "Pace losses, slow stops and tyre cliffs are cards only for a car "
            "running this high or better."
        ),
    )
    pace_loss_min_s: float = Field(default=2.0, gt=0)
    deg_cliff_min_s: float = Field(
        default=1.0,
        gt=0,
        description="Measured seconds a lap the tail ran above the stint's earlier trend.",
    )
    pit_anomaly_min_s: float = Field(
        default=4.0,
        gt=0,
        description="A stop this much slower (or quicker) than usual, near the front, is a card.",
    )
    pit_disaster_min_s: float = Field(
        default=10.0,
        gt=0,
        description="A stop this far off is a card wherever the car was running.",
    )
    max_moments: int = Field(
        default=20,
        ge=1,
        description=(
            "Ceiling on cards per race. Above it the least salient go first; "
            "the start, the finish, neutralizations and retirements are never cut."
        ),
    )

    def with_overrides(self, **overrides: object) -> StoryParams:
        return StoryParams.model_validate({**self.model_dump(), **overrides})


_LANDMARKS: frozenset[AnomalyKind] = frozenset(
    {
        AnomalyKind.RACE_START,
        AnomalyKind.RACE_FINISH,
        AnomalyKind.NEUTRALIZATION,
        AnomalyKind.RETIREMENT,
    }
)


def story_key(anomaly: Anomaly) -> str:
    """One key per real-world event. Kinds whose anchor moves as a live
    race goes on key by what they are about rather than when they were
    noticed."""
    kind = anomaly.kind
    if kind in (AnomalyKind.RACE_START, AnomalyKind.RACE_FINISH):
        return kind.value
    if kind is AnomalyKind.NEUTRALIZATION:
        return f"{kind.value}:{anomaly.summary.get('start_lap')}"
    if kind is AnomalyKind.RETIREMENT:
        return f"{kind.value}:{anomaly.drivers[0]}"
    if kind is AnomalyKind.DEG_CLIFF:
        return f"{kind.value}:{anomaly.drivers[0]}:{anomaly.summary.get('stint_number')}"
    return anomaly_id(anomaly)


def _int(anomaly: Anomaly, key: str) -> int | None:
    value = anomaly.summary.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _float(anomaly: Anomaly, key: str) -> float | None:
    value = anomaly.summary.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def merge_incidents(anomalies: Sequence[Anomaly]) -> list[Anomaly]:
    """One card per incident.

    - A sector loss (`OFF_TRACK`) on a lap that also lost pace overall is
      the same mistake: it is folded into the `PACE_LOSS` as `sector` /
      `sector_delta_s`. Several sector losses on one lap keep the worst.
    - A car that lost several places on track on a lap where it also lost
      pace: the `PACE_LOSS` is folded into the `POSITION_CHANGE` as
      `lap_loss_s`.
    - An overtake whose rival lost pace on the same lap: the loss is why
      the pass happened (the 2024 Belgian GP's Piastri passed Leclerc on
      the lap Leclerc lost 2.6s), so it is folded into the overtake as
      `rival_loss_s` / `rival_loss_sector`.
    - A retirement that a neutralization was called for (the detector lists
      the cars whose race ended as it began) is told by that card; the 2024
      Monaco GP otherwise opened with a red flag and four retirement cards
      for the same crash."""
    told_by_neutralization = {
        driver
        for anomaly in anomalies
        if anomaly.kind is AnomalyKind.NEUTRALIZATION
        for driver in str(anomaly.summary.get("retired") or "").split(",")
        if driver
    }
    anomalies = [
        anomaly
        for anomaly in anomalies
        if not (
            anomaly.kind is AnomalyKind.RETIREMENT and anomaly.drivers[0] in told_by_neutralization
        )
    ]
    off_track: dict[tuple[str, int | None], Anomaly] = {}
    pace_loss: dict[tuple[str, int | None], Anomaly] = {}
    big_losses: dict[tuple[str, int | None], Anomaly] = {}
    rest: list[Anomaly] = []
    for anomaly in anomalies:
        key = (anomaly.drivers[0], anomaly.lap_number) if anomaly.drivers else ("", None)
        if anomaly.kind is AnomalyKind.OFF_TRACK:
            current = off_track.get(key)
            if current is None or anomaly.magnitude > current.magnitude:
                off_track[key] = anomaly
        elif anomaly.kind is AnomalyKind.PACE_LOSS:
            pace_loss[key] = anomaly
        elif anomaly.kind is AnomalyKind.POSITION_CHANGE and anomaly.summary.get("gained") is False:
            big_losses[key] = anomaly
        else:
            rest.append(anomaly)

    for key, loss in list(pace_loss.items()):
        sector = off_track.pop(key, None)
        if sector is not None:
            pace_loss[key] = replace(
                loss,
                summary={
                    **loss.summary,
                    "sector": sector.summary.get("sector"),
                    "sector_delta_s": sector.summary.get("delta_s"),
                },
            )

    merged: list[Anomaly] = []
    explained: set[tuple[str, int | None]] = set()
    for anomaly in rest:
        rival = anomaly.summary.get("rival")
        if anomaly.kind is AnomalyKind.POSITION_CHANGE and isinstance(rival, str):
            loss = pace_loss.get((rival, anomaly.lap_number))
            if loss is not None:
                explained.add((rival, anomaly.lap_number))
                anomaly = replace(
                    anomaly,
                    summary={
                        **anomaly.summary,
                        "rival_loss_s": loss.summary.get("delta_s"),
                        "rival_loss_sector": loss.summary.get("sector"),
                    },
                )
        merged.append(anomaly)

    for key, loss in pace_loss.items():
        drop = big_losses.get(key)
        if drop is not None:
            big_losses[key] = replace(
                drop, summary={**drop.summary, "lap_loss_s": loss.summary.get("delta_s")}
            )
            continue
        if key not in explained:
            merged.append(loss)
    merged.extend(off_track.values())
    merged.extend(big_losses.values())
    return merged


def is_story_moment(anomaly: Anomaly, params: StoryParams) -> bool:
    """Each kind's own rule — see `StoryParams` for the thresholds."""
    kind = anomaly.kind
    if kind in (AnomalyKind.RACE_START, AnomalyKind.RACE_FINISH, AnomalyKind.RETIREMENT):
        return True
    if kind is AnomalyKind.NEUTRALIZATION:
        if anomaly.summary.get("status") != "vsc":
            return True
        return bool(anomaly.summary.get("pitted")) or (_int(anomaly, "laps") or 0) >= 2
    if kind is AnomalyKind.POSITION_CHANGE:
        if anomaly.summary.get("gained") is False:
            start = _int(anomaly, "from_position")
            return start is not None and start <= params.pass_extended_max_position
        place = _int(anomaly, "to_position")
        if place is None:
            return False
        if place <= params.pass_max_position:
            return True
        return (
            place <= params.pass_extended_max_position
            and anomaly.magnitude >= params.pass_extended_min_places
        )
    if kind is AnomalyKind.UNDERCUT_RESOLVED:
        positions = [
            p for p in (_int(anomaly, "attacker_position"), _int(anomaly, "defender_position")) if p
        ]
        if not positions:
            return False
        gap = _float(anomaly, "gap_at_decision_s")
        if gap is None or abs(gap) > params.undercut_max_gap_s:
            return False
        stops = (_int(anomaly, "attacker_stop_lap"), _int(anomaly, "defender_stop_lap"))
        if stops[0] is None or stops[1] is None:
            return False
        if abs(stops[0] - stops[1]) > params.undercut_max_stop_offset_laps:
            return False
        best = min(positions)
        if anomaly.summary.get("order_changed") is True:
            return best <= params.undercut_max_position
        upset = anomaly.summary.get("confidence") == "high" and anomaly.summary.get("hit") is False
        return upset and best <= params.undercut_upset_max_position

    position = _int(anomaly, "position")
    near_front = position is not None and position <= params.incident_max_position
    if kind in (AnomalyKind.PACE_LOSS, AnomalyKind.OFF_TRACK):
        return near_front and anomaly.magnitude >= params.pace_loss_min_s
    if kind is AnomalyKind.DEG_CLIFF:
        return (
            near_front
            and anomaly.magnitude >= params.deg_cliff_min_s
            and anomaly.summary.get("fit_reliability") != "unreliable"
            and anomaly.summary.get("ends_at_finish") is not True
        )
    if kind is AnomalyKind.PIT_ANOMALY:
        if anomaly.magnitude >= params.pit_disaster_min_s:
            return True
        return near_front and anomaly.magnitude >= params.pit_anomaly_min_s
    return False


def as_told(anomalies: Sequence[Anomaly], told: Mapping[str, str]) -> list[Anomaly]:
    """A live race's anomalies, with every neutralization whose card has
    already gone out pinned to what that card said. `told` maps the card's
    `story_key` to the `retired` it listed ("" when it named nobody or was
    never shown).

    A retirement confirmed after its neutralization's card went out then
    gets its own card, rather than being folded into a card nobody will see
    again: live, the 2024 Monaco GP's red-flag card went out on lap 2 naming
    nobody, and the four lap-1 crashers, confirmed on lap 4, were never
    told. A finished race has nothing told yet, so the replay story is
    unchanged."""
    out: list[Anomaly] = []
    for anomaly in anomalies:
        key = story_key(anomaly)
        if anomaly.kind is AnomalyKind.NEUTRALIZATION and key in told:
            anomaly = replace(anomaly, summary={**anomaly.summary, "retired": told[key]})
        out.append(anomaly)
    return out


def select_story(anomalies: Sequence[Anomaly], params: StoryParams | None = None) -> list[Anomaly]:
    """The race's story: merged, filtered, one card per event, in race
    order. When more than `params.max_moments` qualify, the least salient
    non-landmarks are dropped first."""
    params = params or StoryParams()
    kept = [a for a in merge_incidents(anomalies) if is_story_moment(a, params)]

    by_key: dict[str, Anomaly] = {}
    for anomaly in sorted(kept, key=lambda a: (a.anomaly_time, anomaly_id(a))):
        by_key.setdefault(story_key(anomaly), anomaly)
    moments = list(by_key.values())

    if len(moments) > params.max_moments:
        landmarks = [a for a in moments if a.kind in _LANDMARKS]
        others = sorted(
            (a for a in moments if a.kind not in _LANDMARKS),
            key=lambda a: (-a.salience, a.anomaly_time),
        )
        room = max(0, params.max_moments - len(landmarks))
        moments = landmarks + others[:room]

    return sorted(moments, key=lambda a: (a.anomaly_time, anomaly_id(a)))


__all__ = [
    "StoryParams",
    "as_told",
    "is_story_moment",
    "merge_incidents",
    "select_story",
    "story_key",
]
