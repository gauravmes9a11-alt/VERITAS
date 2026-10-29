import fakeredis
import pytest

from app import state
from app.resilience import CircuitBreaker, CircuitOpen, Retryable, call_with_retry


@pytest.fixture(autouse=True)
def redis():
    state.set_redis(fakeredis.FakeRedis(decode_responses=True))


def test_retry_then_success():
    n = {"c": 0}

    def f():
        n["c"] += 1
        if n["c"] < 3:
            raise Retryable("boom")
        return "ok"
    res, attempts = call_with_retry(f, target="t", retries=3, base=0.001, cap=0.01)
    assert res == "ok" and attempts == 3


def test_retry_bounded():
    n = {"c": 0}

    def f():
        n["c"] += 1
        raise Retryable("x")
    with pytest.raises(Retryable):
        call_with_retry(f, target="t", retries=2, base=0.001, cap=0.01)
    assert n["c"] == 3


def test_breaker_opens_and_fails_fast():
    br = CircuitBreaker("t", threshold=2, window_s=30, cooldown_s=5)

    def f():
        raise Retryable("x")
    with pytest.raises(Retryable):
        call_with_retry(f, target="t", retries=5, base=0.001, cap=0.01, breaker=br) if False else call_with_retry(f, target="t", retries=1, base=0.001, cap=0.01, breaker=br)
    assert br.state() == "open"
    with pytest.raises(CircuitOpen):
        call_with_retry(lambda: 1, target="t", retries=1, base=0.001, cap=0.01, breaker=br)
