"""Embedding admin endpoints: rebuild the FAISS index from the DB."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.db_models import CV
from app.models.schemas import IndexRebuildResponse
from app.services.embedding_service import get_embedding_service
from app.services.vector_store import (
    VectorStore,
    get_vector_store,
    rebuild_index,
    reset_vector_store,
)

router = APIRouter(prefix="/api/embeddings", tags=["embeddings"])


@router.post("/rebuild", response_model=IndexRebuildResponse)
def rebuild(db: Session = Depends(get_db)) -> IndexRebuildResponse:
    """Drop the existing index and re-embed every CV currently in the DB.

    Returns counts and a status string. When the neural backend isn't
    installed the endpoint returns `status='disabled'` with a helpful detail
    rather than a 500 — the rule-based matcher continues to work.
    """
    embedder = get_embedding_service()
    if not embedder.is_ready():
        return IndexRebuildResponse(
            status="disabled",
            detail=(
                "Neural embeddings unavailable — install sentence-transformers "
                "and faiss-cpu to enable semantic search."
            ),
        )

    # Force a fresh singleton so dim/model changes are picked up.
    reset_vector_store()
    store = get_vector_store()
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Embedding model loaded but vector store could not initialise.",
        )

    cvs = db.query(CV).all()
    counts = rebuild_index(store, embedder, cvs)
    return IndexRebuildResponse(status="ok", **counts)
