"""Stub LLM provider (D6). OpenAI-compatible /v1/chat/completions that returns
fixed, role-appropriate responses after a realistic delay, so load tests measure
this system rather than a provider's rate limits. Fault modes for the demo:
  POST /admin/mode {"mode": "ok|error|ratelimit|slow|unsupported_first"}"""
import asyncio
import json
import os
import random
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

app = FastAPI(title="stub-llm")
MODE = {"mode": "ok"}
DELAY = float(os.environ.get("STUB_DELAY_MS", "1500")) / 1000
JITTER = float(os.environ.get("STUB_JITTER_MS", "400")) / 1000
CALLS = Counter("stub_calls_total", "Stub calls", ["role", "mode"])


def tokens(s):
    return max(1, len(s) // 4)


def researcher(user: str) -> dict:
    step = int(re.search(r"\[\[STEP:(\d+)\]\]", user).group(1))
    top = re.search(r"\[\[TOP_CLAUSE:([^\]]*)\]\]", user).group(1)
    q = re.search(r"<untrusted_user_text>(.*?)</untrusted_user_text>", user, re.S)
    q = (q.group(1) if q else "gratuity")[:250]
    if step == 1:
        return {"thought": "find the governing provision", "action": "search_corpus", "args": {"query": q, "strategy": "semantic", "top_k": 5}}
    hint = re.search(r"get_provisions\('([^']+)'\)", user)
    if step == 2 and (top or hint):
        sec = hint.group(1) if hint else ":".join(top.split(":")[:2])
        return {"thought": "check for provisos that modify the rule", "action": "get_provisions", "args": {"section_ref": sec}}
    if step == 3:
        return {"thought": "search again using wording from what was read", "action": "search_corpus",
                "args": {"query": "provided further not necessary exception expiry of fixed term", "strategy": "lexical", "top_k": 3}}
    return {"thought": "enough provisions gathered", "action": "finish", "args": {}}


def advisor(user: str) -> dict:
    ps = re.findall(r"\[\[PASSAGE id=(\S+) label=.*? status=(\S+) kind=(\S+)\]\]\n(.*?)\n\[\[/PASSAGE\]\]", user, re.S)
    rnd = int(re.search(r"\[\[ROUND:(\d+)\]\]", user).group(1))
    inf = [(i, k, t) for i, s, k, t in ps if s == "in_force"]
    if not inf:
        return {"summary": "", "assertions": [], "recommendation": "", "cannot_answer": True, "reason": "no in-force passage retrieved"}
    sec = lambda i: ":".join(i.split(":")[:2])
    prov_secs = [sec(i) for i, k, t in inf if k == "proviso"]
    target = prov_secs[0] if prov_secs else sec(inf[0][0])
    chosen = [x for x in inf if sec(x[0]) == target]
    chosen.sort(key=lambda x: 0 if x[1] == "proviso" else 1)
    chosen = chosen[:3]
    if MODE["mode"] == "unsupported_first" and rnd == 0:  # demo: advisor cites only the main clause and invents a number
        main = next((x for x in inf if x[1] == "main"), inf[0])
        return {"summary": "Gratuity is not payable.", "assertions": [{"text": "Gratuity requires 7 years of continuous service.", "citations": [main[0]]}],
                "recommendation": "No action.", "cannot_answer": False, "reason": ""}
    asserts = [{"text": " ".join(t.split()[:30]), "citations": [i]} for i, k, t in chosen]
    return {"summary": "The retrieved in-force provisions indicate the outcome below.", "assertions": asserts,
            "recommendation": "The provisions appear to apply; confirm with legal counsel before acting.", "conflict_detected": False,
            "cannot_answer": False, "reason": ""}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    sysm, user = body["messages"][0]["content"], body["messages"][-1]["content"]
    role = "researcher" if "[[ROLE:researcher_step]]" in sysm else "advisor" if "[[ROLE:advisor_compose]]" in sysm else "other"
    CALLS.labels(role, MODE["mode"]).inc()
    m = MODE["mode"]
    await asyncio.sleep(DELAY + random.uniform(-JITTER, JITTER) + (60 if m == "slow" else 0))
    if m == "error":
        return JSONResponse(status_code=503, content={"error": "stub forced failure"})
    if m == "ratelimit":
        return JSONResponse(status_code=429, content={"error": "stub rate limit"})
    out = researcher(user) if role == "researcher" else advisor(user) if role == "advisor" else {"ok": True}
    text = json.dumps(out)
    return {"id": "stub", "object": "chat.completion", "model": body.get("model", "stub"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": tokens(sysm + user), "completion_tokens": tokens(text), "total_tokens": tokens(sysm + user + text)}}


@app.post("/admin/mode")
async def set_mode(request: Request):
    b = await request.json()
    if b.get("mode") not in ("ok", "error", "ratelimit", "slow", "unsupported_first"):
        return JSONResponse(status_code=400, content={"error": "bad mode"})
    MODE["mode"] = b["mode"]
    return MODE


@app.get("/health")
def health():
    return {"status": "ok", "mode": MODE["mode"]}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
