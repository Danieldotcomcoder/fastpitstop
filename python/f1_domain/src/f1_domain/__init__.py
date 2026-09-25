"""f1_domain: the Phase 1 vocabulary — pure data types, zero logic, zero I/O.

This package is to Phase 1 what `f1_recording` was to Phase 0: the contract
everything later speaks. The normalizer emits `DomainEvent`s, the event log
stores them, the state-builder folds them into `SessionState`, and Phase 2's
kernel consumes both. Nothing here parses, connects, or computes — pydantic
models and enums only, with no dependency on any other workspace package
(deliberately not even `f1_recording`; `source_seq` is a plain int precisely
so the vocabulary stays free-standing).
"""

from f1_domain.events import (
    DOMAIN_EVENT_ADAPTER,
    CarPosition,
    CarTelemetry,
    DomainEvent,
    DriverListUpdated,
    EventBase,
    GapUpdated,
    LapCompleted,
    LapCounterUpdated,
    PitEntry,
    PitExit,
    PositionChanged,
    RaceControlMessage,
    SectorCompleted,
    SessionInfoUpdated,
    SessionStatusChanged,
    StintStarted,
    TeamRadioClip,
    TrackStatusChanged,
    UnknownEvent,
    WeatherSample,
)
from f1_domain.identity import (
    Compound,
    DriverRef,
    Provenance,
    SessionKey,
    SessionType,
    TrackStatusKind,
    session_type_from_label,
)
from f1_domain.state import DriverState, SessionState

__all__ = [
    "DOMAIN_EVENT_ADAPTER",
    "CarPosition",
    "CarTelemetry",
    "Compound",
    "DomainEvent",
    "DriverListUpdated",
    "DriverRef",
    "DriverState",
    "EventBase",
    "GapUpdated",
    "LapCompleted",
    "LapCounterUpdated",
    "PitEntry",
    "PitExit",
    "PositionChanged",
    "Provenance",
    "RaceControlMessage",
    "SectorCompleted",
    "SessionInfoUpdated",
    "SessionKey",
    "SessionState",
    "SessionStatusChanged",
    "SessionType",
    "StintStarted",
    "TeamRadioClip",
    "TrackStatusChanged",
    "TrackStatusKind",
    "UnknownEvent",
    "WeatherSample",
    "session_type_from_label",
]
