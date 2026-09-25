"""`OpenRouterModel`: a second real `NarrationModel` backend, alongside
`f1_why.narration.ClaudeHaikuModel`.

Why a separate module rather than another class in `narration.py`: that
module owns the grounding contract (prompt, validator, confidence
derivation, cache) and should stay readable as one argument. A backend is
a swappable implementation detail behind the `NarrationModel` Protocol —
the same split `f1_radio` already makes between `transcription.py`'s
orchestration and the concrete `WhisperBackend` it loads.

Two things differ deliberately from the Anthropic backend:

- **No structured-output guarantee.** OpenRouter is OpenAI-compatible, but
  free-tier models vary in whether they honour `response_format`. So this
  asks for JSON twice over — the `response_format` hint AND an explicit
  instruction appended to the system prompt. A reply that was cut off at
  the token limit, or that holds no JSON object at all, is a PROVIDER
  failure: the chain moves on, and if every model fails the user reads
  "the narration service did not respond". It is never a decline, because
  `FALLBACK_TEXT` ("no clear cause found") is a claim about the race, and
  an unreadable reply supports no claim either way. Either path still ends
  in no ungrounded text reaching a user — the failure direction CLAUDE.md's
  Phase 5 grounding invariant asks for.
- **It never raises into the caller.** A transport error, a rate limit, or
  a 4xx from a momentarily-unavailable free model returns a decline, so one
  failed call degrades that single anomaly instead of taking down a whole
  race's WebSocket stream. `last_error` records what actually happened for
  an operator reading logs.

`httpx` is imported LAZILY inside `__init__`, the same "importing this
module never requires the optional dependency" precedent
`ClaudeHaikuModel` sets for `anthropic`, so the test suite never needs a
network stack, let alone a live key.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from f1_why.narration import DEFAULT_MAX_TOKENS, SYSTEM_PROMPT, ModelNarration

DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
"""The model this project runs the why-engine on when OpenRouter is
configured. A project decision (like `narration.DEFAULT_MODEL` is for
Anthropic), not this module's own preference — and the reason the parsing
below assumes nothing about structured-output support.

Free slugs on OpenRouter are not stable: the previous default
(`minimax/minimax-m3:free`) was withdrawn and every call started returning
HTTP 404, which `_try_backend`/`complete` correctly turn into a decline — so
the symptom was not an error but every single explanation reading "No clear
cause found in the available evidence". If that text ever becomes universal
again, check this slug against https://openrouter.ai/api/v1/models FIRST.
Chosen over the other free models available at the time because it answered
the narration prompt with clean, unfenced JSON in the expected shape; the
Gemma free tiers were rate-limited (429) and the Nemotron "lightning" tier
prepended chain-of-thought prose the parser would have had to strip."""

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

API_KEY_ENV_VAR = "OPENROUTER_API_KEY"
MODEL_ENV_VAR = "OPENROUTER_MODEL"
MODELS_ENV_VAR = "OPENROUTER_MODELS"
BASE_URL_ENV_VAR = "OPENROUTER_BASE_URL"
"""Overrides the completions endpoint. Two real uses: pointing at an
OpenAI-compatible proxy/self-hosted gateway, and pointing at a local
stand-in server to exercise this backend end-to-end without spending a
live key. Unset (the default) means OpenRouter itself."""

JSON_INSTRUCTION = (
    "Reply with ONE JSON object and nothing else — no prose before or after "
    "it, no markdown code fence. Use exactly this shape: "
    '{"text": "<one-sentence explanation, or an empty string>", '
    '"cited_evidence_ids": ["<evidence id>", "..."], '
    '"no_clear_cause": true or false}'
)

_DECLINE = ModelNarration(text="", cited_evidence_ids=(), no_clear_cause=True)
_PROVIDER_FAILED = ModelNarration(
    text="", cited_evidence_ids=(), no_clear_cause=True, provider_failed=True
)

DEFAULT_OPENROUTER_MODEL_CHAIN: tuple[str, ...] = (
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "inclusionai/ling-3.0-flash-fin:free",
    "nex-agi/nex-n2.5-pro:free",
)
"""Tried in order, moving on only when a provider FAILS (never when a model
honestly declines). Free tiers are overloaded exactly when a live race is
running, so a single slug is a single point of failure; three unrelated
providers is the cheap insurance. Override with `OPENROUTER_MODELS`
(comma-separated). Verify any slug against
https://openrouter.ai/api/v1/models before shipping it -- a withdrawn slug
returns 404 on every call, which is how this engine silently died once.

Measured on 2026-09-23 against a real narration prompt with reasoning off:
Nemotron Ultra 1.6 s, Ling Flash 1.7 s, Nex N2.5 Pro 1.9 s, each returning
clean cited JSON. The third slot was `deepseek/deepseek-v4-flash-0731:free`
until DeepSeek withdrew the free tier (404, "unavailable for free")."""

REASONING_OFF: dict[str, Any] = {"enabled": False}
"""Every model in the chain is a reasoning model, and left to itself each one
spends its whole completion budget thinking before it writes a word of JSON.

That was the entire failure of the story in September 2026: Nemotron hit
`finish_reason=length` on every call at 300 tokens (194 of them reasoning),
the reply held a half-finished chain of thought and no JSON, and the reader
turned that into "No clear cause found" -- a false statement about the race,
made because the model never got to answer. At 3,000 tokens it was STILL
truncated, after 166 seconds. With reasoning off the same prompt answers in
1.6 s. A one-sentence narration over an already-assembled evidence list is
not a problem that benefits from deliberation."""


class RequestBudget:
    """At most `limit` requests in any `window_s`, shared by every caller.

    OpenRouter limits a free-tier key to 20 requests a minute. `f1_api`
    narrates a session on a four-thread pool and each failed call walks a
    three-model chain, so without a shared budget a single story could fire
    thirty-odd requests inside a minute, trip the limit, read the 429s as
    provider failures, retry on the next slug and trip it harder. The budget
    makes callers wait their turn instead, and gives up only when the wait
    would outlast `max_wait_s`, which reads as a provider failure like any
    other.

    A sliding log of send times rather than a token bucket: exact at this
    size (a few dozen entries), and trivially correct across threads under
    one lock. `clock` and `sleep` are injectable so the arithmetic is
    testable without real waiting."""

    def __init__(
        self,
        limit: int,
        window_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._limit = limit
        self._window_s = window_s
        self._clock = clock
        self._sleep = sleep
        self._sent: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self, max_wait_s: float) -> bool:
        """Claim one request slot, waiting up to `max_wait_s` for one. A
        budget of zero never grants one: that is how narration is switched
        off, not a reason to fail."""
        if self._limit <= 0:
            return False
        deadline = self._clock() + max_wait_s
        while True:
            with self._lock:
                now = self._clock()
                while self._sent and now - self._sent[0] >= self._window_s:
                    self._sent.popleft()
                if len(self._sent) < self._limit:
                    self._sent.append(now)
                    return True
                wait = self._window_s - (now - self._sent[0])
            if now + wait > deadline:
                return False
            self._sleep(wait)


FREE_TIER_BUDGET = RequestBudget(limit=18, window_s=60.0)
"""The process-wide budget every `OpenRouterModel` shares by default: 18 a
minute, two under OpenRouter's free-tier limit of 20 so a request from
anything else on the same key does not tip it over."""

BUDGET_MAX_WAIT_S = 45.0
"""How long one call may queue for a slot before it counts as the provider
failing. Past this the moment reads "the narration service did not respond"
and is retried on its next request, rather than holding a stream thread."""


def openrouter_model_chain() -> tuple[str, ...]:
    """`OPENROUTER_MODELS` when set, else the primary model then the rest of
    the default chain."""
    raw = os.environ.get(MODELS_ENV_VAR)
    if raw:
        chain = tuple(part.strip() for part in raw.split(",") if part.strip())
        if chain:
            return chain
    primary = openrouter_model_name()
    return (primary, *(m for m in DEFAULT_OPENROUTER_MODEL_CHAIN if m != primary))


def _strip_json_fence(raw: str) -> str:
    """A fenced ```json … ``` block reduced to the JSON inside it; returned
    unchanged when there is no fence."""
    text = raw.strip()
    if not text.startswith("```"):
        return text
    body = text[3:]
    if body[:4].lower() == "json":
        body = body[4:]
    closing = body.rfind("```")
    return (body[:closing] if closing != -1 else body).strip()


def _first_json_object(raw: str) -> str | None:
    """The first balanced `{…}` span in `raw`, or `None` when there is none.

    The last-resort reader for a model that wrapped its JSON in
    conversational text despite being told not to. Brace-counting (and
    string-aware, so a brace inside an evidence id or a quoted sentence
    does not unbalance it) rather than a regex, which cannot match nested
    braces correctly at all."""
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(raw)):
        char = raw[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return raw[start : index + 1]
    return None


def _json_payload(raw: str) -> dict[str, object] | None:
    """The JSON object a model's reply carries, or `None` when it carries
    none: fenced, bare, or wrapped in conversational text all count."""
    candidate = _strip_json_fence(raw)
    payload: object
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        extracted = _first_json_object(candidate)
        if extracted is None:
            return None
        try:
            payload = json.loads(extracted)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    return {str(key): value for key, value in payload.items()}  # type: ignore[misc]


def parse_narration_json(raw: str) -> ModelNarration:
    """A chat model's raw text answer read as a `ModelNarration`.

    Pure and separately testable, because this is the one place a backend
    without a structured-output guarantee can go wrong: a free-tier chat
    model may fence its JSON, prepend a sentence, or omit a field.

    Every unreadable shape becomes `no_clear_cause=True` rather than an
    exception or a half-parsed claim. `OpenRouterModel` never lets an
    unreadable reply get this far, though: it checks for a JSON object first
    and treats its absence as the provider failing, because "the reply could
    not be read" is not a finding about the race. An answer with an empty
    `text` is treated as a decline whatever the flag says, since there is no
    sentence to show a user either way."""
    fields = _json_payload(raw)
    if fields is None:
        return _DECLINE

    raw_text = fields.get("text")
    text = raw_text.strip() if isinstance(raw_text, str) else ""
    if not text:
        return _DECLINE

    raw_ids = fields.get("cited_evidence_ids")
    id_items: list[object] = list(raw_ids) if isinstance(raw_ids, list) else []  # type: ignore[misc]
    cited = tuple(item for item in id_items if isinstance(item, str) and item)
    declined = fields.get("no_clear_cause")
    return ModelNarration(
        text=text,
        cited_evidence_ids=cited,
        no_clear_cause=bool(declined) if isinstance(declined, bool) else False,
    )


def openrouter_url() -> str:
    """The completions endpoint an `OpenRouterModel` will post to."""
    return os.environ.get(BASE_URL_ENV_VAR) or OPENROUTER_URL


def openrouter_model_name() -> str:
    """The model an `OpenRouterModel()` with no explicit `model=` will use —
    `OPENROUTER_MODEL` when set, else `DEFAULT_OPENROUTER_MODEL`."""
    return os.environ.get(MODEL_ENV_VAR) or DEFAULT_OPENROUTER_MODEL


class OpenRouterModel:
    """One OpenAI-compatible chat completion per explanation. See the
    module docstring for the two guarantees this backend makes."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        url: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout_s: float = 20.0,
        referer: str | None = None,
        title: str = "pitwall",
        client: Any | None = None,
        budget: RequestBudget | None = FREE_TIER_BUDGET,
    ) -> None:
        """`client` injects a pre-built HTTP client (anything with
        `post(url, json=...)`). Tests pass a scripted stand-in through it
        rather than reaching for a private attribute; production leaves it
        unset and gets a real `httpx.Client`."""
        key = api_key if api_key is not None else os.environ.get(API_KEY_ENV_VAR, "")
        if not key:
            raise ValueError(
                f"OpenRouterModel needs an API key — pass api_key= or set {API_KEY_ENV_VAR}"
            )
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        # OpenRouter's optional attribution headers, only sent when actually
        # configured so a local dev run stays anonymous.
        if referer:
            headers["HTTP-Referer"] = referer
        if title:
            headers["X-Title"] = title
        # Untyped past this point for the same reason `ClaudeHaikuModel`
        # keeps its client `Any`: this is a wire client, not a domain type.
        # `httpx` is imported here, not at module scope, so importing this
        # module never requires the optional dependency.
        if client is None:
            import httpx

            client = httpx.Client(timeout=timeout_s, headers=headers)
        self._client: Any = client
        self.model = model or openrouter_model_name()
        # An explicit model= pins exactly that one (tests, and anyone who
        # wants no surprises); otherwise walk the configured chain.
        self._models: tuple[str, ...] = (model,) if model else openrouter_model_chain()
        self.url = url or openrouter_url()
        self._max_tokens = max_tokens
        # `None` means unmetered: tests, and a paid key with no such limit.
        self._budget = budget
        self.last_error: str | None = None

    def complete(self, prompt: str) -> ModelNarration:
        """Ask each model in the chain until one actually answers.

        A model that answers and declines ENDS the walk — that is a real
        finding and must not be retried away. Only a provider failure moves
        on to the next slug.
        """
        errors: list[str] = []
        for model in self._models:
            narration, error = self._complete_one(model, prompt)
            if error is None:
                self.last_error = None
                self.model = model
                return narration
            errors.append(f"{model}: {error}")
        self.last_error = " | ".join(errors)
        return _PROVIDER_FAILED

    def _complete_one(self, model: str, prompt: str) -> tuple[ModelNarration, str | None]:
        """One model. Returns (narration, None) on success, or
        (_PROVIDER_FAILED, reason) when the provider never answered."""
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": self._max_tokens,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "reasoning": REASONING_OFF,
            "messages": [
                {"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{JSON_INSTRUCTION}"},
                {"role": "user", "content": prompt},
            ],
        }
        if not self._claim():
            return _PROVIDER_FAILED, "request budget exhausted (free-tier rate limit)"
        try:
            response = self._client.post(self.url, json=body)
            if response.status_code >= 400 and self._claim():
                # Some free models reject `response_format` outright, and a
                # few refuse to have reasoning switched off ("Reasoning is
                # mandatory for this endpoint"). The system prompt already
                # asks for JSON, so retry once with neither rather than lose
                # the explanation to a 400. A fresh dict, not a mutated one:
                # the first request has already been sent, and editing the
                # object it was built from makes the two attempts
                # indistinguishable to anything holding a reference (a
                # logger, a test, a retrying transport).
                retry_body = {
                    k: v for k, v in body.items() if k not in ("response_format", "reasoning")
                }
                response = self._client.post(self.url, json=retry_body)
            response.raise_for_status()
            payload: Any = response.json()
        except Exception as exc:  # degrade, never break the stream (see class docstring)
            return _PROVIDER_FAILED, f"{type(exc).__name__}: {exc}"

        # OpenRouter reports UPSTREAM failures in the body, with HTTP 200:
        #   {"error": {"message": "Upstream error from Nvidia: Service
        #    temporarily overloaded", "code": 503, ...}}
        # Checking only the status code turned every such outage into a
        # "no clear cause" verdict about the race.
        try:
            in_body_error = payload.get("error")
        except AttributeError:
            in_body_error = None
        if in_body_error:
            return _PROVIDER_FAILED, f"in-body error: {str(in_body_error)[:180]}"

        try:
            choice = payload["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return _PROVIDER_FAILED, f"unexpected response shape: {str(payload)[:180]}"
        if not isinstance(content, str):
            return _PROVIDER_FAILED, "response content was not a string"

        # A reply cut off at the token limit is a model that never finished
        # answering, not one that declined. Reading it as a decline is how
        # every story moment came to say "No clear cause found".
        if choice.get("finish_reason") == "length":
            return _PROVIDER_FAILED, f"reply truncated at max_tokens={self._max_tokens}"
        if _json_payload(content) is None:
            return _PROVIDER_FAILED, f"reply held no JSON object: {content[:120]!r}"

        return parse_narration_json(content), None

    def _claim(self) -> bool:
        return self._budget is None or self._budget.acquire(BUDGET_MAX_WAIT_S)

    def close(self) -> None:
        self._client.close()


__all__ = [
    "API_KEY_ENV_VAR",
    "BASE_URL_ENV_VAR",
    "DEFAULT_OPENROUTER_MODEL",
    "DEFAULT_OPENROUTER_MODEL_CHAIN",
    "FREE_TIER_BUDGET",
    "JSON_INSTRUCTION",
    "MODEL_ENV_VAR",
    "OPENROUTER_URL",
    "REASONING_OFF",
    "OpenRouterModel",
    "RequestBudget",
    "openrouter_model_chain",
    "openrouter_model_name",
    "openrouter_url",
    "parse_narration_json",
]
