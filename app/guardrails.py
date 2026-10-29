"""L4: prompt-injection defence, toxicity detection and output filtering.

Design: (1) scan every free-text field before anything reaches a model;
(2) wrap surviving text as untrusted data, with marker/tag characters removed;
(3) tool arguments chosen by the model are validated against strict schemas
(app/tools.py), so injected text cannot widen what a tool-holding agent can do;
(4) the advisory is filtered so the system cannot issue a determination it is not
authorised to make. The scanners are heuristic (pattern + score); they are a
layer, not a guarantee, and are paired with the structural controls above."""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import List

INJECTION = [
    (r"ignore (all |any |the |your )?(previous|prior|above|earlier|system) (instructions|prompts?|rules|messages)", 3),
    (r"disregard (all |any |the |your )?(previous|prior|above|earlier)?\s*(instructions|rules|guidelines|policy)", 3),
    (r"forget (everything|all|your) (above|previous|instructions|rules)", 3),
    (r"(reveal|print|show|repeat|leak) (me )?(your |the )?(system |hidden |initial )?(prompt|instructions)", 3),
    (r"you are now|from now on you|act as (an? )?(unrestricted|different|new)|pretend (to be|you are)", 2),
    (r"developer mode|jailbreak|do anything now|\bdan\b mode", 3),
    (r"override (the )?(policy|rules|guardrails?|safety|escalation)", 3),
    (r"(do not|don't|never) (escalate|cite|verify|mention (the )?(repealed|disclaimer))", 2),
    (r"(call|invoke|use|run) (the )?\w*\s*(tool|function)\b.{0,40}(with|using)", 2),
    (r"\b(tool_call|function_call|tool_calls)\b", 2),
    (r"</?\s*(system|assistant|user|untrusted_user_text|instructions?)\s*>", 3),
    (r"\[\[\s*/?\s*[A-Z_]+", 3),
    (r"(as the|i am the) (system|administrator|developer|legal counsel)", 2),
    (r"(respond|answer) (only )?with (yes|no|payable|not payable)", 1),
    (r"(give|issue|provide) (me )?(a |the )?(final )?(legal opinion|binding determination|verdict)", 2),
    (r"[A-Za-z0-9+/]{120,}={0,2}", 2),
]
_INJ = [(re.compile(p, re.I | re.S), w) for p, w in INJECTION]

TOXIC = [
    (r"\b(idiot|stupid|moron|imbecile|dumb ?ass|scumbag|bastard|retard)\b", 1),
    (r"\b(shut up|piece of (crap|trash)|go to hell|hate you)\b", 1),
    (r"\b(kill yourself|i will kill|i'll kill|hope you die|deserve to die)\b", 3),
    (r"\bf+u+c+k+( you| off)?\b|\bshit\b|\bbitch\b", 1),
]
_TOX = [(re.compile(p, re.I), w) for p, w in TOXIC]

# Things the system is not authorised to say (output filter).
FORBIDDEN_OUTPUT = [
    r"\bin my (legal )?opinion\b", r"\bi (hereby )?(conclude|determine|rule|declare|certify) that\b",
    r"\bthis (is|constitutes) (a )?(legal opinion|binding determination|legal advice)\b",
    r"\b(guilty|liable for|will (definitely|certainly) (win|lose)|guaranteed)\b",
    r"\byou (must|are required to|have to) (pay|terminate|dismiss|fire|reject|deny)\b",
    r"\b(sue|file a (suit|case|criminal))\b", r"\bignore (the )?(previous|prior|above)\b",
]
_OUT = [re.compile(p, re.I) for p in FORBIDDEN_OUTPUT]


def normalise(text: str) -> str:
    t = unicodedata.normalize("NFKC", text)
    t = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]", "", t)
    return re.sub(r"\s+", " ", t)


@dataclass
class GuardResult:
    blocked: bool
    score: float
    reasons: List[str] = field(default_factory=list)


def scan_injection(text: str, block_at: float = 3) -> GuardResult:
    t = normalise(text or "")
    score, reasons = 0.0, []
    for pat, w in _INJ:
        if pat.search(t):
            score += w
            reasons.append(pat.pattern[:60])
    return GuardResult(score >= block_at, score, reasons)


def scan_toxicity(text: str, block_at: float = 2) -> GuardResult:
    t = normalise(text or "")
    score, reasons = 0.0, []
    for pat, w in _TOX:
        m = pat.findall(t)
        if m:
            score += w * len(m)
            reasons.append(pat.pattern[:40])
    return GuardResult(score >= block_at, score, reasons)


def wrap_untrusted(text: str) -> str:
    """Strip characters the prompts use as control markers, then fence the text."""
    t = normalise(text or "").replace("[[", "(").replace("]]", ")")
    t = re.sub(r"</?\s*\w+[^>]*>", " ", t)
    return f"<untrusted_user_text>{t}</untrusted_user_text>"


def filter_output(text: str):
    hits = [p.pattern for p in _OUT if p.search(text or "")]
    return (not hits), hits


def scan_fields(fields: dict, injection_block=3, toxicity_block=2):
    """Scan every free-text field. Returns (ok, details)."""
    details = []
    for name, val in fields.items():
        if not val:
            continue
        inj = scan_injection(val, injection_block)
        tox = scan_toxicity(val, toxicity_block)
        if inj.blocked:
            details.append({"field": name, "kind": "prompt_injection", "score": inj.score})
        if tox.blocked:
            details.append({"field": name, "kind": "toxicity", "score": tox.score})
    return (not details), details
