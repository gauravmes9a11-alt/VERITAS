"""L3: batch evaluation against the running API.
  python -m eval.run --label baseline [--prompt-version v1] [--out results/eval_baseline.json]
Reports route accuracy, gold-clause recall (retrieved) and precision (cited), repealed-citation
violations, and the four quality metrics (groundedness, context relevance, faithfulness,
hallucination rate; definitions in app/scoring.py). Metric scoring needs the index (INDEX_ROOT)."""
import argparse
import json
import os
import statistics
import sys
import time
from datetime import date
from pathlib import Path

import httpx

from app import scoring
from app.config import settings
from app.corpus.embedder import get_embedder
from app.corpus.store import IndexStore

API = os.environ.get("API_URL", "http://localhost:8000")


def load_store():
    emb = get_embedder(settings.embed_backend, settings.embed_model, settings.embed_dim)
    return IndexStore(Path(settings.index_root) / settings.index_name, emb.dim).load()


def run_one(c, sc, pv):
    body = {"client_id": f"eval-{sc['id'].lower()}-{int(time.time()) % 100000}", "question": sc["question"], "options": {"no_cache": True}}
    if pv:
        body["options"]["prompt_version"] = pv
    for k in ("employment", "establishment", "as_of_date"):
        if k in sc:
            body[k] = sc[k]
    t0 = time.time()
    r = c.post(f"{API}/v1/advisories", json=body)
    if r.status_code != 202:
        return {"id": sc["id"], "http": r.status_code, "route": "blocked" if r.status_code in (400, 422) else "error"}
    rid = r.json()["run_id"]
    while time.time() - t0 < 180:
        s = c.get(f"{API}/v1/advisories/{rid}").json()
        if s["status"] in ("completed", "failed"):
            tr = c.get(f"{API}/v1/advisories/{rid}/trace").json()
            return {"id": sc["id"], "http": 202, "run": s, "trace": tr, "latency_s": time.time() - t0}
        time.sleep(0.7)
    return {"id": sc["id"], "http": 202, "route": "timeout"}


def evaluate(label, pv, out):
    store = load_store()
    scenarios = [json.loads(l) for l in Path("eval/scenarios.jsonl").read_text().splitlines()]
    rows, with_metrics = [], []
    with httpx.Client(timeout=30) as c:
        for sc in scenarios:
            res = run_one(c, sc, pv)
            row = {"id": sc["id"], "expected_route": sc["expected_route"], "note": sc.get("note", "")}
            if "run" not in res:
                row.update(route=res["route"], http=res["http"])
                row["route_ok"] = (res["route"] == sc["expected_route"]) and sc.get("expected_http", res["http"]) == res["http"]
            else:
                run, tr = res["run"], res["trace"]
                retrieved = {i for e in tr["events"] if e.get("kind") == "tool" for i in (e.get("passages") or [])}
                cited = {ci["chunk_id"] for a in run.get("assertions", []) for ci in a["citations"]}
                gold = set(sc.get("gold_clauses", []))
                bad = [i for i in cited if i.split(":")[0] in sc.get("must_not_cite_docs", [])]
                row.update(route=run["route"], route_ok=run["route"] == sc["expected_route"], degraded=run.get("degraded"),
                           gold_retrieved=(len(gold & retrieved) / len(gold)) if gold else None,
                           gold_cited=(len(gold & cited) / len(gold)) if gold else None,
                           repealed_cited=bool(bad), latency_s=round(res["latency_s"], 2), cost_usd=(run.get("cost") or {}).get("cost_usd"),
                           prompt_version=run.get("prompt_version"), tool_calls=run.get("tool_calls"), rounds=run.get("rounds"))
                if run.get("assertions"):
                    passages = {i: store.get(i) for i in retrieved if store.get(i)}
                    asserts = [{"text": a["text"], "citations": [ci["chunk_id"] for ci in a["citations"]]} for a in run["assertions"]]
                    sc_ = scoring.score_run(sc["question"], asserts, passages, date.fromisoformat(run.get("as_of") or sc.get("as_of_date") or date.today().isoformat()))
                    row["metrics"] = sc_
                    with_metrics.append(sc_)
            rows.append(row)
            print(f"{row['id']} expected={row['expected_route']:<35} got={row.get('route'):<35} ok={row.get('route_ok')}", flush=True)

    def mean(k):
        v = [m[k] for m in with_metrics if m.get(k) is not None]
        return round(statistics.mean(v), 4) if v else None
    gr = [r["gold_retrieved"] for r in rows if r.get("gold_retrieved") is not None]
    gc = [r["gold_cited"] for r in rows if r.get("gold_cited") is not None]
    summary = {"label": label, "prompt_version": pv or settings.prompt_version, "n": len(rows),
               "route_accuracy": round(sum(1 for r in rows if r["route_ok"]) / len(rows), 4),
               "gold_recall_retrieved": round(statistics.mean(gr), 4) if gr else None,
               "gold_recall_cited": round(statistics.mean(gc), 4) if gc else None,
               "repealed_citation_violations": sum(1 for r in rows if r.get("repealed_cited")),
               "case_4_1_correct": next((r["route_ok"] and r.get("gold_cited") == 1.0 for r in rows if r["id"] == "S01"), False),
               "groundedness": mean("groundedness"), "context_relevance": mean("context_relevance"),
               "faithfulness": mean("faithfulness"), "hallucination_rate": mean("hallucination_rate"),
               "mean_latency_s": round(statistics.mean([r["latency_s"] for r in rows if r.get("latency_s")] or [0]), 2),
               "mean_cost_usd": round(statistics.mean([r["cost_usd"] for r in rows if r.get("cost_usd") is not None] or [0]), 6)}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--prompt-version")
    ap.add_argument("--out")
    a = ap.parse_args()
    evaluate(a.label, a.prompt_version, a.out or f"results/eval_{a.label}.json")
