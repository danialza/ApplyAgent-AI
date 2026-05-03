"""Generate CV suggestions, a cover letter, and a LinkedIn message.

Single endpoint, multiple artefacts — caller picks which kinds to produce.
Pool resolution and matching mirror `/api/tailor`.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.db_models import CV, UserProfile
from app.models.schemas import (
    GenerateRequest,
    GenerateResponse,
    JobParsed,
)
from app.services.extraction import extract_job
from app.services.generation_service import generate_artefacts
from app.services.matching_engine import match_cv_to_job
from app.services.tailoring_service import build_tailoring_suggestion

router = APIRouter(prefix="/api/generate", tags=["generate"])


_VALID_KINDS = {"cv_suggestions", "cover_letter", "linkedin_message"}


def _profile_to_synthetic_cv(profile: UserProfile) -> SimpleNamespace:
    """Same shape used by `/api/tailor` and `/api/jobs/rank`."""
    skill_names = [s.get("name", "") for s in (profile.skills or []) if s.get("name")]
    tool_names = [t.get("name", "") for t in (profile.tools_and_technologies or []) if t.get("name")]
    experience_texts = [
        e.get("text", "") for e in (profile.work_experience or []) if e.get("text")
    ]
    raw_text = "\n".join(filter(None, [
        profile.summary or "",
        ", ".join(skill_names),
        ", ".join(tool_names),
        "\n".join(experience_texts),
    ]))
    return SimpleNamespace(
        id=-1,
        filename="(profile)",
        name=profile.name or "",
        summary=profile.summary or "",
        skills=skill_names + tool_names,
        experience=experience_texts,
        projects=list(profile.projects or []),
        education=list(profile.education or []),
        certifications=list(profile.certifications or []),
        languages=list(profile.languages or []),
        raw_text=raw_text,
    )


def _resolve_pool(
    db: Session,
    *,
    cv_ids: list[int] | None,
    use_profile_fallback: bool,
) -> tuple[list[Any], bool]:
    if cv_ids:
        cvs = db.query(CV).filter(CV.id.in_(cv_ids)).all()
        missing = set(cv_ids) - {cv.id for cv in cvs}
        if missing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"CV id(s) not found: {sorted(missing)}",
            )
        return list(cvs), False

    cvs = db.query(CV).all()
    if cvs:
        return list(cvs), False

    if use_profile_fallback:
        profile = db.query(UserProfile).first()
        if profile is not None:
            return [_profile_to_synthetic_cv(profile)], True

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=(
            "No CVs uploaded and no unified profile to fall back on. "
            "Upload a CV or call POST /api/profile/build first."
        ),
    )


@router.post("", response_model=GenerateResponse)
def generate(
    payload: GenerateRequest,
    db: Session = Depends(get_db),
) -> GenerateResponse:
    """Pick the best CV for the JD and produce the requested artefacts."""
    requested = [k for k in payload.kinds if k in _VALID_KINDS]
    if not requested:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"`kinds` must be a non-empty subset of {sorted(_VALID_KINDS)}.",
        )

    cvs, used_profile_fallback = _resolve_pool(
        db, cv_ids=payload.cv_ids, use_profile_fallback=payload.use_profile_fallback,
    )

    parsed = extract_job(payload.job_text)
    job = JobParsed(**parsed.to_dict())

    scored = [(cv, match_cv_to_job(cv, job)) for cv in cvs]
    scored.sort(key=lambda pair: (-pair[1].overall_score, -pair[1].skill_score, pair[1].cv_id))
    best_cv, best_match = scored[0]

    suggestions = build_tailoring_suggestion(best_cv, job, best_match)

    bundle = generate_artefacts(
        kinds=requested,
        job=job,
        match=best_match,
        suggestions=suggestions,
        cv_name=best_match.cv_name or "",
        cv_experience=list(getattr(best_cv, "experience", []) or []),
        polish_with_llm=payload.polish_with_llm,
    )

    return GenerateResponse(
        cv_suggestions=bundle.cv_suggestions,
        cover_letter=bundle.cover_letter,
        linkedin_message=bundle.linkedin_message,
        best_cv_id=best_match.cv_id if best_match.cv_id is not None and best_match.cv_id >= 0 else None,
        best_cv_name=best_match.cv_name or "",
        best_cv_filename=best_match.filename or "",
        job=job,
        match=best_match,
        used_llm=bundle.used_llm,
        used_profile_fallback=used_profile_fallback,
    )
