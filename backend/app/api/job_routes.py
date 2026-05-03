"""Job endpoints: parse a raw JD, or fetch + parse one from a URL."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.db_models import UserProfile
from app.models.db_models import CV, UserProfile
from app.models.schemas import (
    DiscoveredJob,
    JobCsvImportResponse,
    JobCsvRowSchema,
    JobDiscoveryRequest,
    JobDiscoveryResponse,
    JobFileResponse,
    JobParsed,
    JobParseRequest,
    JobUrlRequest,
    JobUrlResponse,
    RankJobsRequest,
    RankJobsResponse,
    RankedJobResult,
)
from app.services.extraction import extract_job
from app.services.job_csv_importer import parse_csv_bytes
from app.services.job_discovery import discover_jobs
from app.services.job_scraper import scrape_job_url
from app.services.matching_engine import match_cv_to_job
from app.services.query_builder import build_query_payload
from app.utils.document_text import extract_document_text
from app.utils.file_validation import (
    ALLOWED_PROFILE_DOC_EXTENSIONS,
    MAX_FILE_BYTES,
    get_extension,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.post("/parse", response_model=JobParsed)
def parse_job(payload: JobParseRequest) -> JobParsed:
    if not payload.text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job description text is empty.",
        )
    parsed = extract_job(payload.text)
    return JobParsed(**parsed.to_dict())


@router.post("/from-url", response_model=JobUrlResponse)
def parse_job_from_url(payload: JobUrlRequest) -> JobUrlResponse:
    """Fetch a public job-posting URL and run it through the JD parser.

    Returns `success=False` with an explanatory `error` if the page can't be
    scraped (login wall, JS-rendered content, robots.txt block, etc.). The
    user can always fall back to pasting the JD manually.
    """
    result = scrape_job_url(payload.url)
    if not result.success:
        return JobUrlResponse(
            url=result.url,
            success=False,
            extracted_text=result.extracted_text,
            error=result.error,
            notes=result.notes,
        )

    parsed = extract_job(result.extracted_text)
    return JobUrlResponse(
        url=result.url,
        success=True,
        extracted_text=result.extracted_text,
        parsed_job=JobParsed(**parsed.to_dict()),
        notes=result.notes,
    )


@router.post("/from-file", response_model=JobFileResponse)
async def parse_job_from_file(
    file: UploadFile = File(..., description="PDF / DOCX / TXT containing a JD"),
) -> JobFileResponse:
    """Extract a JD from an uploaded file and run it through the parser.

    Accepts the same extension set as profile documents
    (PDF / DOCX / TXT). Returns `success=False` with a clear `error`
    string when extraction fails — the frontend can fall back to manual
    paste.
    """
    filename = file.filename or "uploaded.txt"
    ext = get_extension(filename)
    if ext not in ALLOWED_PROFILE_DOC_EXTENSIONS:
        return JobFileResponse(
            filename=filename, success=False,
            error=(
                f"Unsupported file type '{ext}'. "
                f"Allowed: {sorted(ALLOWED_PROFILE_DOC_EXTENSIONS)}"
            ),
        )
    if file.size and file.size > MAX_FILE_BYTES:
        return JobFileResponse(
            filename=filename, success=False,
            error=f"File exceeds {MAX_FILE_BYTES // (1024 * 1024)} MB limit.",
        )
    data = await file.read()
    if not data:
        return JobFileResponse(
            filename=filename, success=False, error="Empty file uploaded.",
        )
    if len(data) > MAX_FILE_BYTES:
        return JobFileResponse(
            filename=filename, success=False,
            error=f"File exceeds {MAX_FILE_BYTES // (1024 * 1024)} MB limit.",
        )
    try:
        text = extract_document_text(data, ext)
    except Exception as exc:  # noqa: BLE001
        return JobFileResponse(
            filename=filename, success=False,
            error=f"Failed to extract text: {exc}",
        )
    if not text.strip():
        return JobFileResponse(
            filename=filename, success=False,
            error="No extractable text — the file may be a scanned image.",
        )

    parsed = extract_job(text)
    return JobFileResponse(
        filename=filename, success=True,
        extracted_text=text,
        parsed_job=JobParsed(**parsed.to_dict()),
    )


@router.post("/import-csv", response_model=JobCsvImportResponse)
async def import_csv(
    file: UploadFile = File(..., description="CSV with header row"),
) -> JobCsvImportResponse:
    """Parse and validate a multi-job CSV without running the matcher.

    Useful for previewing what the system extracted before kicking off a
    batch match. Returns `error` (string) for fatal problems (missing
    columns, empty file). Per-row issues are surfaced via `rows[i].error`.
    """
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .csv files are supported.",
        )
    data = await file.read()
    result = parse_csv_bytes(data)
    if result.fatal_error:
        return JobCsvImportResponse(error=result.fatal_error, headers=result.headers)
    return JobCsvImportResponse(
        rows=[JobCsvRowSchema(**r.__dict__) for r in result.rows],
        headers=result.headers,
        truncated=result.truncated,
    )


@router.post("/discover", response_model=JobDiscoveryResponse)
def discover(
    payload: JobDiscoveryRequest | None = None,
    db: Session = Depends(get_db),
) -> JobDiscoveryResponse:
    """Discover jobs from public, no-login JSON APIs only.

    Behaviour:
      - Pulls **queries** and **tags** from the request body if provided.
      - Otherwise derives them from the unified `UserProfile` via
        `query_builder` — so the typical flow is:
        `POST /api/profile/build` → `POST /api/jobs/discover` (empty body).
      - Calls every requested source (RemoteOK / Remotive / HN), dedupes
        by URL, scores against the user's tags, and returns ranked jobs.
      - One source failing surfaces in `errors[]`; the call still
        succeeds with whatever the other sources returned.

    Constraints (enforced at the service layer):
      - Public JSON endpoints only — no HTML scraping.
      - No login flows, no anti-bot bypass.
      - Per-host throttle of 3 s minimum between calls.
      - Hard cap of 100 results regardless of `max_total`.
    """
    body = payload or JobDiscoveryRequest()
    queries = body.queries
    tags_dict: dict[str, list[str]] | None = body.tags.model_dump() if body.tags else None

    if not queries or not tags_dict:
        profile = db.query(UserProfile).first()
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    "No queries provided and no unified profile to derive them from. "
                    "Either include `queries`/`tags` in the body or call "
                    "POST /api/profile/build first."
                ),
            )
        derived = build_query_payload(profile)
        if not queries:
            queries = derived["queries"]
        if not tags_dict:
            tags_dict = derived["tags"]

    result = discover_jobs(
        queries=queries or [],
        tags=tags_dict,
        sources=body.sources,
        max_per_source=body.max_per_source,
        max_total=body.max_total,
    )

    return JobDiscoveryResponse(
        queries_used=result.queries_used,
        results=[DiscoveredJob(**j.to_dict()) for j in result.results],
        skipped_sources=result.skipped_sources,
        errors=result.errors,
    )


# ---------- Multi-job ranking ----------

def _profile_to_synthetic_cv(profile: UserProfile) -> SimpleNamespace:
    """Build a CV-shaped duck-typed object from the unified profile.

    Lets the matcher run when the user hasn't uploaded individual CVs but
    has built a unified profile from supplementary documents.
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
        id=-1,  # sentinel — never collides with a real CV row
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


@router.post("/rank", response_model=RankJobsResponse)
def rank_jobs(payload: RankJobsRequest, db: Session = Depends(get_db)) -> RankJobsResponse:
    """Rank a batch of jobs against the user's CVs and/or unified profile.

    For each job:
      1. Parse the JD via the existing extraction layer.
      2. Score it against every CV in the pool (`match_cv_to_job`).
      3. Take the best CV's overall score as the job's score.

    Pool selection (in order):
      - explicit `cv_ids`,
      - else every CV in the DB,
      - else (when `use_profile_fallback=True`) a single synthetic CV
        derived from the unified profile.
    """
    if payload.cv_ids:
        cvs = (
            db.query(CV)
            .filter(CV.id.in_(payload.cv_ids))
            .all()
        )
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

    cv_pool_ids = [int(cv.id) for cv in cvs if int(getattr(cv, "id", -1)) >= 0]

    ranked: list[RankedJobResult] = []
    for job_input in payload.jobs:
        parsed = extract_job(job_input.job_text)
        job = JobParsed(**parsed.to_dict())
        results = [match_cv_to_job(cv, job) for cv in cvs]
        results.sort(key=lambda r: (-r.overall_score, -r.skill_score, r.cv_id))
        best = results[0]
        ranked.append(RankedJobResult(
            job=job_input,
            best_cv_id=best.cv_id if best.cv_id >= 0 else None,
            best_cv_name=best.cv_name or "",
            best_cv_filename=best.filename or "",
            overall_score=best.overall_score,
            skill_score=best.skill_score,
            semantic_score=best.semantic_score,
            experience_score=best.experience_score,
            education_score=best.education_score,
            project_score=best.project_score,
            matched_skills=best.matched_skills,
            missing_skills=best.missing_skills,
            strongest_points=best.strongest_points,
            explanation=best.explanation,
        ))

    ranked.sort(key=lambda r: (-r.overall_score, -r.skill_score))
    return RankJobsResponse(
        results=ranked[: payload.max_results],
        cv_pool_ids=cv_pool_ids,
        used_profile_fallback=used_profile_fallback,
    )
