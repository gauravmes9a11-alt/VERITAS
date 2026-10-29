"""Embeddings run locally (no API cost). `st` = sentence-transformers; `hash` =
deterministic feature-hashing embedder for CI/offline/quick-start. The LangChain
Embeddings adapter lets either plug into LangChain tooling."""
import hashlib
import re
from typing import List

import numpy as np
from langchain_core.embeddings import Embeddings

STOP = set("a an the of to in on for and or by is are was were be been that this it as at with from shall may any such which who whom his her their its not no".split())


class HashEmbedder:
    def __init__(self, dim=384):
        self.dim = dim
        self.name = f"hash-v1-{dim}"

    def _tok(self, text):
        return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP]

    def encode(self, texts: List[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype="float32")
        for i, t in enumerate(texts):
            toks = self._tok(t)
            feats = toks + [a + "_" + b for a, b in zip(toks, toks[1:])]
            for f in feats:
                h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
                out[i, h % self.dim] += 1.0 if (h >> 40) & 1 else -1.0
            out[i] = np.sign(out[i]) * np.sqrt(np.abs(out[i]))
            n = np.linalg.norm(out[i])
            if n:
                out[i] /= n
        return out


class STEmbedder:
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model_name)
        self.name = model_name
        self.dim = self.model.get_sentence_embedding_dimension()

    def encode(self, texts):
        return np.asarray(self.model.encode(list(texts), normalize_embeddings=True, show_progress_bar=False), dtype="float32")


class LCEmbeddings(Embeddings):
    def __init__(self, enc):
        self.enc = enc

    def embed_documents(self, texts):
        return self.enc.encode(texts).tolist()

    def embed_query(self, text):
        return self.enc.encode([text])[0].tolist()


_cache = {}


def get_embedder(backend: str, model: str, dim: int):
    key = (backend, model, dim)
    if key not in _cache:
        if backend == "st":
            if not model:
                raise RuntimeError("EMBED_BACKEND=st requires EMBED_MODEL")
            _cache[key] = STEmbedder(model)
        else:
            _cache[key] = HashEmbedder(dim)
    return _cache[key]
