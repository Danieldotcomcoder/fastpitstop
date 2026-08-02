"""RecordingSession: the testable core of f1_ingest.

Turns SignalR feed events into an `f1_recording` recording on disk. This
module has no socket, no fastf1 connection logic, and no reconnect/backoff
policy — it's pure event handling, which is exactly what makes it unit
testable with a canned message sequence (see `client.py` and `cli.py` for
the parts that touch a real connection and therefore can't be).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import JsonValue

from f1_ingest.manifest import (
    SESSION_INFO_TOPIC,
    SessionInfoError,
    build_manifest_from_session_info,
)
from f1_recording import Manifest, RawRecord, RecordingWriter

# Sentinel topics for reconnect bookkeeping. Prefixed distinctly from real
# F1 topics (always PascalCase, no leading "_") so they're unmistakably
# synthetic when reading a recording back.
RECONNECT_MARKER_TOPIC = "_meta.reconnect"
RESUBSCRIBE_SNAPSHOT_TOPIC = "_meta.subscribe_snapshot"


class RecordingSessionError(RuntimeError):
    """Raised when feed events arrive out of the expected order."""


class RecordingSession:
    """Wraps an `f1_recording.RecordingWriter`, fed by SignalR feed events.

    Call `on_subscribe_result` once per (re)connect with the full-state
    dict the feed sends when subscribing, and `on_feed_message` for every
    subsequent incremental message. The first `on_subscribe_result` starts
    a new recording (building the manifest from its `SessionInfo` topic);
    every later one is treated as a reconnect — sessions can span
    reconnects, so instead of starting a new recording it appends a marker
    record plus the fresh snapshot to the existing stream.
    """

    def __init__(
        self,
        out_dir: Path,
        *,
        topics: Sequence[str],
        recorder_version: str,
        session_name: str | None = None,
    ) -> None:
        self._out_dir = out_dir
        self._topics = list(topics)
        self._recorder_version = recorder_version
        self._session_name = session_name
        self._writer: RecordingWriter | None = None
        self._reconnect_count = 0

    @property
    def topics(self) -> list[str]:
        return list(self._topics)

    @property
    def directory(self) -> Path | None:
        return self._writer.directory if self._writer is not None else None

    @property
    def manifest(self) -> Manifest | None:
        return self._writer.manifest if self._writer is not None else None

    def on_subscribe_result(self, snapshot: Mapping[str, JsonValue]) -> None:
        """Handle the full-state dict returned when (re)subscribing."""
        if self._writer is None:
            self._start_recording(snapshot)
        else:
            self._record_reconnect(snapshot)

    def on_feed_message(self, topic: str, payload: JsonValue) -> RawRecord:
        """Handle one incremental feed message; return the `RawRecord` that
        was appended to the stream.

        Returning the record lets a Phase 1 consumer (the `f1_pipeline` live
        wiring) feed the exact same raw record into the normalizer that was
        just persisted, without re-reading it from disk — one producer, raw
        to disk and normalized to the DB from a single object."""
        if self._writer is None:
            raise RecordingSessionError(
                "Received a feed message before any subscribe snapshot; "
                "on_subscribe_result must be called first."
            )
        return self._writer.append(topic, payload)

    def close(self) -> None:
        if self._writer is not None:
            self._writer.close()

    def _start_recording(self, snapshot: Mapping[str, JsonValue]) -> None:
        session_info = snapshot.get(SESSION_INFO_TOPIC)
        if not isinstance(session_info, Mapping):
            raise SessionInfoError(f"subscribe snapshot has no usable {SESSION_INFO_TOPIC!r} topic")
        manifest = build_manifest_from_session_info(
            session_info, recorder_version=self._recorder_version, topics=self._topics
        )
        directory = self._out_dir / self._directory_name(manifest)
        self._writer = RecordingWriter(directory, manifest, subscribe_snapshot=dict(snapshot))

    def _record_reconnect(self, snapshot: Mapping[str, JsonValue]) -> None:
        if self._writer is None:
            raise RecordingSessionError("_record_reconnect called without an existing writer")
        self._reconnect_count += 1
        self._writer.append(RECONNECT_MARKER_TOPIC, {"attempt": self._reconnect_count})
        self._writer.append(RESUBSCRIBE_SNAPSHOT_TOPIC, dict(snapshot))

    def _directory_name(self, manifest: Manifest) -> str:
        if self._session_name:
            return _slugify(self._session_name)
        return f"{manifest.year}_{_slugify(manifest.grand_prix)}_{_slugify(manifest.session_type)}"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value.strip()).strip("_")
    return slug or "session"
