"""CV Library + tailored-LaTeX-CV endpoints.

    GET  /api/cv/library    — fetch the CV library (header, projects, etc.)
    PUT  /api/cv/library    — replace the library wholesale
    POST /api/cv/render     — render a tailored .tex (and optionally PDF)

The library is a singleton row. All editing flows through PUT — partial
patch semantics aren't worth the complexity for an MVP this size.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.db_models import CVLibrary
from app.models.schemas import (
    CVLibraryBase,
    CVLibraryOut,
    JobParsed,
    RenderCVRequest,
    RenderCVResponse,
)
from app.services.codex_cv_polish import polish_library_with_llm
from app.services.cv_renderer import render_cv
from app.services.extraction import extract_job

router = APIRouter(prefix="/api/cv", tags=["cv"])


def _to_out(row: CVLibrary) -> CVLibraryOut:
    """ORM row → Pydantic. Lists stored as JSON come back as raw dicts;
    Pydantic re-validates them into nested models."""
    return CVLibraryOut(
        id=int(row.id),
        header=row.header or {},
        summary=row.summary or "",
        skills_groups=row.skills_groups or [],
        education=row.education or [],
        selected_projects=row.selected_projects or [],
        additional_projects=row.additional_projects or [],
        experience=row.experience or [],
        publications=row.publications or [],
        certifications=row.certifications or [],
        languages=row.languages or [],
        updated_at=row.updated_at or datetime.utcnow(),
    )


@router.get("/library", response_model=CVLibraryOut)
def get_library(db: Session = Depends(get_db)) -> CVLibraryOut:
    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No CV library yet. PUT /api/cv/library to create one, "
                "or run `python -m scripts.seed_cv_library` to seed the bundled sample."
            ),
        )
    return _to_out(row)


@router.put("/library", response_model=CVLibraryOut)
def upsert_library(
    payload: CVLibraryBase,
    db: Session = Depends(get_db),
) -> CVLibraryOut:
    """Create or replace the singleton CV library."""
    data = payload.model_dump()
    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None:
        row = CVLibrary(id=1)
        db.add(row)
    for k, v in data.items():
        setattr(row, k, v)
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.post("/render", response_model=RenderCVResponse)
def render_tailored_cv(
    payload: RenderCVRequest,
    db: Session = Depends(get_db),
) -> RenderCVResponse:
    """Render a tailored CV. Empty `job_text` produces an unfiltered master CV."""
    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No CV library yet. PUT /api/cv/library first.",
        )

    job: JobParsed | None = None
    if payload.job_text.strip():
        parsed = extract_job(payload.job_text)
        job = JobParsed(**parsed.to_dict())

    library_out = _to_out(row)
    used_llm = False
    llm_skip_reason = ""

    # Career-ops style LLM polish — only when explicitly requested + the
    # LLM layer is configured. Failure here is non-fatal; we fall back to
    # the rule-based renderer with the original library.
    if payload.use_llm:
        polished, _bold_keywords, skip = polish_library_with_llm(library_out, job)
        if polished is not None:
            library_out = polished
            used_llm = True
        else:
            llm_skip_reason = skip

    result = render_cv(
        library_out,
        job=job,
        max_selected_projects=payload.max_selected_projects,
        max_additional_projects=payload.max_additional_projects,
        max_experience=payload.max_experience,
        compile_pdf=payload.compile_pdf,
    )

    return RenderCVResponse(
        latex=result.latex,
        pdf_b64=result.pdf_b64,
        compiled=result.compiled,
        compile_error=result.compile_error,
        sections_chosen=result.sections_chosen,
        matched_skills=result.matched_skills,
        used_llm=used_llm,
        llm_skip_reason=llm_skip_reason,
    )
