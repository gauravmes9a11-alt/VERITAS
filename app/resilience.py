"""A5: bounded retry with exponential backoff + jitter, and a circuit breaker whose
state lives in Redis so every worker/agent process sees the same provider health."""
import logging
import random
import time

from . import metrics
from .logging_setup import log
from .state import r

L = logging.getLogger("resilience")


class CircuitOpen(Exception):
    pass


class Retryable(Exception):
    """Raise inside a call to signal a retryable failure (timeouts, 5xx, 429)."""


class CircuitBreaker:
    def __init__(self, name, threshold, window_s, cooldown_s):
        self.name, self.threshold, self.window, self.cooldown = name, threshold, window_s, cooldown_s
        self.k_fail, self.k_open, self.k_probe = f"cb:{name}:fail", f"cb:{name}:open_until", f"cb:{name}:probe"

    def state(self) -> str:
        try:
            ou = r().get(self.k_open)
        except Exception:
            return "closed"
        if ou is None:
            return "closed"
        return "open" if time.time() < float(ou) else "half_open"

    def allow(self) -> bool:
        s = self.state()
        metrics.BREAKER_STATE.labels(self.name).set(1 if s == "open" else 0)
        if s == "closed":
            return True
        if s == "open":
            return False
        # half-open: let exactly one probe through
        return bool(r().set(self.k_probe, "1", nx=True, ex=int(max(self.cooldown, 5))))

    def success(self):
        try:
            r().delete(self.k_fail, self.k_open, self.k_probe)
        except Exception:
            pass
        metrics.BREAKER_STATE.labels(self.name).set(0)

    def failure(self):
        try:
            n = r().incr(self.k_fail)
            r().expire(self.k_fail, int(self.window))
            if n >= self.threshold or self.state() == "half_open":
                r().set(self.k_open, str(time.time() + self.cooldown), ex=int(self.cooldown * 4))
                r().delete(self.k_probe)
                metrics.BREAKER_STATE.labels(self.name).set(1)
                metrics.BREAKER_TRIPS.labels(self.name).inc()
                log(L, logging.WARNING, "circuit opened", target=self.name, failures=n, cooldown_s=self.cooldown)
        except Exception:
            pass


def call_with_retry(fn, *, target, retries, base, cap, breaker: CircuitBreaker = None, deadline: float = None):
    """Call fn() with retry on Retryable. Every attempt is guarded by the breaker.
    Returns (result, attempts). Raises CircuitOpen or the last Retryable."""
    attempt = 0
    while True:
        if breaker and not breaker.allow():
            raise CircuitOpen(target)
        attempt += 1
        metrics.CALLS.labels(target).inc()
        try:
            res = fn()
            if breaker:
                breaker.success()
            return res, attempt
        except Retryable as e:
            if breaker:
                breaker.failure()
            if attempt > retries:
                raise
            delay = random.uniform(0, min(cap, base * (2 ** (attempt - 1))))  # full jitter
            if deadline and time.time() + delay > deadline:
                raise
            metrics.RETRIES.labels(target).inc()
            log(L, logging.INFO, "retrying", target=target, attempt=attempt, delay_s=round(delay, 2), error=str(e)[:120])
            time.sleep(delay)
