"""`decode(record) -> DecodedMessage`: the layer-one entry point.

Dispatches on the record's topic to the per-family decoder modules. Three
rules, all in service of "the feed is unofficial and changes without
notice":

1. Unknown topics are NOT an error — they come back as a `Family.UNKNOWN`
   passthrough carrying the payload verbatim.
2. A decoder blowing up on a malformed payload is contained: the message
   degrades to the same passthrough (with a warning logged) instead of
   taking the pipeline down. The raw record is still in the recording;
   nothing is lost by degrading here.
3. Our own `_meta.*` sentinel topics (reconnects, resubscribe snapshots —
   see docs/DATA_CONTRACTS.md) pass through as `Family.META` so layer two
   can react to them (e.g. re-seed state from a resubscribe snapshot).

This layer is STATELESS: timing topics decode to per-driver partial
deltas, not merged snapshots (that's layer two, using `deep_merge`), and
no domain events are emitted anywhere here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from pydantic import JsonValue

from f1_parse.car_data import CarDataSample, decode_car_data
from f1_parse.clock import ClockDecoded, decode_clock
from f1_parse.driver_list import DriverEntryDelta, decode_driver_list
from f1_parse.lap_count import LapCountDecoded, decode_lap_count
from f1_parse.position import PositionSample, decode_position
from f1_parse.race_control import RaceControlEntry, decode_race_control
from f1_parse.session_data import SessionDataDecoded, decode_session_data
from f1_parse.session_info import SessionInfoDecoded, decode_session_info
from f1_parse.team_radio import TeamRadioCapture, decode_team_radio
from f1_parse.timing import TimingLinesDelta, decode_timing
from f1_parse.track_status import TrackStatusDecoded, decode_track_status
from f1_parse.weather import WeatherDecoded, decode_weather
from f1_recording import RawRecord

logger = logging.getLogger(__name__)


class Family(StrEnum):
    """Topic family: which decoder handled the message."""

    TIMING = "timing"
    CAR_DATA = "car_data"
    POSITION = "position"
    WEATHER = "weather"
    TRACK_STATUS = "track_status"
    RACE_CONTROL = "race_control"
    TEAM_RADIO = "team_radio"
    SESSION_INFO = "session_info"
    SESSION_DATA = "session_data"
    LAP_COUNT = "lap_count"
    CLOCK = "clock"
    DRIVER_LIST = "driver_list"
    META = "meta"
    """Our own recording sentinels (`_meta.*`), passed through verbatim."""
    UNKNOWN = "unknown"
    """Unrecognized topic or undecodable payload, passed through verbatim."""


DecodedData = (
    TimingLinesDelta
    | list[CarDataSample]
    | list[PositionSample]
    | WeatherDecoded
    | TrackStatusDecoded
    | list[RaceControlEntry]
    | list[TeamRadioCapture]
    | SessionInfoDecoded
    | SessionDataDecoded
    | LapCountDecoded
    | ClockDecoded
    | dict[str, DriverEntryDelta]
    | JsonValue
)


@dataclass(frozen=True, slots=True)
class DecodedMessage:
    """One raw record, decoded. `source_seq` carries the RawRecord's seq
    forward so anything derived downstream stays traceable to the wire."""

    topic: str
    source_seq: int
    family: Family
    data: DecodedData


# The `Lines`-shaped timing topics all share one decoder.
_TIMING_TOPICS = frozenset({"TimingData", "TimingAppData", "TimingStats"})


def decode(record: RawRecord) -> DecodedMessage:
    """Decode one raw record. Never raises on feed content — malformed or
    unrecognized messages degrade to a verbatim passthrough (see module
    docstring)."""
    topic, payload = record.topic, record.payload
    try:
        if topic in _TIMING_TOPICS:
            return _msg(record, Family.TIMING, decode_timing(payload))
        if topic == "CarData.z" and isinstance(payload, str):
            return _msg(record, Family.CAR_DATA, decode_car_data(payload))
        if topic == "Position.z" and isinstance(payload, str):
            return _msg(record, Family.POSITION, decode_position(payload))
        if topic == "WeatherData":
            return _msg(record, Family.WEATHER, decode_weather(payload))
        if topic == "TrackStatus":
            return _msg(record, Family.TRACK_STATUS, decode_track_status(payload))
        if topic == "RaceControlMessages":
            return _msg(record, Family.RACE_CONTROL, decode_race_control(payload))
        if topic == "TeamRadio":
            return _msg(record, Family.TEAM_RADIO, decode_team_radio(payload))
        if topic == "SessionInfo":
            return _msg(record, Family.SESSION_INFO, decode_session_info(payload))
        if topic == "SessionData":
            return _msg(record, Family.SESSION_DATA, decode_session_data(payload))
        if topic == "LapCount":
            return _msg(record, Family.LAP_COUNT, decode_lap_count(payload))
        if topic == "ExtrapolatedClock":
            return _msg(record, Family.CLOCK, decode_clock(payload))
        if topic == "DriverList":
            return _msg(record, Family.DRIVER_LIST, decode_driver_list(payload))
        if topic.startswith("_meta."):
            return _msg(record, Family.META, payload)
    except Exception:
        logger.warning(
            "Decoder for topic %r failed on seq %d; passing payload through undecoded",
            topic,
            record.seq,
            exc_info=True,
        )
        return _msg(record, Family.UNKNOWN, payload)
    return _msg(record, Family.UNKNOWN, payload)


def _msg(record: RawRecord, family: Family, data: DecodedData) -> DecodedMessage:
    return DecodedMessage(topic=record.topic, source_seq=record.seq, family=family, data=data)
