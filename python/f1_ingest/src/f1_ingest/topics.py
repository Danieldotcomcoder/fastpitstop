"""The standard topic set, read live from FastF1 rather than hardcoded.

CLAUDE.md requires verifying topic names against FastF1's current source
instead of trusting memory (the feed is unofficial and can change). Reading
`SignalRClient.topics` at import/call time means we automatically track
whatever FastF1 ships, instead of maintaining a second, driftable copy.
"""

from __future__ import annotations

import os

from fastf1.livetiming.client import SignalRClient


def default_topics() -> list[str]:
    """Return FastF1's current full standard topic set.

    Constructing `SignalRClient` does not touch the network or filesystem
    (it only opens `filename` once `.start()`/`._run()` is called), so this
    is safe to call at any time. `os.devnull` is used defensively in case
    that ever changes.
    """
    reference = SignalRClient(filename=os.devnull)
    return list(reference.topics)
