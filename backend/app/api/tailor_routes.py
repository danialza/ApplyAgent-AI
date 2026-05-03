"""CV tailoring endpoint: pick best CV + structured tailoring suggestions."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.db_models import CV, UserProfile
from app.models.schemas import JobParsed, TailorRequest, TailorResponse
from app.services.extraction import extract_job
from app.services.matching_engine import match_cv_to_job
from app.services.tailoring_service import build_tailoring_suggestion

router = APIRouter(prefix="/api/tailor", tags=["tailor"])


def _profile_to_synthetic_cv(profile: UserProfile) -> SimpleNamespace:
    """Build a CV-shaped duck-typed object from the unified profile.

    Same shape as the helper in `job_routes.py` — kept local to avoid
    cross-router imports for a 30-line helper.
    """
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
        "\n".join(profile.projects or []),
        "\n".join(profile.education or []),
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


@router.post("", response_model=TailorResponse)
def tailor_cv(
    payload: TailorRequest,
    db: Session = Depends(get_db),
) -> TailorResponse:
    """Pick the best CV for a JD and emit structured tailoring suggestions.

    Pool selection mirrors `/api/jobs/rank`:
      - explicit `cv_ids`,
      - else every CV,
      - else (when `use_profile_fallback=true`) a synthetic CV from the
        unified profile.
    """
    if payload.cv_ids:
        cvs = db.query(CV).filter(CV.id.in_(payload.cv_ids)).all()
        missing = set(payload.cv_ids) - {cv.id for cv in cvs}
        if missing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"CV id(s) not found: {sorted(missing)}",
            )
    else:
        cvs = db.query(CV).all()

    used_profile_fallback = False
    if not cvs:
        if payload.use_profile_fallback:
            profile = db.query(UserProfile).first()
            if profile is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=(
                        "No CVs uploaded and no unified profile to fall back on. "
                        "Upload a CV or call POST /api/profile/build first."
                    ),
                )
            cvs = [_profile_to_synthetic_cv(profile)]
            used_profile_fallback = True
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No CVs uploaded yet.",
            )

    parsed = extract_job(payload.job_text)
    job = JobParsed(**parsed.to_dict())

    results = [(cv, match_cv_to_job(cv, job)) for cv in cvs]
    results.sort(
        key=lambda pair: (-pair[1].overall_score, -pair[1].skill_score, pair[1].cv_id),
    )
    best_cv, best_match = results[0]

    suggestions = build_tailoring_suggestion(best_cv, job, best_match)

    return TailorResponse(
        best_cv_id=best_match.cv_id if best_match.cv_id >= 0 else None,
        best_cv_name=best_match.cv_name or "",
        best_cv_filename=best_match.filename or "",
        job=job,
        match=best_match,
        suggestions=suggestions,
        used_profile_fallback=used_profile_fallback,
    )
