"""Run coordinator (executed by the RQ worker). A LangGraph state machine that
hands work between two independent agent services over HTTP:

  prep -> research -> advise -> finalize            (happy path)
  advise -> research  (verification gaps; <= MAX_ROUNDS)
  research/advise -> degrade -> finalize            (LLM or agent unavailable)
  prep -> finalize                                   (escalate / ask for detail, no LLM spend)

Failure semantics are in docs/architecture.md. Invariant: a run that did not
complete verification is never returned as answer-with-citation."""
import logging
import random
import time
from datetime import date, datetime
from typing import Any, TypedDict

import httpx
from langgraph.graph import END, StateGraph

from . import metrics, state
from .classify import classify_domain, detect_intent, extract_entities, missing_fields, straddles_commencement
from .config import settings
from .contracts import AskRequest
from .guardrails import filter_output
from .logging_setup import correlation_id, log
from .resilience import CircuitBreaker, CircuitOpen, Retryable, call_with_retry
from .telemetry import tracer

L = logging.getLogger("coordinator")
BR = {n: CircuitBreaker(n, settings.breaker_threshold, settings.breaker_window_s, settings.breaker_cooldown_s) for n in ("researcher_svc", "advisor_svc")}
ANSWER, MORE, LEGAL = "answer-with-citation", "request-more-establishment-detail", "legal-counsel-review"


class LLMUnavailable(Exception):
    pass


class S(TypedDict, total=False):
    run: dict
    req: dict
    entities: dict
    domain: str
    as_of: str
    pver: str
    deadline: float
    passages: dict
    queries: list
    hints: list
    rounds: int
    steps: int
    tool_calls: int
    cost: dict
    advisory: Any
    verification: Any
    route: str
    basis: str
    degraded: bool
    unverified: bool
    escalation: dict
    missing: list
    index_version: str
    scores: dict


def _trace(s, ev):
    ev.setdefault("ts", round(time.time(), 3))
    ev.setdefault("agent", "coordinator")
    state.add_trace(s["run"]["run_id"], ev)


def _add_cost(s, agent, tokens):
    c = s["cost"]
    c.setdefault("by_step", {}).setdefault(agent, {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0})
    b = c["by_step"][agent]
    b["prompt_tokens"] += tokens.get("prompt", 0)
    b["completion_tokens"] += tokens.get("completion", 0)
    b["cost_usd"] += tokens.get("cost", 0.0)


def call_agent(base, path, payload, target, deadline):
    url = base.rstrip("/") + path
    headers = {"X-Correlation-ID": correlation_id.get()}
    timeout = max(2.0, min(settings.agent_http_timeout_s, deadline - time.time()))

    def once():
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=httpx.Timeout(timeout))
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise Retryable(f"{target}: {type(e).__name__}")
        if resp.status_code == 503:
            d = resp.json().get("detail") if resp.headers.get("content-type", "").startswith("application/json") else None
            if isinstance(d, dict) and d.get("error") == "llm_unavailable":
                raise LLMUnavailable(d.get("detail", ""))
            raise Retryable(f"{target}: 503")
        if resp.status_code >= 500:
            raise Retryable(f"{target}: http {resp.status_code}")
        if resp.status_code >= 400:
            raise RuntimeError(f"{target}: http {resp.status_code} {resp.text[:200]}")
        return resp.json()

    res, _ = call_with_retry(once, target=target, retries=min(settings.retry_max, 2), base=settings.retry_base_s,
                             cap=settings.retry_cap_s, breaker=BR[target], deadline=deadline)
    return res


# ------------------------------------------------------------------ nodes
def prep(s: S):
    req = AskRequest(**s["req"])
    as_of = (req.as_of_date or date.today())
    domain = classify_domain(req.question)
    ent = extract_entities(req)
    intent = detect_intent(req.question, domain)
    out = {"domain": domain, "entities": ent, "as_of": as_of.isoformat(), "passages": {}, "queries": [], "hints": [], "rounds": 0,
           "steps": 0, "tool_calls": 0, "cost": {"by_step": {}}, "degraded": False}
    _trace(s, {"kind": "classify", "name": "classify", "domain": domain, "entities": {k: v for k, v in ent.items() if k != "sources"}, "intent": intent})
    route = None
    if domain == "other":
        route, basis = LEGAL, "The question falls outside gratuity, maternity benefit and workplace harassment, which are the only areas this desk's corpus covers."
    elif intent["termination_dispute"]:
        route, basis = LEGAL, "Termination disputes are never answered automatically; a qualified professional must review."
    elif intent["harassment_complaint"]:
        route, basis = LEGAL, "A harassment complaint is a live matter; the desk escalates instead of advising on it."
    elif straddles_commencement(ent, as_of):
        route, basis = LEGAL, "Service began before 21 Nov 2025 and continues after it; the repealed Act, the Code and any savings/transitional provisions may all apply, so provisions may conflict."
    if route:
        out.update(route=route, basis=basis)
    else:
        miss = missing_fields(domain, ent)
        if miss:
            out.update(route=MORE, basis=f"The facts needed to apply the {domain.replace('_', ' ')} provisions are missing: {', '.join(miss)}.", missing=miss)
    _trace(s, {"kind": "route", "name": "pre_route", "route": out.get("route"), "basis": out.get("basis")})
    return out


def after_prep(s: S):
    return "finalize" if s.get("route") else "research"


def research(s: S):
    t0 = time.time()
    if time.time() > s["deadline"]:
        return {"degraded": True, "basis": "deadline exceeded before research"}
    payload = {"run_id": s["run"]["run_id"], "question": s["req"]["question"], "entities": s["entities"], "domain": s["domain"],
               "as_of_date": s["as_of"], "prompt_version": s["pver"], "hints": s["hints"], "round": s["rounds"], "deadline_epoch": s["deadline"]}
    try:
        with tracer().start_as_current_span("agent.research"):
            res = call_agent(settings.researcher_url, "/research", payload, "researcher_svc", s["deadline"])
    except (LLMUnavailable, CircuitOpen, Retryable, RuntimeError) as e:
        _trace(s, {"kind": "failure", "name": "researcher_failed", "error": str(e)[:200], "type": type(e).__name__})
        return {"degraded": True, "basis": f"research agent unavailable ({type(e).__name__}: {str(e)[:100]})"}
    for ev in res["trace"]:
        state.add_trace(s["run"]["run_id"], ev)
    passages = dict(s["passages"])
    passages.update(res["passages"])
    _add_cost(s, "researcher", res["tokens"])
    metrics.STEP_LAT.labels("coordinator", "research").observe(time.time() - t0)
    return {"passages": passages, "queries": s["queries"] + res["queries"], "steps": s["steps"] + res["steps"],
            "tool_calls": s["tool_calls"] + res["tool_calls"], "index_version": res.get("index_version"), "cost": s["cost"]}


def after_research(s: S):
    return "degrade" if s.get("degraded") else "advise"


def advise(s: S):
    t0 = time.time()
    payload = {"run_id": s["run"]["run_id"], "question": s["req"]["question"], "entities": s["entities"], "domain": s["domain"],
               "as_of_date": s["as_of"], "prompt_version": s["pver"], "passages": s["passages"], "round": s["rounds"], "deadline_epoch": s["deadline"]}
    try:
        with tracer().start_as_current_span("agent.advise"):
            res = call_agent(settings.advisor_url, "/advise", payload, "advisor_svc", s["deadline"])
    except (LLMUnavailable, CircuitOpen, Retryable, RuntimeError) as e:
        _trace(s, {"kind": "failure", "name": "advisor_failed", "error": str(e)[:200], "type": type(e).__name__})
        return {"degraded": True, "unverified": True, "basis": f"advisor unavailable ({type(e).__name__}: {str(e)[:100]})"}
    for ev in res["trace"]:
        state.add_trace(s["run"]["run_id"], ev)
    _add_cost(s, "advisor", res["tokens"])
    metrics.STEP_LAT.labels("coordinator", "advise").observe(time.time() - t0)
    v = res["verdict"]
    out = {"verification": res.get("verification"), "cost": s["cost"]}
    if v == "verified":
        adv = res["advisory"]
        out["scores"] = res.get("scores")
        if adv.get("conflict_detected"):
            out.update(route=LEGAL, basis="The advisor detected conflicting provisions; escalated.", advisory=adv)
        else:
            out.update(advisory=adv, route=ANSWER, basis="Every assertion cites a retrieved, in-force provision and passed verification (resolution, status, support, proviso completeness).")
    elif v == "cannot_answer":
        out.update(route=LEGAL, basis=f"The corpus does not support an answer: {res.get('reason','')}")
    elif v == "blocked_output":
        out.update(route=LEGAL, basis=res.get("reason", "output filter blocked the advisory"))
    else:
        gaps = res.get("gaps", [])
        if s["rounds"] + 1 <= settings.max_rounds and time.time() < s["deadline"]:
            out.update(rounds=s["rounds"] + 1, hints=[g["hint"] for g in gaps][:6] + ["use a different strategy from the previous round"], route=None)
            _trace(s, {"kind": "route", "name": "retry_research", "round": s["rounds"] + 1, "gaps": gaps})
        else:
            out.update(route=LEGAL, unverified=True, basis=f"Verification failed after {s['rounds'] + 1} round(s) ({'; '.join(g['type'] for g in gaps) or 'unsupported'}); no unverified answer is returned.")
    return out


def after_advise(s: S):
    if s.get("degraded"):
        return "degrade"
    return "finalize" if s.get("route") else "research"


def degrade(s: S):
    """Fallback path: retrieval-only evidence for a human, never an answer."""
    passages, reason = s.get("passages") or {}, s.get("basis", "")
    if not passages:
        try:
            res = call_agent(settings.researcher_url, "/degraded_search",
                             {"run_id": s["run"]["run_id"], "question": s["req"]["question"], "entities": s["entities"], "domain": s["domain"],
                              "as_of_date": s["as_of"], "prompt_version": s["pver"]}, "researcher_svc", time.time() + 15)
            passages = res["passages"]
        except Exception as e:
            _trace(s, {"kind": "failure", "name": "degraded_search_failed", "error": str(e)[:200]})
    metrics.GUARD  # keep import used
    _trace(s, {"kind": "route", "name": "degrade", "reason": reason, "passages": list(passages)})
    return {"passages": passages, "route": LEGAL, "degraded": True, "unverified": True,
            "basis": f"Degraded mode: {reason}. Unverified provisions are attached for the reviewer; no answer was produced."}


def finalize(s: S):
    run = s["run"]
    as_of = date.fromisoformat(s["as_of"]) if s.get("as_of") else date.today()
    route, basis = s["route"], s["basis"]
    adv = s.get("advisory")
    if route == ANSWER:  # last line of defence
        text = " ".join([adv["summary"], adv["recommendation"]] + [a["text"] for a in adv["assertions"]])
        ok, hits = filter_output(text)
        if not ok or not all(a["citations"] for a in adv["assertions"]):
            metrics.GUARD.labels("output_filter", "coordinator").inc()
            route, basis, adv = LEGAL, "Output filter blocked the advisory at the final gate.", None
    pr = s.get("passages") or {}
    esc = None
    if route == LEGAL:
        esc = {"to": "legal-counsel", "reason": basis, "domain": s.get("domain"), "as_of": s.get("as_of"),
               "facts": {k: v for k, v in (s.get("entities") or {}).items() if k != "sources" and v is not None},
               "provisions_considered": [{"chunk_id": k, "label": p["label"], "kind": p["kind"], "text": p["text"][:400],
                                          "unverified": bool(s.get("unverified") or s.get("degraded"))} for k, p in list(pr.items())[:12]]}
    total = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
    for b in s["cost"]["by_step"].values():
        total["prompt_tokens"] += b["prompt_tokens"]; total["completion_tokens"] += b["completion_tokens"]; total["cost_usd"] += b["cost_usd"]
    now = time.time()
    run.update({"status": "completed", "route": route, "route_basis": basis, "prompt_version": s["pver"], "index_version": s.get("index_version"),
                "summary": adv["summary"] if adv else None, "recommendation": adv["recommendation"] if adv else None,
                "assertions": adv["assertions"] if adv else [], "escalation": esc, "missing_fields": s.get("missing") or [],
                "degraded": bool(s.get("degraded")), "verification": s.get("verification"), "domain": s.get("domain"), "as_of": s.get("as_of"),
                "entities": {k: v for k, v in (s.get("entities") or {}).items() if k != "sources"},
                "cost": {**total, "by_step": s["cost"]["by_step"]}, "steps": s.get("steps", 0), "tool_calls": s.get("tool_calls", 0),
                "rounds": s.get("rounds", 0), "finished_at": now, "duration_s": round(now - run["created_at"], 3)})
    state.save_run(run)
    _trace(s, {"kind": "route", "name": "final", "route": route, "basis": basis, "degraded": run["degraded"], "prompt_version": s["pver"],
               "duration_s": run["duration_s"], "cost": run["cost"]})
    metrics.RUNS.labels("completed", route, str(run["degraded"]).lower()).inc()
    metrics.RUN_LAT.observe(now - run["created_at"])
    if s.get("steps") is not None and (s.get("steps") or s.get("tool_calls")):
        metrics.STEPS_PER_RUN.observe(s["steps"])
        metrics.TOOLS_PER_RUN.observe(s["tool_calls"])
    if run.get("cache_key") and not run["degraded"] and settings.cache_enabled:
        import json
        state.r().set(f"cache:{run['cache_key']}", json.dumps({k: run[k] for k in ("route", "route_basis", "summary", "recommendation", "assertions", "escalation",
                      "missing_fields", "verification", "domain", "entities", "prompt_version", "index_version")}, default=str), ex=settings.cache_ttl_s)
    if s.get("scores") and random.random() < settings.live_eval_rate:
        from . import live_eval
        live_eval.record(run["run_id"], s["scores"])
    return {}


def build():
    g = StateGraph(S)
    for n, f in [("prep", prep), ("research", research), ("advise", advise), ("degrade", degrade), ("finalize", finalize)]:
        g.add_node(n, f)
    g.set_entry_point("prep")
    g.add_conditional_edges("prep", after_prep, {"finalize": "finalize", "research": "research"})
    g.add_conditional_edges("research", after_research, {"degrade": "degrade", "advise": "advise"})
    g.add_conditional_edges("advise", after_advise, {"degrade": "degrade", "finalize": "finalize", "research": "research"})
    g.add_edge("degrade", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


GRAPH = build()


def execute_run(run_id: str):
    """RQ job entry point."""
    run = state.load_run(run_id)
    if not run:
        return
    tok = correlation_id.set(run_id)
    started = time.time()
    try:
        metrics.QUEUE_WAIT.observe(started - run["created_at"])
        run["status"], run["started_at"] = "running", started
        state.save_run(run)
        pver = (run["request"].get("options") or {}).get("prompt_version") if settings.allow_options_override else None
        init: S = {"run": run, "req": run["request"], "pver": pver or settings.prompt_version, "deadline": started + settings.run_deadline_s}
        with tracer().start_as_current_span("run", attributes={"run_id": run_id}):
            GRAPH.invoke(init, {"recursion_limit": 40})
    except Exception as e:  # never leave a run hanging, never return a confident answer
        log(L, logging.ERROR, "run crashed", error=str(e))
        run = state.load_run(run_id) or run
        now = time.time()
        run.update({"status": "failed", "route": LEGAL, "route_basis": f"internal error: {type(e).__name__}", "finished_at": now,
                    "duration_s": round(now - run["created_at"], 3), "escalation": {"to": "legal-counsel", "reason": "internal error; no advisory produced"}})
        state.save_run(run)
        state.add_trace(run_id, {"ts": now, "agent": "coordinator", "kind": "failure", "name": "run_crashed", "error": str(e)[:300]})
        metrics.RUNS.labels("failed", LEGAL, "false").inc()
        metrics.RUN_LAT.observe(now - run["created_at"])
    finally:
        correlation_id.reset(tok)
