"""A3: verify every answer against the retrieved passages before it is returned.
Checks: (1) each assertion cites something; (2) each citation resolves to
retrieved text; (3) cited text is in force on the as-of date; (4) cited text
supports the assertion; (5) completeness: every proviso/explanation attached to a
cited sub-section was actually retrieved. Check 5 is what catches the section 4.1
trap: citing the five-year rule without its fixed-term proviso."""
from datetime import date

from . import scoring
from .corpus.store import IndexStore, status_asof


def verify(assertions, passages: dict, store: IndexStore, as_of: date):
    gaps, checks = [], []
    if not assertions:
        return {"ok": False, "outcome": "no_assertions", "checks": [], "gaps": []}
    ac = scoring.assertion_checks(assertions, passages, as_of)
    for c in ac:
        if not c["citations"]:
            checks.append({"check": "cited", "ok": False, "detail": c["text"][:80]})
        elif not c["resolved"]:
            checks.append({"check": "citation_resolves", "ok": False, "detail": [i for i in c["citations"] if i not in passages]})
        elif not c["in_force"]:
            checks.append({"check": "in_force", "ok": False, "detail": c["citations"]})
            for i in c["citations"]:
                ch = passages.get(i)
                if ch and status_asof(ch, as_of) != "in_force" and ch.get("superseded_by"):
                    gaps.append({"type": "repealed_cited", "chunk_id": i, "hint": f"{i} is not in force on {as_of}; search {ch['superseded_by']} for the in-force counterpart"})
        elif c["support"] < scoring.TAU or c["unsupported_numbers"]:
            checks.append({"check": "supported", "ok": False, "detail": {"support": c["support"], "numbers": c["unsupported_numbers"], "text": c["text"][:80]}})
            gaps.append({"type": "unsupported", "hint": f"assertion not supported by cited text: '{c['text'][:80]}'; search with different wording or drop it"})
        else:
            checks.append({"check": "assertion", "ok": True, "detail": c["citations"]})
    seen = set()
    for a in assertions:
        for cid in a.get("citations", []):
            ch = passages.get(cid)
            if not ch or ch.get("kind") == "naive":
                continue
            key = (ch["doc_id"], ch["section"], ch["sub"])
            if key in seen:
                continue
            seen.add(key)
            need = [c["chunk_id"] for c in store.by_section(ch["doc_id"], ch["section"])
                    if c["sub"] == ch["sub"] and c["kind"] in ("proviso", "explanation")]
            missing = [i for i in need if i not in passages]
            ok = not missing
            checks.append({"check": "completeness", "ok": ok, "detail": f"{ch['doc_id']}:s{ch['section']}({ch['sub']}) provisos retrieved" if ok else {"missing": missing}})
            if not ok:
                gaps.append({"type": "missing_modifier", "section_ref": f"{ch['doc_id']}:s{ch['section']}", "missing": missing,
                             "hint": f"{ch['label']} has provisos not yet read ({', '.join(missing)}); call get_provisions('{ch['doc_id']}:s{ch['section']}') and reconsider"})
    ok = all(c["ok"] for c in checks if "ok" in c) and not gaps
    return {"ok": ok, "outcome": "verified" if ok else "failed", "checks": checks, "gaps": gaps}
