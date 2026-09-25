"""DB-free why-engine narration for Phase 5.5's `just verify-phase5`.

Playwright boots a REAL `f1_api` process against the checked-in kernel
fixture — the same "no data, no database, no network" discipline every
other `verify-phaseN` harness already follows. Without a live
`ANTHROPIC_API_KEY`, `get_narration_model` would otherwise resolve to a
declining mock that always emits the canonical fallback, so an E2E suite
could never assert a grounded, cited explanation.

`F1_API_WHY_FIXTURE` (any non-empty value) makes `get_narration_model`
resolve to `CitingNarrationModel` instead: a deterministic scripted stand-in
that cites explanatory evidence ids (`race_control:` / `radio_clip:`) from
the prompt it is given — the same behaviour `test_why.py`'s own
`_CitingMock` already uses under `app.dependency_overrides`, just reachable
by an external process through an env var. Low-evidence bundles still
short-circuit inside `explain` before the model is called, so the harness
also keeps a real fallback case to assert against.
"""

from __future__ import annotations

import re

from f1_why import ModelNarration

GROUNDED_FIXTURE_TEXT = "Race control confirms an on-track incident at this moment."
"""Stable prose `CitingNarrationModel` returns for a clear-cause bundle —
pinned so the Playwright suite can assert exact text rather than a
paraphrase."""


class CitingNarrationModel:
    """A strict scripted narrator: explains a moment by citing up to two of
    its race-control or radio items, and declines when it has neither.

    Declining on lap context alone is a choice a real model is free to make
    (the evidence rule sends it the lap context and lets it decide), and it
    keeps the checked-in fixture producing BOTH shapes `just verify-phase5`
    asserts in the browser: a grounded explanation and an honest fallback.
    Never touches a network."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, prompt: str) -> ModelNarration:
        self.calls.append(prompt)
        # Each evidence line is `- id=<id> | ...` (see `f1_why.build_prompt`).
        ids = re.findall(r"^- id=(\S+) \|", prompt, flags=re.MULTILINE)
        cited = tuple(eid for eid in ids if eid.startswith(("race_control:", "radio_clip:")))[:2]
        if not cited:
            return ModelNarration(text="", cited_evidence_ids=(), no_clear_cause=True)
        return ModelNarration(
            text=GROUNDED_FIXTURE_TEXT,
            cited_evidence_ids=cited,
            no_clear_cause=False,
        )


__all__ = ["GROUNDED_FIXTURE_TEXT", "CitingNarrationModel"]
