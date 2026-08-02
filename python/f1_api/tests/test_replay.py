"""Unit tests for `f1_api.stream.replay`'s wall-clock timestamp remap — see
that module's own docstring for why `replay_envelopes` must NOT forward a
historical `DomainEvent.ts_utc` verbatim into `event_time_utc`. The checked-in
fixture is anchored at a fixed 2024-03-02 (`f1_kernel/fixtures/generate.py`'s
`SESSION_START`), which is always in the past relative to whenever this test
(or a real developer's `mode=replay` session) actually runs — so a bug here
would silently make Phase 3.3's Delay Sync gate (`event_time_utc <= wall_now
- delay`) a no-op for every historical session, not just this fixture.

Real `clock`/`sleep`/`wall_clock` are never exercised here — a fake
monotonic clock that `sleep` advances directly, and a fixed `wall_clock`,
make every assertion exact and the whole suite instant.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from api_fixture import HISTORICAL_ROOT

from f1_api.stream.replay import (
    DEFAULT_STREAM_KINDS,
    RADIO_CLIP_KIND,
    domain_event_envelope,
    envelope_for_event,
    replay_envelopes,
)
from f1_domain import LapCompleted, Provenance, SessionKey, SessionType, TeamRadioClip
from f1_kernel import HistoricalSource, MemorySource
from f1_radio.store import MemoryTranscriptStore, TranscriptRecord

SESSION_KEY = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
FAKE_WALL_START = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


class _FakeClock:
    """A monotonic clock that only ever advances when `sleep` is called —
    so a replay covering minutes of simulated race time still runs this
    test instantly, with `event_time_utc` values still exactly predictable.
    """

    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def test_domain_event_envelope_without_override_passes_through_original_ts() -> None:
    """The `mode=live` path (`f1_api.stream.websocket._stream_live`) calls
    `domain_event_envelope` with no `override_ts` — its events are already
    genuinely live, so this must stay a straight passthrough of `ts_utc`."""
    source = HistoricalSource(HISTORICAL_ROOT)
    event = next(iter(source.events(SESSION_KEY)))

    envelope = domain_event_envelope(event)

    assert envelope.event_time_utc == event.ts_utc


def test_replay_envelopes_remaps_to_wall_clock_not_the_original_2024_date() -> None:
    source = HistoricalSource(HISTORICAL_ROOT)
    fake = _FakeClock()

    envelopes = list(
        replay_envelopes(
            source,
            SESSION_KEY,
            speed=0,  # as-fast-as-possible: every event gets the same target_offset (0)
            clock=fake.now,
            sleep=fake.sleep,
            wall_clock=lambda: FAKE_WALL_START,
        )
    )

    assert envelopes, "expected at least one envelope"
    for envelope in envelopes:
        assert envelope.event_time_utc == FAKE_WALL_START
        assert envelope.event_time_utc.year == 2026, (
            "event_time_utc must track the injected wall clock, not the fixture's "
            "fixed 2024-03-02 SESSION_START — a live-2026 comparison against a "
            "2024 timestamp would release every envelope instantly regardless of "
            "delay, defeating Delay Sync's playback-clock gate"
        )


def test_replay_envelopes_paces_remapped_timestamps_by_speed() -> None:
    """Isolates the speed-scaling formula from anchoring (`anchor_to_first_lap=
    False`, see the dedicated anchor tests below) — this test's job is the
    arithmetic, not which event counts as elapsed=0."""
    source = HistoricalSource(HISTORICAL_ROOT)
    # `replay_envelopes` defaults to `DEFAULT_STREAM_KINDS` (excludes
    # car_telemetry — see f1_api.stream.replay's module docstring), so the
    # ground-truth event list here must be filtered the same way to compare
    # like for like.
    raw_events = sorted(
        source.events(SESSION_KEY, kinds=DEFAULT_STREAM_KINDS), key=lambda event: event.ts_utc
    )
    start_ts = raw_events[0].ts_utc

    fake = _FakeClock()
    speed = 4.0
    domain_envelopes = [
        envelope
        for envelope in replay_envelopes(
            source,
            SESSION_KEY,
            speed=speed,
            anchor_to_first_lap=False,
            clock=fake.now,
            sleep=fake.sleep,
            wall_clock=lambda: FAKE_WALL_START,
        )
        if envelope.kind != "kernel_snapshot"
    ]

    assert len(domain_envelopes) == len(raw_events)
    for raw_event, envelope in zip(raw_events, domain_envelopes, strict=True):
        expected_offset_s = (raw_event.ts_utc - start_ts).total_seconds() / speed
        actual_offset_s = (envelope.event_time_utc - FAKE_WALL_START).total_seconds()
        assert actual_offset_s == pytest.approx(expected_offset_s, abs=1e-6)

    # The remap must preserve order: event_time_utc never goes backwards.
    times = [envelope.event_time_utc for envelope in domain_envelopes]
    assert times == sorted(times)


def test_replay_envelopes_excludes_car_telemetry_by_default() -> None:
    """SESSION_KEY (2024-1-race) is the one fixture race with a car_telemetry
    table (api_fixture.py) — the regression case: an unfiltered
    `source.events()` call would include it, and streaming that over the
    WebSocket by default is what pegged the browser tab against a real
    race (docs/PHASE3.md's WebSocket section)."""
    source = HistoricalSource(HISTORICAL_ROOT)
    fake = _FakeClock()

    envelopes = list(
        replay_envelopes(
            source,
            SESSION_KEY,
            speed=0,
            clock=fake.now,
            sleep=fake.sleep,
            wall_clock=lambda: FAKE_WALL_START,
        )
    )

    assert envelopes, "expected at least one envelope"
    assert all(envelope.kind != "car_telemetry" for envelope in envelopes)


def test_replay_envelopes_includes_car_telemetry_on_request() -> None:
    """The on-demand escape hatch: passing `kinds=None` (what
    `include_telemetry=true` maps to at the WebSocket route) still streams
    it — excluding it by default must not delete the capability."""
    source = HistoricalSource(HISTORICAL_ROOT)
    fake = _FakeClock()

    envelopes = list(
        replay_envelopes(
            source,
            SESSION_KEY,
            speed=0,
            kinds=None,
            clock=fake.now,
            sleep=fake.sleep,
            wall_clock=lambda: FAKE_WALL_START,
        )
    )

    assert any(envelope.kind == "car_telemetry" for envelope in envelopes)


def test_replay_envelopes_anchors_pacing_to_first_lap_by_default() -> None:
    """The fixture's own first event (a `stint_started`) sits ~98s before its
    first `lap_completed` (checked directly against the fixture data). With
    the default anchor, that first `lap_completed` should land at
    (approximately) target_offset=0 — i.e. `event_time_utc == FAKE_WALL_START`
    — rather than ~98s after it, and every pre-anchor event should arrive at
    or before FAKE_WALL_START (a non-positive target_offset, per this
    module's docstring on why that's a burst, not a block)."""
    source = HistoricalSource(HISTORICAL_ROOT)
    fake = _FakeClock()

    envelopes = [
        envelope
        for envelope in replay_envelopes(
            source,
            SESSION_KEY,
            speed=4.0,
            clock=fake.now,
            sleep=fake.sleep,
            wall_clock=lambda: FAKE_WALL_START,
        )
        if envelope.kind != "kernel_snapshot"
    ]
    lap_completed_envelopes = [e for e in envelopes if e.kind == "lap_completed"]

    assert lap_completed_envelopes, "expected at least one lap_completed envelope"
    first_lap_envelope = lap_completed_envelopes[0]
    assert first_lap_envelope.event_time_utc == FAKE_WALL_START

    pre_anchor_envelopes = envelopes[: envelopes.index(first_lap_envelope)]
    assert pre_anchor_envelopes, "expected at least one pre-anchor envelope in this fixture"
    for envelope in pre_anchor_envelopes:
        assert envelope.event_time_utc <= FAKE_WALL_START


def test_replay_envelopes_anchor_can_be_disabled() -> None:
    """`anchor_to_first_lap=False` is the on-demand escape hatch back to raw,
    unanchored pacing (mirrors `kinds=None` for telemetry) — the first
    lap_completed should NOT land at target_offset=0 here, it should land
    ~98s/speed after the stream's own first event, same as before this
    module ever anchored anything."""
    source = HistoricalSource(HISTORICAL_ROOT)
    fake = _FakeClock()

    envelopes = [
        envelope
        for envelope in replay_envelopes(
            source,
            SESSION_KEY,
            speed=4.0,
            anchor_to_first_lap=False,
            clock=fake.now,
            sleep=fake.sleep,
            wall_clock=lambda: FAKE_WALL_START,
        )
        if envelope.kind != "kernel_snapshot"
    ]
    first_lap_envelope = next(e for e in envelopes if e.kind == "lap_completed")

    assert first_lap_envelope.event_time_utc > FAKE_WALL_START


def test_replay_envelopes_kernel_snapshots_share_the_remapped_timeline() -> None:
    source = HistoricalSource(HISTORICAL_ROOT)
    fake = _FakeClock()

    envelopes = list(
        replay_envelopes(
            source,
            SESSION_KEY,
            speed=0,
            clock=fake.now,
            sleep=fake.sleep,
            wall_clock=lambda: FAKE_WALL_START,
        )
    )
    snapshots = [envelope for envelope in envelopes if envelope.kind == "kernel_snapshot"]

    assert snapshots, "expected at least one kernel_snapshot for this fixture race"
    for snapshot in snapshots:
        assert snapshot.event_time_utc == FAKE_WALL_START


# -- Phase 4.4: team radio joins the timeline --------------------------------
#
# The checked-in kernel fixture carries no `team_radio_clip` events at all
# (no `team_radio.parquet` — no fixture race has been backfilled with any),
# so these tests build a small synthetic session directly via `MemorySource`
# rather than `HistoricalSource`, the same "no DB-free real corpus" reality
# `f1_api.radio`'s own module docstring documents.

RADIO_KEY = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
_LAP_TS = datetime(2024, 3, 2, 15, 0, 0, tzinfo=UTC)


def _radio_events() -> list[LapCompleted | TeamRadioClip]:
    lap = LapCompleted(
        session_key=RADIO_KEY,
        ts_utc=_LAP_TS,
        source_seq=0,
        provenance=Provenance.HISTORICAL,
        driver="44",
        lap_number=1,
    )
    # ts_utc (pipeline-observed time) and captured_at (when the exchange
    # actually happened) are deliberately different here -- the whole point
    # of these tests is proving pacing follows captured_at, not ts_utc.
    clip = TeamRadioClip(
        session_key=RADIO_KEY,
        ts_utc=_LAP_TS + timedelta(seconds=5),
        source_seq=1,
        provenance=Provenance.HISTORICAL,
        driver="44",
        audio_url="https://example.com/radio/44_1.mp3",
        captured_at=_LAP_TS + timedelta(seconds=2),
    )
    return [lap, clip]


def test_radio_clip_envelope_kind_is_distinct_from_the_raw_domain_event() -> None:
    source = MemorySource(_radio_events())
    fake = _FakeClock()

    envelopes = list(
        replay_envelopes(
            source,
            RADIO_KEY,
            speed=1.0,
            anchor_to_first_lap=True,
            clock=fake.now,
            sleep=fake.sleep,
            wall_clock=lambda: FAKE_WALL_START,
        )
    )

    kinds = {e.kind for e in envelopes}
    assert "team_radio_clip" not in kinds
    assert RADIO_CLIP_KIND in kinds


def test_radio_clip_pacing_follows_captured_at_not_ts_utc() -> None:
    """`captured_at` is 2s after the anchoring lap; `ts_utc` is 5s after it.
    With speed=1, delay=0, `pacing_offset_s` is the identity — so the clip's
    remapped `event_time_utc` must land at FAKE_WALL_START + 2s, never +5s."""
    source = MemorySource(_radio_events())
    fake = _FakeClock()

    envelopes = list(
        replay_envelopes(
            source,
            RADIO_KEY,
            speed=1.0,
            anchor_to_first_lap=True,
            clock=fake.now,
            sleep=fake.sleep,
            wall_clock=lambda: FAKE_WALL_START,
        )
    )
    radio_envelope = next(e for e in envelopes if e.kind == RADIO_CLIP_KIND)

    assert radio_envelope.event_time_utc == FAKE_WALL_START + timedelta(seconds=2)


def test_radio_clip_envelope_joins_transcript_when_radio_store_has_one() -> None:
    clip_event = next(e for e in _radio_events() if isinstance(e, TeamRadioClip))
    store = MemoryTranscriptStore()
    store.insert(
        TranscriptRecord(
            session_key=RADIO_KEY,
            driver="44",
            captured_at=clip_event.captured_at,
            audio_path="44_1.mp3",
            transcript_text="box this lap, box box",
            language="en",
            confidence=0.92,
            model_version="faster-whisper-small",
            segments=[{"start_s": 0.0, "end_s": 1.5, "text": "box this lap", "confidence": 0.9}],
            lap_number=1,
            position=3,
            track_status="green",
            track_status_flags=[],
        )
    )
    source = MemorySource(_radio_events())
    fake = _FakeClock()

    envelopes = list(
        replay_envelopes(
            source,
            RADIO_KEY,
            speed=1.0,
            radio_store=store,
            clock=fake.now,
            sleep=fake.sleep,
            wall_clock=lambda: FAKE_WALL_START,
        )
    )
    radio_envelope = next(e for e in envelopes if e.kind == RADIO_CLIP_KIND)

    assert radio_envelope.payload["has_transcript"] is True
    assert radio_envelope.payload["transcript_text"] == "box this lap, box box"
    assert radio_envelope.payload["lap_number"] == 1
    assert radio_envelope.payload["track_status"] == "green"
    assert radio_envelope.payload["audio_url"] == "https://example.com/radio/44_1.mp3"


def test_radio_clip_envelope_degrades_gracefully_with_no_radio_store() -> None:
    source = MemorySource(_radio_events())
    fake = _FakeClock()

    envelopes = list(
        replay_envelopes(
            source,
            RADIO_KEY,
            speed=1.0,
            radio_store=None,
            clock=fake.now,
            sleep=fake.sleep,
            wall_clock=lambda: FAKE_WALL_START,
        )
    )
    radio_envelope = next(e for e in envelopes if e.kind == RADIO_CLIP_KIND)

    assert radio_envelope.payload["has_transcript"] is False
    assert radio_envelope.payload["transcript_text"] is None
    assert radio_envelope.payload["audio_url"] == "https://example.com/radio/44_1.mp3"


def test_envelope_for_event_live_mode_uses_captured_at_over_ts_utc() -> None:
    """`mode=live`'s polling loop calls `envelope_for_event` with no
    `override_ts` — a radio clip should still gate on its own `captured_at`
    (the real, already-observed moment the exchange happened), not the
    pipeline's `ts_utc`, since that is what CLAUDE.md's Phase 4 spoiler-
    safety invariant is actually about."""
    clip_event = next(e for e in _radio_events() if isinstance(e, TeamRadioClip))

    envelope = envelope_for_event(clip_event, radio_store=None)

    assert envelope.event_time_utc == clip_event.captured_at
    assert envelope.event_time_utc != clip_event.ts_utc
