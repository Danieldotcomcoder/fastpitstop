"""Phase 0 verification harness — the `just verify-phase0` entrypoint.

Generates a small synthetic recording via `f1_recording.RecordingWriter`,
replays it through `f1_replay.replay` at `speed=0`, and asserts the
round-trip invariant: the record stream that comes out of the replayer is
exactly the record stream that was written — same count, same order, same
`seq`/`topic`/`payload`/timestamps — preceded only by the synthetic
subscribe-snapshot record carrying the recording's `subscribe.json`
verbatim.

That invariant IS the contract between the recorder and the replayer
(CLAUDE.md, "Hard architectural invariants": both sides depend on
`f1_recording`, nobody invents their own format). If either side ever
drifts — a field renamed, a record dropped, ordering broken, a payload
mutated in flight — this fails loudly with the first mismatching record,
and CI goes red (`just check` runs it).
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from pydantic import JsonValue

from f1_recording import Manifest, RawRecord, RecordingReader, RecordingWriter
from f1_replay.replayer import SUBSCRIBE_SNAPSHOT_TOPIC, Sink, replay
from f1_replay.sinks import list_sink

ReplayFn = Callable[[RecordingReader, Sink], None]
"""Injectable replay runner: `verify_roundtrip` defaults to the real
`replay(..., speed=0)`; tests inject a deliberately-broken one to prove a
contract violation is actually detected (the harness itself needs a test
that it fails when it should)."""


class VerificationError(AssertionError):
    """The recorder<->replayer round-trip invariant was violated."""


_SUBSCRIBE_SNAPSHOT: JsonValue = {
    "SessionInfo": {"Meeting": {"Name": "Synthetic Grand Prix"}, "Type": "Race"},
    "TimingData": {"Lines": {"1": {"Position": "1"}}},
}

# Deliberately varied payload shapes: nested dicts, lists, empty strings,
# unicode, and a raw base64 string standing in for a compressed `.z` topic
# (which the format stores verbatim, never decoded — see DATA_CONTRACTS.md).
_MESSAGES: list[tuple[str, JsonValue]] = [
    ("TrackStatus", {"Status": "1", "Message": "AllClear"}),
    ("TimingData", {"Lines": {"1": {"GapToLeader": "", "Sectors": [{"Value": "28.4"}]}}}),
    ("CarData.z", "eJxLzs8rzs9J1XPMS8xJLE4FAB0JBKE="),
    ("WeatherData", {"AirTemp": "23.4", "Rainfall": "0"}),
    ("RaceControlMessages", {"Messages": [{"Category": "Flag", "Flag": "GREEN"}]}),
    ("TeamRadio", {"Captures": [{"Path": "TeamRadio/MAXVER01_1_20240302_154321.mp3"}]}),
    ("TimingData", {"Lines": {"1": {"GapToLeader": "+1.234", "Driver": "Ferstàppen"}}}),
]


def generate_synthetic_recording(directory: Path) -> RecordingReader:
    """Write a small, fixed synthetic recording and return a reader for it.

    Timestamps are fixed (250ms apart) rather than real-clock so the
    recording is byte-deterministic across runs — the harness verifies the
    contract, not the wall clock.
    """
    manifest = Manifest.model_validate(
        {
            "year": 2024,
            "grand_prix": "Synthetic Grand Prix",
            "session_type": "Race",
            "session_start_utc": "2024-01-01T00:00:00Z",
            "recorder_version": "verify-phase0",
            "topics": sorted({topic for topic, _ in _MESSAGES}),
        }
    )
    with RecordingWriter(directory, manifest, subscribe_snapshot=_SUBSCRIBE_SNAPSHOT) as writer:
        for i, (topic, payload) in enumerate(_MESSAGES):
            writer.append(
                topic,
                payload,
                ts_wall=1_704_067_200.0 + i * 0.25,
                ts_mono=i * 250_000_000,
            )
    return RecordingReader(directory)


def verify_roundtrip(reader: RecordingReader, *, replay_fn: ReplayFn | None = None) -> int:
    """Assert record-stream-out == record-stream-in for `reader`.

    Replays the recording (at `speed=0` by default) into a buffer, then
    compares the emission against a fresh read of the recording itself:
    snapshot first and verbatim, then every stream record in order with
    identical fields. Returns the number of stream records verified;
    raises `VerificationError` naming the first mismatch otherwise.
    """
    emitted: list[RawRecord] = []
    if replay_fn is None:
        replay(reader, list_sink(emitted), speed=0)
    else:
        replay_fn(reader, list_sink(emitted))

    if not emitted:
        raise VerificationError("replay emitted nothing, not even the subscribe snapshot")

    snapshot = emitted[0]
    if snapshot.topic != SUBSCRIBE_SNAPSHOT_TOPIC:
        raise VerificationError(
            f"first emission must be the subscribe snapshot "
            f"({SUBSCRIBE_SNAPSHOT_TOPIC!r}), got topic {snapshot.topic!r}"
        )
    if snapshot.payload != reader.subscribe_snapshot:
        raise VerificationError(
            "subscribe snapshot payload was not passed through verbatim:\n"
            f"  written:  {reader.subscribe_snapshot!r}\n"
            f"  replayed: {snapshot.payload!r}"
        )

    stream_out = emitted[1:]
    expected = list(reader.records())
    if len(stream_out) != len(expected):
        raise VerificationError(
            f"record count mismatch: recording contains {len(expected)} stream "
            f"records, replay emitted {len(stream_out)}"
        )
    for i, (out, src) in enumerate(zip(stream_out, expected, strict=True)):
        if out != src:
            raise VerificationError(
                f"stream record {i} mismatch:\n  written:  {src!r}\n  replayed: {out!r}"
            )
    return len(expected)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        reader = generate_synthetic_recording(Path(tmp) / "synthetic")
        try:
            count = verify_roundtrip(reader)
        except VerificationError as exc:
            print(
                f"PHASE 0 VERIFY: FAILED - the recorder<->replayer contract is broken:\n{exc}",
                file=sys.stderr,
            )
            return 1
    print(
        f"PHASE 0 VERIFY: OK - {count} stream records + subscribe snapshot "
        "round-tripped through f1_recording -> f1_replay with identical order "
        "and payloads."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
