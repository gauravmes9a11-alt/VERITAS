"""L5: cost per 100 runs and largest cost driver, from per-step costs recorded at call time.
  python scripts/cost_report.py            (reads runs from Redis; run inside any app container)"""
import json
import sys
from collections import defaultdict
from app.state import r

runs = [json.loads(r().get(k)) for k in r().scan_iter("run:*", count=1000)]
runs = [x for x in runs if x.get("status") == "completed" and not x.get("cached") and x.get("cost")]
if not runs:
    sys.exit("no completed, non-cached runs with cost data")
by = defaultdict(lambda: [0.0, 0, 0])
for x in runs:
    for step, b in x["cost"]["by_step"].items():
        by[step][0] += b["cost_usd"]; by[step][1] += b["prompt_tokens"]; by[step][2] += b["completion_tokens"]
tot = sum(v[0] for v in by.values())
n = len(runs)
print(f"runs counted: {n}")
print(f"cost per 100 runs: ${tot / n * 100:.4f}   (prompt tokens/run {sum(v[1] for v in by.values())/n:.0f}, completion tokens/run {sum(v[2] for v in by.values())/n:.0f})")
for s, (c, p, k) in sorted(by.items(), key=lambda kv: -kv[1][0]):
    print(f"  {s:12} ${c/n*100:.4f}/100 runs  {c/tot*100 if tot else 0:5.1f}% of cost  prompt {p/n:.0f} completion {k/n:.0f} tokens/run")
if tot == 0:
    print("note: costs are $0 because LLM_PRICE_IN/OUT_PER_1K are 0 (stub). Token counts above still show the driver; set prices for real dollars.")
big = max(by, key=lambda s: by[s][1] + by[s][2])
print(f"largest driver by tokens: {big}")
