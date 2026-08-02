"""Tests for decode_z, the read-side .z topic decoder."""

from __future__ import annotations

import base64
import json
import zlib
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from f1_recording import decode_z

# A fixed, independently-verifiable sample: base64(raw-deflate(b'{"hello": "world"}')).
# Computed once with `zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)` and
# hardcoded here (rather than generated inline) so this test also catches
# accidental breakage of the encoding assumptions, not just a decoder that's
# merely self-consistent with its own encoder.
_KNOWN_SAMPLE_B64 = "q1bKSM3JyVeyUlAqzy/KSVGqBQA="
_KNOWN_SAMPLE_VALUE = {"hello": "world"}


def test_decode_z_against_known_sample() -> None:
    assert decode_z(_KNOWN_SAMPLE_B64) == _KNOWN_SAMPLE_VALUE


def _encode_z(value: Any) -> str:
    raw = json.dumps(value).encode("utf-8")
    compressor = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    compressed = compressor.compress(raw) + compressor.flush()
    return base64.b64encode(compressed).decode("ascii")


@given(
    value=st.dictionaries(
        st.text(max_size=10),
        st.one_of(st.integers(), st.text(max_size=10), st.booleans(), st.none()),
        max_size=5,
    )
)
def test_decode_z_roundtrips_arbitrary_values(value: dict[str, Any]) -> None:
    assert decode_z(_encode_z(value)) == value
