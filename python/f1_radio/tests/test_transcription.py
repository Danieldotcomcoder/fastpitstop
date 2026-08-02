"""`transcribe_clip`: confidence derivation, the hallucination guard on
silence/noise, and language pass-through -- all against a fake
`WhisperBackend` (no model, no audio file even needs to exist for these).

`test_real_model.py` is the opt-in sibling that runs these same claims
against a REAL faster-whisper model and the checked-in synthetic audio
fixtures -- skipped by default, the same DB/live-feed-gated pattern this
project already uses (`test_migrations.py`'s `F1_STORE_RUN_DB_TESTS`,
`docs/PHASE0.md`'s live-feed reality check) for a dependency this
environment cannot assume is available (a model download plus real CPU
inference time).
"""

from __future__ import annotations

from pathlib import Path

from radio_data import FakeWhisperBackend, FakeWhisperInfo, clean_segment

from f1_radio.transcription import transcribe_clip

_PATH = Path("clip.mp3")


def test_a_confident_segment_produces_non_empty_text_and_high_confidence() -> None:
    backend = FakeWhisperBackend(
        segments=[clean_segment("Box this lap, box box.", avg_logprob=-0.1)],
        info=FakeWhisperInfo(language="en", language_probability=0.98),
    )

    result = transcribe_clip(_PATH, backend)

    assert result.text == "Box this lap, box box."
    assert result.language == "en"
    assert result.language_probability == 0.98
    assert not result.no_speech_detected
    assert not result.low_confidence
    assert 0.0 < result.confidence <= 1.0


def test_no_segments_after_vad_is_reported_as_no_speech_not_hallucinated_text() -> None:
    """The hallucination guard: silence/noise must never surface as text."""
    backend = FakeWhisperBackend(segments=[], info=FakeWhisperInfo(language="en"))

    result = transcribe_clip(_PATH, backend)

    assert result.text == ""
    assert result.segments == []
    assert result.no_speech_detected is True
    assert result.low_confidence is True
    assert result.confidence == 0.0


def test_low_avg_logprob_is_flagged_low_confidence_but_not_no_speech() -> None:
    backend = FakeWhisperBackend(
        segments=[clean_segment("mumble mumble", avg_logprob=-3.0)],
    )

    result = transcribe_clip(_PATH, backend, low_confidence_threshold=0.5)

    assert result.text == "mumble mumble"
    assert result.no_speech_detected is False
    assert result.low_confidence is True
    assert result.confidence < 0.5


def test_confidence_is_duration_weighted_across_segments() -> None:
    # exp(-0.1) ~= 0.9048, exp(-2.0) ~= 0.1353. A 3s confident segment and a
    # 1s garbled one should pull the overall confidence toward the longer one.
    from math import exp

    long_confident = clean_segment("clear radio call", avg_logprob=-0.1)
    long_confident.start, long_confident.end = 0.0, 3.0
    short_garbled = clean_segment("uh", avg_logprob=-2.0)
    short_garbled.start, short_garbled.end = 3.0, 4.0
    backend = FakeWhisperBackend(segments=[long_confident, short_garbled])

    result = transcribe_clip(_PATH, backend)

    expected = (3.0 * exp(-0.1) + 1.0 * exp(-2.0)) / 4.0
    assert result.confidence == expected
    assert result.text == "clear radio call uh"


def test_empty_text_despite_segments_is_flagged_low_confidence() -> None:
    backend = FakeWhisperBackend(segments=[clean_segment("   ", avg_logprob=-0.1)])

    result = transcribe_clip(_PATH, backend)

    assert result.text == ""
    assert result.low_confidence is True
    assert result.no_speech_detected is False  # segments existed; just no usable text


def test_very_short_clip_needs_no_special_case() -> None:
    backend = FakeWhisperBackend(segments=[clean_segment("Copy.", avg_logprob=-0.3)])
    result = transcribe_clip(_PATH, backend)
    assert result.text == "Copy."
