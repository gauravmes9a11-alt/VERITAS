"""D7: per-client rate limiting as cost control. Sliding window per minute plus a
daily run budget. One client looping against a multi-step agent hits 429 long
before it can spend the budget."""
import time
import uuid

from .config import settings
from .state import r


def check(client_id: str):
    """Returns (allowed, reason, retry_after_s)."""
    now = time.time()
    k = f"rl:{client_id}"
    pipe = r().pipeline()
    pipe.zremrangebyscore(k, 0, now - 60)
    pipe.zcard(k)
    _, n = pipe.execute()
    if n >= settings.rate_per_min:
        oldest = r().zrange(k, 0, 0, withscores=True)
        wait = int(60 - (now - oldest[0][1])) + 1 if oldest else 60
        return False, "per_minute", max(wait, 1)
    day = time.strftime("%Y%m%d", time.gmtime())
    dk = f"rl:day:{client_id}:{day}"
    used = int(r().get(dk) or 0)
    if used >= settings.rate_daily:
        return False, "daily_budget", 3600
    r().zadd(k, {uuid.uuid4().hex: now})
    r().expire(k, 120)
    r().incr(dk)
    r().expire(dk, 90000)
    return True, "", 0
