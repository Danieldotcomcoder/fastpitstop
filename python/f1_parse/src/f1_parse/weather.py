"""Decoder for `WeatherData`: one full snapshot per message (this topic,
unusually, is NOT a partial delta — every field arrives every time, as
numeric strings)."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue

from f1_parse._json import as_bool_flag, as_dict, as_float


@dataclass(frozen=True, slots=True)
class WeatherDecoded:
    air_temp: float | None
    """°C."""
    track_temp: float | None
    """°C."""
    humidity: float | None
    """Percent."""
    pressure: float | None
    """mbar."""
    wind_speed: float | None
    """m/s."""
    wind_dir: float | None
    """Degrees, direction the wind comes from."""
    rainfall: bool | None
    """The feed sends "0"/"1"."""


def decode_weather(payload: JsonValue) -> WeatherDecoded:
    body = as_dict(payload) or {}
    return WeatherDecoded(
        air_temp=as_float(body.get("AirTemp")),
        track_temp=as_float(body.get("TrackTemp")),
        humidity=as_float(body.get("Humidity")),
        pressure=as_float(body.get("Pressure")),
        wind_speed=as_float(body.get("WindSpeed")),
        wind_dir=as_float(body.get("WindDirection")),
        rainfall=as_bool_flag(body.get("Rainfall")),
    )
