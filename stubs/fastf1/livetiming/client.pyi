"""Minimal stub for the parts of fastf1.livetiming.client that f1_ingest uses.

fastf1 ships no type information of its own. Rather than sprinkling
`# pyright: ignore` throughout f1_ingest for every attribute access on this
class, we declare the small slice of its (otherwise-untyped) public and
private surface that `f1_ingest.client.LiveFeedClient` actually subclasses
and calls. Keep in sync with the real class if fastf1's signature changes —
see f1_ingest/client.py's module docstring.
"""

import logging
from collections.abc import Iterable
from typing import Any

# Re-exported by fastf1 from signalrcore. `f1_ingest.client._run` swaps this
# module attribute for a subclass for the duration of one call (the
# signalrcore>=1.0 no-auth shim — see that module's docstring), so it has to
# be declared here or every line of that swap is an unknown attribute.
HubConnectionBuilder: type[Any]

class SignalRClient:
    topics: list[str]
    filename: str
    filemode: str
    timeout: int
    logger: logging.Logger

    def __init__(
        self,
        filename: str,
        filemode: str = ...,
        debug: bool = ...,
        timeout: int = ...,
        logger: logging.Logger | None = ...,
        no_auth: bool = ...,
    ) -> None: ...
    def start(self) -> None: ...
    def _run(self) -> None: ...
    def _supervise(self) -> None: ...
    def _exit(self) -> None: ...
    def _on_connect(self) -> None: ...
    def _on_close(self) -> None: ...
    def _on_message(self, msg: Any) -> None: ...
    _connection: Any
    _is_connected: bool
    _t_last_message: float | None
    _output_file: Any

def messages_from_raw(r: Iterable[str]) -> tuple[list[Any], int]: ...
