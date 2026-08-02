"""One-clip transcription via faster-whisper -- local, CPU-friendly, no
per-minute API cost (CLAUDE.md's Phase 4 budget invariant). `load_model`
loads the real thing; `transcribe_clip` wraps one model call into a
`TranscriptionResult` with a documented hallucination guard, since F1 radio
is short, noisy, jargon-heavy and often clipped, and Whisper is well known to
invent fluent-sounding text over silence/noise rather than saying nothing.

`WhisperBackend` is a narrow structural Protocol (only the two calls this
module actually makes) rather than an import of `faster_whisper.WhisperModel`
itself, so a test can substitute a fake with no dependency on the real
model/ctranslate2 being importable -- the same fake-over-mock precedent
`f1_radio.fetcher.HttpSession` sets for HTTP. `load_model` imports
`faster_whisper` lazily for the same reason: importing this module (and
running the fake-backed tests) never requires the model's heavy dependency
chain (ctranslate2, onnxruntime, av) to actually be usable, only calling
`load_model` does.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import exp
from pathlib import Path
from typing import Protocol

DEFAULT_MODEL_SIZE = "small"
DEFAULT_DEVICE = "cpu"
DEFAULT_COMPUTE_TYPE = "int8"

# Below this, a transcript is flagged low-confidence rather than trusted at
# face value -- see `transcribe_clip`'s docstring for the derivation. Not
# claimed CALIBRATED (per CLAUDE.md's own taxonomy): it is a structural
# threshold, not fit against a measured corpus of known-good/known-bad F1
# radio transcripts.
DEFAULT_LOW_CONFIDENCE_THRESHOLD = 0.5


def model_version(model_size: str = DEFAULT_MODEL_SIZE) -> str:
    """The string recorded in `radio_transcripts.model_version` -- identifies
    which model produced a row, so a future re-transcription pass (a
    different model size, say) can be told apart from this one."""
    return f"faster-whisper-{model_size}"


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start_s: float
    end_s: float
    text: str
    confidence: float
    """`exp(avg_logprob)`, clamped to [0, 1] -- see `_segment_confidence`."""


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    """Empty when no speech was detected -- never hallucinated filler."""
    segments: list[TranscriptSegment]
    language: str | None
    language_probability: float | None
    confidence: float
    """Duration-weighted mean of the segments' own confidence; 0.0 when no
    speech was detected."""
    low_confidence: bool
    """True when `confidence` fell below the threshold, or no speech was
    detected at all -- the label a caller should show rather than presenting
    the transcript as certain (CLAUDE.md's Phase 4 honesty contract)."""
    no_speech_detected: bool
    """True when the VAD pre-filter found nothing to transcribe (silence or
    noise) -- distinct from `low_confidence` alone, which can also fire on a
    genuine but mumbled/garbled exchange."""


class _WhisperSegment(Protocol):
    start: float
    end: float
    text: str
    avg_logprob: float


class _WhisperInfo(Protocol):
    language: str
    language_probability: float


class WhisperBackend(Protocol):
    """Structural match to `faster_whisper.WhisperModel`'s own `.transcribe`
    -- narrowed to what this module reads (segments + info), so a fake
    implementing just this shape is a legitimate substitute in tests. The
    first parameter is positional-only (`/`): real `WhisperModel.transcribe`
    names it `audio`, not `audio_path`, and positional-only sidesteps that
    mismatch for protocol structural matching -- this module always calls it
    positionally anyway."""

    def transcribe(
        self, audio_path: str, /, *, vad_filter: bool
    ) -> tuple[Iterable[_WhisperSegment], _WhisperInfo]: ...


def load_model(
    model_size: str = DEFAULT_MODEL_SIZE,
    *,
    device: str = DEFAULT_DEVICE,
    compute_type: str = DEFAULT_COMPUTE_TYPE,
) -> WhisperBackend:
    """Loads a real faster-whisper model (downloads it to the local HF cache
    on first use, then reused offline). `device`/`compute_type` default to a
    CPU-only, int8-quantized setup so this runs with no GPU and no per-minute
    cost -- override to `device="cuda"` on a machine that has one."""
    from faster_whisper import WhisperModel

    return WhisperModel(model_size, device=device, compute_type=compute_type)


def transcribe_clip(
    audio_path: Path,
    backend: WhisperBackend,
    *,
    low_confidence_threshold: float = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
) -> TranscriptionResult:
    """Transcribes one clip. `vad_filter=True` is the hallucination guard:
    faster-whisper's Silero-VAD pre-filter strips silence/noise BEFORE it
    ever reaches the decoder, so a genuinely silent or noise-only clip
    yields zero segments rather than the decoder inventing fluent-sounding
    text over nothing -- the specific, well-documented Whisper failure mode
    this deliverable calls out. A clip with zero segments after VAD is
    reported as `no_speech_detected=True`, `text=""`, `confidence=0.0` --
    never as a transcript.

    For genuine speech, confidence is derived from faster-whisper's own
    per-segment `avg_logprob` (a natural-log probability, so `exp(...)`
    maps it back to roughly [0, 1]), duration-weighted across segments so a
    long confident segment isn't swamped by a short garbled one. Below
    `low_confidence_threshold` (or if the recovered text is empty despite
    segments existing -- e.g. an all-punctuation/whitespace decode), the
    result is flagged `low_confidence=True` rather than presented as
    certain, matching the kernel's own "every value carries a quality
    signal" discipline (CLAUDE.md's Phase 2 invariant, reused here).

    Very short clips (F1 radio exchanges are often a couple of words) need
    no special case: faster-whisper decodes sub-second audio directly, and
    a clip too short for VAD to find any speech in falls out through the
    same zero-segments path as silence.
    """
    segments_iter, info = backend.transcribe(str(audio_path), vad_filter=True)
    segments = list(segments_iter)

    if not segments:
        return TranscriptionResult(
            text="",
            segments=[],
            language=getattr(info, "language", None),
            language_probability=getattr(info, "language_probability", None),
            confidence=0.0,
            low_confidence=True,
            no_speech_detected=True,
        )

    built = [
        TranscriptSegment(
            start_s=segment.start,
            end_s=segment.end,
            text=segment.text.strip(),
            confidence=_segment_confidence(segment),
        )
        for segment in segments
    ]
    text = " ".join(segment.text for segment in built if segment.text).strip()
    confidence = _weighted_confidence(built)
    low_confidence = confidence < low_confidence_threshold or not text

    return TranscriptionResult(
        text=text,
        segments=built,
        language=getattr(info, "language", None),
        language_probability=getattr(info, "language_probability", None),
        confidence=confidence,
        low_confidence=low_confidence,
        no_speech_detected=False,
    )


def _segment_confidence(segment: _WhisperSegment) -> float:
    return max(0.0, min(1.0, exp(segment.avg_logprob)))


def _weighted_confidence(segments: list[TranscriptSegment]) -> float:
    weights = [max(segment.end_s - segment.start_s, 1e-6) for segment in segments]
    total_weight = sum(weights)
    if total_weight <= 0:
        return sum(segment.confidence for segment in segments) / len(segments)
    return sum(w * s.confidence for w, s in zip(weights, segments, strict=True)) / total_weight
