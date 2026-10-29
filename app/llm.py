"""Provider-agnostic LLM client (OpenAI-compatible). Timeouts on every call,
retry/backoff, shared circuit breaker, token/cost capture per step (L5)."""
import json
import logging
import re
import time
from dataclasses import dataclass, field

import httpx

from . import metrics
from .config import settings
from .logging_setup import correlation_id, log
from .resilience import CircuitBreaker, CircuitOpen, Retryable, call_with_retry

L = logging.getLogger("llm")
breaker = CircuitBreaker("llm_provider", settings.breaker_threshold, settings.breaker_window_s, settings.breaker_cooldown_s)


class LLMError(Exception):
    pass


@dataclass
class LLMResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: float
    attempts: int
    model: str = ""


def extract_json(text: str):
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    s, e = t.find("{"), t.rfind("}")
    if s >= 0 and e > s:
        return json.loads(t[s:e + 1])
    raise ValueError("no JSON object in model output")


def cost_of(pt: int, ct: int) -> float:
    return pt / 1000 * settings.price_in + ct / 1000 * settings.price_out


def chat(system: str, user: str, *, step: str, deadline: float = None) -> LLMResult:
    """One model call. Raises CircuitOpen if the provider breaker is open, LLMError
    when retries are exhausted."""
    if not settings.llm_base_url or not settings.llm_model:
        raise LLMError("LLM_BASE_URL / LLM_MODEL not configured")
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json", "X-Correlation-ID": correlation_id.get()}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"
    body = {"model": settings.llm_model, "temperature": 0, "max_tokens": settings.llm_max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if settings.llm_json_mode:
        body["response_format"] = {"type": "json_object"}
    timeout = settings.llm_timeout_s
    if deadline:
        timeout = max(1.0, min(timeout, deadline - time.time()))

    def once():
        try:
            resp = httpx.post(url, json=body, headers=headers, timeout=httpx.Timeout(timeout))
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise Retryable(f"transport: {type(e).__name__}")
        if resp.status_code == 429 or resp.status_code >= 500:
            raise Retryable(f"http {resp.status_code}")
        if resp.status_code >= 400:
            raise LLMError(f"provider rejected request: http {resp.status_code}")
        return resp.json()

    t0 = time.time()
    try:
        data, attempts = call_with_retry(once, target="llm_provider", retries=settings.retry_max,
                                         base=settings.retry_base_s, cap=settings.retry_cap_s, breaker=breaker, deadline=deadline)
    except Retryable as e:
        raise LLMError(f"provider unavailable after retries: {e}")
    lat = (time.time() - t0) * 1000
    try:
        text = data["choices"][0]["message"]["content"] or ""
        u = data.get("usage") or {}
        pt, ct = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))
    except Exception:
        raise LLMError("malformed provider response")
    if pt == 0:  # provider gave no usage: estimate so cost accounting never silently reads zero
        pt, ct = (len(system) + len(user)) // 4, len(text) // 4
    cost = cost_of(pt, ct)
    metrics.LLM_TOKENS.labels(step, "prompt").inc(pt)
    metrics.LLM_TOKENS.labels(step, "completion").inc(ct)
    metrics.LLM_COST.labels(step).inc(cost)
    metrics.LLM_LAT.labels(step).observe(lat / 1000)
    log(L, logging.INFO, "llm call", step=step, prompt_tokens=pt, completion_tokens=ct, cost_usd=cost, latency_ms=round(lat), attempts=attempts)
    return LLMResult(text, pt, ct, cost, lat, attempts, settings.llm_model)
