"""default_topics() should reflect FastF1's current SignalRClient.topics."""

from __future__ import annotations

from f1_ingest.topics import default_topics


def test_default_topics_includes_the_standard_set() -> None:
    topics = default_topics()

    assert isinstance(topics, list)
    assert all(isinstance(topic, str) for topic in topics)
    # The "standard set" mentioned in CLAUDE.md / the Phase 0.3 brief -
    # spot-check a few, rather than asserting the exact full list, since
    # that list is FastF1's to change (that's the point of reading it live
    # instead of hardcoding it).
    for expected in ("TimingData", "CarData.z", "Position.z", "WeatherData", "TrackStatus"):
        assert expected in topics


def test_default_topics_returns_a_fresh_list_each_call() -> None:
    first = default_topics()
    first.append("Mutated")
    assert "Mutated" not in default_topics()
