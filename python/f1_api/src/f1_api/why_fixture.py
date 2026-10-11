"""A scripted, DB-free narrator reachable by an external process.

`F1_API_WHY_FIXTURE` (any non-empty value) makes `get_narration_model`
resolve to `CitingNarrationModel`: a deterministic stand-in for a language
model that cites explanatory evidence ids (`race_control:` / `radio_clip:`)
from the prompt it is given and declines otherwise. It was how
`just verify-phase5` got grounded explanations without an API key; since
Story 2.0 that suite runs the production `TemplateNarrationModel`, which
needs no key at all, and this stays for tests that want a scripted model
behind the prompt-based `NarrationModel` path.
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
