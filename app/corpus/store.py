"""On-disk index: chunks.jsonl + faiss.index + manifest.json. FAISS IndexIDMap lets
single chunks be removed/replaced, which is what makes incremental indexing and
re-embedding possible without rebuilding the whole index (L1, D2)."""
import hashlib
import json
import math
import re
from collections import Counter
from datetime import date
from pathlib import Path

import faiss
import numpy as np


def fid(chunk_id: str) -> int:
    return int.from_bytes(hashlib.blake2b(chunk_id.encode(), digest_size=8).digest(), "little") & 0x7FFFFFFFFFFFFFFF


def status_asof(chunk: dict, as_of: date) -> str:
    f, u = chunk.get("in_force_from"), chunk.get("in_force_until")
    if f and as_of < date.fromisoformat(f):
        return "not_yet_in_force"
    if u and as_of > date.fromisoformat(u):
        return "repealed"
    return "in_force"


class IndexStore:
    def __init__(self, path: str, dim: int):
        self.path = Path(path)
        self.dim = dim
        self.chunks = {}
        self.manifest = {}
        self.index = faiss.IndexIDMap2(faiss.IndexFlatIP(dim))
        self._bm = None

    # ---- persistence
    def load(self):
        p = self.path
        if (p / "manifest.json").exists():
            self.manifest = json.loads((p / "manifest.json").read_text())
        if (p / "chunks.jsonl").exists():
            self.chunks = {}
            for line in (p / "chunks.jsonl").read_text().splitlines():
                c = json.loads(line)
                self.chunks[c["chunk_id"]] = c
        if (p / "faiss.index").exists():
            self.index = faiss.read_index(str(p / "faiss.index"))
        self._bm = None
        return self

    def save(self):
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / "chunks.jsonl.tmp").write_text("\n".join(json.dumps(c) for c in self.chunks.values()))
        faiss.write_index(self.index, str(self.path / "faiss.index.tmp"))
        (self.path / "chunks.jsonl.tmp").replace(self.path / "chunks.jsonl")
        (self.path / "faiss.index.tmp").replace(self.path / "faiss.index")
        (self.path / "manifest.json").write_text(json.dumps(self.manifest, indent=2))

    # ---- mutation
    def remove(self, chunk_ids):
        ids = np.array([fid(c) for c in chunk_ids], dtype="int64")
        if len(ids):
            self.index.remove_ids(ids)
        for c in chunk_ids:
            self.chunks.pop(c, None)
        self._bm = None

    def upsert(self, chunks, vecs):
        self.remove([c["chunk_id"] for c in chunks if c["chunk_id"] in self.chunks])
        if chunks:
            self.index.add_with_ids(np.asarray(vecs, dtype="float32"), np.array([fid(c["chunk_id"]) for c in chunks], dtype="int64"))
            for c in chunks:
                self.chunks[c["chunk_id"]] = c
        self._bm = None

    # ---- query
    def get(self, chunk_id):
        return self.chunks.get(chunk_id)

    def by_section(self, doc_id, section):
        return sorted([c for c in self.chunks.values() if c["doc_id"] == doc_id and c["section"] == section],
                      key=lambda c: (c["sub"], {"main": 0, "body": 1, "proviso": 2, "explanation": 3}.get(c["kind"], 4), c.get("proviso_no") or 0))

    def semantic(self, vec, k, pred=None):
        if self.index.ntotal == 0:
            return []
        kk = self.index.ntotal if pred else min(k, self.index.ntotal)
        D, I = self.index.search(np.asarray([vec], dtype="float32"), kk)
        by_id = {fid(cid): cid for cid in self.chunks}
        out = []
        for s, i in zip(D[0], I[0]):
            cid = by_id.get(int(i))
            if cid and (not pred or pred(self.chunks[cid])):
                out.append((self.chunks[cid], float(s)))
            if len(out) >= k:
                break
        return out

    def _build_bm(self):
        docs = {cid: re.findall(r"[a-z0-9]+", (c["embed_text"]).lower()) for cid, c in self.chunks.items()}
        df = Counter(t for toks in docs.values() for t in set(toks))
        self._bm = (docs, df, sum(len(v) for v in docs.values()) / max(len(docs), 1))

    def lexical(self, query, k, pred=None):
        if self._bm is None:
            self._build_bm()
        docs, df, avg = self._bm
        q = re.findall(r"[a-z0-9]+", query.lower())
        N, out = len(docs), []
        for cid, toks in docs.items():
            if pred and not pred(self.chunks[cid]):
                continue
            tf, s = Counter(toks), 0.0
            for t in q:
                if t in tf:
                    idf = math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5))
                    s += idf * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(toks) / avg))
            if s > 0:
                out.append((self.chunks[cid], s))
        out.sort(key=lambda x: -x[1])
        return out[:k]
