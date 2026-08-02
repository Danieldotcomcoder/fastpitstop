"""Decoder for `CarData.z`: compressed batches of car telemetry.

The payload is base64 + raw-deflate (inflated via `f1_recording.decode_z`)
and decodes to a BATCH — `{"Entries": [{"Utc": ..., "Cars": {...}}, ...]}`
— where each entry carries its own timestamp and every car's channel
values at that instant. One message is NOT one sample: this decoder fans
the batch out into one `CarDataSample` per (entry, driver).

Channel numbers, per the feed (and FastF1's `api.py`):
0 = rpm, 2 = speed (km/h), 3 = gear (0 = neutral), 4 = throttle (%),
5 = brake (%), 45 = DRS code (0/1 off, 8 eligible, 10/12/14 open).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from f1_parse._json import as_dict, as_float, as_int, as_list, as_str
from f1_parse.times import parse_utc
from f1_recording import decode_z


@dataclass(frozen=True, slots=True)
class CarDataSample:
    """One driver's telemetry at one instant."""

    ts_utc: datetime | None
    driver: str
    rpm: int | None
    speed: float | None
    """km/h."""
    gear: int | None
    throttle: float | None
    brake: float | None
    drs: int | None


def decode_car_data(payload: str) -> list[CarDataSample]:
    """Inflate one `CarData.z` message and fan it out to samples."""
    body = as_dict(decode_z(payload)) or {}
    samples: list[CarDataSample] = []
    for entry_raw in as_list(body.get("Entries")) or []:
        entry = as_dict(entry_raw)
        if entry is None:
            continue
        ts_utc = parse_utc(as_str(entry.get("Utc")))
        cars = as_dict(entry.get("Cars")) or {}
        for driver, car_raw in cars.items():
            channels = as_dict((as_dict(car_raw) or {}).get("Channels")) or {}
            samples.append(
                CarDataSample(
                    ts_utc=ts_utc,
                    driver=driver,
                    rpm=as_int(channels.get("0")),
                    speed=as_float(channels.get("2")),
                    gear=as_int(channels.get("3")),
                    throttle=as_float(channels.get("4")),
                    brake=as_float(channels.get("5")),
                    drs=as_int(channels.get("45")),
                )
            )
    return samples
