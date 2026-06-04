"""Application tracker — career-ops style spreadsheet inside the app.

Endpoints:
  GET    /api/applications              → list (newest first)
  POST   /api/applications              → create one
  PATCH  /api/applications/{id}         → update fields
  DELETE /api/applications/{id}         → remove
  GET    /api/applications/check        → dedupe ("did I apply already?")
  GET    /api/applications/export.csv   → CSV download mirroring the
                                          When/DeadLine/Where/What/Status/
                                          How/Link columns the user keeps.

Dedupe lookup ranks by URL exact match, then jd_hash exact match, then
company+role lower-case match. Anything still missing returns
matched=false.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import re
from datetime import datetime, date

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import desc, or_
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.db_models import Application
from app.models.schemas import (
    ApplicationCreate,
    ApplicationDuplicateMatch,
    ApplicationOut,
    ApplicationUpdate,
)

router = APIRouter(prefix="/api/applications", tags=["applications"])


# ---------- helpers ----------

def _normalise_jd(text: str) -> str:
    """Whitespace-collapse + lower-case the first 4 KB. Stable across
    minor reformatting (trailing newline, double spaces)."""
    return re.sub(r"\s+", " ", (text or "")[:4096]).strip().lower()


def hash_jd(text: str) -> str:
    """SHA-1 of normalised JD. Empty string when no JD provided."""
    norm = _normalise_jd(text)
    if not norm:
        return ""
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def _today_iso() -> str:
    return date.today().isoformat()


def _to_out(row: Application) -> ApplicationOut:
    """ORM → Pydantic with derived `has_cv_*` flags so list endpoints
    don't have to ship the (possibly hundreds-of-KB) tailored CV
    payload to the UI every refresh."""
    return ApplicationOut(
        id=row.id,
        apply_date=row.apply_date or "",
        deadline=row.deadline or "",
        company=row.company or "",
        role=row.role or "",
        status=row.status or "",
        how=row.how or "",
        url=row.url or "",
        notes=row.notes or "",
        jd_hash=row.jd_hash or "",
        has_cv_latex=bool(getattr(row, "cv_latex", "") or ""),
        has_cv_pdf=bool(getattr(row, "cv_pdf_b64", "") or ""),
        has_jd=bool((getattr(row, "jd_text", "") or "").strip()),
        cv_filename=getattr(row, "cv_filename", "") or "",
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------- list / create ----------

@router.get("", response_model=list[ApplicationOut])
def list_applications(db: Session = Depends(get_db)) -> list[ApplicationOut]:
    rows = (
        db.query(Application).order_by(desc(Application.created_at)).all()
    )
    return [_to_out(r) for r in rows]


@router.post("", response_model=ApplicationOut, status_code=status.HTTP_201_CREATED)
def create_application(
    payload: ApplicationCreate,
    db: Session = Depends(get_db),
) -> ApplicationOut:
    """Add one row to the tracker. Default `apply_date` to today when
    status implies the application has been sent and the user didn't
    fill it in; "To-Apply" / "Skipped" rows leave it blank."""
    apply_date = payload.apply_date
    if not apply_date and payload.status not in ("To-Apply", "Skipped", ""):
        apply_date = _today_iso()

    row = Application(
        apply_date=apply_date,
        deadline=payload.deadline,
        company=payload.company,
        role=payload.role,
        status=payload.status or "To-Apply",
        how=payload.how,
        url=payload.url,
        notes=payload.notes,
        jd_text=payload.jd_text,
        jd_hash=hash_jd(payload.jd_text),
        cv_latex=payload.cv_latex or "",
        cv_pdf_b64=payload.cv_pdf_b64 or "",
        cv_filename=payload.cv_filename or "",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.patch("/{app_id}", response_model=ApplicationOut)
def update_application(
    app_id: int,
    payload: ApplicationUpdate,
    db: Session = Depends(get_db),
) -> ApplicationOut:
    row = db.query(Application).filter(Application.id == app_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.delete("/{app_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_application(app_id: int, db: Session = Depends(get_db)) -> None:
    row = db.query(Application).filter(Application.id == app_id).first()
    if row is None:
        return
    db.delete(row)
    db.commit()


# ---------- tailored-CV download ----------

@router.get("/{app_id}/cv.tex")
def download_cv_latex(app_id: int, db: Session = Depends(get_db)) -> Response:
    """Serve the LaTeX snapshot attached when the row was tracked."""
    row = db.query(Application).filter(Application.id == app_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    if not (row.cv_latex or "").strip():
        raise HTTPException(status_code=404, detail="No CV attached to this application.")
    filename = (row.cv_filename or "tailored-cv") + ".tex"
    return Response(
        content=row.cv_latex,
        media_type="application/x-tex; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/{app_id}/jd.txt")
def download_jd(app_id: int, db: Session = Depends(get_db)) -> Response:
    """Serve the JD text snapshot taken when the row was tracked.
    Useful for re-rendering a tailored CV later without re-finding
    the original posting."""
    row = db.query(Application).filter(Application.id == app_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    if not (row.jd_text or "").strip():
        raise HTTPException(status_code=404, detail="No JD text saved for this application.")
    company = (row.company or "company").strip().lower().replace(" ", "-")
    filename = f"jd-{company}-{row.id}.txt"
    return Response(
        content=row.jd_text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/{app_id}/cv.pdf")
def download_cv_pdf(app_id: int, db: Session = Depends(get_db)) -> Response:
    """Serve the compiled PDF (base64-decoded) attached when tracked."""
    row = db.query(Application).filter(Application.id == app_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    if not (row.cv_pdf_b64 or "").strip():
        raise HTTPException(status_code=404, detail="No PDF attached to this application.")
    try:
        pdf_bytes = base64.b64decode(row.cv_pdf_b64)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Bad PDF payload: {exc}") from exc
    filename = (row.cv_filename or "tailored-cv") + ".pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------- dedupe check ----------

@router.get("/check", response_model=ApplicationDuplicateMatch)
def check_duplicate(
    url: str = "",
    jd_text: str = "",
    company: str = "",
    role: str = "",
    db: Session = Depends(get_db),
) -> ApplicationDuplicateMatch:
    """Did the user already track this JD? Hits in priority order:

      1. URL exact match (case-sensitive — URLs differ in case, but
         hosts not, so leave it to the caller to canonicalise).
      2. jd_hash exact match (SHA-1 of normalised JD).
      3. (company.lower, role.lower) match when both non-empty.

    Returns ``matched=false`` when none of those hit.
    """
    # 1. URL.
    if url.strip():
        row = db.query(Application).filter(Application.url == url.strip()).first()
        if row is not None:
            return ApplicationDuplicateMatch(
                matched=True,
                match_kind="url",
                application=_to_out(row),
            )

    # 2. JD hash.
    h = hash_jd(jd_text)
    if h:
        row = db.query(Application).filter(Application.jd_hash == h).first()
        if row is not None:
            return ApplicationDuplicateMatch(
                matched=True,
                match_kind="jd_hash",
                application=_to_out(row),
            )

    # 3. Company + role fuzzy (lower-case, trimmed). When the caller
    # didn't supply them explicitly, derive from `jd_text` via the
    # existing extractor — frontend currently passes only jd_text+url,
    # so this is the path that catches re-pastes of the same JD whose
    # raw text differs by a few characters (LinkedIn promo blocks,
    # cookie banners, etc.) and therefore misses jd_hash match.
    cl = company.strip().lower()
    rl = role.strip().lower()
    if (not cl or not rl) and (jd_text or "").strip():
        try:
            from app.services.extraction import extract_job
            parsed = extract_job(jd_text)
            if not cl:
                cl = (parsed.company or "").strip().lower()
            if not rl:
                rl = (parsed.job_title or "").strip().lower()
        except Exception:  # noqa: BLE001
            pass
    if cl and rl:
        rows = db.query(Application).all()
        for r in rows:
            if (r.company or "").strip().lower() == cl and (r.role or "").strip().lower() == rl:
                return ApplicationDuplicateMatch(
                    matched=True,
                    match_kind="company_role",
                    application=_to_out(r),
                )
        # Last-ditch: company match alone with fuzzy role overlap
        # (token-set Jaccard ≥ 0.6). Catches casing/word-order drift
        # like "Graduate AI Software Engineer" vs "Graduate AI software
        # engineer" (already lower-cased above, but also "AI Engineer,
        # Graduate" etc.).
        if cl:
            role_tokens = set(t for t in rl.split() if len(t) > 2)
            for r in rows:
                if (r.company or "").strip().lower() != cl:
                    continue
                r_tokens = set(
                    t for t in (r.role or "").strip().lower().split() if len(t) > 2
                )
                if not role_tokens or not r_tokens:
                    continue
                inter = len(role_tokens & r_tokens)
                union = len(role_tokens | r_tokens)
                if union and inter / union >= 0.6:
                    return ApplicationDuplicateMatch(
                        matched=True,
                        match_kind="company_role_fuzzy",
                        application=_to_out(r),
                    )

    return ApplicationDuplicateMatch(matched=False)


# ---------- export ----------

@router.get("/export.csv")
def export_csv(db: Session = Depends(get_db)) -> StreamingResponse:
    """Download the tracker as CSV. Columns match the user's existing
    spreadsheet (When/DeadLine/Where?/What?/Status/How/Link) plus a
    Notes column. Missing values render as ``-`` to match the
    convention shown in the source sheet.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["When", "DeadLine", "Where?", "What?", "Status", "How", "Link", "Notes"])

    rows = db.query(Application).order_by(desc(Application.created_at)).all()
    for r in rows:
        writer.writerow([
            r.apply_date or "-",
            r.deadline or "-",
            r.company or "-",
            r.role or "-",
            r.status or "-",
            r.how or "-",
            r.url or "-",
            (r.notes or "").replace("\n", " ").strip() or "-",
        ])
    buf.seek(0)
    today = _today_iso()
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=applications-{today}.csv",
        },
    )
