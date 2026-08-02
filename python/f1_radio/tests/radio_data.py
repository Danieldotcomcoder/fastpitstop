"""Shared test builders: a `TeamRadioClip` event factory, a fake HTTP
session/response pair (structural, matching `f1_radio.fetcher.HttpSession`/
`HttpResponse` -- no mocking framework needed, mirroring
`f1_backfill/tests/test_openf1.py`'s own fakes), a fake
`f1_radio.transcription.WhisperBackend`, and a fake
`f1_radio.embeddings.EmbeddingBackend` (same fake-over-mock precedent, so
`transcribe_clip`/`run_batch`/`run_index_batch`/`search_transcripts` tests
need neither a real model nor real audio)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from f1_backfill.rate_limiter import RateLimiter
from f1_domain import Provenance, SessionKey, SessionType, TeamRadioClip

KEY = SessionKey(year=2024, round=1, session_type=SessionType.RACE)


def make_clip(
    *,
    driver: str | None = "44",
    audio_url: str | None = "https://example.com/radio/44_1.mp3",
    captured_at: datetime | None = datetime(2024, 3, 2, 15, 0, 0, tzinfo=UTC),
    source_seq: int = 0,
    session_key: SessionKey = KEY,
) -> TeamRadioClip:
    ts = captured_at if captured_at is not None else datetime(2024, 3, 2, 15, 0, 0, tzinfo=UTC)
    return TeamRadioClip(
        session_key=session_key,
        ts_utc=ts,
        source_seq=source_seq,
        provenance=Provenance.HISTORICAL,
        driver=driver,
        audio_url=audio_url,
        captured_at=captured_at,
    )


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeResponse:
    def __init__(
        self, status_code: int, content: bytes = b"", headers: Mapping[str, str] | None = None
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.headers: Mapping[str, str] = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Replays a scripted list of responses, one per `.get()` call, and
    records every call for assertions."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []

    def get(self, url: str, *, timeout: float | None = None) -> FakeResponse:
        self.calls.append(url)
        return self._responses.pop(0)


class RecordingRateLimiter(RateLimiter):
    """A real `RateLimiter` (so it still type-checks everywhere one is
    expected) that also counts `acquire()` calls, so a test can assert every
    HTTP attempt -- including retries -- went through the limiter, without
    re-exercising the sliding-window math `f1_backfill`'s own `RateLimiter`
    tests already cover."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.acquire_count = 0

    def acquire(self) -> None:
        self.acquire_count += 1
        super().acquire()


@dataclass
class FakeWhisperSegment:
    start: float
    end: float
    text: str
    avg_logprob: float


@dataclass
class FakeWhisperInfo:
    language: str = "en"
    language_probability: float = 0.95


@dataclass
class FakeWhisperBackend:
    """A `WhisperBackend` returning one canned `(segments, info)` result for
    EVERY call, or -- if `by_path` is given -- a different one per audio
    path (keyed by `str(path)`), so a batch test can give each clip in a
    small corpus a distinct outcome. Records every path it was asked to
    transcribe, so a test can assert model reuse / call counts."""

    segments: list[FakeWhisperSegment] = field(default_factory=list[FakeWhisperSegment])
    info: FakeWhisperInfo = field(default_factory=FakeWhisperInfo)
    by_path: dict[str, tuple[list[FakeWhisperSegment], FakeWhisperInfo]] | None = None
    calls: list[str] = field(default_factory=list[str])

    def transcribe(
        self, audio_path: str, /, *, vad_filter: bool
    ) -> tuple[Iterable[FakeWhisperSegment], FakeWhisperInfo]:
        del vad_filter
        self.calls.append(audio_path)
        if self.by_path is not None:
            return self.by_path[audio_path]
        return self.segments, self.info


def clean_segment(
    text: str = "Box this lap, box box.", avg_logprob: float = -0.2
) -> FakeWhisperSegment:
    return FakeWhisperSegment(start=0.0, end=1.8, text=text, avg_logprob=avg_logprob)


@dataclass
class FakeEmbeddingBackend:
    """An `EmbeddingBackend` whose vectors are fully controlled by the test:
    `vectors` maps an exact input text to the vector it should embed to, so
    a test can plant "these two differently-worded texts are semantically
    close" (near-identical vectors) or "these are unrelated" (near-orthogonal
    vectors) directly, without depending on a real model's actual output --
    the same scripted-fake precedent `FakeWhisperBackend` sets. A text with
    no entry falls back to `default`. Records every batch it was asked to
    encode, so a test can assert the model was (or was not) called."""

    vectors: dict[str, list[float]] = field(default_factory=dict[str, list[float]])
    default: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    calls: list[list[str]] = field(default_factory=list[list[str]])

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self.vectors.get(text, self.default) for text in texts]
