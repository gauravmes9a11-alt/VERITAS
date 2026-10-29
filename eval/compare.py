"""Print before/after for two result files: python -m eval.compare results/a.json results/b.json"""
import json
import sys

a, b = [json.load(open(p))["summary"] for p in sys.argv[1:3]]
keys = ["route_accuracy", "gold_recall_retrieved", "gold_recall_cited", "groundedness", "context_relevance", "faithfulness",
        "hallucination_rate", "repealed_citation_violations", "case_4_1_correct", "mean_latency_s", "mean_cost_usd"]
print(f"{'metric':32}{a['label']:>16}{b['label']:>16}{'delta':>12}")
for k in keys:
    x, y = a.get(k), b.get(k)
    d = (round(y - x, 4) if isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool) else "")
    print(f"{k:32}{str(x):>16}{str(y):>16}{str(d):>12}")
