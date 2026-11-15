"""Public REST API (F1, A4, D1). Submission validates and returns a run id
immediately (202); a queue + worker execute the run; the caller polls
GET /v1/advisories/{run_id}.

Why async + polling: runs take tens of seconds (several model calls). Holding an
HTTP connection that long ties up proxies/timeouts and couples client latency to
provider latency. 202 + poll is stateless for the client, trivially retryable,
and needs no inbound network path to the caller (a callback would add SSRF and
delivery-retry concerns for no benefit here).

Dual-Portal Extension (2026): New /auth, /employee, /hr, /admin routers added.
All portal routes require JWT authentication. The existing /v1/* endpoints are
preserved exactly. The frontend (web/index.html) now serves the dual-portal SPA.
"""
import hashlib
import json
import logging
import time
import uuid
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from rq import Queue

from . import live_eval, metrics, prompts, state
from .config import settings
from .contracts import AskRequest, FeedbackIn, field_errors
from .db import init_db
from .guardrails import scan_fields
from .logging_setup import log
from .ratelimit import check as rate_check
from .routers.admin_router import router as admin_router
from .routers.auth_router import router as auth_router
from .routers.employee_router import router as employee_router
from .routers.hr_router import router as hr_router
from .service_base import make_app

L = logging.getLogger("api")
app = make_app("api", "VERITAS — Workplace Statutory Compliance Intelligence", None)
WEB = Path(__file__).resolve().parent.parent / "web"

# ─────────────────────────── CORS ────────────────────────────────────────────
# Allow the front-end (served from same origin or localhost dev) to call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten in production with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────── Database init on startup ────────────────────────

@app.on_event("startup")
def startup():
    try:
        init_db()
        L.info("Database tables verified/created on startup")
    except Exception as e:
        L.error("Database init failed on startup: %s", e)
        # Do not crash — Redis-only features still work


# ─────────────────────────── Mount new routers ───────────────────────────────
app.include_router(auth_router)
app.include_router(employee_router)
app.include_router(hr_router)
app.include_router(admin_router)


# ─────────────────────────── Existing API (unchanged) ────────────────────────

_SENSITIVE = ("password", "token", "secret")


def _redact(obj):
    """Never persist credentials, even in a quarantined payload."""
    if isinstance(obj, dict):
        return {k: ("***" if any(w in str(k).lower() for w in _SENSITIVE) else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def quarantine(reason: str, raw, errors, client=None):
    qid = uuid.uuid4().hex[:12]
    rec = {"quarantine_id": qid, "ts": time.time(), "reason": reason, "client_id": client, "errors": errors, "raw": str(raw)[:1500]}
    try:
        state.r().lpush("quarantine", json.dumps(rec, default=str))
        state.r().ltrim("quarantine", 0, 999)
    except Exception as e:  # a store outage must not turn a 4xx into a 500
        L.warning("quarantine store unavailable: %s", e)
    metrics.VALIDATION_REJECTS.labels(reason).inc()
    return qid


@app.exception_handler(RequestValidationError)
async def on_validation(request: Request, exc: RequestValidationError):
    errors = field_errors(exc.errors())
    if not request.url.path.startswith("/v1/"):
        # Portal routes (/auth, /employee, /hr, /admin): plain 422, nothing is stored.
        return JSONResponse(status_code=422, content={"error": "validation_failed", "errors": errors, "run_started": False})
    body = exc.body if isinstance(exc.body, (dict, list)) else str(exc.body)
    client = body.get("client_id") if isinstance(body, dict) else None
    qid = quarantine("schema_validation", json.dumps(_redact(body), default=str), errors, client)
    log(L, logging.WARNING, "request quarantined", quarantine_id=qid, errors=len(errors))
    return JSONResponse(status_code=422, content={"error": "validation_failed", "quarantine_id": qid, "errors": errors, "run_started": False})


@app.post("/v1/advisories", status_code=202, tags=["advisories"])
def submit(req: AskRequest):
    """Validate, guard, rate-limit, then enqueue. Nothing below reaches an agent
    unless every check passes."""
    ok, details = scan_fields({"question": req.question, "notes": req.notes}, settings.injection_block, settings.toxicity_block)
    if not ok:
        for d in details:
            metrics.GUARD.labels(d["kind"], "ingress").inc()
        qid = quarantine("guardrail:" + ",".join(sorted({d['kind'] for d in details})), req.model_dump_json(), details, req.client_id)
        return JSONResponse(status_code=400, content={"error": "guardrail_blocked", "quarantine_id": qid, "details": details, "run_started": False})
    opts = req.options
    if opts and not settings.allow_options_override and (opts.no_cache or opts.prompt_version):
        raise HTTPException(403, "options overrides are disabled")
    if opts and opts.prompt_version and opts.prompt_version not in prompts.available():
        raise HTTPException(400, f"unknown prompt_version; available: {prompts.available()}")
    allowed, why, retry = rate_check(req.client_id)
    if not allowed:
        metrics.RATE_LIMITED.labels(why).inc()
        return JSONResponse(status_code=429, headers={"Retry-After": str(retry)}, content={"error": "rate_limited", "reason": why, "retry_after_s": retry})

    run_id = uuid.uuid4().hex
    pv = (opts.prompt_version if opts and opts.prompt_version else settings.prompt_version)
    payload = req.model_dump(mode="json")
    run = {"run_id": run_id, "status": "queued", "created_at": time.time(), "request": payload, "prompt_version": pv}
    ckey = None
    if settings.cache_enabled and not (opts and opts.no_cache):
        norm = {k: v for k, v in payload.items() if k not in ("client_id", "options")}
        norm["question"] = " ".join(norm["question"].lower().split())
        ckey = hashlib.sha256(json.dumps([norm, pv, settings.llm_model], sort_keys=True).encode()).hexdigest()
        hit = state.r().get(f"cache:{ckey}")
        if hit:
            metrics.CACHE.labels("run", "hit").inc()
            run.update(json.loads(hit))
            run.update({"status": "completed", "cached": True, "finished_at": time.time(), "duration_s": 0.0})
            state.save_run(run)
            state.add_trace(run_id, {"ts": time.time(), "agent": "api", "kind": "cache", "name": "run_cache_hit"})
            metrics.RUNS.labels("completed", run.get("route", "-"), "false").inc()
            return {"run_id": run_id, "status": "completed", "status_url": f"/v1/advisories/{run_id}", "cached": True}
        metrics.CACHE.labels("run", "miss").inc()
    run["cache_key"] = ckey
    state.save_run(run)
    state.add_trace(run_id, {"ts": time.time(), "agent": "api", "kind": "submit", "name": "accepted", "client_id": req.client_id, "prompt_version": pv})
    q = Queue("runs", connection=state.rq_conn())
    q.enqueue("app.coordinator.execute_run", run_id, job_timeout=int(settings.run_deadline_s * 2 + 30), result_ttl=60, failure_ttl=3600)
    metrics.QUEUE_DEPTH.set(len(q))
    log(L, logging.INFO, "run enqueued", run_id=run_id, client_id=req.client_id)
    return {"run_id": run_id, "status": "queued", "status_url": f"/v1/advisories/{run_id}"}


PUBLIC_HIDE = {"request", "cache_key"}


@app.get("/v1/advisories/{run_id}", tags=["advisories"])
def status(run_id: str):
    run = state.load_run(run_id)
    if not run:
        raise HTTPException(404, "unknown run_id")
    out = {k: v for k, v in run.items() if k not in PUBLIC_HIDE}
    out["disclaimer"] = "Advisory only; not a legal opinion. A qualified professional decides."
    return out


@app.get("/v1/advisories/{run_id}/trace", tags=["advisories"])
def trace(run_id: str):
    """Full reconstruction of a run: every tool call, query, passage, prompt version, latency and cost."""
    run = state.load_run(run_id)
    if not run:
        raise HTTPException(404, "unknown run_id")
    ev = state.get_trace(run_id)
    return {"run_id": run_id, "status": run["status"], "prompt_version": run.get("prompt_version"), "index_version": run.get("index_version"),
            "cost": run.get("cost"), "events": ev,
            "summary": {"llm_calls": sum(1 for e in ev if e.get("kind") == "llm"), "tool_calls": sum(1 for e in ev if e.get("kind") == "tool"),
                        "queries": [e.get("query") for e in ev if e.get("query")], "verification": [e.get("outcome") for e in ev if e.get("kind") == "verify"]}}


@app.post("/v1/feedback", status_code=201, tags=["feedback"])
def feedback(fb: FeedbackIn):
    if not state.load_run(fb.run_id):
        raise HTTPException(404, "unknown run_id")
    state.r().rpush("feedback", json.dumps({**fb.model_dump(), "ts": time.time()}))
    metrics.FEEDBACK.labels(fb.rating).inc()
    return {"stored": True}


@app.get("/v1/feedback/summary", tags=["feedback"])
def feedback_summary():
    rows = [json.loads(x) for x in state.r().lrange("feedback", 0, -1)]
    n = len(rows)
    sat = sum(1 for x in rows if x["rating"] == "satisfactory")
    reasons = {}
    for x in rows:
        if x["rating"] == "unsatisfactory":
            reasons[x["reason"][:60]] = reasons.get(x["reason"][:60], 0) + 1
    return {"total": n, "satisfactory": sat, "satisfaction_rate": round(sat / n, 3) if n else None, "top_unsatisfactory_reasons": sorted(reasons.items(), key=lambda kv: -kv[1])[:5]}


@app.get("/v1/quality", tags=["feedback"])
def quality():
    return {"rolling_20": live_eval.rolling(20), "series": live_eval.series(200)}


@app.get("/v1/quarantine", tags=["ops"])
def list_quarantine(limit: int = 20):
    return [json.loads(x) for x in state.r().lrange("quarantine", 0, max(min(limit, 100), 1) - 1)]


@app.get("/v1/contracts/{name}", tags=["ops"])
def contract(name: str):
    p = Path(__file__).resolve().parent.parent / "contracts" / f"{name}.schema.json"
    if not p.exists():
        raise HTTPException(404, "unknown contract")
    return json.loads(p.read_text())


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB / "index.html")
