"""Researcher tools (A1). The agent chooses which to call; arguments are validated
against strict schemas, so text injected into the question cannot widen what a
tool-holding agent can do (L4). Every call has a timeout (A5) and is metered."""
import concurrent.futures as cf
import functools
import hashlib
import json
import re
import time
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from . import metrics
from .config import settings
from .corpus.embedder import get_embedder
from .corpus.store import IndexStore, status_asof
from .state import r

DOMAIN_DOCS = {
    "gratuity": {"coss_2020", "payment_of_gratuity_1972", "labour_codes_commencement"},
    "maternity_benefit": {"coss_2020", "labour_codes_commencement"},
    "workplace_harassment": {"posh_2013"},
}
CLAUSE_ID = r"^[a-z0-9_]{3,40}:(s[0-9]{1,3}[A-Z]{0,2}:[0-9]+[A-Za-z]?:(main|body[0-9]+|proviso[0-9]+|explanation[0-9]+)|naive:[0-9]+)$"
SECTION_REF = r"^[a-z0-9_]{3,40}:s[0-9]{1,3}[A-Z]{0,2}$"
REQ_FIELDS = {"employment_type", "contract_months", "years_of_service", "employee_count", "days_worked_last_12_months",
              "separation_reason", "engaged_after_commencement", "start_date", "end_date", "establishment_type"}


class SearchArgs(BaseModel):
    query: str = Field(..., min_length=3, max_length=300)
    strategy: Literal["semantic", "lexical"] = "semantic"
    top_k: int = Field(5, ge=1, le=8)


class ClauseArgs(BaseModel):
    clause_id: str = Field(..., pattern=CLAUSE_ID)


class SectionArgs(BaseModel):
    section_ref: str = Field(..., pattern=SECTION_REF)


class FieldArgs(BaseModel):
    field: str

    def check(self):
        if self.field not in REQ_FIELDS:
            raise ValueError("unknown request field")


TOOL_SPECS = {
    "search_corpus": (SearchArgs, "Search the legal corpus. args: query (3-300 chars), strategy 'semantic'|'lexical', top_k (1-8). Returns passages with id, status in force on the as-of date, and how many provisos qualify them."),
    "get_clause": (ClauseArgs, "Fetch one clause by identifier. args: clause_id like 'coss_2020:s53:1:main'. Returns full text, status and related ids."),
    "get_provisions": (SectionArgs, "Fetch every proviso/explanation (and the main clause) of a section. args: section_ref like 'coss_2020:s53'. Use this to discover exceptions that qualify a rule."),
    "get_request_field": (FieldArgs, "Read one extracted fact about the request. args: field, one of " + ", ".join(sorted(REQ_FIELDS)) + "."),
}


@functools.lru_cache(maxsize=2048)
def _embed_cached(model_name: str, text: str):
    metrics.CACHE.labels("embedding", "miss").inc()
    emb = get_embedder(settings.embed_backend, settings.embed_model, settings.embed_dim)
    return tuple(emb.encode([text])[0].tolist())


def embed_query(text: str):
    before = _embed_cached.cache_info().hits
    v = _embed_cached(settings.embed_backend + settings.embed_model, text)
    if _embed_cached.cache_info().hits > before:
        metrics.CACHE.labels("embedding", "hit").inc()
    return list(v)


def brief(c: dict, as_of: date, snippet=300, score=None):
    d = {"chunk_id": c["chunk_id"], "label": c["label"], "statute": c["statute"], "kind": c["kind"],
         "status": status_asof(c, as_of), "has_provisos": c.get("has_provisos", 0), "snippet": c["text"][:snippet]}
    if c.get("superseded_by"):
        d["superseded_by"] = c["superseded_by"]
    if score is not None:
        d["score"] = round(score, 3)
    return d


def full(c: dict, as_of: date):
    d = brief(c, as_of, snippet=10_000)
    d.update({"text": c["text"], "proviso_ids": c.get("proviso_ids", []), "parent_id": c.get("parent_id"),
              "doc_id": c["doc_id"], "section": c["section"], "sub": c["sub"], "proviso_no": c.get("proviso_no")})
    return d


class Toolbox:
    def __init__(self, store: IndexStore, entities: dict, domain: str, as_of: date):
        self.store, self.entities, self.domain, self.as_of = store, entities, domain, as_of
        self.docs = DOMAIN_DOCS.get(domain)

    def _pred(self, c):
        return not self.docs or c["doc_id"] in self.docs

    def search_corpus(self, a: SearchArgs):
        key = "ret:" + hashlib.sha256(json.dumps([a.query, a.strategy, a.top_k, self.domain, self.store.manifest.get("index_version")]).encode()).hexdigest()
        if settings.cache_enabled:
            try:
                hit = r().get(key)
                if hit:
                    metrics.CACHE.labels("retrieval", "hit").inc()
                    return {"hits": [dict(h, status=status_asof(self.store.get(h["chunk_id"]), self.as_of)) for h in json.loads(hit)], "cached": True}
            except Exception:
                pass
            metrics.CACHE.labels("retrieval", "miss").inc()
        if a.strategy == "lexical":
            res = self.store.lexical(a.query, a.top_k, self._pred)
        else:
            res = self.store.semantic(embed_query(a.query), a.top_k, self._pred)
        hits = [brief(c, self.as_of, score=s) for c, s in res]
        if settings.cache_enabled:
            try:
                r().set(key, json.dumps(hits), ex=settings.cache_ttl_s)
            except Exception:
                pass
        return {"hits": hits}

    def get_clause(self, a: ClauseArgs):
        c = self.store.get(a.clause_id)
        return {"clause": full(c, self.as_of)} if c else {"error": "unknown clause_id"}

    def get_provisions(self, a: SectionArgs):
        doc, sec = a.section_ref.split(":s", 1)
        cs = self.store.by_section(doc, sec)
        if not cs:
            return {"error": "unknown section_ref"}
        return {"section_ref": a.section_ref, "clauses": [full(c, self.as_of) for c in cs]}

    def get_request_field(self, a: FieldArgs):
        a.check()
        return {"field": a.field, "value": self.entities.get(a.field)}

    def call(self, name: str, args: dict):
        """Validate, execute with a timeout, meter. Returns (result, outcome)."""
        if name not in TOOL_SPECS:
            metrics.TOOL_CALLS.labels(name[:30], "invalid").inc()
            return {"error": f"unknown tool '{name[:30]}'"}, "invalid"
        schema = TOOL_SPECS[name][0]
        try:
            parsed = schema(**(args or {}))
        except (ValidationError, TypeError) as e:
            metrics.TOOL_CALLS.labels(name, "invalid").inc()
            return {"error": "invalid arguments", "detail": str(e)[:200]}, "invalid"
        fn = getattr(self, name)
        with cf.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(fn, parsed)
            try:
                res = fut.result(timeout=settings.tool_timeout_s)
            except cf.TimeoutError:
                metrics.TOOL_CALLS.labels(name, "timeout").inc()
                return {"error": "tool timeout"}, "timeout"
            except Exception as e:
                metrics.TOOL_CALLS.labels(name, "error").inc()
                return {"error": f"tool error: {type(e).__name__}"}, "error"
        out = "error" if "error" in res else "ok"
        metrics.TOOL_CALLS.labels(name, out).inc()
        return res, out
