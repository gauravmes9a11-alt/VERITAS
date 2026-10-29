import time
from pathlib import Path

from .. import metrics
from ..config import settings
from ..corpus.embedder import get_embedder
from ..corpus.store import IndexStore


def load_store() -> IndexStore:
    emb = get_embedder(settings.embed_backend, settings.embed_model, settings.embed_dim)
    st = IndexStore(Path(settings.index_root) / settings.index_name, emb.dim).load()
    m = st.manifest
    if m:
        metrics.INDEX_BUILT.set(m.get("built_at_epoch", 0))
        for d, n in m.get("chunks_per_doc", {}).items():
            metrics.INDEX_CHUNKS.labels(d).set(n)
        lr = m.get("last_run", {})
        metrics.PIPE_THROUGHPUT.set(lr.get("throughput_chunks_per_s", 0))
        metrics.PIPE_DURATION.set(lr.get("duration_s", 0))
    return st


def index_health(st: IndexStore) -> dict:
    m = st.manifest
    kinds = {d["source_kind"] for d in m.get("documents", [])}
    return {"index_loaded": bool(st.chunks), "index_version": m.get("index_version"), "chunks": m.get("total_chunks", 0),
            "built_at": m.get("built_at"), "chunking": m.get("chunking"), "embed_model": m.get("embed_model"),
            "corpus_is_synthetic": "synthetic_fixture" in kinds, "prompt_version_default": settings.prompt_version}


def passage(c: dict) -> dict:
    return {k: v for k, v in c.items() if k != "embed_text"}


def ev(agent, kind, name, **kw):
    d = {"ts": round(time.time(), 3), "agent": agent, "kind": kind, "name": name}
    d.update(kw)
    return d
