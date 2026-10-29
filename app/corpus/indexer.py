"""D2: idempotent corpus pipeline: read -> parse -> chunk -> embed -> index.
Usage:
  python -m app.corpus.indexer build                 # incremental, idempotent
  python -m app.corpus.indexer build --chunking naive --index-name naive
  python -m app.corpus.indexer reembed [--doc DOC]   # re-embed existing chunks only (e.g. new EMBED_MODEL)
  python -m app.corpus.indexer status
"""
import argparse
import hashlib
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .. import logging_setup
from ..config import settings
from .chunker import chunk
from .embedder import get_embedder
from .store import IndexStore

L = logging.getLogger("indexer")


def parse_file(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        pages = [(p.extract_text() or "") for p in PdfReader(str(path)).pages]
        if sum(len(p) for p in pages) < 200 * max(len(pages), 1) * 0.2:
            try:  # scanned document: OCR fallback
                import pytesseract
                from pdf2image import convert_from_path
                pages = [pytesseract.image_to_string(im) for im in convert_from_path(str(path))]
            except Exception as e:
                raise RuntimeError(f"{path.name} has no text layer and OCR is unavailable ({e}); OCR it first") from e
        return "\n".join(pages)
    return path.read_text(encoding="utf-8")


def resolve_sources(corpus_dir: Path):
    cfg = yaml.safe_load((corpus_dir / "sources.yaml").read_text())
    out = []
    for d in cfg["documents"]:
        off, fix = corpus_dir / d["official_file"], corpus_dir / d["fixture_file"]
        if off.exists():
            d["path"], d["source_kind"] = off, "official"
        elif fix.exists():
            d["path"], d["source_kind"] = fix, "synthetic_fixture"
            L.warning("using SYNTHETIC fixture for %s (official file %s missing)", d["doc_id"], d["official_file"])
        else:
            raise FileNotFoundError(f"no source for {d['doc_id']}")
        out.append(d)
    return out


def meta_for(d):
    return {"status": d["status"], "in_force_from": d.get("in_force_from"), "in_force_until": d.get("in_force_until"),
            "superseded_by": d.get("superseded_by"), "supersedes": d.get("supersedes", []), "source_kind": d["source_kind"],
            "doc_version": d.get("version")}


def build(chunking=None, index_name=None, reembed=False, only_doc=None, corpus_dir=None):
    t0 = time.time()
    chunking = chunking or settings.chunking
    name = index_name or settings.index_name
    corpus_dir = Path(corpus_dir or settings.corpus_dir)
    emb = get_embedder(settings.embed_backend, settings.embed_model, settings.embed_dim)
    store = IndexStore(Path(settings.index_root) / name, emb.dim).load()
    srcs = resolve_sources(corpus_dir)
    if settings.require_official and any(s["source_kind"] != "official" for s in srcs):
        raise SystemExit("REQUIRE_OFFICIAL_CORPUS=true but synthetic fixtures were found; add the official PDFs to corpus/data/")

    if reembed:  # no parse/chunk: just re-vectorise stored chunks, in place
        targets = [c for c in store.chunks.values() if not only_doc or c["doc_id"] == only_doc]
        vecs = emb.encode([c["embed_text"] for c in targets])
        for c in targets:
            c["embed_model"] = emb.name
        store.upsert(targets, vecs)
        added, updated, removed, unchanged = 0, len(targets), 0, len(store.chunks) - len(targets)
    else:
        wanted = {}
        for d in srcs:
            text = parse_file(d["path"])
            d["sha256"] = hashlib.sha256(text.encode()).hexdigest()[:16]
            for doc in chunk(d, text, chunking):
                c = dict(doc.metadata)
                c["text"] = doc.page_content
                c.update(meta_for(d))
                c["content_hash"] = hashlib.sha256((c["embed_text"] + emb.name).encode()).hexdigest()[:16]
                c["embed_model"] = emb.name
                wanted[c["chunk_id"]] = c
        have = store.chunks
        todo = [c for cid, c in wanted.items() if cid not in have or have[cid].get("content_hash") != c["content_hash"]
                or any(have[cid].get(k) != c.get(k) for k in ("status", "in_force_until", "source_kind", "has_provisos"))]
        gone = [cid for cid in have if cid not in wanted]
        added = sum(1 for c in todo if c["chunk_id"] not in have)
        updated, removed = len(todo) - added, len(gone)
        unchanged = len(wanted) - len(todo)
        if gone:
            store.remove(gone)
        if todo:
            store.upsert(todo, emb.encode([c["embed_text"] for c in todo]))

    dur = time.time() - t0
    changed = bool(added or updated or removed) or not store.manifest
    per_doc = {}
    for c in store.chunks.values():
        per_doc[c["doc_id"]] = per_doc.get(c["doc_id"], 0) + 1
    ver = hashlib.sha256("".join(sorted(f"{k}:{v.get('content_hash','')}" for k, v in store.chunks.items())).encode()).hexdigest()[:12]
    now = datetime.now(timezone.utc)
    m = store.manifest
    if changed or reembed:
        m.update({"built_at": now.isoformat(), "built_at_epoch": now.timestamp()})
    m.update({"index_name": name, "index_version": ver, "chunking": chunking, "embed_model": emb.name, "embed_dim": emb.dim,
              "total_chunks": len(store.chunks), "chunks_per_doc": per_doc, "last_checked_at": now.isoformat(),
              "documents": [{"doc_id": d["doc_id"], "version": d.get("version"), "sha256": d.get("sha256"), "source_kind": d["source_kind"],
                             "status": d["status"], "path": str(d["path"].name)} for d in srcs],
              "last_run": {"added": added, "updated": updated, "removed": removed, "unchanged": unchanged, "duration_s": round(dur, 3),
                           "throughput_chunks_per_s": round((added + updated) / dur, 2) if dur > 0 else 0, "mode": "reembed" if reembed else "build"}})
    store.manifest = m
    store.save()
    logging_setup.log(L, logging.INFO, "index built", **m["last_run"], total=len(store.chunks), version=ver)
    return m


def main():
    logging_setup.setup("indexer")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "reembed", "status"])
    ap.add_argument("--chunking")
    ap.add_argument("--index-name")
    ap.add_argument("--doc")
    ap.add_argument("--corpus-dir")
    a = ap.parse_args()
    if a.cmd == "status":
        print(json.dumps(IndexStore(Path(settings.index_root) / (a.index_name or settings.index_name), settings.embed_dim).load().manifest, indent=2))
        return
    m = build(a.chunking, a.index_name, a.cmd == "reembed", a.doc, a.corpus_dir)
    print(json.dumps(m["last_run"]))


if __name__ == "__main__":
    sys.exit(main())
