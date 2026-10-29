"""D7: measured effect of caching on p95 and cost per run.
  API_URL=http://localhost:8000 python scripts/cache_benchmark.py [N=20]
Phase A: N runs with options.no_cache (retrieval/embedding caches still warm up).
Phase B: the same N questions twice more, whole-run cache enabled (first pass fills, second pass hits)."""
import json
import os
import statistics
import sys
import time

import httpx

API = os.environ.get("API_URL", "http://localhost:8000")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
QS = [f"An employee with {y} years of continuous service is retiring. Is gratuity payable?" for y in range(5, 5 + 6)]


def one(c, q, cid, no_cache):
    t0 = time.time()
    body = {"client_id": cid, "question": q, "options": {"no_cache": no_cache}}
    r = c.post(f"{API}/v1/advisories", json=body)
    r.raise_for_status()
    rid = r.json()["run_id"]
    while True:
        s = c.get(f"{API}/v1/advisories/{rid}").json()
        if s["status"] in ("completed", "failed"):
            return time.time() - t0, (s.get("cost") or {}).get("cost_usd", 0.0), bool(s.get("cached"))
        time.sleep(0.3)


def p95(x):
    x = sorted(x)
    return x[min(len(x) - 1, int(0.95 * len(x)))]


with httpx.Client(timeout=60) as c:
    out = {}
    for label, nc, rounds in (("no_whole_run_cache", True, 1), ("whole_run_cache_first_pass", False, 1), ("whole_run_cache_repeat", False, 1)):
        res = [one(c, QS[i % len(QS)], f"cb-{label[:6]}-{i % 50}", nc) for i in range(N)]
        lat = [x[0] for x in res]
        out[label] = {"p50_s": round(statistics.median(lat), 3), "p95_s": round(p95(lat), 3), "cost_usd_per_run": round(sum(x[1] for x in res) / N, 6), "cache_hits": sum(1 for x in res if x[2])}
        print(label, out[label])
os.makedirs("results", exist_ok=True)
json.dump(out, open("results/cache_benchmark.json", "w"), indent=2)
