"""Model providers. Fixed pair in v1: translation A = Claude, review B = OpenAI.

Keys live in memory for the session only and are never logged or written.
Each call returns a CallResult; errors are values, not exceptions, so the
pipeline can record "unevaluated: <reason>" instead of crashing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

TRANSLATE_MODEL = "claude-opus-5-5"
TRANSLATE_EFFORT = "medium"
# OpenAI review model: confirmed by Sho 2026-11-17 after spike 5 (dated snapshot, A/B comparable).
REVIEW_MODEL = "gpt-5.5-2026-04-23"
ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# USD per 1M tokens (input, output) as recorded on 2026-11-17. None = unknown.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    REVIEW_MODEL: None,
}

DESTINATIONS = {
    "translate": "api.anthropic.com (Anthropic, Claude)",
    "review": "api.openai.com (OpenAI)",
}


@dataclass
class CallResult:
    data: dict | None
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str = ""  # auth | rate_limit | timeout | network | refused | bad_response | api_error


class Provider:
    model = ""

    def call(self, system: str, user: str, schema: dict, max_tokens: int = 16000) -> CallResult:
        raise NotImplementedError


class ClaudeProvider(Provider):
    def __init__(self, api_key: str, model: str = TRANSLATE_MODEL, timeout: float = 180.0):
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=timeout)
        self.model = model

    def call(self, system, user, schema, max_tokens=16000):
        a = self._anthropic
        try:
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": TRANSLATE_EFFORT, "format": {"type": "json_schema", "schema": schema}},
                betas=[ANTHROPIC_FALLBACK_BETA],
                extra_body={"fallbacks": "default"},  # SDK 0.97 has no named argument for it
            )
        except a.AuthenticationError:
            return CallResult(None, self.model, error="auth")
        except a.RateLimitError:
            return CallResult(None, self.model, error="rate_limit")
        except a.APITimeoutError:
            return CallResult(None, self.model, error="timeout")
        except a.APIConnectionError:
            return CallResult(None, self.model, error="network")
        except a.APIStatusError as e:
            return CallResult(None, self.model, error=f"api_error {e.status_code}")
        u = resp.usage
        res = CallResult(None, getattr(resp, "model", self.model), u.input_tokens, u.output_tokens)
        if resp.stop_reason == "refusal":
            res.error = "refused"
            return res
        if resp.stop_reason == "max_tokens":
            res.error = "bad_response (cut off)"
            return res
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), None)
        try:
            res.data = json.loads(text)
        except (TypeError, ValueError):
            res.error = "bad_response"
        return res


class OpenAIProvider(Provider):
    URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, api_key: str, model: str = REVIEW_MODEL, timeout: float = 180.0, transport=None):
        self.model = model
        self._key = api_key
        self.http = httpx.Client(timeout=timeout, transport=transport)

    def call(self, system, user, schema, max_tokens=16000):
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "result", "strict": True, "schema": schema}},
            "max_completion_tokens": max_tokens,
        }
        try:
            r = self.http.post(self.URL, json=body, headers={"Authorization": f"Bearer {self._key}"})
        except httpx.TimeoutException:
            return CallResult(None, self.model, error="timeout")
        except httpx.HTTPError:
            return CallResult(None, self.model, error="network")
        if r.status_code == 401:
            return CallResult(None, self.model, error="auth")
        if r.status_code == 429:
            return CallResult(None, self.model, error="rate_limit")
        if r.status_code >= 400:
            return CallResult(None, self.model, error=f"api_error {r.status_code}")
        try:
            js = r.json()
            u = js.get("usage") or {}
            res = CallResult(None, js.get("model", self.model), u.get("prompt_tokens"), u.get("completion_tokens"))
            ch = js["choices"][0]
            if ch.get("finish_reason") == "length":
                res.error = "bad_response (cut off)"
                return res
            if ch["message"].get("refusal"):
                res.error = "refused"
                return res
            res.data = json.loads(ch["message"]["content"])
            return res
        except (ValueError, KeyError, IndexError, TypeError):
            return CallResult(None, self.model, error="bad_response")


class MockProvider(Provider):
    """Deterministic provider for tests and saved examples. fn(system, user, schema) -> dict | CallResult."""

    def __init__(self, fn, model="mock"):
        self.fn = fn
        self.model = model

    def call(self, system, user, schema, max_tokens=16000):
        out = self.fn(system, user, schema)
        if isinstance(out, CallResult):
            return out
        return CallResult(out, self.model, None, None)


def estimate_cost(usages) -> dict:
    """Sum only what was reported and priced; the rest is 'unknown'."""
    known, unknown_calls = 0.0, 0
    for u in usages:
        price = PRICES.get(u.model)
        if price is None or u.input_tokens is None or u.output_tokens is None:
            unknown_calls += 1
            continue
        known += u.input_tokens / 1e6 * price[0] + u.output_tokens / 1e6 * price[1]
    return {"usd_known": round(known, 4), "calls_unknown": unknown_calls}
