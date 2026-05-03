"""Semantic search endpoint: free-text query → top-k matching CV chunks."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.models.schemas import (
    SemanticMatch,
    SemanticSearchRequest,
    SemanticSearchResponse,
)
from app.services.embedding_service import get_embedding_service
from app.services.vector_store import get_vector_store

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("/semantic", response_model=SemanticSearchResponse)
def semantic_search(payload: SemanticSearchRequest) -> SemanticSearchResponse:
    embedder = get_embedding_service()
    if not embedder.is_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Semantic search disabled — install sentence-transformers and "
                "faiss-cpu, then call POST /api/embeddings/rebuild."
            ),
        )

    store = get_vector_store()
    if store is None or store.size() == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Index is empty. Call POST /api/embeddings/rebuild first.",
        )

    qvec = embedder.encode([payload.query])[0]
    hits = store.search(qvec, top_k=payload.top_k)

    results = [
        SemanticMatch(
            cv_id=int(meta["cv_id"]),
            cv_name=meta.get("cv_name", ""),
            filename=meta.get("filename", ""),
            kind=meta.get("kind", ""),
            idx=int(meta.get("idx", 0)),
            text=meta.get("text", ""),
            score=round(float(score), 4),
        )
        for score, meta in hits
    ]
    return SemanticSearchResponse(query=payload.query, results=results)
