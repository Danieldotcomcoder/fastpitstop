"""Minimal stub for signalrcore, which ships no type information."""

from typing import Any

class CompletionMessage:
    invocation_id: str
    result: dict[str, Any]
    error: str | None
