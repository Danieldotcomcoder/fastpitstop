"""Shared test fixtures for f1_recording."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from f1_recording import Manifest

ManifestFactory = Callable[..., Manifest]


@pytest.fixture
def manifest_factory() -> ManifestFactory:
    """Return a factory building a valid Manifest, with any field overridable."""

    def _make(**overrides: object) -> Manifest:
        defaults: dict[str, object] = {
            "year": 2024,
            "grand_prix": "Test Grand Prix",
            "session_type": "Race",
            "session_start_utc": "2024-01-01T00:00:00Z",
            "recorder_version": "test",
            "topics": ["TimingData"],
        }
        defaults.update(overrides)
        return Manifest.model_validate(defaults)

    return _make
