"""Profile aggregator endpoints.

  POST   /api/profile/build  — optional file uploads, then build/refresh profile
  GET    /api/profile        — return the unified profile (or 404)
  DELETE /api/profile        — delete the profile + supplementary documents
                                (individual CVs are intentionally preserved)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.db_models import Document, UserProfile
from app.models.schemas import QueryBuilderResponse, UserProfileOut
from app.services.cv_parser import extract_text_from_docx, extract_text_from_pdf
from app.services.profile_service import (
    build_profile_payload,
    delete_user_profile,
    upsert_user_profile,
)
from app.services.query_builder import build_query_payload
from app.utils.file_validation import (
    ALLOWED_PROFILE_DOC_EXTENSIONS,
    MAX_FILE_BYTES,
    get_extension,
)
from app.utils.text_cleaning import clean_text


def _extract_doc_text(data: bytes, ext: str) -> str:
    """Pull text from a PDF / DOCX / TXT byte buffer.

    Local helper rather than re-using `cv_parser.extract_text` because
    profile docs additionally support .txt (CVs don't).
    """
    ext = ext.lower()
    if ext == ".pdf":
        return extract_text_from_pdf(data)
    if ext == ".docx":
        return extract_text_from_docx(data)
    if ext == ".txt":
        return clean_text(data.decode("utf-8", errors="replace"))
    raise ValueError(f"Unsupported extension: {ext}")

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.post("/build", response_model=UserProfileOut)
async def build_profile(
    files: list[UploadFile] | None = File(default=None),
    db: Session = Depends(get_db),
) -> UserProfileOut:
    """Aggregate CVs + Documents into a unified profile.

    Any files attached to this request are first persisted as Documents,
    then aggregation runs across (all CVs + all Documents).
    """
    # 1. Persist uploaded files (PDF / DOCX / TXT).
    if files:
        for file in files:
            ext = get_extension(file.filename or "")
            if ext not in ALLOWED_PROFILE_DOC_EXTENSIONS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Unsupported file type '{ext}' for profile docs. "
                        f"Allowed: {sorted(ALLOWED_PROFILE_DOC_EXTENSIONS)}"
                    ),
                )
            if file.size and file.size > MAX_FILE_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"{file.filename}: exceeds {MAX_FILE_BYTES // (1024 * 1024)} MB limit.",
                )
            data = await file.read()
            if not data:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{file.filename}: empty file.",
                )
            try:
                text = _extract_doc_text(data, ext)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"{file.filename}: failed to extract text: {exc}",
                ) from exc
            if not text.strip():
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"{file.filename}: extracted text is empty.",
                )
            db.add(Document(filename=file.filename or "uploaded", raw_text=text))
        db.commit()

    # 2. Run the UserProfile aggregator (existing flow).
    payload = build_profile_payload(db)
    profile = upsert_user_profile(db, payload)

    # 3. Also rebuild the unified CV library so section 5 (Tailored CV)
    # picks up skills / projects / publications mentioned in any uploaded
    # Document. Best-effort; failures shouldn't break the profile build.
    try:
        from datetime import datetime as _dt
        from app.models.db_models import CVLibrary
        from app.services.cv_library_builder import build_library_from_all

        lib_payload = build_library_from_all(db).model_dump()
        row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
        if row is None:
            row = CVLibrary(id=1, **lib_payload)
            db.add(row)
        else:
            for k, v in lib_payload.items():
                setattr(row, k, v)
            row.updated_at = _dt.utcnow()
        db.commit()
    except Exception as exc:  # pragma: no cover
        import logging as _log
        _log.getLogger("ai_job_cv_matcher.profile").warning(
            "CV library rebuild on profile-build failed: %s", exc,
        )

    return UserProfileOut.model_validate(profile)


@router.get("", response_model=UserProfileOut)
def get_profile(db: Session = Depends(get_db)) -> UserProfileOut:
    profile = db.query(UserProfile).first()
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No profile built yet. Call POST /api/profile/build first.",
        )
    return UserProfileOut.model_validate(profile)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def remove_profile(db: Session = Depends(get_db)) -> None:
    """Delete the unified profile and supplementary documents.

    CVs are NOT touched — use DELETE /api/cvs/{id} to remove those.
    """
    delete_user_profile(db)


@router.get("/queries", response_model=QueryBuilderResponse)
def profile_queries(db: Session = Depends(get_db)) -> QueryBuilderResponse:
    """Generate optimised job-search queries + tags from the unified profile."""
    profile = db.query(UserProfile).first()
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No profile built yet. Call POST /api/profile/build first.",
        )
    return QueryBuilderResponse(**build_query_payload(profile))
