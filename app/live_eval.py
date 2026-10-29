"""L6: sampled live scoring. Scores (computed by the advisor with app/scoring.py)
of a sample of live runs are kept in a time-ordered set, exported as gauges and
served at /v1/quality for charting."""
import json
import time

from . import metrics
from .state import r

KEY = "quality"


def record(run_id: str, scores: dict):
    r().zadd(KEY, {json.dumps({"run_id": run_id, **scores}): time.time()})
    r().zremrangebyrank(KEY, 0, -2001)
    rolling()


def series(limit=200):
    out = []
    for raw, ts in r().zrange(KEY, -limit, -1, withscores=True):
        d = json.loads(raw)
        d["ts"] = ts
        out.append(d)
    return out


def rolling(n=20):
    rows = series(n)
    agg = {}
    for m in ("groundedness", "faithfulness", "hallucination_rate", "context_relevance"):
        vals = [x[m] for x in rows if x.get(m) is not None]
        if vals:
            agg[m] = sum(vals) / len(vals)
            metrics.QUALITY.labels(m).set(agg[m])
    return agg
