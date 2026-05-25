"""Unified source registry — the single feed for the master CV library.

Lists every input artifact (uploaded CV, uploaded Document, added
WebSource) so the new unified-uploader UI can render them in one table.
Also owns URL ingestion: POST a portfolio link or GitHub profile, the
ingester fetches + extracts + triggers a master rebuild.
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc
from sqlalchemy.orm import Session

from datetime import datetime as _datetime

from app.db.database import get_db
from app.models.db_models import CV, CVLibrary, Document, WebSource
from app.models.schemas import (
    UnifiedSource,
    WebSourceCreate,
    WebSourceOut,
)
from app.services import web_ingest
from app.services.cv_library_builder import build_library_from_all

logger = logging.getLogger("ai_job_cv_matcher.sources")

router = APIRouter(prefix="/api/sources", tags=["sources"])


# ---------- helpers ----------

def _web_to_out(row: WebSource) -> WebSourceOut:
    return WebSourceOut(
        id=row.id,
        url=row.url,
        kind=row.kind,
        title=row.title,
        status=row.status,
        error=row.error,
        has_extracted=bool(row.extracted),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _rebuild_library_silent(db: Session) -> None:
    """Trigger a master rebuild after every source mutation. Skips
    silently when library is hand-locked. Errors swallowed."""
    from app.services.master_rebuild import try_rebuild_master
    try_rebuild_master(db)


# ---------- unified listing ----------

@router.get("", response_model=list[UnifiedSource])
def list_all_sources(db: Session = Depends(get_db)) -> list[UnifiedSource]:
    """Every CV + Document + WebSource, newest first. Powers the
    unified-uploader source list in the UI."""
    items: list[UnifiedSource] = []
    for cv in db.query(CV).all():
        items.append(UnifiedSource(
            id=cv.id,
            kind="cv",
            title=cv.filename or cv.name or "CV",
            detail=f"name={cv.name or '—'} · {len(cv.skills or [])} skills",
            status="done",
            created_at=cv.created_at,
        ))
    for d in db.query(Document).all():
        items.append(UnifiedSource(
            id=d.id,
            kind="document",
            title=d.filename or "document",
            detail=f"{len((d.raw_text or '').splitlines())} lines",
            status="done",
            created_at=d.created_at,
        ))
    for w in db.query(WebSource).all():
        kind_label = {"web": "web", "github_user": "github_user", "github_repo": "github_repo"}.get(w.kind, "web")
        items.append(UnifiedSource(
            id=w.id,
            kind=kind_label,
            title=w.title or w.url,
            detail=w.url,
            status=w.status,
            error=w.error,
            created_at=w.created_at,
        ))
    items.sort(key=lambda s: s.created_at, reverse=True)
    return items


# ---------- Per-source contribution rollup ----------

@router.get("/breakdown")
def source_breakdown(db: Session = Depends(get_db)) -> dict:
    """For each source row (cv:N, document:N, web:N), count how many
    master-library entries it contributed to. Powers the per-source
    rollup chips in the unified panel.

    The library entries carry a `sources` list populated by the
    builder; we just invert it here.
    """
    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None:
        return {"by_source": {}}

    def _bucket() -> dict:
        return {
            "education": 0,
            "selected_projects": 0,
            "additional_projects": 0,
            "experience": 0,
            "publications": 0,
            "certifications": 0,
        }

    by_source: dict[str, dict] = {}

    def _count(entries: list, section: str) -> None:
        for e in entries or []:
            for sk in (e.get("sources") or []) if isinstance(e, dict) else []:
                by_source.setdefault(sk, _bucket())[section] += 1

    _count(row.education, "education")
    _count(row.selected_projects, "selected_projects")
    _count(row.additional_projects, "additional_projects")
    _count(row.experience, "experience")
    _count(row.publications, "publications")
    _count(row.certifications, "certifications")

    return {"by_source": by_source}


# ---------- Free-text notes ingest ----------

@router.post("/notes", response_model=UnifiedSource, status_code=status.HTTP_201_CREATED)
def add_notes(payload: dict, db: Session = Depends(get_db)) -> UnifiedSource:
    """Dump free-form text into the master CV. The builder + LLM
    curator extract projects / skills / experience / publications
    from the text on the next rebuild, then merge into master.

    Body: ``{"text": "...", "title": "optional name"}``. Stored as
    a Document row (existing infra), so it shows up in the source
    list and contributes to every future master rebuild until you
    delete it.
    """
    text = (payload or {}).get("text", "")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=400, detail="Empty 'text' field.")
    title = ((payload or {}).get("title") or "").strip()
    if not title:
        title = f"notes-{_datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.txt"
    doc = Document(filename=title, raw_text=text.strip())
    db.add(doc)
    db.commit()
    db.refresh(doc)

    _rebuild_library_silent(db)

    return UnifiedSource(
        id=doc.id,
        kind="document",
        title=doc.filename,
        detail=f"{len(text.splitlines())} lines · free-form notes",
        status="done",
        created_at=doc.created_at,
    )


@router.delete("/notes/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_notes(doc_id: int, db: Session = Depends(get_db)) -> None:
    """Remove a notes/document source and rebuild master."""
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if doc is None:
        return
    db.delete(doc)
    db.commit()
    _rebuild_library_silent(db)


# ---------- WebSource CRUD ----------

@router.post("/url", response_model=WebSourceOut, status_code=status.HTTP_201_CREATED)
def add_url_source(payload: WebSourceCreate, db: Session = Depends(get_db)) -> WebSourceOut:
    """Add a portfolio URL or GitHub link. Ingests synchronously
    (fetch + scrape + LLM extract), saves the result, triggers a
    master library rebuild. Errors from the fetch land on the row's
    `status="failed"` + `error` fields so the UI can surface them
    without breaking the request."""
    url = (payload.url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="URL must start with http:// or https://",
        )

    existing = db.query(WebSource).filter(WebSource.url == url).first()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"This URL is already a source (id={existing.id}).",
        )

    kind, raw_text, extracted, error = web_ingest.ingest(url)
    row = WebSource(
        url=url,
        kind=kind or "web",
        title=_derive_title(url, extracted),
        raw_text=raw_text,
        extracted=extracted or {},
        status="done" if not error else "failed",
        error=error,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    if not error:
        _rebuild_library_silent(db)
    return _web_to_out(row)


@router.delete("/url/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_url_source(source_id: int, db: Session = Depends(get_db)) -> None:
    row = db.query(WebSource).filter(WebSource.id == source_id).first()
    if row is None:
        return
    db.delete(row)
    db.commit()
    _rebuild_library_silent(db)


@router.post("/url/{source_id}/refresh", response_model=WebSourceOut)
def refresh_url_source(source_id: int, db: Session = Depends(get_db)) -> WebSourceOut:
    """Re-fetch a URL (its content may have changed) and re-extract."""
    row = db.query(WebSource).filter(WebSource.id == source_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    kind, raw_text, extracted, error = web_ingest.ingest(row.url)
    row.kind = kind or row.kind
    row.raw_text = raw_text
    row.extracted = extracted or {}
    row.title = _derive_title(row.url, extracted)
    row.status = "done" if not error else "failed"
    row.error = error
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    if not error:
        _rebuild_library_silent(db)
    return _web_to_out(row)


def _derive_title(url: str, extracted: dict | None) -> str:
    """Best-effort title for the source list — username for GitHub,
    hostname+path otherwise."""
    from urllib.parse import urlparse
    p = urlparse(url)
    host = (p.hostname or "").replace("www.", "")
    path = p.path.strip("/")
    if "github.com" in host and path:
        return f"@{path.split('/')[0]}" + (f"/{path.split('/')[1]}" if "/" in path else "")
    if extracted and isinstance(extracted, dict):
        bio = extracted.get("bio") or ""
        if bio:
            return (bio.split(".")[0][:60]).strip() or f"{host}{('/' + path) if path else ''}"
    return f"{host}{('/' + path) if path else ''}" or url
