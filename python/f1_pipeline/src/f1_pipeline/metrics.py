"""Per-run metrics: what went in, what came out, how long it took.

The point of the unknown-topic count is operational early warning: the feed
is unofficial and can change without notice, so a sudden spike in
`UnknownEvent`s (a topic we stopped recognizing, or a brand-new one) is the
first sign the parser needs updating — surfaced here rather than buried.

Metrics are measured AROUND the transform, never inside it: `wall_seconds`
is real elapsed time, but no wall-clock read ever reaches an event, so the
determinism invariant (same records in -> byte-identical events out) holds.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from f1_domain import DomainEvent, UnknownEvent


@dataclass(frozen=True, slots=True)
class RunMetrics:
    """A summary of one pipeline run."""

    records_in: int
    events_out: int
    events_by_kind: dict[str, int]
    unknown_topics_total: int
    unknown_topics_by_topic: dict[str, int]
    rows_written: dict[str, int]
    wall_seconds: float

    def render(self) -> str:
        by_kind = ", ".join(
            f"{kind}={count}" for kind, count in sorted(self.events_by_kind.items())
        )
        rows = ", ".join(f"{table}={count}" for table, count in sorted(self.rows_written.items()))
        unknown = f"  unknown_topics: {self.unknown_topics_total}"
        if self.unknown_topics_by_topic:
            detail = ", ".join(
                f"{topic}={count}" for topic, count in sorted(self.unknown_topics_by_topic.items())
            )
            unknown += f" ({detail})"
        return "\n".join(
            [
                f"records_in={self.records_in} events_out={self.events_out} "
                f"wall={self.wall_seconds:.3f}s",
                f"  events_by_kind: {by_kind or '(none)'}",
                f"  rows_written:   {rows or '(none)'}",
                unknown,
            ]
        )


@dataclass
class MetricsCollector:
    """Accumulates counts across a run, then freezes them into `RunMetrics`."""

    records_in: int = 0
    events_out: int = 0
    events_by_kind: dict[str, int] = field(default_factory=dict[str, int])
    unknown_by_topic: dict[str, int] = field(default_factory=dict[str, int])

    def record_input(self) -> None:
        self.records_in += 1

    def observe(self, events: Iterable[DomainEvent]) -> None:
        for event in events:
            self.events_out += 1
            self.events_by_kind[event.kind] = self.events_by_kind.get(event.kind, 0) + 1
            if isinstance(event, UnknownEvent):
                self.unknown_by_topic[event.topic] = self.unknown_by_topic.get(event.topic, 0) + 1

    def finish(self, rows_written: Mapping[str, int], wall_seconds: float) -> RunMetrics:
        return RunMetrics(
            records_in=self.records_in,
            events_out=self.events_out,
            events_by_kind=dict(self.events_by_kind),
            unknown_topics_total=sum(self.unknown_by_topic.values()),
            unknown_topics_by_topic=dict(self.unknown_by_topic),
            rows_written=dict(rows_written),
            wall_seconds=wall_seconds,
        )
