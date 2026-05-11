"""Match endpoints: rank all CVs or score a single CV against a JD."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.utils.document_text import extract_document_text
from app.utils.file_validation import (
    ALLOWED_PROFILE_DOC_EXTENSIONS,
    MAX_FILE_BYTES,
    get_extension,
)
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.db_models import CV
from app.models.schemas import (
    BatchMatchResponse,
    BatchMatchRow,
    JobParsed,
    JobUrlRequest,
    MatchRequest,
    MatchResult,
    RankedMatchResponse,
    SingleMatchRequest,
)
from app.services.embedding_service import get_embedding_service
from app.services.extraction import extract_job
from app.services.job_csv_importer import parse_csv_bytes
from app.services.job_scraper import scrape_job_url
from app.services.matching_engine import match_cv_to_job, rank_cvs
from app.services.unified_candidate import get_unified_candidate
from app.services.vector_store import get_vector_store

router = APIRouter(prefix="/api/match", tags=["match"])


@router.post("", response_model=RankedMatchResponse)
def match_all(payload: MatchRequest, db: Session = Depends(get_db)) -> RankedMatchResponse:
    """Match the JD against the unified CV library (aggregate of every upload).

    Falls back to per-CV ranking only when the library hasn't been built
    yet — once any CV is uploaded the library auto-rebuilds, so the
    fallback only fires on a totally empty DB.
    """
    unified = get_unified_candidate(db)
    if unified is not None:
        cvs = [unified]
    else:
        cvs = db.query(CV).all()
        if not cvs:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No CVs uploaded yet.",
            )
    job, results = rank_cvs(cvs, payload.job_text)
    recommended = results[0].cv_id if results else None
    return RankedMatchResponse(job=job, results=results, recommended_cv_id=recommended)


@router.post("/from-url", response_model=RankedMatchResponse)
def match_from_url(payload: JobUrlRequest, db: Session = Depends(get_db)) -> RankedMatchResponse:
    """Scrape a JD URL, then rank every uploaded CV against it.

    Returns 422 with a clear message if the page can't be scraped — the
    frontend should then offer the user the manual paste fallback.
    """
    unified = get_unified_candidate(db)
    if unified is not None:
        cvs = [unified]
    else:
        cvs = db.query(CV).all()
        if not cvs:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No CVs uploaded yet.",
            )
    result = scrape_job_url(payload.url)
    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=result.error or "Could not extract a job description from the URL.",
        )
    job, results = rank_cvs(cvs, result.extracted_text)
    recommended = results[0].cv_id if results else None
    return RankedMatchResponse(job=job, results=results, recommended_cv_id=recommended)


@router.post("/batch-csv", response_model=BatchMatchResponse)
async def match_batch_csv(
    file: UploadFile = File(..., description="CSV with header row"),
    db: Session = Depends(get_db),
) -> BatchMatchResponse:
    """Run the matcher across every JD in an uploaded CSV.

    Returns a flat result row per CSV row containing the best CV plus the
    headline numbers (overall score, skill score, semantic score) and the
    matched / missing skill lists. Bad rows (empty description, parser
    errors) are returned with `error` set so the frontend can render them
    inline rather than failing the whole batch.
    """
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .csv files are supported.",
        )

    unified = get_unified_candidate(db)
    if unified is not None:
        cvs = [unified]
    else:
        cvs = db.query(CV).all()
        if not cvs:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No CVs uploaded yet.",
            )

    data = await file.read()
    parsed = parse_csv_bytes(data)
    if parsed.fatal_error:
        return BatchMatchResponse(error=parsed.fatal_error)

    # Resolve embedder + store once so each row reuses the loaded model.
    embedder = get_embedding_service()
    store = get_vector_store()

    rows_out: list[BatchMatchRow] = []
    processed = 0
    skipped = 0

    for row in parsed.rows:
        if not row.is_usable:
            skipped += 1
            rows_out.append(
                BatchMatchRow(
                    row_index=row.row_index,
                    job_title=row.job_title,
                    company=row.company,
                    location=row.location,
                    url=row.url,
                    salary=row.salary,
                    employment_type=row.employment_type,
                    error=row.error or "Row skipped — empty description.",
                )
            )
            continue

        try:
            jd_parsed = extract_job(row.to_jd_text())
            job = JobParsed(**jd_parsed.to_dict())
            results = [
                match_cv_to_job(cv, job, embedder=embedder, store=store) for cv in cvs
            ]
            results.sort(key=lambda r: (-r.overall_score, -r.skill_score, r.cv_id))
            best = results[0]
            rows_out.append(
                BatchMatchRow(
                    row_index=row.row_index,
                    job_title=row.job_title or job.job_title,
                    company=row.company or job.company,
                    location=row.location or job.location,
                    url=row.url,
                    salary=row.salary or job.salary,
                    employment_type=row.employment_type or job.employment_type,
                    best_cv_id=best.cv_id,
                    best_cv_name=best.cv_name or "",
                    best_cv_filename=best.filename,
                    best_score=best.overall_score,
                    skill_score=best.skill_score,
                    semantic_score=best.semantic_score,
                    matched_skills=best.matched_skills,
                    missing_skills=best.missing_skills,
                    strongest_points=best.strongest_points,
                )
            )
            processed += 1
        except Exception as e:  # noqa: BLE001  # never fail the whole batch
            skipped += 1
            rows_out.append(
                BatchMatchRow(
                    row_index=row.row_index,
                    job_title=row.job_title,
                    company=row.company,
                    location=row.location,
                    url=row.url,
                    salary=row.salary,
                    employment_type=row.employment_type,
                    error=f"Row failed during matching: {e}",
                )
            )

    return BatchMatchResponse(
        rows=rows_out,
        truncated=parsed.truncated,
        rows_processed=processed,
        rows_skipped=skipped,
    )


@router.post("/from-file", response_model=RankedMatchResponse)
async def match_from_file(
    file: UploadFile = File(..., description="PDF / DOCX / TXT containing a JD"),
    db: Session = Depends(get_db),
) -> RankedMatchResponse:
    """Extract a JD from an uploaded file, then rank every CV against it.

    422 with a clear message if the file can't be parsed — the frontend
    should then offer the manual-paste fallback.
    """
    unified = get_unified_candidate(db)
    if unified is not None:
        cvs = [unified]
    else:
        cvs = db.query(CV).all()
        if not cvs:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No CVs uploaded yet.",
            )

    filename = file.filename or "uploaded.txt"
    ext = get_extension(filename)
    if ext not in ALLOWED_PROFILE_DOC_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported file type '{ext}'. "
                f"Allowed: {sorted(ALLOWED_PROFILE_DOC_EXTENSIONS)}"
            ),
        )
    if file.size and file.size > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {MAX_FILE_BYTES // (1024 * 1024)} MB limit.",
        )
    data = await file.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty file uploaded.",
        )
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {MAX_FILE_BYTES // (1024 * 1024)} MB limit.",
        )
    try:
        text = extract_document_text(data, ext)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to extract text from {filename}: {exc}",
        ) from exc
    if not text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No extractable text in {filename} — the file may be a scanned image.",
        )

    job, results = rank_cvs(cvs, text)
    recommended = results[0].cv_id if results else None
    return RankedMatchResponse(job=job, results=results, recommended_cv_id=recommended)


@router.post("/single", response_model=MatchResult)
def match_single(payload: SingleMatchRequest, db: Session = Depends(get_db)) -> MatchResult:
    """Score one specific CV against a job description."""
    cv = db.get(CV, payload.cv_id)
    if not cv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CV not found")
    parsed = extract_job(payload.job_text)
    job = JobParsed(**parsed.to_dict())
    return match_cv_to_job(cv, job)
