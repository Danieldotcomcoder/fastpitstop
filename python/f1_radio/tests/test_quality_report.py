"""`build_quality_report`/`render_markdown`: pure stats over an
already-loaded list of `TranscriptRecord`s -- no store, no database."""

from __future__ import annotations

from radio_data import KEY

from f1_radio.quality_report import build_quality_report, render_markdown
from f1_radio.store import TranscriptRecord


def _record(**overrides: object) -> TranscriptRecord:
    fields: dict[str, object] = {
        "session_key": KEY,
        "driver": "44",
        "captured_at": None,
        "audio_path": "a.mp3",
        "transcript_text": "box this lap",
        "language": "en",
        "confidence": 0.9,
        "model_version": "faster-whisper-small",
        "segments": None,
    }
    fields.update(overrides)
    return TranscriptRecord(**fields)  # type: ignore[arg-type]


def test_counts_and_no_speech_are_tallied() -> None:
    records = [
        _record(driver="44", transcript_text="box this lap", confidence=0.9),
        _record(driver="1", transcript_text="", confidence=0.0, audio_path="b.mp3"),
    ]

    report = build_quality_report(records, season=2024)

    assert report.transcript_count == 2
    assert report.no_speech_count == 1


def test_confidence_distribution_and_flagging_use_the_same_threshold() -> None:
    records = [
        _record(driver="44", confidence=0.95, audio_path="a.mp3"),
        _record(driver="1", confidence=0.3, audio_path="b.mp3"),  # below default threshold
        _record(driver="16", confidence=0.7, audio_path="c.mp3"),
    ]

    report = build_quality_report(records, season=2024, low_confidence_threshold=0.5)

    assert report.mean_confidence is not None
    assert report.median_confidence == 0.7
    assert report.confidence_buckets["0.8-1.0"] == 1
    assert report.confidence_buckets["0.2-0.4"] == 1
    assert len(report.flagged_low_confidence) == 1
    assert report.flagged_low_confidence[0].driver == "1"
    assert report.flagged_low_confidence[0].reason == "low_confidence"


def test_no_speech_clips_are_flagged_with_their_own_reason() -> None:
    records = [_record(driver="44", transcript_text="", confidence=0.0, audio_path="a.mp3")]

    report = build_quality_report(records, season=2024)

    [flagged] = report.flagged_low_confidence
    assert flagged.reason == "no_speech_detected"


def test_language_and_model_version_breakdowns() -> None:
    records = [
        _record(
            driver="44", language="en", model_version="faster-whisper-small", audio_path="a.mp3"
        ),
        _record(
            driver="1", language="fr", model_version="faster-whisper-small", audio_path="b.mp3"
        ),
        _record(
            driver="16", language=None, model_version="faster-whisper-tiny", audio_path="c.mp3"
        ),
    ]

    report = build_quality_report(records, season=2024)

    assert report.language_breakdown == {"en": 1, "fr": 1, "unknown": 1}
    assert report.model_versions == {"faster-whisper-small": 2, "faster-whisper-tiny": 1}


def test_empty_corpus_degrades_gracefully_not_a_crash() -> None:
    report = build_quality_report([], season=2024)

    assert report.transcript_count == 0
    assert report.mean_confidence is None
    assert report.median_confidence is None
    assert report.flagged_low_confidence == []
    markdown = render_markdown(report)
    assert "season 2024" in markdown


def test_render_markdown_includes_every_section() -> None:
    records = [_record(driver="44", confidence=0.9, audio_path="a.mp3")]
    report = build_quality_report(records, season=2024)

    markdown = render_markdown(report)

    assert "# Team-radio transcription quality report -- season 2024" in markdown
    assert "## Confidence distribution" in markdown
    assert "## Language breakdown" in markdown
    assert "## Model versions" in markdown
    assert "## Flagged clips" in markdown
