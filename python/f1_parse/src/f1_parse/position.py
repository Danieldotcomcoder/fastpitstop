"""Decoder for `Position.z`: compressed batches of on-track positions.

Like `CarData.z`, the payload inflates to a BATCH — `{"Position":
[{"Timestamp": ..., "Entries": {...}}, ...]}` — each entry timestamped
independently and covering every car. Fanned out to one `PositionSample`
per (entry, driver). Coordinates are the feed's own track-local units
(~1/10 m); `status` is passed through verbatim ("OnTrack"/"OffTrack").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from f1_parse._json import as_dict, as_float, as_list, as_str
from f1_parse.times import parse_utc
from f1_recording import decode_z


@dataclass(frozen=True, slots=True)
class PositionSample:
    """One driver's on-track position at one instant."""

    ts_utc: datetime | None
    driver: str
    x: float | None
    y: float | None
    z: float | None
    status: str | None


def decode_position(payload: str) -> list[PositionSample]:
    """Inflate one `Position.z` message and fan it out to samples."""
    body = as_dict(decode_z(payload)) or {}
    samples: list[PositionSample] = []
    for entry_raw in as_list(body.get("Position")) or []:
        entry = as_dict(entry_raw)
        if entry is None:
            continue
        ts_utc = parse_utc(as_str(entry.get("Timestamp")))
        entries = as_dict(entry.get("Entries")) or {}
        for driver, car_raw in entries.items():
            car = as_dict(car_raw) or {}
            samples.append(
                PositionSample(
                    ts_utc=ts_utc,
                    driver=driver,
                    x=as_float(car.get("X")),
                    y=as_float(car.get("Y")),
                    z=as_float(car.get("Z")),
                    status=as_str(car.get("Status")),
                )
            )
    return samples
