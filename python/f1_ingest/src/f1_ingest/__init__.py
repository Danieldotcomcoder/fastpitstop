"""f1_ingest: the live-feed connector.

Phase 0 (current): connects to F1's unofficial SignalR live timing feed and
records every message raw via `f1_recording` — the on-disk contract owned
by that package. This module does NOT decode payloads into domain events,
compute anything, or touch a database; that's Phase 1's job, which will
extend this same package with normalization + a TimescaleDB producer (see
CLAUDE.md: "One producer, event-sourced, replay and live share one
pipeline").

REALITY CHECK: the live connection (`client.py`) can only be FULLY tested
against F1's real feed, which only exists during a live session
(~24 weekends/year). It is deliberately NOT unit tested. `--dry-run`
(`f1-record --dry-run`) is the only way to smoke-test it outside a race
weekend — it connects, reports whether a session is currently live, and
exits; an empty/no-data response is the expected, non-error outcome when
nothing is running. This is exactly why Phase 0 also has a replayer
(`f1_replay`) and a historical backfill (`f1_backfill`): day-to-day
development runs off recordings and historical tables, not the live feed.
See `docs/PHASE0.md` for how to validate this against a real
session, including cross-validating our capture against FastF1's own
recorder (`python -m fastf1.livetiming save`), which can run in parallel
during a real session.

The one part of this package that IS unit tested without a socket is
`RecordingSession` (`recording_session.py`): it's fed a canned sequence of
subscribe/feed events and asserts a well-formed recording is produced.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("f1-ingest")
except PackageNotFoundError:  # pragma: no cover - only when not installed
    __version__ = "0.0.0+unknown"

from f1_ingest.recording_session import RecordingSession
from f1_ingest.topics import default_topics

__all__ = [
    "RecordingSession",
    "__version__",
    "default_topics",
]
