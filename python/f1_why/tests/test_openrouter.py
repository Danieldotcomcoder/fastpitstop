"""`f1_why.openrouter` — the OpenRouter narration backend.

Two things are worth testing here and nothing else is: the JSON reader
(`parse_narration_json`, a pure function, which is where a chat model
without a structured-output guarantee actually goes wrong), and that
`OpenRouterModel.complete` degrades every failure mode to an honest
decline rather than raising into a live race stream.

No test makes a network call: `OpenRouterModel` takes its HTTP client as
an optional constructor argument, so a scripted stand-in goes in through
the front door — the same "scripted fake, not a mock-library double"
precedent `MockNarrationModel` and `FakeWhisperBackend` already set in
this project.
"""

from __future__ import annotations

from typing import Any

import pytest

from f1_why import parse_narration_json
from f1_why.openrouter import (
    DEFAULT_OPENROUTER_MODEL,
    DEFAULT_OPENROUTER_MODEL_CHAIN,
    MODEL_ENV_VAR,
    REASONING_OFF,
    OpenRouterModel,
    RequestBudget,
    openrouter_model_name,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    """Replays `responses` in order, recording every request body."""

    def __init__(self, *responses: _FakeResponse | Exception) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def post(self, url: str, json: dict[str, Any]) -> _FakeResponse:
        self.requests.append(json)
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def close(self) -> None:
        pass


def _model(*responses: _FakeResponse | Exception) -> tuple[OpenRouterModel, _FakeClient]:
    client = _FakeClient(*responses)
    # Unmetered: the process-wide free-tier budget would make this suite
    # wait on a real clock. The budget has its own tests below.
    return OpenRouterModel(api_key="test-key", client=client, budget=None), client


def _chat(content: str, finish_reason: str | None = "stop") -> _FakeResponse:
    return _FakeResponse(
        200, {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}
    )


# --- parse_narration_json ---------------------------------------------------


def test_parses_a_clean_json_object() -> None:
    result = parse_narration_json(
        '{"text": "Lost time behind a slower car.", '
        '"cited_evidence_ids": ["race_control:1"], "no_clear_cause": false}'
    )
    assert result.text == "Lost time behind a slower car."
    assert result.cited_evidence_ids == ("race_control:1",)
    assert result.no_clear_cause is False


def test_parses_json_inside_a_markdown_fence() -> None:
    """The single most common free-model deviation."""
    raw = '```json\n{"text": "Pit stop was slow.", "cited_evidence_ids": ["a"], '
    raw += '"no_clear_cause": false}\n```'
    result = parse_narration_json(raw)
    assert result.text == "Pit stop was slow."
    assert result.cited_evidence_ids == ("a",)


def test_parses_json_wrapped_in_conversational_text() -> None:
    result = parse_narration_json(
        'Sure! Here is the analysis: {"text": "Safety car.", '
        '"cited_evidence_ids": ["rc:9"], "no_clear_cause": false} Let me know if you need more.'
    )
    assert result.text == "Safety car."
    assert result.cited_evidence_ids == ("rc:9",)


def test_braces_inside_strings_do_not_unbalance_extraction() -> None:
    result = parse_narration_json(
        'Answer: {"text": "Radio said {box now}.", "cited_evidence_ids": [], '
        '"no_clear_cause": false}'
    )
    assert result.text == "Radio said {box now}."


@pytest.mark.parametrize(
    "raw",
    [
        "I'm not sure what happened here.",
        "",
        "{not valid json at all",
        '["a", "list", "not", "an", "object"]',
    ],
)
def test_unreadable_answers_decline_rather_than_raise(raw: str) -> None:
    """The safety property: anything this reader cannot understand becomes
    "no clear cause", which `explain` normalizes to FALLBACK_TEXT. It must
    never raise, and must never yield a half-parsed claim."""
    result = parse_narration_json(raw)
    assert result.no_clear_cause is True
    assert result.text == ""
    assert result.cited_evidence_ids == ()


def test_empty_text_counts_as_a_decline_whatever_the_flag_says() -> None:
    result = parse_narration_json(
        '{"text": "", "cited_evidence_ids": ["a"], "no_clear_cause": false}'
    )
    assert result.no_clear_cause is True


def test_non_string_citation_entries_are_dropped() -> None:
    result = parse_narration_json(
        '{"text": "A thing happened.", "cited_evidence_ids": ["ok", 7, null, "also"], '
        '"no_clear_cause": false}'
    )
    assert result.cited_evidence_ids == ("ok", "also")


# --- OpenRouterModel.complete ------------------------------------------------


def test_complete_reads_a_normal_response() -> None:
    model, client = _model(_chat('{"text": "T1 lock-up.", "cited_evidence_ids": ["x"]}'))
    result = model.complete("prompt")
    assert result.text == "T1 lock-up."
    assert result.no_clear_cause is False
    assert model.last_error is None
    assert client.requests[0]["model"] == DEFAULT_OPENROUTER_MODEL
    assert client.requests[0]["response_format"] == {"type": "json_object"}


def test_reasoning_is_switched_off() -> None:
    """Every model in the chain reasons by default and spent the whole
    budget doing it: the story's September 2026 outage in one field."""
    model, client = _model(_chat('{"text": "T1 lock-up.", "cited_evidence_ids": ["x"]}'))
    model.complete("prompt")
    assert client.requests[0]["reasoning"] == REASONING_OFF == {"enabled": False}


def test_a_4xx_retries_once_without_response_format() -> None:
    """Some free models reject `response_format` outright. The prompt asks
    for JSON anyway, so the retry is what saves the explanation."""
    model, client = _model(
        _FakeResponse(400, {"error": "response_format unsupported"}),
        _chat('{"text": "Recovered.", "cited_evidence_ids": ["x"]}'),
    )
    result = model.complete("prompt")
    assert result.text == "Recovered."
    assert len(client.requests) == 2
    assert "response_format" in client.requests[0]
    assert "response_format" not in client.requests[1]
    # Some endpoints also refuse to have reasoning switched off.
    assert "reasoning" not in client.requests[1]


def test_a_transport_error_declines_instead_of_raising() -> None:
    """One dead call must degrade a single anomaly, never take down the
    race stream it is running inside. Every model in the chain is tried
    before giving up, so the fake needs one failure each."""
    model, _ = _model(
        RuntimeError("connection reset"),
        RuntimeError("connection reset"),
        RuntimeError("connection reset"),
    )
    result = model.complete("prompt")
    assert result.provider_failed is True
    assert model.last_error is not None
    assert "connection reset" in model.last_error


def test_an_in_body_error_is_a_provider_failure_not_a_decline() -> None:
    """The 2026 Spanish GP regression: OpenRouter answers HTTP 200 while
    reporting the upstream outage IN THE BODY. Reading only the status code
    turned every explanation that weekend into "No clear cause found in the
    available evidence" — an assertion about the race, produced by an
    overloaded GPU pool."""
    overloaded = _FakeResponse(
        200,
        {
            "id": "gen-1",
            "error": {
                "message": "Upstream error from Nvidia: Service temporarily overloaded",
                "code": 503,
                "metadata": {"error_type": "provider_overloaded"},
            },
        },
    )
    model, _ = _model(overloaded, overloaded, overloaded)
    result = model.complete("prompt")
    assert result.provider_failed is True, "an outage must never read as a decline"
    assert model.last_error is not None
    assert "provider_overloaded" in model.last_error


def test_the_chain_moves_on_after_a_provider_failure() -> None:
    """Free tiers are overloaded exactly when a live race is running, so a
    single slug is a single point of failure."""
    model, client = _model(
        _FakeResponse(200, {"error": {"message": "overloaded", "code": 503}}),
        _chat('{"text": "Second model answered.", "cited_evidence_ids": ["x"]}'),
    )
    result = model.complete("prompt")
    assert result.text == "Second model answered."
    assert result.provider_failed is False
    assert model.last_error is None
    assert [r["model"] for r in client.requests] == list(DEFAULT_OPENROUTER_MODEL_CHAIN[:2])


def test_an_honest_decline_is_never_retried() -> None:
    """A model that answers and says "no clear cause" has made a finding.
    Retrying it on another provider would be shopping for a better answer."""
    model, client = _model(_chat('{"text": "", "no_clear_cause": true}'))
    result = model.complete("prompt")
    assert result.no_clear_cause is True
    assert result.provider_failed is False
    assert len(client.requests) == 1


def test_a_persistent_4xx_moves_to_the_next_model() -> None:
    """A rate-limited provider is a provider failure, not a verdict: after
    the no-response_format retry fails too, the chain moves on."""
    rate_limited = _FakeResponse(429, {"error": "rate limited"})
    model, client = _model(
        rate_limited,
        rate_limited,
        _chat('{"text": "Next provider answered.", "cited_evidence_ids": ["x"]}'),
    )
    result = model.complete("prompt")
    assert result.text == "Next provider answered."
    assert result.provider_failed is False
    # two attempts on the first model (with and without response_format),
    # then one on the second.
    assert [r["model"] for r in client.requests] == [
        DEFAULT_OPENROUTER_MODEL_CHAIN[0],
        DEFAULT_OPENROUTER_MODEL_CHAIN[0],
        DEFAULT_OPENROUTER_MODEL_CHAIN[1],
    ]


def test_a_truncated_reply_is_a_provider_failure_not_a_decline() -> None:
    """The regression behind every "No clear cause found" in the September
    2026 story: Nemotron ran out of tokens mid-thought, the reply held no
    JSON, and it was read as the model declining -- a claim about the race
    made by a model that never finished answering. Truncation moves on to
    the next model instead."""
    cut_off = _chat("The user wants me to explain an anomaly. First, the", "length")
    model, client = _model(
        cut_off, _chat('{"text": "Second model answered.", "cited_evidence_ids": ["x"]}')
    )
    result = model.complete("prompt")
    assert result.text == "Second model answered."
    assert [r["model"] for r in client.requests] == list(DEFAULT_OPENROUTER_MODEL_CHAIN[:2])


def test_truncation_everywhere_reads_as_an_outage() -> None:
    cut_off = _chat('{"text": "NOR overtook PIA for P3 on lap 30 due', "length")
    model, _ = _model(cut_off, cut_off, cut_off)
    result = model.complete("prompt")
    assert result.provider_failed is True, "a cut-off reply must never read as a decline"
    assert model.last_error is not None
    assert "truncated" in model.last_error


def test_a_reply_with_no_json_is_a_provider_failure() -> None:
    """Prose where JSON was asked for supports no claim either way."""
    prose = _chat("I think the driver probably had a problem with the tyres.")
    model, _ = _model(prose, prose, prose)
    result = model.complete("prompt")
    assert result.provider_failed is True
    assert model.last_error is not None
    assert "no JSON" in model.last_error


def test_an_unexpected_response_shape_declines() -> None:
    odd = _FakeResponse(200, {"unexpected": "shape"})
    model, _ = _model(odd, odd, odd)
    result = model.complete("prompt")
    assert result.provider_failed is True
    assert model.last_error is not None
    assert "unexpected" in model.last_error


def test_a_missing_key_is_a_clear_error_not_a_silent_default() -> None:
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        OpenRouterModel(api_key="")


def test_model_name_comes_from_the_environment_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MODEL_ENV_VAR, "some/other-model")
    assert openrouter_model_name() == "some/other-model"
    monkeypatch.delenv(MODEL_ENV_VAR)
    assert openrouter_model_name() == DEFAULT_OPENROUTER_MODEL


# --- RequestBudget ------------------------------------------------------------


class _FakeClock:
    """A clock that only moves when something sleeps on it."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_the_budget_lets_a_burst_through_up_to_its_limit() -> None:
    clock = _FakeClock()
    budget = RequestBudget(limit=3, window_s=60.0, clock=clock, sleep=clock.sleep)
    assert [budget.acquire(max_wait_s=0) for _ in range(3)] == [True, True, True]
    assert clock.slept == []


def test_the_budget_waits_for_the_oldest_request_to_leave_the_window() -> None:
    """Twenty-first request in a minute: wait, do not fire it and eat a 429."""
    clock = _FakeClock()
    budget = RequestBudget(limit=2, window_s=60.0, clock=clock, sleep=clock.sleep)
    budget.acquire(max_wait_s=0)
    clock.now += 10
    budget.acquire(max_wait_s=0)

    assert budget.acquire(max_wait_s=120) is True
    assert clock.slept == [50.0], "the first request leaves the window 50 s later"


def test_the_budget_gives_up_rather_than_wait_past_its_deadline() -> None:
    clock = _FakeClock()
    budget = RequestBudget(limit=1, window_s=60.0, clock=clock, sleep=clock.sleep)
    budget.acquire(max_wait_s=0)

    assert budget.acquire(max_wait_s=30) is False
    assert clock.slept == [], "it must not sleep at all when the wait cannot fit"


def test_an_exhausted_budget_is_a_provider_failure() -> None:
    """Out of budget, the call is never sent and the moment reads as the
    service not responding: retried on its next request, never a verdict."""
    clock = _FakeClock()
    budget = RequestBudget(limit=0, window_s=60.0, clock=clock, sleep=clock.sleep)
    client = _FakeClient()
    model = OpenRouterModel(api_key="test-key", client=client, budget=budget)

    result = model.complete("prompt")

    assert result.provider_failed is True
    assert client.requests == []
    assert model.last_error is not None
    assert "budget" in model.last_error
