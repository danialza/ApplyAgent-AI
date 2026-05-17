"""CV endpoints: upload, list, retrieve, delete."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.db_models import CV
from app.models.schemas import CVOut
from app.services.cv_parser import extract_text
from app.services.extraction import extract_cv as extract_cv_structured
from app.services.embedding_service import get_embedding_service
from app.services.vector_store import get_vector_store, index_cv
from app.utils.file_validation import MAX_FILE_BYTES, validate_upload

router = APIRouter(prefix="/api/cvs", tags=["cvs"])


@router.post("/upload", response_model=list[CVOut], status_code=status.HTTP_201_CREATED)
async def upload_cvs(
    files: list[UploadFile] = File(..., description="One or more PDF/DOCX CVs"),
    db: Session = Depends(get_db),
) -> list[CVOut]:
    """Accept N PDF/DOCX files, parse each, persist, return parsed records."""
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one file is required.",
        )

    saved: list[CV] = []
    for file in files:
        # Cheap early reject on stated size to avoid buffering huge files.
        if file.size and file.size > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"{file.filename}: exceeds {MAX_FILE_BYTES // (1024 * 1024)} MB limit.",
            )
        data = await file.read()
        ext = validate_upload(file, len(data))

        try:
            raw_text = extract_text(data, ext)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Failed to extract text from {file.filename}: {exc}",
            ) from exc

        if not raw_text.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"No text extracted from {file.filename} (scanned PDF?).",
            )

        parsed = extract_cv_structured(raw_text)
        cv = CV(
            filename=file.filename or "uploaded.pdf",
            name=parsed.name,
            summary=parsed.summary,
            skills=parsed.skills,
            education=parsed.education,
            experience=parsed.experience,
            projects=parsed.projects,
            certifications=parsed.certifications,
            languages=parsed.languages,
            email=parsed.email,
            phone=parsed.phone,
            linkedin=parsed.linkedin,
            github=parsed.github,
            portfolio=parsed.portfolio,
            raw_text=raw_text,
        )
        db.add(cv)
        saved.append(cv)

    db.commit()
    for cv in saved:
        db.refresh(cv)

    # Best-effort: index newly uploaded CVs into the FAISS store.
    embedder = get_embedding_service()
    store = get_vector_store()
    if embedder.is_ready() and store is not None:
        for cv in saved:
            try:
                index_cv(store, embedder, cv)
            except Exception:  # pragma: no cover  # don't fail the upload on index error
                pass
        store.save()

    # ONLY auto-seed the library the very first time. Never overwrite an
    # existing library with PDF-derived data — pdfplumber's word
    # boundaries are too unreliable on real-world fonts (charter, kerned
    # PDFs, etc.) and the result is bullets misclassified across
    # sections. Once the user uploads a clean cv.md via
    # POST /api/cv/library/from-markdown, every future PDF upload skips
    # this hook and feeds only the matcher / vector index. The user
    # can always trigger an explicit rebuild via
    # POST /api/cv/library/rebuild if they want.
    if saved:
        # Rebuild master library — honours the hand-edit lock.
        from app.services.master_rebuild import try_rebuild_master
        try_rebuild_master(db)

    return [CVOut.model_validate(cv) for cv in saved]


@router.get("", response_model=list[CVOut])
def list_cvs(db: Session = Depends(get_db)) -> list[CVOut]:
    cvs = db.query(CV).order_by(CV.created_at.desc()).all()
    return [CVOut.model_validate(cv) for cv in cvs]


@router.get("/{cv_id}", response_model=CVOut)
def get_cv(cv_id: int, db: Session = Depends(get_db)) -> CVOut:
    cv = db.get(CV, cv_id)
    if not cv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CV not found")
    return CVOut.model_validate(cv)


@router.delete("/{cv_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cv(cv_id: int, db: Session = Depends(get_db)) -> None:
    cv = db.get(CV, cv_id)
    if not cv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CV not found")
    db.delete(cv)
    db.commit()

    # Keep the vector index in sync with the DB.
    store = get_vector_store()
    if store is not None:
        store.remove_cv(cv_id)
        store.save()

    # Honours lock; silent if hand-edited.
    from app.services.master_rebuild import try_rebuild_master
    try_rebuild_master(db)
