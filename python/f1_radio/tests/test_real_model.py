"""Opt-in: `transcribe_clip` against a REAL faster-whisper model and the
checked-in synthetic audio fixtures (`tests/fixtures/audio/`) -- the
deliverable's own "a handful of checked-in sample clips transcribe to
non-empty text... a silent/noise clip is flagged low-confidence and does NOT
produce hallucinated content" requirement, proven for real rather than only
against the fake backend `test_transcription.py` uses.

Every fixture clip is SYNTHESIZED, not real F1 broadcast audio (Windows SAPI
text-to-speech for the two speech clips; generated silence/white-noise PCM
for the other two) -- clean of any broadcast-rights concern, per CLAUDE.md's
legal posture, while still exercising the real model end-to-end.

Gated (skipped by default): downloads a ~75MB model from the Hugging Face
Hub on first run and needs real CPU time, the same "not exercisable in every
environment" reality this project already accepts for the live feed
(`docs/PHASE0.md`) and the database (`test_migrations.py`'s
`F1_STORE_RUN_DB_TESTS`). Opt in with:

    F1_RADIO_RUN_MODEL_TESTS=1 uv run pytest python/f1_radio/tests/test_real_model.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from f1_radio.transcription import load_model, transcribe_clip

pytestmark = pytest.mark.skipif(
    os.environ.get("F1_RADIO_RUN_MODEL_TESTS") != "1",
    reason="model-gated: set F1_RADIO_RUN_MODEL_TESTS=1 to download+run a real model",
)

_AUDIO_DIR = Path(__file__).parent / "fixtures" / "audio"

# "tiny" rather than the production default ("small") -- fast enough for a
# gated-but-still-CI-runnable test; the confidence/hallucination-guard logic
# under test is identical across model sizes.
_TEST_MODEL_SIZE = "tiny"


@pytest.fixture(scope="module")
def backend():
    return load_model(_TEST_MODEL_SIZE, device="cpu", compute_type="int8")


def test_clean_speech_clip_transcribes_to_non_empty_text(backend: object) -> None:
    result = transcribe_clip(_AUDIO_DIR / "clean_radio.wav", backend)  # type: ignore[arg-type]

    assert result.text.strip() != ""
    assert result.language is not None
    assert result.confidence > 0.0
    assert result.no_speech_detected is False


def test_short_clip_also_transcribes_to_non_empty_text(backend: object) -> None:
    result = transcribe_clip(_AUDIO_DIR / "short_clip.wav", backend)  # type: ignore[arg-type]

    assert result.text.strip() != ""
    assert result.no_speech_detected is False


def test_silence_clip_is_flagged_low_confidence_with_no_hallucinated_text(
    backend: object,
) -> None:
    result = transcribe_clip(_AUDIO_DIR / "silence.wav", backend)  # type: ignore[arg-type]

    assert result.text == ""
    assert result.no_speech_detected is True
    assert result.low_confidence is True
    assert result.confidence == 0.0


def test_noise_clip_is_flagged_low_confidence_with_no_hallucinated_text(backend: object) -> None:
    result = transcribe_clip(_AUDIO_DIR / "noise.wav", backend)  # type: ignore[arg-type]

    assert result.text == ""
    assert result.no_speech_detected is True
    assert result.low_confidence is True
    assert result.confidence == 0.0
