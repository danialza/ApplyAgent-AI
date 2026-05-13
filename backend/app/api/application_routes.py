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

import csv
import hashlib
import io
import re
from datetime import datetime, date

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
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


# ---------- list / create ----------

@router.get("", response_model=list[ApplicationOut])
def list_applications(db: Session = Depends(get_db)) -> list[ApplicationOut]:
    rows = (
        db.query(Application).order_by(desc(Application.created_at)).all()
    )
    return [ApplicationOut.model_validate(r) for r in rows]


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
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return ApplicationOut.model_validate(row)


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
    return ApplicationOut.model_validate(row)


@router.delete("/{app_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_application(app_id: int, db: Session = Depends(get_db)) -> None:
    row = db.query(Application).filter(Application.id == app_id).first()
    if row is None:
        return
    db.delete(row)
    db.commit()


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
                application=ApplicationOut.model_validate(row),
            )

    # 2. JD hash.
    h = hash_jd(jd_text)
    if h:
        row = db.query(Application).filter(Application.jd_hash == h).first()
        if row is not None:
            return ApplicationDuplicateMatch(
                matched=True,
                match_kind="jd_hash",
                application=ApplicationOut.model_validate(row),
            )

    # 3. Company + role fuzzy (lower-case, trimmed).
    cl = company.strip().lower()
    rl = role.strip().lower()
    if cl and rl:
        rows = db.query(Application).all()
        for r in rows:
            if (r.company or "").strip().lower() == cl and (r.role or "").strip().lower() == rl:
                return ApplicationDuplicateMatch(
                    matched=True,
                    match_kind="company_role",
                    application=ApplicationOut.model_validate(r),
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
