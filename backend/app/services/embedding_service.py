"""Embedding service.

Two paths share a single API:

* **Neural** (preferred): sentence-transformers model — `all-MiniLM-L6-v2` by
  default, configurable via `APP_EMBEDDING_MODEL`. Embeddings are L2-normalised
  so dot product == cosine similarity.
* **Fallback** (zero-dependency): bag-of-words cosine over the same
  `similarity(a, b)` API. Used when sentence-transformers / numpy aren't
  installed (e.g. unit tests on the rule-based matcher). The vector store and
  semantic-search endpoints are disabled in this mode.

Singleton accessor: `get_embedding_service()`.
"""
from __future__ import annotations

import math
import os
from collections import Counter
from typing import Sequence

from app.utils.text_cleaning import tokenize

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class EmbeddingService:
    """Pluggable embedder. Falls back to BoW cosine when neural deps are missing."""

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or os.getenv("APP_EMBEDDING_MODEL", DEFAULT_MODEL)
        self._model = None
        self._dim: int | None = None
        self._tried_load = False

    # ---------- Neural lifecycle ----------

    def _try_load(self) -> None:
        """Attempt to load the sentence-transformers model exactly once."""
        if self._tried_load:
            return
        self._tried_load = True
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            import numpy as _np  # noqa: F401  # ensures numpy is importable too

            self._model = SentenceTransformer(self.model_name)
            self._dim = int(self._model.get_sentence_embedding_dimension())
        except Exception:  # pragma: no cover  # neural deps optional
            self._model = None
            self._dim = None

    def is_ready(self) -> bool:
        """True if the neural model loaded successfully."""
        self._try_load()
        return self._model is not None

    @property
    def dim(self) -> int:
        """Embedding dimensionality (raises if neural path unavailable)."""
        self._try_load()
        if self._dim is None:
            raise RuntimeError(
                "Neural embedding model not loaded. Install sentence-transformers and faiss-cpu."
            )
        return self._dim

    # ---------- Encoding ----------

    def encode(self, texts: Sequence[str]):
        """Return an (N, D) float32 numpy array of L2-normalised vectors.

        Raises `RuntimeError` if the neural model isn't available.
        """
        if not self.is_ready():
            raise RuntimeError(
                "encode() requires sentence-transformers; run "
                "`pip install sentence-transformers faiss-cpu`."
            )
        # Batch-encode and normalise so cosine == dot product downstream.
        vecs = self._model.encode(  # type: ignore[union-attr]
            list(texts),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return vecs.astype("float32")

    # ---------- Always-available API ----------

    def similarity(self, a: str, b: str) -> float:
        """Cosine similarity in [0, 1]. Uses the neural model when available,
        falls back to bag-of-words cosine otherwise.
        """
        if self.is_ready():
            vecs = self.encode([a or "", b or ""])
            return max(0.0, float(vecs[0] @ vecs[1]))
        return _bow_cosine(a, b)


# ---------- Fallback ----------

def _bow_cosine(a: str, b: str) -> float:
    va = Counter(tokenize(a or ""))
    vb = Counter(tokenize(b or ""))
    if not va or not vb:
        return 0.0
    dot = sum(va[k] * vb.get(k, 0) for k in va)
    na = math.sqrt(sum(v * v for v in va.values()))
    nb = math.sqrt(sum(v * v for v in vb.values()))
    if na == 0 or nb == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))


# ---------- Singleton ----------

_default: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    """Process-wide embedder. Cheap when only `similarity()` is used."""
    global _default
    if _default is None:
        _default = EmbeddingService()
    return _default
