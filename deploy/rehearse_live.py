"""Rehearse the live path on a weekday: the real `f1-pipeline live`, with
a recorded session standing in for F1's socket.

Live is the product, and only ~24 weekends a year exercise it. This plays a
raw recording (`f1_recording` format, as the worker writes them) into the
REAL `f1_pipeline.cli` live command, so everything after the socket runs as
on a race day: the state machine, the store, the API's live hub, the
browser. Records keep their recorded spacing, divided by `--speed`; the
pre-session hour is skipped (sent at once) up to `--lead` seconds before
lights out.

Run it on the VM inside the api image, against a scratch database (never
production's): see docs/ROADMAP.md W6 for the runbook.

    python deploy/rehearse_live.py --recording /data/recordings/<session> \
        --dsn postgresql://.../pitwall_rehearsal --speed 4

Only the first subscribe snapshot is replayed: later `_meta.*` records are
the recorder's own reconnect markers, not feed data.

Every `Utc` field in a payload (race control, team radio, session status,
the clock) is moved onto the replay's own clock, compressed by `--speed`
like the records' spacing. Some domain events take their time from those
fields and others from receipt; on a race day the two agree within
seconds, and left as recorded they would sit days apart (a catch-up from
lights out then ran a day behind live).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast


def records(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def lights_out(path: Path, session_type: str = "Race") -> float | None:
    """The recorded wall time the session of `session_type` started.

    The worker runs all weekend on one connection, so a recording is named
    after the session it connected in and holds the ones after it too (the
    2026 Singapore race is in its qualifying recording). The start that
    counts is the first "Started" once the feed has announced a session of
    this type."""
    current: str | None = None
    for record in records(path):
        topic = record.get("topic")
        raw = record.get("payload")
        if not isinstance(raw, dict):
            continue
        payload = cast(dict[str, Any], raw)
        if topic == "SessionInfo" and payload.get("Type"):
            current = str(payload["Type"])
        elif (
            topic == "SessionStatus"
            and payload.get("Status") == "Started"
            and current == session_type
        ):
            return float(record["ts_wall"])
    return None


_ISO = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?Z?$")


def _epoch(text: str) -> float | None:
    match = _ISO.match(text)
    if match is None:
        return None
    whole = datetime.fromisoformat(match.group(1)).replace(tzinfo=UTC).timestamp()
    fraction = match.group(2)
    return whole + (float(f"0.{fraction}") if fraction else 0.0)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def shift_utc(value: Any, move: Callable[[float], float]) -> Any:
    """`value` with every string under a "Utc" key moved by `move`."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in cast(dict[str, Any], value).items():
            if key == "Utc" and isinstance(item, str) and (epoch := _epoch(item)) is not None:
                out[key] = _iso(move(epoch))
            else:
                out[key] = shift_utc(item, move)
        return out
    if isinstance(value, list):
        return [shift_utc(item, move) for item in cast(list[Any], value)]
    return value


class RecordedFeed:
    """Stands in for `f1_ingest.client.LiveFeedClient`: same constructor,
    same `run_until`. Plays the recording once; a reconnect afterwards
    just idles, as the real feed does between sessions."""

    recording: Path
    speed: float = 1.0
    lead_s: float = 120.0
    session_type: str = "Race"
    played = False

    def __init__(
        self,
        on_subscribe_result: Callable[[dict[str, Any]], None],
        on_feed_message: Callable[[str, Any], object],
        *,
        topics: Sequence[str] | None = None,
        no_auth: bool = False,
        idle_timeout: float = 60.0,
    ) -> None:
        del topics, no_auth, idle_timeout
        self._on_subscribe = on_subscribe_result
        self._on_message = on_feed_message

    def run_until(self, stop_event: threading.Event) -> None:
        cls = type(self)
        if cls.played:
            stop_event.wait()
            return
        cls.played = True
        start = lights_out(cls.recording, cls.session_type)
        print(f"lights out at recorded {start}", flush=True)
        paced_from = None if start is None else start - cls.lead_s
        seeded = False
        anchor: tuple[float, float] | None = None  # (recorded, monotonic)
        # Recorded epoch -> replay epoch. Until pacing starts, the anchor is
        # provisional (pre-session records arrive at once, as a backlog).
        clock = {"recorded": paced_from or 0.0, "wall": time.time()}

        def move(epoch: float) -> float:
            return clock["wall"] + (epoch - clock["recorded"]) / cls.speed

        sent = 0
        for record in records(cls.recording):
            if stop_event.is_set():
                return
            topic = str(record.get("topic"))
            if topic == "_meta.subscribe_snapshot":
                if not seeded:
                    self._on_subscribe(dict(shift_utc(record["payload"], move)))
                    seeded = True
                continue
            if topic.startswith("_meta."):
                continue
            recorded = float(record["ts_wall"])
            if paced_from is None or recorded >= paced_from:
                if anchor is None:
                    anchor = (recorded, time.monotonic())
                    clock.update(recorded=recorded, wall=time.time())
                    print(f"pacing from here at {cls.speed}x", flush=True)
                due = anchor[1] + (recorded - anchor[0]) / cls.speed
                wait = due - time.monotonic()
                if wait > 0 and stop_event.wait(timeout=wait):
                    return
            self._on_message(topic, shift_utc(record["payload"], move))
            sent += 1
            if sent % 2000 == 0:
                print(f"{sent} records played", flush=True)
        print(f"recording finished: {sent} records", flush=True)
        stop_event.wait()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recording", type=Path, required=True, help="a recording directory")
    parser.add_argument("--dsn", required=True, help="a SCRATCH database, never production's")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--lead", type=float, default=120.0, help="seconds before lights out")
    parser.add_argument("--type", default="Race", help="the session to pace from (feed's Type)")
    args = parser.parse_args(argv[1:])
    if "pitwall_rehearsal" not in args.dsn:
        print("refusing: the DSN must name the pitwall_rehearsal database", file=sys.stderr)
        return 2

    import f1_ingest.client
    from f1_pipeline import cli

    RecordedFeed.recording = args.recording / "stream.jsonl"
    RecordedFeed.speed = args.speed
    RecordedFeed.lead_s = args.lead
    RecordedFeed.session_type = args.type
    f1_ingest.client.LiveFeedClient = RecordedFeed  # type: ignore[misc,assignment]
    with tempfile.TemporaryDirectory() as out:
        return cli.main(
            ["--dsn", args.dsn, "--batch-size", "1", "live", "--out", out, "--idle-timeout", "0"]
        )


if __name__ == "__main__":
    sys.exit(main(sys.argv))
