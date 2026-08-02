"""Shared fixtures for f1_parse tests.

Fixture payloads live in `tests/fixtures/*.json` (one file per topic, a
JSON list of raw payloads, verbatim). They are currently hand-written
from the known feed shapes — no real recording exists yet (first live
weekend pending, see docs/PHASE0.md). Once one does, regenerate them
from reality with:

    uv run python python/f1_parse/tests/make_fixtures.py data/recordings/<dir>
"""

from __future__ import annotations

import base64
import json
import sys
import zlib
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import JsonValue

from f1_recording import RawRecord

# Test-helper modules (race_sim) live next to the tests; with pytest's
# importlib import mode this directory isn't importable by default.
_TESTS_DIR = str(Path(__file__).parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

FIXTURES_DIR = Path(__file__).parent / "fixtures"

FixtureLoader = Callable[[str], list[JsonValue]]
ZEncoder = Callable[[JsonValue], str]
RecordFactory = Callable[..., RawRecord]


@pytest.fixture
def fixture_payloads() -> FixtureLoader:
    """Load one topic's fixture file: a JSON list of raw payloads."""

    def _load(name: str) -> list[JsonValue]:
        payloads: JsonValue = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
        assert isinstance(payloads, list)
        return payloads

    return _load


@pytest.fixture
def encode_z() -> ZEncoder:
    """Inverse of `f1_recording.decode_z`: JSON -> raw deflate -> base64.

    Used to build `CarData.z`/`Position.z` test payloads, since those
    can't sit in plain JSON fixture files (they're compressed on the
    wire, and the tests must exercise the real inflation path).
    """

    def _encode(value: JsonValue) -> str:
        raw = json.dumps(value).encode("utf-8")
        compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        return base64.b64encode(compressor.compress(raw) + compressor.flush()).decode("ascii")

    return _encode


@pytest.fixture
def make_record() -> RecordFactory:
    def _make(topic: str, payload: JsonValue, *, seq: int = 0) -> RawRecord:
        return RawRecord(
            seq=seq, ts_wall=1_709_391_600.0, ts_mono=seq * 1_000_000, topic=topic, payload=payload
        )

    return _make
