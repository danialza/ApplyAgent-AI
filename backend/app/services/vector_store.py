"""Local vector store for CV-chunk embeddings.

Backed by a numpy matrix (always present) plus an optional FAISS
`IndexFlatIP` accelerator for global semantic search. Per-CV access goes
through numpy because FAISS doesn't expose vectors directly and we want
deterministic ordering by chunk index.

Persistence: vectors → `cv_vectors.npy`, metadata → `cv_metas.json`. Living
under `backend/data/index/` by default; override with `APP_INDEX_DIR`.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from app.services.cv_chunker import CVChunk, chunk_cv
from app.services.embedding_service import EmbeddingService

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_INDEX_DIR = _BACKEND_ROOT / "data" / "index"


class VectorStore:
    """In-process vector store with optional FAISS acceleration."""

    def __init__(self, dim: int, persist_dir: Path | str | None = None) -> None:
        import numpy as np  # local: this class always needs numpy

        self.dim = int(dim)
        self._np = np
        self.persist_dir = Path(persist_dir or os.getenv("APP_INDEX_DIR", _DEFAULT_INDEX_DIR))
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._matrix: np.ndarray = np.zeros((0, self.dim), dtype="float32")
        self._metas: list[dict[str, Any]] = []
        self._cv_to_rows: dict[int, list[int]] = {}

        self._faiss = None
        self._faiss_dirty = True
        self._try_init_faiss()

    # ---------- FAISS helpers ----------

    def _try_init_faiss(self) -> None:
        try:
            import faiss  # type: ignore
            self._faiss = faiss.IndexFlatIP(self.dim)
        except Exception:  # pragma: no cover  # FAISS is optional
            self._faiss = None

    def _rebuild_faiss(self) -> None:
        if self._faiss is None:
            return
        self._faiss.reset()
        if len(self._matrix) > 0:
            self._faiss.add(self._matrix)
        self._faiss_dirty = False

    # ---------- Mutations ----------

    def clear(self) -> None:
        with self._lock:
            self._matrix = self._np.zeros((0, self.dim), dtype="float32")
            self._metas = []
            self._cv_to_rows = {}
            self._faiss_dirty = True

    def add(self, vectors, metas: list[dict[str, Any]]) -> None:
        """Append new rows. `vectors` shape: (N, dim). `metas` length: N."""
        if len(metas) == 0:
            return
        vecs = self._np.asarray(vectors, dtype="float32").reshape(-1, self.dim)
        if vecs.shape[0] != len(metas):
            raise ValueError("vectors and metas length mismatch")

        with self._lock:
            start = self._matrix.shape[0]
            self._matrix = self._np.vstack([self._matrix, vecs]) if start else vecs
            for offset, meta in enumerate(metas):
                row = start + offset
                self._metas.append(meta)
                self._cv_to_rows.setdefault(int(meta["cv_id"]), []).append(row)
            self._faiss_dirty = True

    def remove_cv(self, cv_id: int) -> None:
        """Drop every chunk belonging to `cv_id`. O(N) — fine for MVP."""
        cv_id = int(cv_id)
        with self._lock:
            rows_to_drop = set(self._cv_to_rows.pop(cv_id, []))
            if not rows_to_drop:
                return
            keep_mask = self._np.array(
                [i not in rows_to_drop for i in range(len(self._metas))], dtype=bool
            )
            self._matrix = self._matrix[keep_mask]
            self._metas = [m for i, m in enumerate(self._metas) if keep_mask[i]]
            # Rebuild row index after compaction.
            self._cv_to_rows = {}
            for i, m in enumerate(self._metas):
                self._cv_to_rows.setdefault(int(m["cv_id"]), []).append(i)
            self._faiss_dirty = True

    # ---------- Queries ----------

    def has_cv(self, cv_id: int) -> bool:
        return int(cv_id) in self._cv_to_rows

    def size(self) -> int:
        return int(self._matrix.shape[0])

    def get_for_cv(self, cv_id: int):
        """Return (vectors, metas) for one CV. Empty arrays if absent."""
        cv_id = int(cv_id)
        rows = self._cv_to_rows.get(cv_id, [])
        if not rows:
            return self._np.zeros((0, self.dim), dtype="float32"), []
        return self._matrix[rows], [self._metas[i] for i in rows]

    def search(self, query_vec, top_k: int = 5) -> list[tuple[float, dict[str, Any]]]:
        """Top-k chunks across the whole store. Vectors must be normalised."""
        if self._matrix.shape[0] == 0:
            return []
        q = self._np.asarray(query_vec, dtype="float32").reshape(1, self.dim)

        if self._faiss is not None:
            if self._faiss_dirty:
                self._rebuild_faiss()
            distances, indices = self._faiss.search(q, min(top_k, self._matrix.shape[0]))
            return [
                (float(distances[0][i]), self._metas[int(indices[0][i])])
                for i in range(indices.shape[1])
                if int(indices[0][i]) >= 0
            ]

        # Numpy fallback.
        sims = (self._matrix @ q[0]).astype(float)
        order = self._np.argsort(-sims)[:top_k]
        return [(float(sims[i]), self._metas[int(i)]) for i in order]

    # ---------- Persistence ----------

    def save(self) -> None:
        with self._lock:
            self._np.save(self.persist_dir / "cv_vectors.npy", self._matrix)
            (self.persist_dir / "cv_metas.json").write_text(
                json.dumps(self._metas, ensure_ascii=False)
            )

    def load(self) -> bool:
        vec_path = self.persist_dir / "cv_vectors.npy"
        meta_path = self.persist_dir / "cv_metas.json"
        if not (vec_path.exists() and meta_path.exists()):
            return False
        with self._lock:
            self._matrix = self._np.load(vec_path).astype("float32")
            self._metas = json.loads(meta_path.read_text())
            self._cv_to_rows = {}
            for i, m in enumerate(self._metas):
                self._cv_to_rows.setdefault(int(m["cv_id"]), []).append(i)
            self._faiss_dirty = True
        return True


# ---------- Indexing helpers ----------

def index_cv(store: VectorStore, embedder: EmbeddingService, cv: Any) -> int:
    """Chunk one CV, embed each chunk, and add to the store. Returns chunk count."""
    chunks: list[CVChunk] = chunk_cv(cv)
    if not chunks:
        return 0
    if not embedder.is_ready():
        return 0  # Skip silently in BoW-fallback mode.
    vectors = embedder.encode([c.text for c in chunks])
    store.add(vectors, [c.to_meta() for c in chunks])
    return len(chunks)


def rebuild_index(store: VectorStore, embedder: EmbeddingService, cvs: list[Any]) -> dict[str, int]:
    """Drop and re-index every CV from the database."""
    store.clear()
    total_chunks = 0
    for cv in cvs:
        total_chunks += index_cv(store, embedder, cv)
    store.save()
    return {"cvs_indexed": len(cvs), "chunks_indexed": total_chunks}


# ---------- Singleton ----------

_default: VectorStore | None = None


def get_vector_store() -> VectorStore | None:
    """Lazy singleton. Returns None when neural deps aren't installed.

    Call sites should branch on `None` and fall back to BoW similarity.
    """
    global _default
    if _default is not None:
        return _default

    from app.services.embedding_service import get_embedding_service

    embedder = get_embedding_service()
    if not embedder.is_ready():
        return None
    _default = VectorStore(dim=embedder.dim)
    _default.load()
    return _default


def reset_vector_store() -> None:
    """Test/admin hook: drop the singleton so it gets re-created next call."""
    global _default
    _default = None
