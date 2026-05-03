"""Split a parsed CV into typed chunks for embedding.

Each chunk has:
    cv_id, cv_name, kind (summary|skills|experience|project|education|certification|languages),
    idx (position within its kind), text (the chunk content).

Chunks are intentionally short and self-contained so retrieval can pinpoint
the exact bullet/section that explains a match.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Iterable


@dataclass
class CVChunk:
    cv_id: int
    cv_name: str
    filename: str
    kind: str
    idx: int
    text: str

    def to_meta(self) -> dict[str, Any]:
        """Metadata payload stored alongside the embedding vector."""
        return asdict(self)


def _push(chunks: list[CVChunk], cv: Any, kind: str, items: Iterable[str]) -> None:
    for i, raw in enumerate(items or []):
        text = (raw or "").strip()
        if not text:
            continue
        chunks.append(
            CVChunk(
                cv_id=int(cv.id),
                cv_name=cv.name or "",
                filename=cv.filename or "",
                kind=kind,
                idx=i,
                text=text,
            )
        )


def chunk_cv(cv: Any) -> list[CVChunk]:
    """Return a list of `CVChunk` objects for the given CV row.

    Skills are joined into a single chunk (recall over precision: skill lists
    are short and we want one vector that represents the whole skill set).
    Everything else is one-chunk-per-entry.
    """
    chunks: list[CVChunk] = []

    if cv.summary:
        chunks.append(
            CVChunk(int(cv.id), cv.name or "", cv.filename or "", "summary", 0, cv.summary)
        )

    if cv.skills:
        chunks.append(
            CVChunk(
                int(cv.id), cv.name or "", cv.filename or "",
                "skills", 0, ", ".join(cv.skills),
            )
        )

    _push(chunks, cv, "experience", cv.experience or [])
    _push(chunks, cv, "project", cv.projects or [])
    _push(chunks, cv, "education", cv.education or [])
    _push(chunks, cv, "certification", cv.certifications or [])
    _push(chunks, cv, "languages", cv.languages or [])

    return chunks
