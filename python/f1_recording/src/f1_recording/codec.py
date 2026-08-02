"""Read-side decoding for the compressed `.z` live-timing topics.

The F1 SignalR feed sends some topics (`CarData.z`, `Position.z`) as
base64-encoded, raw-deflate-compressed JSON to save bandwidth. The recorder
always stores that string verbatim (see `f1_recording.models.RawRecord`);
this module exists purely so *readers* can inflate it on demand.
"""

from __future__ import annotations

import base64
import json
import zlib

from pydantic import JsonValue


def decode_z(payload: str) -> JsonValue:
    """Inflate a base64 + raw-deflate `.z` topic payload into a JSON value.

    The feed compresses with a raw deflate stream (no zlib header), hence
    the negative window-bits argument to `zlib.decompress`.
    """
    compressed = base64.b64decode(payload)
    inflated = zlib.decompress(compressed, -zlib.MAX_WBITS)
    return json.loads(inflated.decode("utf-8"))
