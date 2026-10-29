"""Advisor Agent (own container). Receives the Research Agent's bundle, composes
a cited advisory using only those passages, then verifies it (A3). It has no
retrieval tools: it can only reject and say what is missing."""
import json
import logging
import time
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel

from .. import llm, metrics, prompts, scoring
from ..config import settings
from ..corpus.store import status_asof
from ..guardrails import filter_output, wrap_untrusted
from ..logging_setup import log
from ..resilience import CircuitOpen
from ..service_base import make_app
from ..verify import verify
from .common import ev, index_health, load_store

L = logging.getLogger("advisor")
STORE = load_store()
app = make_app("advisor", "Advisor Agent", lambda: index_health(STORE))


class AdviseIn(BaseModel):
    run_id: str
    question: str
    entities: Dict[str, Any]
    domain: str
    as_of_date: str
    prompt_version: str
    passages: Dict[str, Dict[str, Any]]
    round: int = 0
    deadline_epoch: Optional[float] = None


def render_passages(passages, as_of):
    out = []
    for cid, p in passages.items():
        out.append(f"[[PASSAGE id={cid} label={p['label']!r} status={status_asof(p, as_of)} kind={p['kind']}]]\n{p['text']}\n[[/PASSAGE]]")
    return "\n".join(out)


@app.post("/advise")
def advise(req: AdviseIn):
    t0 = time.time()
    as_of = date.fromisoformat(req.as_of_date)
    system, ptag = prompts.load("advisor", req.prompt_version)
    trace, tokens, parsed = [], {"prompt": 0, "completion": 0, "cost": 0.0}, None
    user = (f"As-of date: {req.as_of_date}\nDomain: {req.domain}\nQuestion: {wrap_untrusted(req.question)}\n"
            f"Request facts: {json.dumps({k: v for k, v in req.entities.items() if k != 'sources' and v is not None})}\n"
            f"[[ROUND:{req.round}]]\nPassages (the only permitted sources):\n{render_passages(req.passages, as_of)}\nCompose the advisory.")
    for attempt in range(2):
        try:
            res = llm.chat(system, user + ("\nYour previous reply was not valid JSON. Reply with the JSON object only." if attempt else ""),
                           step="advisor", deadline=req.deadline_epoch)
        except (CircuitOpen, llm.LLMError) as e:
            raise HTTPException(503, detail={"error": "llm_unavailable", "detail": str(e)})
        tokens["prompt"] += res.prompt_tokens
        tokens["completion"] += res.completion_tokens
        tokens["cost"] += res.cost_usd
        trace.append(ev("advisor", "llm", "compose", prompt_version=req.prompt_version, prompt_hash=ptag, prompt_tokens=res.prompt_tokens,
                        completion_tokens=res.completion_tokens, cost_usd=res.cost_usd, latency_ms=round(res.latency_ms),
                        attempts=res.attempts, output=res.text[:800]))
        try:
            parsed = llm.extract_json(res.text)
            assert isinstance(parsed, dict)
            break
        except Exception:
            parsed = None
    metrics.STEP_LAT.labels("advisor", "compose").observe(time.time() - t0)

    def done(verdict, **kw):
        base = {"verdict": verdict, "trace": trace, "tokens": tokens, "duration_ms": round((time.time() - t0) * 1000)}
        base.update(kw)
        return base

    if parsed is None:
        metrics.VERIFY.labels("invalid_output").inc()
        return done("failed", gaps=[{"type": "advisor_invalid_output", "hint": "advisor returned invalid JSON"}], verification=None)
    if parsed.get("cannot_answer"):
        metrics.VERIFY.labels("cannot_answer").inc()
        trace.append(ev("advisor", "verify", "cannot_answer", reason=str(parsed.get("reason"))[:300]))
        return done("cannot_answer", reason=str(parsed.get("reason", ""))[:300], gaps=[], verification=None)

    assertions = [{"text": str(a.get("text", "")), "citations": [str(c) for c in (a.get("citations") or [])]} for a in (parsed.get("assertions") or []) if isinstance(a, dict)]
    t1 = time.time()
    vr = verify(assertions, req.passages, STORE, as_of)
    metrics.STEP_LAT.labels("advisor", "verify").observe(time.time() - t1)
    metrics.VERIFY.labels(vr["outcome"]).inc()
    trace.append(ev("advisor", "verify", "verify", outcome=vr["outcome"], checks=vr["checks"], gaps=vr["gaps"], latency_ms=round((time.time() - t1) * 1000)))
    text_all = " ".join([str(parsed.get("summary", "")), str(parsed.get("recommendation", ""))] + [a["text"] for a in assertions])
    ok_out, hits = filter_output(text_all)
    if not ok_out:
        metrics.GUARD.labels("output_filter", "advisor").inc()
        trace.append(ev("advisor", "guardrail", "output_filter", blocked=hits))
        return done("blocked_output", gaps=[], verification=vr, reason="output filter: advisory contained a determination the desk may not make")
    if not vr["ok"]:
        return done("failed", gaps=vr["gaps"], verification=vr)
    adv = {"summary": str(parsed.get("summary", "")), "recommendation": str(parsed.get("recommendation", "")),
           "conflict_detected": bool(parsed.get("conflict_detected", False)),
           "assertions": [{"text": a["text"], "citations": [{"chunk_id": c, "label": req.passages[c]["label"],
                                                              "status": status_asof(req.passages[c], as_of), "text": req.passages[c]["text"]} for c in a["citations"]]}
                          for a in assertions]}
    scores = scoring.score_run(req.question, assertions, req.passages, as_of)
    return done("verified", advisory=adv, verification=vr, scores=scores)
