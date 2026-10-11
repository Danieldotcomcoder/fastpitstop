"""Story 2.0 against `landmark_race.py`: what the detector finds on a race
with every landmark planted, what the story keeps, and what the template
narrator writes about it — each against the race's exact ground truth.

The real-race measurements this was tuned on (2024 Bahrain, Miami,
Australia, Monaco, Austria, Belgium, São Paulo; 2025 Spain) live in
`tests/eval/` and run only when the backfill is present.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import pytest
from landmark_race import GRID, SESSION_KEY, TOTAL_LAPS, build_frames, driver_list, grid_events

from f1_kernel import KernelParams, KernelReport, SessionFrames, analyze_session_from_frames
from f1_why import (
    Anomaly,
    AnomalyKind,
    AnomalyParams,
    EvidenceParams,
    EvidenceSources,
    NarrationParams,
    TemplateNarrationModel,
    assemble_evidence,
    detect_anomalies,
    explain,
    select_story,
    starting_grid,
    story_key,
)
from f1_why.templates import surname


@pytest.fixture(scope="module")
def frames() -> SessionFrames:
    return build_frames()


@pytest.fixture(scope="module")
def report(frames: SessionFrames) -> KernelReport:
    return analyze_session_from_frames(frames, KernelParams(), total_laps=TOTAL_LAPS)


@pytest.fixture(scope="module")
def anomalies(frames: SessionFrames, report: KernelReport) -> list[Anomaly]:
    return detect_anomalies(frames, report, AnomalyParams(), grid=GRID)


def _of(anomalies: Sequence[Anomaly], kind: AnomalyKind) -> list[Anomaly]:
    return [a for a in anomalies if a.kind is kind]


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------


def test_the_grid_is_read_from_the_stream_before_lights_out(frames: SessionFrames) -> None:
    assert starting_grid(grid_events(), frames.laps) == GRID


def test_no_grid_before_lap_one_has_started(frames: SessionFrames) -> None:
    assert starting_grid(grid_events(), frames.laps.head(0)) is None


def test_the_start_reports_the_lead_change(anomalies: list[Anomaly]) -> None:
    (start,) = _of(anomalies, AnomalyKind.RACE_START)
    assert start.summary["leader"] == "2"
    assert start.summary["pole"] == "1"
    assert start.summary["lead_changed"] is True


def test_only_the_on_track_pass_is_an_overtake(anomalies: list[Anomaly]) -> None:
    """ "4" passing "3" on lap 3 is real; "6" moving ahead of "5" while "5"
    was on its out-lap is the pit lane, and cars moving up when "3" and "7"
    stopped passed nobody."""
    passes = _of(anomalies, AnomalyKind.POSITION_CHANGE)
    assert [(a.lap_number, a.drivers) for a in passes] == [(3, ("4", "3"))]
    assert passes[0].summary["to_position"] == 3


def test_both_retirements_are_found_at_their_running_position(anomalies: list[Anomaly]) -> None:
    retired = {a.drivers[0]: a for a in _of(anomalies, AnomalyKind.RETIREMENT)}
    assert set(retired) == {"3", "7"}
    assert retired["3"].summary["position"] == 4
    assert retired["3"].summary["last_completed_lap"] == 8
    # "7" ran last, with nobody behind it: confirmed by the cars ahead going on.
    assert retired["7"].summary["position"] == 7
    assert retired["7"].summary["last_completed_lap"] == 4


def test_the_safety_car_names_who_pitted_and_why(anomalies: list[Anomaly]) -> None:
    (sc,) = _of(anomalies, AnomalyKind.NEUTRALIZATION)
    assert sc.summary["status"] == "sc"
    assert (sc.summary["start_lap"], sc.summary["end_lap"]) == (9, 10)
    assert sc.summary["pitted"] == "2"
    assert sc.summary["retired"] == "3"
    assert sc.summary["leader_before"] == sc.summary["leader_after"] == "2"


def test_the_finish_is_the_order_across_the_line(anomalies: list[Anomaly]) -> None:
    (finish,) = _of(anomalies, AnomalyKind.RACE_FINISH)
    assert finish.drivers == ("2", "1", "4")
    assert finish.summary["winner_grid"] == 2
    assert finish.summary["margin_s"] == pytest.approx(1.0)


def test_every_moment_says_which_lap_reveals_it(anomalies: list[Anomaly]) -> None:
    """A safety car is numbered with the lap it began but describes
    everything to its restart, so a lap-gated client must wait for the
    restart lap, which `reveal_lap` names."""
    for anomaly in anomalies:
        assert isinstance(anomaly.summary.get("reveal_driver"), str)
        assert isinstance(anomaly.summary.get("reveal_lap"), int)
    (sc,) = _of(anomalies, AnomalyKind.NEUTRALIZATION)
    assert sc.summary["reveal_lap"] == 11
    assert sc.summary["reveal_driver"] == "2"


def test_no_grid_means_no_start_and_no_lap_one_retirement(
    frames: SessionFrames, report: KernelReport
) -> None:
    found = detect_anomalies(frames, report, AnomalyParams())
    assert not _of(found, AnomalyKind.RACE_START)


# ---------------------------------------------------------------------------
# the story
# ---------------------------------------------------------------------------


def test_the_story_follows_the_race(anomalies: list[Anomaly]) -> None:
    """Start, the pass, "7"'s retirement, the safety car (which tells "3"'s
    retirement, so that is no separate card), the flag."""
    story = select_story(anomalies)
    assert [(a.kind, a.lap_number) for a in story] == [
        (AnomalyKind.RACE_START, 1),
        (AnomalyKind.POSITION_CHANGE, 3),
        (AnomalyKind.RETIREMENT, 5),
        (AnomalyKind.NEUTRALIZATION, 9),
        (AnomalyKind.RACE_FINISH, TOTAL_LAPS),
    ]


def test_a_moment_found_again_on_a_later_lap_is_one_card(anomalies: list[Anomaly]) -> None:
    """Live re-detection anchors a tyre cliff to the latest lap; it is still
    the same stint, so the same card."""
    (finish,) = _of(anomalies, AnomalyKind.RACE_FINISH)
    cliff = Anomaly(
        kind=AnomalyKind.DEG_CLIFF,
        lap_number=6,
        anomaly_time=finish.anomaly_time,
        drivers=("1",),
        magnitude=1.5,
        salience=0.3,
        summary={"stint_number": 1, "position": 2, "fit_reliability": "good"},
        reasons=(),
    )
    later = Anomaly(
        kind=cliff.kind,
        lap_number=7,
        anomaly_time=cliff.anomaly_time,
        drivers=cliff.drivers,
        magnitude=cliff.magnitude,
        salience=cliff.salience,
        summary=cliff.summary,
        reasons=(),
    )
    assert story_key(cliff) == story_key(later)
    assert len([a for a in select_story([cliff, later]) if a.kind is AnomalyKind.DEG_CLIFF]) == 1


def test_a_tyre_cliff_at_the_flag_is_not_a_card() -> None:
    """The last laps before the flag are often slow by choice."""
    from datetime import UTC, datetime

    cliff = Anomaly(
        kind=AnomalyKind.DEG_CLIFF,
        lap_number=57,
        anomaly_time=datetime(2031, 3, 9, 16, 30, tzinfo=UTC),
        drivers=("1",),
        magnitude=2.5,
        salience=0.4,
        summary={"stint_number": 3, "position": 2, "ends_at_finish": True},
        reasons=(),
    )
    assert select_story([cliff]) == []


# ---------------------------------------------------------------------------
# narration
# ---------------------------------------------------------------------------


def _story_text(
    frames: SessionFrames, report: KernelReport, anomalies: list[Anomaly]
) -> dict[AnomalyKind, str]:
    sources = EvidenceSources(
        session_frames=frames, kernel_report=report, events=(driver_list(), *grid_events())
    )
    texts: dict[AnomalyKind, str] = {}
    for moment in select_story(anomalies):
        bundle = assemble_evidence(moment, sources, EvidenceParams())
        explanation = explain(bundle, TemplateNarrationModel(), NarrationParams())
        assert explanation.grounded, explanation.fallback_reason
        assert "anomaly" in explanation.cited_evidence_ids
        texts[moment.kind] = explanation.text
    return texts


def test_every_card_is_written_from_its_own_numbers(
    frames: SessionFrames, report: KernelReport, anomalies: list[Anomaly]
) -> None:
    texts = _story_text(frames, report, anomalies)
    assert texts[AnomalyKind.RACE_START] == (
        "Norris took the lead from pole-sitter Verstappen on lap 1."
    )
    assert texts[AnomalyKind.POSITION_CHANGE] == "Piastri passed Leclerc for P3 on lap 3."
    assert texts[AnomalyKind.RETIREMENT] == "Antonelli retired from P7 on lap 5."
    assert texts[AnomalyKind.NEUTRALIZATION] == (
        "Safety car from lap 9 to lap 10, after Leclerc retired. "
        "Norris pitted under it and kept the lead."
    )
    assert texts[AnomalyKind.RACE_FINISH] == (
        "Norris took the chequered flag from P2 on the grid, 1.0 s ahead of Verstappen, "
        "with Piastri third."
    )


def test_no_card_states_a_number_the_bundle_does_not_hold(
    frames: SessionFrames, report: KernelReport, anomalies: list[Anomaly]
) -> None:
    """The template's accuracy guarantee, checked: every number in every
    sentence is one of the moment's own summary values or a lap number."""
    sources = EvidenceSources(session_frames=frames, kernel_report=report, events=(driver_list(),))
    for moment in select_story(anomalies):
        bundle = assemble_evidence(moment, sources, EvidenceParams())
        text = explain(bundle, TemplateNarrationModel(), NarrationParams()).text
        known: set[str] = set()
        for item in bundle.items:
            for value in item.data.values():
                if isinstance(value, bool) or not isinstance(value, int | float):
                    continue
                known.update({str(value), f"{value:.1f}", str(round(value))})
        known.update(str(n) for n in range(1, TOTAL_LAPS + 2))
        for number in re.findall(r"\d+(?:\.\d+)?", text):
            assert number in known, f"{number!r} in {text!r} is not in the bundle"


@pytest.mark.parametrize(
    ("full_name", "expected"),
    [
        ("Max VERSTAPPEN", "Verstappen"),
        ("Max Verstappen", "Verstappen"),
        ("Andrea Kimi ANTONELLI", "Antonelli"),
        ("Andrea Kimi Antonelli", "Antonelli"),
        ("Nyck De Vries", "De Vries"),
        ("Guanyu ZHOU", "Zhou"),
    ],
)
def test_surnames_from_live_and_historical_names(full_name: str, expected: str) -> None:
    from f1_domain import DriverRef

    assert surname(DriverRef(number="1", full_name=full_name)) == expected


def test_a_lost_lap_with_no_cause_is_not_narrated(
    frames: SessionFrames, report: KernelReport
) -> None:
    """Nothing in the bundle explains it, so the narrator declines rather
    than write a card that says nothing."""
    (finish,) = _of(detect_anomalies(frames, report, AnomalyParams()), AnomalyKind.RACE_FINISH)
    loss = Anomaly(
        kind=AnomalyKind.PACE_LOSS,
        lap_number=7,
        anomaly_time=finish.anomaly_time,
        drivers=("1",),
        magnitude=2.5,
        salience=0.4,
        summary={"delta_s": 2.5, "position": 2},
        reasons=("slow lap",),
    )
    sources = EvidenceSources(session_frames=frames, kernel_report=report, events=(driver_list(),))
    bundle = assemble_evidence(loss, sources, EvidenceParams())
    explanation = explain(bundle, TemplateNarrationModel(), NarrationParams())
    assert explanation.grounded is False
    assert explanation.fallback_reason == "model_declined"


def test_a_retirement_keeps_the_incident_that_caused_it(
    frames: SessionFrames, report: KernelReport, anomalies: list[Anomaly]
) -> None:
    """The 2024 Austrian GP regression: race control noted the Norris /
    Verstappen collision, then sent five newer messages naming nobody (pit
    lane closed, a VSC deployed and ended), and those pushed the collision
    out of Norris's retirement. Incidents come first now; a message about
    some other car is never evidence; and the car is never named back at
    itself."""
    from datetime import timedelta

    from f1_domain import Provenance, RaceControlMessage

    (retired,) = [a for a in _of(anomalies, AnomalyKind.RETIREMENT) if a.drivers == ("3",)]
    stopped = retired.anomaly_time - timedelta(seconds=60)

    def message(seconds: int, text: str, seq: int) -> RaceControlMessage:
        return RaceControlMessage(
            session_key=SESSION_KEY,
            ts_utc=stopped + timedelta(seconds=seconds),
            source_seq=seq,
            provenance=Provenance.HISTORICAL,
            category="Other",
            message=text,
        )

    events = (
        driver_list(),
        message(
            0, "TURN 3 INCIDENT INVOLVING CARS 3 (LEC) AND 4 (PIA) NOTED - CAUSING A COLLISION", 1
        ),
        message(5, "CAR 6 (ZHO) TIME 1:31.000 DELETED - TRACK LIMITS AT TURN 9", 2),
        *(message(10 + i, f"PIT LANE ENTRY CLOSED {i}", 3 + i) for i in range(6)),
    )
    sources = EvidenceSources(session_frames=frames, kernel_report=report, events=events)
    bundle = assemble_evidence(retired, sources, EvidenceParams())

    messages = [str(i.data.get("message")) for i in bundle.items if i.kind.value == "race_control"]
    assert any("INCIDENT INVOLVING CARS 3" in m for m in messages)
    assert not any("ZHO" in m for m in messages)
    text = explain(bundle, TemplateNarrationModel(), NarrationParams()).text
    assert text == "Leclerc retired from P4 on lap 9, after an incident at turn 3 with Piastri."
