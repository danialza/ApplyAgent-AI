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

from app.db.database import get_db
from app.models.db_models import CV, Document, WebSource
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
    """Trigger a master rebuild after every source mutation.

    Failures here are logged + swallowed so a single bad source can't
    block the user's next upload. The rebuild is also surfaced by the
    library GET endpoint, so transient errors recover on next add.
    """
    try:
        payload = build_library_from_all(db).model_dump()
        from app.models.db_models import CVLibrary
        row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
        if row is None:
            row = CVLibrary(id=1)
            db.add(row)
        for k, v in payload.items():
            setattr(row, k, v)
        row.updated_at = datetime.utcnow()
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("master library rebuild failed: %s", exc)
        db.rollback()


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
