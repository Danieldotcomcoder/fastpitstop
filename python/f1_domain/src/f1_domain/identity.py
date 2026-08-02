"""Identity types and closed enums: who/where/when-in-the-championship.

Drivers are referred to throughout the domain by their *racing number as a
string* ("44", "1") — that is the feed's own primary key (TimingData,
CarData, DriverList are all keyed by it) and survives mid-season code/team
changes. `DriverRef` is the richer identity record, produced whenever the
feed's DriverList topic tells us who a number currently is.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class SessionType(StrEnum):
    """The kind of session within a race weekend."""

    PRACTICE_1 = "practice_1"
    PRACTICE_2 = "practice_2"
    PRACTICE_3 = "practice_3"
    SPRINT_QUALIFYING = "sprint_qualifying"
    SPRINT = "sprint"
    QUALIFYING = "qualifying"
    RACE = "race"
    UNKNOWN = "unknown"


class Provenance(StrEnum):
    """Where an event's underlying data came from.

    The live pipeline and the historical adapter emit the SAME
    `DomainEvent` types (that is the whole point of the Phase 1 seam), but
    Phase 2 sometimes needs to tell them apart: historical events carry a
    synthetic `source_seq`, reconstructed timestamps, and coarser detail
    than the live delta stream (see `f1_historical_adapter`). `LIVE` is the
    default so the live/replay pipeline never has to think about it.
    """

    LIVE = "live"
    HISTORICAL = "historical"


class TrackStatusKind(StrEnum):
    """Normalized track status. The wire sends numeric codes ("1", "2", ...);
    mapping codes onto these is the parser's job, not this enum's.
    """

    GREEN = "green"
    YELLOW = "yellow"
    SC = "sc"
    VSC = "vsc"
    RED = "red"
    CHEQUERED = "chequered"


class Compound(StrEnum):
    """Tyre compound. `UNKNOWN` covers the feed's blanks and any future
    compound name we haven't seen — never guess a compound.
    """

    SOFT = "soft"
    MEDIUM = "medium"
    HARD = "hard"
    INTERMEDIATE = "intermediate"
    WET = "wet"
    UNKNOWN = "unknown"


class SessionKey(BaseModel):
    """Identifies one session of one championship round.

    Distinct from `f1_backfill.schedule.SessionKey` (that one is Phase 0
    bookkeeping for the historical fixture track); this is the domain
    identity every event and state model carries.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    year: int
    round: int
    session_type: SessionType = SessionType.UNKNOWN

    def __hash__(self) -> int:
        """Declared explicitly even though `frozen=True` already generates
        one at runtime: type checkers cannot see a `ConfigDict` flag, so
        without this they reject `dict[SessionKey, ...]` — and this type is
        the natural key for any per-session map (Phase 2 keys frame caches
        and event-name lookups by it).
        """
        return hash((self.year, self.round, self.session_type))


class DriverRef(BaseModel):
    """A driver identity as of some point in time. `number` is the racing
    number as the feed sends it (a string, e.g. "44"); `code` is the TLA
    ("HAM"); `team` the current team name. Only the number is required —
    the feed reveals the rest incrementally via DriverList.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    number: str
    code: str | None = None
    team: str | None = None
