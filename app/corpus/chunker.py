"""Chunking strategy (L1). The corpus is hard because exceptions live in provisos
that the question never mentions. So the section-aware chunker:
  * splits each section into sub-sections, and each sub-section into
    main clause / provisos / explanations, one chunk each;
  * gives every chunk a header naming statute, section and sub-section;
  * for a proviso, prefixes the embedding text with the main clause it qualifies,
    so a proviso is retrievable by what it modifies as well as by its own words;
  * records `proviso_ids` and `has_provisos` on the main-clause chunk, so an agent
    reading a rule is told a modifier exists and can ask for it by identifier.
`naive` is the ablation baseline: fixed windows, no structure."""
import re
from typing import List

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

HEAD = re.compile(r"^(\d{1,3}[A-Z]{0,2})\.\s+([^.\n\u2014\u2013]{2,100})[.\u2014\u2013]+-?\s*(.*)$")
SUB = re.compile(r"(?m)^\((\d+[A-Za-z]?)\)\s*")
PROV = re.compile(r"\s*(?=\bProvided\b(?:\s+further|\s+also)?(?:\s+that)?\b)")
EXPL = re.compile(r"\s*(?=\bExplanation\b\s*[.\u2014\u2013:-])")


def _key(num: str):
    m = re.match(r"(\d+)([A-Z]*)", num)
    return int(m.group(1)), m.group(2)


def split_sections(text: str):
    secs, cur, last = [], None, (0, "")
    for line in text.splitlines():
        s = line.strip()
        m = HEAD.match(s)
        if m and _key(m.group(1)) >= last:
            last = _key(m.group(1))
            cur = {"section": m.group(1), "title": m.group(2).strip(), "lines": [m.group(3)] if m.group(3) else []}
            secs.append(cur)
        elif cur is not None and s:
            cur["lines"].append(s)
    out = {}
    for s in secs:  # TOC lines produce tiny duplicates; keep the longest body per section number
        body = "\n".join(s["lines"]).strip()
        if len(body) >= 40 and (s["section"] not in out or len(body) > len(out[s["section"]]["body"])):
            out[s["section"]] = {**s, "body": body}
    return list(out.values())


def _split_sub(body: str):
    parts = SUB.split(body)
    if len(parts) == 1:
        return [("0", body)]
    subs = []
    if parts[0].strip():
        subs.append(("0", parts[0].strip()))
    for i in range(1, len(parts), 2):
        subs.append((parts[i], parts[i + 1].strip()))
    return subs


def chunk_section_aware(doc: dict, text: str) -> List[Document]:
    docs: List[Document] = []
    for sec in split_sections(text):
        for sub, body in _split_sub(sec["body"]):
            flat = re.sub(r"\s+", " ", body)
            segs = [x.strip(" :;—-") for x in PROV.split(flat) if x.strip()]
            pieces = []  # (kind, text)
            for j, seg in enumerate(segs):
                if j > 0 and seg.startswith("Provided"):
                    pieces.append(("proviso", seg))
                else:
                    for k, part in enumerate([p.strip() for p in EXPL.split(seg) if p.strip()]):
                        pieces.append(("explanation" if part.startswith("Explanation") else ("main" if not pieces else "body"), part))
            if not pieces:
                continue
            base = f"{doc['doc_id']}:s{sec['section']}:{sub}"
            label = f"{doc['short']} s.{sec['section']}" + (f"({sub})" if sub != "0" else "")
            main_id = f"{base}:main"
            main_text = next((t for k, t in pieces if k == "main"), pieces[0][1])
            n_prov = n_expl = n_body = 0
            prov_ids = []
            for kind, t in pieces:
                if kind == "proviso":
                    n_prov += 1
                    cid, k2, pn = f"{base}:proviso{n_prov}", "proviso", n_prov
                    prov_ids.append(cid)
                elif kind == "explanation":
                    n_expl += 1
                    cid, k2, pn = f"{base}:explanation{n_expl}", "explanation", None
                elif kind == "main":
                    cid, k2, pn = main_id, "main", None
                else:
                    n_body += 1
                    cid, k2, pn = f"{base}:body{n_body}", "body", None
                head = f"[{doc['statute']} | {label} | {sec['title']}"
                head += f" | proviso {pn}]" if pn else f" | {k2}]"
                embed = head + " " + (f"Qualifies the rule that says: {main_text[:220]} -- " if k2 == "proviso" else "") + t
                docs.append(Document(page_content=t, metadata={
                    "chunk_id": cid, "doc_id": doc["doc_id"], "statute": doc["statute"], "short": doc["short"],
                    "section": sec["section"], "sub": sub, "title": sec["title"], "kind": k2, "proviso_no": pn,
                    "label": label + (f" proviso {pn}" if pn else (" explanation" if k2 == "explanation" else "")),
                    "embed_text": embed, "parent_id": main_id if k2 != "main" else None}))
            for d in docs:
                if d.metadata["chunk_id"] == main_id:
                    d.metadata["proviso_ids"] = prov_ids
                    d.metadata["has_provisos"] = len(prov_ids)
    return docs


def chunk_naive(doc: dict, text: str, size=500, overlap=50) -> List[Document]:
    sp = RecursiveCharacterTextSplitter(chunk_size=size, chunk_overlap=overlap)
    out = []
    for i, t in enumerate(sp.split_text(re.sub(r"\s+", " ", text))):
        out.append(Document(page_content=t, metadata={
            "chunk_id": f"{doc['doc_id']}:naive:{i}", "doc_id": doc["doc_id"], "statute": doc["statute"], "short": doc["short"],
            "section": "?", "sub": "0", "title": "", "kind": "naive", "proviso_no": None, "label": f"{doc['short']} (chunk {i})",
            "embed_text": t, "parent_id": None}))
    return out


def chunk(doc: dict, text: str, strategy: str) -> List[Document]:
    return chunk_naive(doc, text) if strategy == "naive" else chunk_section_aware(doc, text)
