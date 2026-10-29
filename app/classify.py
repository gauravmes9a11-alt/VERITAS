"""F3: domain classification and entity extraction. Deterministic and rule-based
on purpose: zero cost, auditable, and no model (hence no injection surface) on
the routing decision. Structured request fields override text-extracted values."""
import re
from datetime import date
from typing import Optional

from .contracts import COMMENCEMENT, AskRequest

DOMAIN_KW = {
    "gratuity": [r"gratuity", r"continuous service", r"five years", r"fixed.?term"],
    "maternity_benefit": [r"maternity", r"pregnan", r"childbirth", r"expected delivery", r"miscarriage", r"adopt(ing|ion)"],
    "workplace_harassment": [r"harass", r"\bposh\b", r"internal (complaints )?committee", r"\bicc\b", r"local committee", r"sexual"],
}
DISPUTE = r"(dispute|wrongful|unfair|illegal|contest|challeng|sacked|fired|dismiss|retrench|show.?cause|misconduct|tribunal|court|legal notice|\bsue\b|laid off|victimi[sz])"
TERMINATION = r"(terminat|dismiss|fired|sacked|retrench|laid off|removed from (service|employment))"
COMPLAINT = (r"((we|she|he|they|an? employee|our employee|a woman|someone|my colleague)[^.?]{0,40}(filed|lodged|made|submitted|reported|received)[^.?]{0,25}complaint"
             r"|complaint (was|has been|is) (filed|lodged|received|made)|allege|accus|\bvictim\b|\brespondent\b|being investigated|ongoing (inquiry|investigation)|inquiry (is|was) (ongoing|pending))")
MONTHS = {m: i + 1 for i, m in enumerate("january february march april may june july august september october november december".split())}


def classify_domain(q: str) -> str:
    t = q.lower()
    best, score = "other", 0
    for dom, pats in DOMAIN_KW.items():
        s = sum(1 for p in pats if re.search(p, t))
        # fixed-term alone is weak evidence; require a gratuity word for gratuity unless 2+ hits
        if dom == "gratuity" and not re.search(r"gratuity", t) and s < 2:
            s = 0
        if s > score:
            best, score = dom, s
    return best


def _parse_date(s: str) -> Optional[date]:
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})", s)
    if m and m.group(2).lower() in MONTHS:
        try:
            return date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
        except ValueError:
            return None
    return None


def extract_entities(req: AskRequest) -> dict:
    q = req.question
    t = q.lower()
    ent = {"employment_type": None, "contract_months": None, "years_of_service": None, "employee_count": None,
           "days_worked_last_12_months": None, "separation_reason": None, "engaged_after_commencement": None,
           "start_date": None, "end_date": None, "establishment_type": None, "sources": {}}

    def put(k, v, src):
        if v is not None and ent.get(k) is None:
            ent[k] = v
            ent["sources"][k] = src

    # structured fields win
    emp, est = req.employment, req.establishment
    if emp:
        for k in ("employment_type", "contract_months", "years_of_service", "days_worked_last_12_months", "separation_reason", "start_date", "end_date"):
            v = getattr(emp, k)
            put(k, v.value if hasattr(v, "value") else (v.isoformat() if isinstance(v, date) else v), "request")
    if est:
        put("employee_count", est.employee_count, "request")
        put("establishment_type", est.establishment_type.value if est.establishment_type else None, "request")

    # text extraction
    if re.search(r"fixed.?term", t):
        put("employment_type", "fixed_term", "text")
    elif re.search(r"permanent|regular employee", t):
        put("employment_type", "permanent", "text")
    m = re.search(r"(\d+)\s*[- ]?\s*months?", t)
    if m:
        put("contract_months", int(m.group(1)), "text")
    m = re.search(r"(\d+(?:\.\d+)?)\s*[- ]?\s*(?:years?|yrs?)\b", t)
    if m:
        put("years_of_service", float(m.group(1)), "text")
    m = re.search(r"(\d[\d,]*)\s+(?:employees|staff|workers|people)", t)
    if m:
        put("employee_count", int(m.group(1).replace(",", "")), "text")
    m = re.search(r"(\d+)\s+days", t)
    if m and re.search(r"worked|work(ing)? days|days of work", t):
        put("days_worked_last_12_months", int(m.group(1)), "text")
    for pat, val in [(r"(completed|expir|ended|end of) (the |her |his )?(contract|term)|contract (has )?(ended|expired|completed)", "contract_expiry"),
                     (r"resign", "resignation"), (r"retire", "retirement"), (r"superannuat", "superannuation"),
                     (r"\bdied|death", "death"), (r"disablement|disabled", "disablement"), (r"terminat|dismiss|fired|sacked", "termination")]:
        if re.search(pat, t):
            put("separation_reason", val, "text")
            break
    m = re.search(r"(engaged|joined|hired|appointed|employed|started)[^.?]{0,25}(after|on or after|from|since)\s+(\d{1,2}\w*\s+\w+\s+\d{4})", t)
    if m:
        d = _parse_date(m.group(3))
        if d and m.group(2) in ("after", "on or after", "from", "since"):
            ent["engaged_after_commencement"] = d >= COMMENCEMENT
            ent["sources"]["engaged_after_commencement"] = "text"
    if ent["start_date"]:
        sd = date.fromisoformat(ent["start_date"])
        ent["engaged_after_commencement"] = sd >= COMMENCEMENT
        ent["sources"]["engaged_after_commencement"] = ent["sources"].get("start_date", "request")
    return ent


def detect_intent(q: str, domain: str) -> dict:
    t = q.lower()
    return {
        "termination_dispute": bool(re.search(TERMINATION, t) and re.search(DISPUTE, t)),
        "harassment_complaint": domain == "workplace_harassment" and bool(re.search(COMPLAINT, t)),
    }


def straddles_commencement(ent: dict, as_of: date) -> bool:
    if not ent.get("start_date"):
        return False
    sd = date.fromisoformat(ent["start_date"])
    end = date.fromisoformat(ent["end_date"]) if ent.get("end_date") else as_of
    return sd < COMMENCEMENT <= max(end, as_of)


def _gratuity_missing(e):
    has_duration = e["years_of_service"] is not None or e["contract_months"] or (e["start_date"] and e["end_date"])
    if has_duration or e["separation_reason"] in ("death", "disablement"):
        return []
    return ["employment.years_of_service (or contract_months / start_date + end_date)"]


REQUIRED = {
    "gratuity": _gratuity_missing,
    "maternity_benefit": lambda e: [] if e["days_worked_last_12_months"] is not None else ["employment.days_worked_last_12_months"],
    "workplace_harassment": lambda e: [] if e["employee_count"] else ["establishment.employee_count"],
}


def missing_fields(domain: str, ent: dict) -> list:
    fn = REQUIRED.get(domain)
    return fn(ent) if fn else []
