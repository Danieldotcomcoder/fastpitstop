"""Roundtrip tests: what's written is exactly what's read back.

Uses hypothesis to generate arbitrary JSON-shaped payloads and topic names,
since the contract this package guarantees is "any JSON value survives
unchanged" — not just the handful of shapes a hand-written example would
cover.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import JsonValue

from f1_recording import Manifest, RecordingReader, RecordingWriter

ManifestFactory = Callable[..., Manifest]


def _static_manifest() -> Manifest:
    # A plain, side-effect-free constructor (not the `manifest_factory`
    # fixture) — hypothesis flags function-scoped fixtures as unsafe to
    # reuse across generated examples, but this needs no per-example reset.
    return Manifest.model_validate(
        {
            "year": 2024,
            "grand_prix": "Test Grand Prix",
            "session_type": "Race",
            "session_start_utc": "2024-01-01T00:00:00Z",
            "recorder_version": "test",
            "topics": ["TimingData"],
        }
    )


_json_scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-1_000_000, max_value=1_000_000),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.text(max_size=20),
)

_json_values: st.SearchStrategy[Any] = st.recursive(
    _json_scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(max_size=10), children, max_size=4),
    ),
    max_leaves=8,
)

_topics = st.sampled_from(
    [
        "TimingData",
        "CarData.z",
        "Position.z",
        "TeamRadio",
        "RaceControlMessages",
        "WeatherData",
        "TrackStatus",
    ]
)

_messages = st.lists(st.tuples(_topics, _json_values), max_size=25)


@given(messages=_messages)
@settings(max_examples=50, deadline=None)
def test_roundtrip_preserves_seq_order_and_payload(
    messages: list[tuple[str, Any]],
) -> None:
    # A fresh temp dir per hypothesis example — a shared pytest tmp_path
    # fixture would be reused (and thus polluted) across examples.
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp) / "session"
        with RecordingWriter(directory, _static_manifest(), subscribe_snapshot={}) as writer:
            for topic, payload in messages:
                writer.append(topic, payload)

        records = list(RecordingReader(directory).records())

        assert len(records) == len(messages)
        for i, (record, (topic, payload)) in enumerate(zip(records, messages, strict=True)):
            assert record.seq == i
            assert record.topic == topic
            assert record.payload == payload


def test_manifest_and_subscribe_snapshot_roundtrip(
    tmp_path: Path, manifest_factory: ManifestFactory
) -> None:
    manifest = manifest_factory()
    snapshot: JsonValue = {
        "SessionInfo": {"Meeting": {"Name": "Test Grand Prix"}},
        "TimingData": {"Lines": {}},
    }
    directory = tmp_path / "session"

    RecordingWriter(directory, manifest, subscribe_snapshot=snapshot).close()

    reader = RecordingReader(directory)
    assert reader.manifest == manifest
    assert reader.subscribe_snapshot == snapshot


def test_seq_is_assigned_even_without_explicit_timestamps(
    tmp_path: Path, manifest_factory: ManifestFactory
) -> None:
    directory = tmp_path / "session"
    with RecordingWriter(directory, manifest_factory(), subscribe_snapshot={}) as writer:
        first = writer.append("TrackStatus", {"Status": "1"})
        second = writer.append("TrackStatus", {"Status": "2"})

    assert (first.seq, second.seq) == (0, 1)
    assert first.ts_mono <= second.ts_mono
