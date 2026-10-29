"""L3/L6 metrics, computed locally (TruLens-equivalent, no extra provider calls).

groundedness       share of assertions that have >=1 citation, every citation
                   resolving to retrieved text in force on the as-of date, AND
                   whose cited text supports the assertion (support >= tau).
faithfulness       mean claim-level support: fraction of an assertion's content
                   tokens found in its cited passages, zeroed when the assertion
                   contains a number or section reference absent from them.
context_relevance  mean cosine similarity between the question and each retrieved
                   passage (same embedder as the index). Absolute values depend
                   on the embedder; compare runs, not models.
hallucination_rate share of assertions that are uncited, cite something not
                   retrieved or not in force, or fall below the support threshold.
All four are proxies from lexical/embedding overlap, not an LLM judge."""
import re
from datetime import date

from .corpus.embedder import get_embedder
from .config import settings
from .corpus.store import status_asof

STOP = set("the a an of to in on for and or by is are was were be been that this it as at with from shall may any such which who whom his her their its not no under per upon where when if then than also".split())
TAU = 0.45


def toks(t):
    return {w for w in re.findall(r"[a-z]+", t.lower()) if len(w) > 3 and w not in STOP}


def nums(t):
    return set(re.findall(r"\d+(?:\.\d+)?", t))


def support(claim: str, cited_texts) -> float:
    ct = toks(claim)
    if not ct:
        return 1.0
    pool = set()
    for t in cited_texts:
        pool |= toks(t)
    lex = len(ct & pool) / len(ct)
    return lex


def assertion_checks(assertions, passages: dict, as_of: date):
    out = []
    for a in assertions:
        ids = a.get("citations") or []
        resolved = [passages[i] for i in ids if i in passages]
        res_ok = bool(ids) and len(resolved) == len(ids)
        inforce = all(status_asof(p, as_of) == "in_force" for p in resolved) if resolved else False
        texts = [p["text"] for p in resolved]
        sup = support(a["text"], texts) if texts else 0.0
        extra_nums = nums(a["text"]) - set().union(*[nums(t) for t in texts]) if texts else nums(a["text"])
        out.append({"text": a["text"], "citations": ids, "resolved": res_ok, "in_force": inforce, "support": round(sup, 3),
                    "unsupported_numbers": sorted(extra_nums)})
    return out


def score_run(question: str, assertions, passages: dict, as_of: date):
    chk = assertion_checks(assertions, passages, as_of)
    n = max(len(chk), 1)
    grounded = sum(1 for c in chk if c["resolved"] and c["in_force"] and c["support"] >= TAU) / n
    faith = sum((0 if c["unsupported_numbers"] else c["support"]) for c in chk) / n
    halluc = sum(1 for c in chk if (not c["resolved"]) or (not c["in_force"]) or c["support"] < TAU) / n
    emb = get_embedder(settings.embed_backend, settings.embed_model, settings.embed_dim)
    ps = list(passages.values())
    if ps:
        import numpy as np
        qv = emb.encode([question])[0]
        pv = emb.encode([p["text"] for p in ps])
        rel = float(np.mean(pv @ qv))
    else:
        rel = 0.0
    if not chk:
        return {"groundedness": None, "faithfulness": None, "hallucination_rate": None, "context_relevance": round(rel, 4), "n_assertions": 0}
    return {"groundedness": round(grounded, 4), "faithfulness": round(faith, 4), "hallucination_rate": round(halluc, 4),
            "context_relevance": round(rel, 4), "n_assertions": len(chk)}
