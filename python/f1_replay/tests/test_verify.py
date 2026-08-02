"""Tests for the Phase 0 verification harness itself.

The harness is what CI trusts to catch a broken recorder<->replayer
contract, so it needs proof in both directions: it passes on a faithful
replay, and it actually FAILS when fed a replay that violates the contract
(drops a record, reorders, mutates a payload, loses the snapshot). A
verifier that can't fail verifies nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from f1_recording import RawRecord, RecordingReader
from f1_replay.replayer import Sink, replay
from f1_replay.verify import (
    VerificationError,
    generate_synthetic_recording,
    main,
    verify_roundtrip,
)


@pytest.fixture
def reader(tmp_path: Path) -> RecordingReader:
    return generate_synthetic_recording(tmp_path / "synthetic")


def test_faithful_replay_passes(reader: RecordingReader) -> None:
    count = verify_roundtrip(reader)
    assert count == len(list(reader.records()))
    assert count > 0


def test_main_exit_code_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main() == 0
    assert "PHASE 0 VERIFY: OK" in capsys.readouterr().out


def test_detects_dropped_record(reader: RecordingReader) -> None:
    def dropping_replay(r: RecordingReader, sink: Sink) -> None:
        emitted: list[RawRecord] = []
        replay(r, emitted.append, speed=0)
        for record in emitted[:-1]:  # silently lose the last record
            sink(record)

    with pytest.raises(VerificationError, match="count mismatch"):
        verify_roundtrip(reader, replay_fn=dropping_replay)


def test_detects_reordering(reader: RecordingReader) -> None:
    def reordering_replay(r: RecordingReader, sink: Sink) -> None:
        emitted: list[RawRecord] = []
        replay(r, emitted.append, speed=0)
        snapshot, stream = emitted[0], emitted[1:]
        sink(snapshot)
        for record in reversed(stream):
            sink(record)

    with pytest.raises(VerificationError, match="mismatch"):
        verify_roundtrip(reader, replay_fn=reordering_replay)


def test_detects_mutated_payload(reader: RecordingReader) -> None:
    def mutating_replay(r: RecordingReader, sink: Sink) -> None:
        emitted: list[RawRecord] = []
        replay(r, emitted.append, speed=0)
        sink(emitted[0])
        for i, record in enumerate(emitted[1:]):
            if i == 1:
                record = record.model_copy(update={"payload": {"tampered": True}})
            sink(record)

    with pytest.raises(VerificationError, match="stream record 1 mismatch"):
        verify_roundtrip(reader, replay_fn=mutating_replay)


def test_detects_missing_snapshot(reader: RecordingReader) -> None:
    def snapshotless_replay(r: RecordingReader, sink: Sink) -> None:
        emitted: list[RawRecord] = []
        replay(r, emitted.append, speed=0)
        for record in emitted[1:]:
            sink(record)

    with pytest.raises(VerificationError, match="subscribe snapshot"):
        verify_roundtrip(reader, replay_fn=snapshotless_replay)


def test_detects_empty_replay(reader: RecordingReader) -> None:
    def silent_replay(_r: RecordingReader, _sink: Sink) -> None:
        pass

    with pytest.raises(VerificationError, match="emitted nothing"):
        verify_roundtrip(reader, replay_fn=silent_replay)


def test_synthetic_recording_is_deterministic(tmp_path: Path) -> None:
    a = generate_synthetic_recording(tmp_path / "a")
    b = generate_synthetic_recording(tmp_path / "b")
    assert (a.directory / "stream.jsonl").read_text(encoding="utf-8") == (
        b.directory / "stream.jsonl"
    ).read_text(encoding="utf-8")
    assert a.manifest == b.manifest
