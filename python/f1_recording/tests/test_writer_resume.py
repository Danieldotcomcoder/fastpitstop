"""Reopening a recording continues it.

The live recorder restarts into the same directory (a deploy, a crash, the
VM being reset). It used to start `seq` again at 0, so a recording's
sequence went backwards mid-stream and the live follower, which then
tracked events by sequence, never delivered another event. The 2026
Bahrain GP qualifying froze for every viewer after one worker restart.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from f1_recording import Manifest, RecordingReader, RecordingWriter
from f1_recording.models import STREAM_FILENAME


def _manifest() -> Manifest:
    return Manifest(
        format_version=1,
        year=2026,
        grand_prix="Bahrain Grand Prix",
        session_type="Qualifying",
        session_start_utc=datetime(2026, 10, 3, 8, 0, tzinfo=UTC),
        recorder_version="test",
        topics=["TimingData"],
    )


def _write(directory: Path, count: int) -> list[int]:
    with RecordingWriter(directory, _manifest(), {"SessionInfo": {}}) as writer:
        return [writer.append("TimingData", {"n": i}).seq for i in range(count)]


def test_a_reopened_recording_continues_its_sequence(tmp_path: Path) -> None:
    assert _write(tmp_path, 3) == [0, 1, 2]
    assert _write(tmp_path, 2) == [3, 4]
    seqs = [record.seq for record in RecordingReader(tmp_path).records()]
    assert seqs == [0, 1, 2, 3, 4]


def test_a_torn_final_line_is_trimmed_before_appending(tmp_path: Path) -> None:
    """A crash mid-write leaves half a record. Appending after it would glue
    the next record on, making a corrupt line mid-file that the reader
    rejects; the torn record is dropped instead (the reader already ignores
    it), and the sequence continues from the last complete one."""
    _write(tmp_path, 2)
    stream = tmp_path / STREAM_FILENAME
    with stream.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 2, "ts_wall": 1.0, "ts_m')
    assert _write(tmp_path, 1) == [2]
    seqs = [record.seq for record in RecordingReader(tmp_path).records()]
    assert seqs == [0, 1, 2]


def test_a_last_record_longer_than_the_read_window_is_found(tmp_path: Path) -> None:
    with RecordingWriter(tmp_path, _manifest(), {}) as writer:
        writer.append("TimingData", {"n": 0})
        writer.append("TimingData", {"blob": "x" * (3 << 20)})  # 3 MB record
    assert _write(tmp_path, 1) == [2]


def test_an_empty_or_new_stream_starts_at_zero(tmp_path: Path) -> None:
    (tmp_path / STREAM_FILENAME).write_text("", encoding="utf-8")
    assert _write(tmp_path, 1) == [0]
