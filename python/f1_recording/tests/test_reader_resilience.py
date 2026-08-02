"""Crash-resilience tests: a partially-written last line must not be fatal."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from f1_recording import Manifest, RecordingReader, RecordingWriter

ManifestFactory = Callable[..., Manifest]


def _write_two_records(directory: Path, manifest_factory: ManifestFactory) -> Path:
    with RecordingWriter(directory, manifest_factory(), subscribe_snapshot={}) as writer:
        writer.append("TimingData", {"a": 1})
        writer.append("TimingData", {"a": 2})
    return directory / "stream.jsonl"


def test_tolerates_truncated_final_line(tmp_path: Path, manifest_factory: ManifestFactory) -> None:
    directory = tmp_path / "session"
    stream_path = _write_two_records(directory, manifest_factory)

    lines = stream_path.read_text(encoding="utf-8").splitlines(keepends=True)
    truncated_last = lines[-1][: len(lines[-1]) // 2]
    stream_path.write_text("".join(lines[:-1]) + truncated_last, encoding="utf-8")

    records = list(RecordingReader(directory).records())

    assert [r.seq for r in records] == [0]


def test_tolerates_dangling_newline_at_end_of_file(
    tmp_path: Path, manifest_factory: ManifestFactory
) -> None:
    directory = tmp_path / "session"
    stream_path = _write_two_records(directory, manifest_factory)

    with stream_path.open("a", encoding="utf-8") as f:
        f.write("\n")

    records = list(RecordingReader(directory).records())

    assert [r.seq for r in records] == [0, 1]


def test_corruption_before_the_last_line_still_raises(
    tmp_path: Path, manifest_factory: ManifestFactory
) -> None:
    directory = tmp_path / "session"
    stream_path = _write_two_records(directory, manifest_factory)

    lines = stream_path.read_text(encoding="utf-8").splitlines(keepends=True)
    lines[0] = "{not valid json\n"
    stream_path.write_text("".join(lines), encoding="utf-8")

    with pytest.raises(ValidationError):
        list(RecordingReader(directory).records())
