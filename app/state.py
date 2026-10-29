"""Redis access and run/trace persistence."""
import json
import time
import redis
from .config import settings

_r = None


def r() -> redis.Redis:
    global _r
    if _r is None:
        _r = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    return _r


_rq = None


def rq_conn() -> redis.Redis:
    """RQ stores pickled/bytes payloads, so it needs a connection WITHOUT decode_responses."""
    global _rq
    if _rq is None:
        _rq = redis.Redis.from_url(settings.redis_url)
    return _rq


def set_redis(client):  # tests
    global _r
    _r = client


def save_run(run: dict):
    run["updated_at"] = time.time()
    r().set(f"run:{run['run_id']}", json.dumps(run, default=str))


def load_run(run_id: str):
    v = r().get(f"run:{run_id}")
    return json.loads(v) if v else None


def add_trace(run_id: str, events):
    if not events:
        return
    if isinstance(events, dict):
        events = [events]
    r().rpush(f"trace:{run_id}", *[json.dumps(e, default=str) for e in events])


def get_trace(run_id: str):
    return [json.loads(x) for x in r().lrange(f"trace:{run_id}", 0, -1)]
