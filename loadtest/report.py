"""Compare Locust CSV output with the pre-declared SLO: python loadtest/report.py results/lt_stats.csv"""
import csv
import json
import sys

slo = json.load(open("loadtest/slo.json"))
rows = {r["Name"]: r for r in csv.DictReader(open(sys.argv[1]))}
r = rows["end_to_end_run"]
p95, p50 = float(r["95%"]) / 1000, float(r["50%"]) / 1000
n, f = int(r["Request Count"]), int(r["Failure Count"])
print(f"runs={n} failures={f} RPS={float(r['Requests/s']):.3f} p50={p50:.1f}s p95={p95:.1f}s target_p95={slo['target_p95_s']}s")
print("SLO MET" if p95 <= slo["target_p95_s"] and (1 - f / max(n, 1)) >= slo["min_success_rate"] else "SLO NOT MET")
