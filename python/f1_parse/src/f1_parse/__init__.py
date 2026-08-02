"""f1_parse: Phase 1 parsing — raw SignalR payloads to typed data to
domain events.

Two layers live here, matching the pipeline's layering (CLAUDE.md):

- Layer one (`decode`): one message in, structured data out. Stateless —
  timing topics come back as the partial deltas they are, no domain
  events, no I/O. Depends on `f1_recording` for `RawRecord` and `.z`
  inflation only. Unrecognized topics and malformed payloads pass
  through verbatim rather than raising — the feed is unofficial; the
  pipeline must never crash on a message it doesn't recognize.
- Layer two (`SessionStateMachine`): consumes the record stream via
  `decode`, folds deltas into materialized state with `deep_merge`, and
  emits `f1_domain.DomainEvent`s on meaningful transitions. Pure and
  deterministic; the DB sink comes later and is NOT a dependency.
"""

from f1_parse.car_data import CarDataSample, decode_car_data
from f1_parse.decode import DecodedData, DecodedMessage, Family, decode
from f1_parse.driver_list import DriverEntryDelta, decode_driver_list
from f1_parse.lap_count import LapCountDecoded, decode_lap_count
from f1_parse.merge import deep_merge
from f1_parse.position import PositionSample, decode_position
from f1_parse.race_control import RaceControlEntry, decode_race_control
from f1_parse.session_data import SessionDataDecoded, StatusSeriesEntry, decode_session_data
from f1_parse.session_info import SessionInfoDecoded, decode_session_info
from f1_parse.state_machine import LapGap, SessionStateMachine, parse_gap
from f1_parse.team_radio import TeamRadioCapture, decode_team_radio
from f1_parse.times import parse_clock_offset, parse_lap_time, parse_utc
from f1_parse.timing import TimingLinesDelta, decode_timing
from f1_parse.track_status import TrackStatusDecoded, decode_track_status
from f1_parse.weather import WeatherDecoded, decode_weather

__all__ = [
    "CarDataSample",
    "DecodedData",
    "DecodedMessage",
    "DriverEntryDelta",
    "Family",
    "LapCountDecoded",
    "LapGap",
    "PositionSample",
    "RaceControlEntry",
    "SessionDataDecoded",
    "SessionInfoDecoded",
    "SessionStateMachine",
    "StatusSeriesEntry",
    "TeamRadioCapture",
    "TimingLinesDelta",
    "TrackStatusDecoded",
    "WeatherDecoded",
    "decode",
    "decode_car_data",
    "decode_driver_list",
    "decode_lap_count",
    "decode_position",
    "decode_race_control",
    "decode_session_data",
    "decode_session_info",
    "decode_team_radio",
    "decode_timing",
    "decode_track_status",
    "decode_weather",
    "deep_merge",
    "parse_clock_offset",
    "parse_gap",
    "parse_lap_time",
    "parse_utc",
]
