"""`assemble_evidence` against the checked-in synthetic race (`race_fixture.py`),
reusing the SAME `anomalies` conftest fixture `test_detect.py` asserts the
detector itself against — the same "assert against reality, not against
this package's own output" discipline the rest of this project follows.
"""

from __future__ import annotations

import math
from datetime import timedelta

import pytest
from race_fixture import HERO, SESSION_KEY

from f1_domain import DomainEvent, DriverListUpdated, DriverRef, RaceControlMessage
from f1_kernel import KernelReport, SessionFrames
from f1_radio.search import MemorySearchIndex
from f1_radio.store import TranscriptRecord
from f1_why.anomaly import Anomaly, AnomalyKind
from f1_why.evidence import (
    EvidenceItem,
    EvidenceKind,
    EvidenceParams,
    EvidenceSources,
    assemble_evidence,
)


def _one(anomalies: list[Anomaly], kind: AnomalyKind) -> Anomaly:
    matches = [a for a in anomalies if a.kind is kind]
    assert len(matches) == 1, f"expected exactly one {kind.value}, got {len(matches)}: {matches}"
    return matches[0]


def _clip(**overrides: object) -> TranscriptRecord:
    fields: dict[str, object] = {
        "session_key": SESSION_KEY,
        "driver": HERO,
        "captured_at": None,
        "audio_path": "clip.mp3",
        "transcript_text": "box this lap, tyres are gone",
        "language": "en",
        "confidence": 0.9,
        "model_version": "faster-whisper-small",
    }
    fields.update(overrides)
    return TranscriptRecord(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# property test: no evidence item is ever after its own anomaly's time
# ---------------------------------------------------------------------------


def test_no_evidence_item_is_ever_after_its_own_anomaly_time(
    race_frames: SessionFrames, race_report: KernelReport, anomalies: list[Anomaly]
) -> None:
    """For EVERY real anomaly the fixture plants, seed a shared pool of
    race-control "landmines" placed one second after (and one second
    before) THAT anomaly's own `anomaly_time`, then assert that assembling
    evidence for every single one of them never returns an item whose
    `event_time` exceeds its own boundary.

    A landmine planted just after anomaly A can legitimately show up in a
    LATER anomaly B's own bundle (it is genuinely in B's past) — the
    property is checked per-bundle, never globally, which is exactly why
    this plants one pair per real anomaly rather than one global pair.
    """
    events: list[DomainEvent] = []
    for index, anomaly in enumerate(anomalies):
        events.append(
            RaceControlMessage(
                session_key=SESSION_KEY,
                ts_utc=anomaly.anomaly_time + timedelta(seconds=1),
                source_seq=10_000 + 2 * index,
                category="Flag",
                flag="YELLOW",
                message=f"FUTURE LANDMINE {index}",
            )
        )
        events.append(
            RaceControlMessage(
                session_key=SESSION_KEY,
                ts_utc=anomaly.anomaly_time - timedelta(seconds=1),
                source_seq=10_001 + 2 * index,
                category="Flag",
                flag="GREEN",
                message=f"PAST LANDMINE {index}",
            )
        )

    params = EvidenceParams(lookback_s=1_000_000.0)
    sources = EvidenceSources(
        session_frames=race_frames, kernel_report=race_report, events=tuple(events)
    )

    for index, anomaly in enumerate(anomalies):
        bundle = assemble_evidence(anomaly, sources, params)
        for item in bundle.items:
            assert item.event_time <= anomaly.anomaly_time, (
                f"{item.evidence_id} ({item.kind.value}) leaked {item.event_time} "
                f"past anomaly_time {anomaly.anomaly_time}"
            )
        leaked_future = f"FUTURE LANDMINE {index}"
        assert not any(leaked_future in item.summary for item in bundle.items)


# ---------------------------------------------------------------------------
# a relevant radio clip is retrieved and ranked
# ---------------------------------------------------------------------------


def test_relevant_radio_clip_is_retrieved_and_ranked(
    race_frames: SessionFrames, race_report: KernelReport, anomalies: list[Anomaly]
) -> None:
    pace_loss = _one(anomalies, AnomalyKind.PACE_LOSS)
    clip = _clip(
        captured_at=pace_loss.anomaly_time - timedelta(seconds=20),
        audio_path="hero_lockup.mp3",
        transcript_text="yeah I locked it up into turn four, box this lap",
        confidence=0.88,
    )
    sources = EvidenceSources(
        session_frames=race_frames,
        kernel_report=race_report,
        radio_index=MemorySearchIndex([clip]),
    )

    bundle = assemble_evidence(pace_loss, sources, EvidenceParams())

    radio_items = [item for item in bundle.items if item.kind is EvidenceKind.RADIO_CLIP]
    assert len(radio_items) == 1
    assert "locked it up" in radio_items[0].summary
    assert radio_items[0].drivers == (HERO,)
    assert radio_items[0].confidence == pytest.approx(0.88)
    assert not bundle.low_evidence

    # Ranked: the anomaly's own fact is always first; the radio clip — the
    # single highest base-weighted kind, and the only other evidence here —
    # is the very next item.
    assert bundle.items[0].kind is EvidenceKind.ANOMALY_FACT
    assert bundle.items[1].kind is EvidenceKind.RADIO_CLIP


def test_radio_clip_outside_the_lookback_window_is_not_retrieved(
    race_frames: SessionFrames, race_report: KernelReport, anomalies: list[Anomaly]
) -> None:
    pace_loss = _one(anomalies, AnomalyKind.PACE_LOSS)
    params = EvidenceParams(lookback_s=30.0)
    too_early = _clip(captured_at=pace_loss.anomaly_time - timedelta(seconds=45))
    sources = EvidenceSources(
        session_frames=race_frames,
        kernel_report=race_report,
        radio_index=MemorySearchIndex([too_early]),
    )

    bundle = assemble_evidence(pace_loss, sources, params)

    assert not any(item.kind is EvidenceKind.RADIO_CLIP for item in bundle.items)


def test_radio_clip_with_no_transcript_yet_is_skipped(
    race_frames: SessionFrames, race_report: KernelReport, anomalies: list[Anomaly]
) -> None:
    pace_loss = _one(anomalies, AnomalyKind.PACE_LOSS)
    not_yet_transcribed = _clip(
        captured_at=pace_loss.anomaly_time - timedelta(seconds=10),
        transcript_text=None,
        confidence=None,
    )
    sources = EvidenceSources(
        session_frames=race_frames,
        kernel_report=race_report,
        radio_index=MemorySearchIndex([not_yet_transcribed]),
    )

    bundle = assemble_evidence(pace_loss, sources, EvidenceParams())

    assert not any(item.kind is EvidenceKind.RADIO_CLIP for item in bundle.items)


# ---------------------------------------------------------------------------
# trimming respects the token budget and keeps the highest-salience items
# ---------------------------------------------------------------------------


def test_trimming_respects_token_budget_and_keeps_highest_salience_items(
    race_frames: SessionFrames, race_report: KernelReport, anomalies: list[Anomaly]
) -> None:
    pace_loss = _one(anomalies, AnomalyKind.PACE_LOSS)
    # Five clips at increasing distance from the anomaly, EVERY summary the
    # same fixed length (a 2-digit index padded with 'x's) so each one's
    # estimated token cost is identical and the survivor count is exact,
    # not a guess.
    clips = [
        _clip(
            captured_at=pace_loss.anomaly_time - timedelta(seconds=10 * (i + 1)),
            audio_path=f"clip_{i}.mp3",
            transcript_text=f"m{i:02d} " + "x" * 40,
        )
        for i in range(5)
    ]
    sources = EvidenceSources(
        session_frames=race_frames,
        kernel_report=race_report,
        radio_index=MemorySearchIndex(clips),
    )
    generous_params = EvidenceParams(max_radio_clips_per_driver=5, token_budget=10_000)

    # Learn the anomaly fact's own cost empirically (it varies with the
    # planted numbers) rather than hardcoding it, then build a budget that
    # fits the anomaly fact plus EXACTLY two of the five identically-sized
    # clips — deterministic, not a guess at the right constant.
    generous = assemble_evidence(pace_loss, sources, generous_params)
    anomaly_fact = next(item for item in generous.items if item.kind is EvidenceKind.ANOMALY_FACT)
    per_clip_cost = math.ceil(len(generous.items[1].summary) / generous_params.chars_per_token)
    anomaly_fact_cost = math.ceil(len(anomaly_fact.summary) / generous_params.chars_per_token)
    tight_budget = anomaly_fact_cost + per_clip_cost * 2

    params = EvidenceParams(max_radio_clips_per_driver=5, token_budget=tight_budget)
    bundle = assemble_evidence(pace_loss, sources, params)

    radio_items = [item for item in bundle.items if item.kind is EvidenceKind.RADIO_CLIP]
    assert len(radio_items) == 2
    assert bundle.estimated_tokens <= params.token_budget
    assert any("dropped to fit the token budget" in note for note in bundle.notes)

    # The survivors are exactly the two HIGHEST-salience (closest-in-time,
    # since every clip here shares the same base weight) clips: 0 and 1.
    def _clip_index(item: EvidenceItem) -> int:
        audio_path = item.data["audio_path"]
        assert isinstance(audio_path, str)
        return int(audio_path.removeprefix("clip_").removesuffix(".mp3"))

    surviving = sorted(_clip_index(item) for item in radio_items)
    assert surviving == [0, 1]


# ---------------------------------------------------------------------------
# low_evidence
# ---------------------------------------------------------------------------


def test_measured_kernel_context_is_enough_to_ask_the_narrator(
    race_frames: SessionFrames, race_report: KernelReport, anomalies: list[Anomaly]
) -> None:
    """No radio corpus and no race control: the measured lap context still
    goes to the narrator, which decides whether it explains the moment.

    This bundle used to be `low_evidence` and was never shown to a model,
    which is why a pass on tyres eight laps fresher could never be
    explained by the tyre age that caused it."""
    pace_loss = _one(anomalies, AnomalyKind.PACE_LOSS)
    sources = EvidenceSources(session_frames=race_frames, kernel_report=race_report)

    bundle = assemble_evidence(pace_loss, sources, EvidenceParams())

    assert not bundle.low_evidence
    assert any("radio corpus" in note for note in bundle.notes)
    assert any(item.kind is EvidenceKind.LAP_CONTEXT for item in bundle.items)


def test_low_evidence_when_nothing_beyond_the_anomaly_fact_survives(
    race_frames: SessionFrames, race_report: KernelReport, anomalies: list[Anomaly]
) -> None:
    """The line that still holds: with nothing but the anomaly's own
    numbers, the narrator is never asked. A one-token budget leaves exactly
    the anomaly fact, which trimming never drops."""
    pace_loss = _one(anomalies, AnomalyKind.PACE_LOSS)
    sources = EvidenceSources(session_frames=race_frames, kernel_report=race_report)

    bundle = assemble_evidence(pace_loss, sources, EvidenceParams(token_budget=1))

    assert [item.kind for item in bundle.items] == [EvidenceKind.ANOMALY_FACT]
    assert bundle.low_evidence


def test_a_track_wide_race_control_message_is_evidence(
    race_frames: SessionFrames, race_report: KernelReport, anomalies: list[Anomaly]
) -> None:
    pace_loss = _one(anomalies, AnomalyKind.PACE_LOSS)
    message = RaceControlMessage(
        session_key=SESSION_KEY,
        ts_utc=pace_loss.anomaly_time - timedelta(seconds=5),
        source_seq=1,
        category="Flag",
        flag="YELLOW",
        scope="Sector",
        message="YELLOW IN SECTOR 2",
    )
    sources = EvidenceSources(
        session_frames=race_frames, kernel_report=race_report, events=(message,)
    )

    bundle = assemble_evidence(pace_loss, sources, EvidenceParams())

    assert not bundle.low_evidence
    assert any(item.kind is EvidenceKind.RACE_CONTROL for item in bundle.items)


def _message(anomaly: Anomaly, seq: int, driver: str | None, text: str) -> RaceControlMessage:
    return RaceControlMessage(
        session_key=SESSION_KEY,
        ts_utc=anomaly.anomaly_time - timedelta(seconds=seq),
        source_seq=seq,
        category="Flag",
        flag="BLUE" if driver else "YELLOW",
        scope="Driver" if driver else "Sector",
        driver=driver,
        message=text,
    )


def test_race_control_about_an_uninvolved_driver_is_not_evidence(
    race_frames: SessionFrames, race_report: KernelReport, anomalies: list[Anomaly]
) -> None:
    """The 2025 Spanish GP regression: blue flags for three backmarkers were
    the whole race-control contribution to a Russell/Antonelli undercut
    verdict. A message is evidence about THIS moment only if it names one of
    its drivers or names nobody."""
    pace_loss = _one(anomalies, AnomalyKind.PACE_LOSS)
    bystander = next(d for d in ("87", "30", "14") if d not in pace_loss.drivers)
    sources = EvidenceSources(
        session_frames=race_frames,
        kernel_report=race_report,
        events=(
            _message(pace_loss, 1, bystander, "WAVED BLUE FLAG FOR A BACKMARKER"),
            _message(pace_loss, 2, HERO, "CAR UNDER INVESTIGATION"),
            _message(pace_loss, 3, None, "YELLOW IN SECTOR 2"),
        ),
    )

    bundle = assemble_evidence(pace_loss, sources, EvidenceParams())

    kept = {item.summary for item in bundle.items if item.kind is EvidenceKind.RACE_CONTROL}
    assert not any("BACKMARKER" in summary for summary in kept)
    assert any("INVESTIGATION" in summary for summary in kept)
    assert any("SECTOR 2" in summary for summary in kept)


def _roster_event(anomaly: Anomaly, offset_s: float, *refs: DriverRef) -> DriverListUpdated:
    return DriverListUpdated(
        session_key=SESSION_KEY,
        ts_utc=anomaly.anomaly_time + timedelta(seconds=offset_s),
        source_seq=0,
        drivers=list(refs),
    )


def test_the_roster_names_only_the_drivers_the_bundle_mentions(
    race_frames: SessionFrames, race_report: KernelReport, anomalies: list[Anomaly]
) -> None:
    """A narrator told who "44" is writes a name instead of a number; it is
    not told about anyone the bundle does not mention."""
    pace_loss = _one(anomalies, AnomalyKind.PACE_LOSS)
    hero = DriverRef(number=HERO, code="HRO", full_name="Hero DRIVER", team="Team A")
    other = DriverRef(number="999", code="OTH", full_name="Other Person", team="Team B")
    sources = EvidenceSources(
        session_frames=race_frames,
        kernel_report=race_report,
        events=(_roster_event(pace_loss, -60, hero, other),),
    )

    bundle = assemble_evidence(pace_loss, sources, EvidenceParams())

    assert bundle.roster == (hero,)


def test_the_roster_is_held_to_the_anomaly_boundary(
    race_frames: SessionFrames, race_report: KernelReport, anomalies: list[Anomaly]
) -> None:
    """Identity rather than race data, but "nothing after the anomaly" holds
    without exceptions: a driver list published later is not used."""
    pace_loss = _one(anomalies, AnomalyKind.PACE_LOSS)
    early = DriverRef(number=HERO, code="OLD")
    late = DriverRef(number=HERO, code="NEW")
    sources = EvidenceSources(
        session_frames=race_frames,
        kernel_report=race_report,
        events=(_roster_event(pace_loss, -60, early), _roster_event(pace_loss, 60, late)),
    )

    bundle = assemble_evidence(pace_loss, sources, EvidenceParams())

    assert [ref.code for ref in bundle.roster] == ["OLD"]


# ---------------------------------------------------------------------------
# degradation evidence is walk-forward, not whole-session
# ---------------------------------------------------------------------------


def test_degradation_evidence_for_an_early_anomaly_ignores_later_laps(
    race_frames: SessionFrames, race_report: KernelReport, anomalies: list[Anomaly]
) -> None:
    """The hero's planted degradation is a quadratic CLIFF: its rate over
    the whole 24-lap stint is materially different from its rate over just
    the first 8 laps. If `_degradation_items` quietly used the full-session
    `KernelReport.degradation_fits` instead of refitting walk-forward, the
    lap-8 PACE_LOSS anomaly's own evidence would describe a stint slope
    informed by laps the anomaly's own moment could not have known about.
    """
    pace_loss = _one(anomalies, AnomalyKind.PACE_LOSS)
    sources = EvidenceSources(session_frames=race_frames, kernel_report=race_report)

    bundle = assemble_evidence(pace_loss, sources, EvidenceParams())

    walk_forward = next(item for item in bundle.items if item.kind is EvidenceKind.DEGRADATION_FIT)
    whole_session = next(
        fit for fit in race_report.degradation_fits if fit.driver == HERO and fit.stint_number == 1
    )
    n_laps_used = walk_forward.data["n_laps_used"]
    assert isinstance(n_laps_used, int)
    assert n_laps_used < whole_session.n_laps_used
