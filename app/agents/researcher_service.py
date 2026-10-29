"""Research Agent (own container). Owns the retrieval tools and a bounded
LangGraph loop in which the LLM chooses the next tool. It never composes an
answer: its output is a bundle of passages, which is the hand-off contract."""
import json
import logging
import time
from datetime import date
from typing import Any, Dict, List, Optional, TypedDict

from fastapi import HTTPException
from langgraph.graph import END, StateGraph
from pydantic import BaseModel

from .. import llm, metrics, prompts
from ..config import settings
from ..corpus.store import status_asof
from ..guardrails import wrap_untrusted
from ..logging_setup import log
from ..resilience import CircuitOpen
from ..service_base import make_app
from ..tools import TOOL_SPECS, Toolbox
from .common import ev, index_health, load_store, passage

L = logging.getLogger("researcher")
STORE = load_store()
app = make_app("researcher", "Research Agent", lambda: index_health(STORE))


class ResearchIn(BaseModel):
    run_id: str
    question: str
    entities: Dict[str, Any]
    domain: str
    as_of_date: str
    prompt_version: str
    hints: List[str] = []
    round: int = 0
    deadline_epoch: Optional[float] = None


class RState(TypedDict):
    step: int
    tool_calls: int
    obs: list
    passages: dict
    queries: list
    trace: list
    stop: str
    action: dict
    invalid: int
    tokens: dict


def tools_text():
    return "\n".join(f"- {n}: {d}" for n, (_, d) in TOOL_SPECS.items())


def build_graph(req: ResearchIn, tb: Toolbox, system: str, pver: str, ptag: str):
    as_of = date.fromisoformat(req.as_of_date)
    deadline = req.deadline_epoch or (time.time() + settings.run_deadline_s)

    def decide(s: RState):
        if s["step"] >= settings.max_steps:
            return {"stop": "max_steps"}
        if s["tool_calls"] >= settings.max_tool_calls:
            return {"stop": "max_tool_calls"}
        if time.time() > deadline:
            return {"stop": "deadline"}
        top = next((p for p in s["passages"].values() if status_asof(p, as_of) == "in_force"), None)  # best-ranked in-force hit
        user = (f"[[STEP:{s['step'] + 1}]]\nAs-of date: {req.as_of_date}\nDomain: {req.domain}\n"
                f"Question: {wrap_untrusted(req.question)}\nRequest facts: {json.dumps({k: v for k, v in req.entities.items() if k != 'sources' and v is not None})}\n"
                f"Advisor hints (address first): {json.dumps(req.hints) if req.hints else 'none'}\n"
                f"Budget used: steps {s['step']}/{settings.max_steps}, tool calls {s['tool_calls']}/{settings.max_tool_calls}\n"
                f"Observations so far:\n{chr(10).join(s['obs'][-14:]) or '(none)'}\n"
                f"[[TOP_CLAUSE:{top['chunk_id'] if top else ''}]]\nChoose the next action.")
        t0 = time.time()
        res = llm.chat(system, user, step="researcher", deadline=deadline)
        metrics.STEP_LAT.labels("researcher", "decide").observe(time.time() - t0)
        trace = [ev("researcher", "llm", "decide", step=s["step"] + 1, prompt_version=pver, prompt_hash=ptag,
                    prompt_tokens=res.prompt_tokens, completion_tokens=res.completion_tokens, cost_usd=res.cost_usd,
                    latency_ms=round(res.latency_ms), attempts=res.attempts, output=res.text[:600])]
        try:
            action = llm.extract_json(res.text)
            assert isinstance(action, dict) and "action" in action
        except Exception:
            return {"step": s["step"] + 1, "invalid": s["invalid"] + 1, "trace": s["trace"] + trace, "action": {},
                    "stop": "llm_invalid_output" if s["invalid"] + 1 >= 2 else "",
                    "tokens": _tok(s, res)}
        return {"step": s["step"] + 1, "action": action, "trace": s["trace"] + trace, "tokens": _tok(s, res), "stop": ""}

    def act(s: RState):
        a = s["action"]
        name, args = a.get("action"), a.get("args") or {}
        t0 = time.time()
        res, outcome = tb.call(str(name), args if isinstance(args, dict) else {})
        dt = time.time() - t0
        metrics.STEP_LAT.labels("researcher", "tool:" + str(name)[:20]).observe(dt)
        passages, queries, obs = dict(s["passages"]), list(s["queries"]), list(s["obs"])
        if name == "search_corpus" and "hits" in res:
            queries.append({"query": args.get("query"), "strategy": args.get("strategy", "semantic")})
            for h in res["hits"]:
                passages[h["chunk_id"]] = passage(STORE.get(h["chunk_id"]))
            obs.append(f"search_corpus({args.get('query')!r}, {args.get('strategy','semantic')}):")
            obs += [f"  - {h['chunk_id']} | {h['label']} | {h['kind']} | {h['status']} | provisos={h['has_provisos']}"
                    + (f" | superseded_by={h['superseded_by']}" if h.get('superseded_by') else "") + f" | {h['snippet'][:140]}" for h in res["hits"]]
        elif name == "get_provisions" and "clauses" in res:
            for c in res["clauses"]:
                passages[c["chunk_id"]] = passage(STORE.get(c["chunk_id"]))
            obs.append(f"get_provisions({args.get('section_ref')}):")
            obs += [f"  - {c['chunk_id']} | {c['label']} | {c['kind']} | {c['status']} | {c['snippet'][:160]}" for c in res["clauses"]]
        elif name == "get_clause" and "clause" in res:
            c = res["clause"]
            passages[c["chunk_id"]] = passage(STORE.get(c["chunk_id"]))
            obs.append(f"get_clause: {c['chunk_id']} | {c['label']} | {c['status']} | {c['snippet'][:200]}")
        else:
            obs.append(f"{name}: {json.dumps(res)[:200]}")
        trace = s["trace"] + [ev("researcher", "tool", str(name), step=s["step"], args=args, outcome=outcome, latency_ms=round(dt * 1000),
                                 passages=[h["chunk_id"] for h in res.get("hits", [])] or [c["chunk_id"] for c in res.get("clauses", [])] or ([res["clause"]["chunk_id"]] if "clause" in res else []),
                                 query=args.get("query"))]
        return {"tool_calls": s["tool_calls"] + 1, "passages": passages, "queries": queries, "obs": obs, "trace": trace}

    def route(s: RState):
        if s["stop"]:
            return END
        if s["action"].get("action") == "finish":
            return "finish"
        return "act" if s["action"] else "decide"

    def finish(s):
        return {"stop": "finish"}

    g = StateGraph(RState)
    g.add_node("decide", decide)
    g.add_node("act", act)
    g.add_node("finish", finish)
    g.set_entry_point("decide")
    g.add_conditional_edges("decide", route, {END: END, "finish": "finish", "act": "act", "decide": "decide"})
    g.add_edge("act", "decide")
    g.add_edge("finish", END)
    return g.compile()


def _tok(s, res):
    t = dict(s["tokens"])
    t["prompt"] += res.prompt_tokens
    t["completion"] += res.completion_tokens
    t["cost"] += res.cost_usd
    return t


@app.post("/research")
def research(req: ResearchIn):
    if not STORE.chunks:
        raise HTTPException(503, "index not loaded")
    t0 = time.time()
    system, ptag = prompts.load("researcher", req.prompt_version)
    system = system.replace("{tools}", tools_text())
    tb = Toolbox(STORE, req.entities, req.domain, date.fromisoformat(req.as_of_date))
    g = build_graph(req, tb, system, req.prompt_version, ptag)
    init: RState = {"step": 0, "tool_calls": 0, "obs": [], "passages": {}, "queries": [], "trace": [], "stop": "", "action": {},
                    "invalid": 0, "tokens": {"prompt": 0, "completion": 0, "cost": 0.0}}
    try:
        out = g.invoke(init, {"recursion_limit": 4 * settings.max_steps + 10})
    except (CircuitOpen, llm.LLMError) as e:
        log(L, logging.WARNING, "llm unavailable in researcher", error=str(e))
        raise HTTPException(503, detail={"error": "llm_unavailable", "detail": str(e)})
    log(L, logging.INFO, "research done", steps=out["step"], tool_calls=out["tool_calls"], stop=out["stop"], passages=len(out["passages"]))
    return {"passages": out["passages"], "queries": out["queries"], "trace": out["trace"], "steps": out["step"],
            "tool_calls": out["tool_calls"], "stopped_reason": out["stop"], "tokens": out["tokens"],
            "duration_ms": round((time.time() - t0) * 1000), "index_version": STORE.manifest.get("index_version")}


@app.post("/degraded_search")
def degraded_search(req: ResearchIn):
    """No-LLM fallback path: deterministic retrieval + provisos of top in-force hits.
    The result is evidence for a human, never a verified answer."""
    as_of = date.fromisoformat(req.as_of_date)
    tb = Toolbox(STORE, req.entities, req.domain, as_of)
    from ..tools import SearchArgs, SectionArgs
    passages = {}
    hits = tb.search_corpus(SearchArgs(query=req.question[:300], strategy="lexical", top_k=5))["hits"]
    for h in hits:
        passages[h["chunk_id"]] = passage(STORE.get(h["chunk_id"]))
        c = STORE.get(h["chunk_id"])
        if c.get("has_provisos") and status_asof(c, as_of) == "in_force":
            for x in tb.get_provisions(SectionArgs(section_ref=f"{c['doc_id']}:s{c['section']}")).get("clauses", []):
                passages[x["chunk_id"]] = passage(STORE.get(x["chunk_id"]))
    return {"passages": passages, "index_version": STORE.manifest.get("index_version")}
