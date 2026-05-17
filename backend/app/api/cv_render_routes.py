"""CV Library + tailored-LaTeX-CV endpoints.

    GET  /api/cv/library    — fetch the CV library (header, projects, etc.)
    PUT  /api/cv/library    — replace the library wholesale
    POST /api/cv/render     — render a tailored .tex (and optionally PDF)

The library is a singleton row. All editing flows through PUT — partial
patch semantics aren't worth the complexity for an MVP this size.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
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
from app.models.db_models import CV
from app.services.codex_cv_polish import polish_library_with_llm
from app.services.cv_library_builder import build_library_from_all, build_library_from_cv
from app.services.cv_markdown_converter import convert_cv_text_to_markdown
from app.services.cv_markdown_parser import parse_cv_markdown
from app.services.cv_core_competencies import generate_competencies
from app.services.cv_coverage_booster import boost_coverage
from app.services.cv_renderer import render_cv
from app.services.cv_section_planner import plan_sections
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
        core_competencies=getattr(row, "core_competencies", None) or [],
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


@router.get("/template")
def get_template() -> dict:
    """Return the canonical CV markdown template so the UI can offer a
    one-click download. Keeps the source-of-truth in
    `docs/cv_template.md` — change it there, all consumers pick it up.
    """
    from pathlib import Path

    this = Path(__file__).resolve()
    candidates: list[Path] = [Path("/app/docs/cv_template.md")]
    # Walk up to repo root in dev so this works without Docker too.
    for n in range(2, 6):
        if len(this.parents) > n:
            candidates.append(this.parents[n] / "docs" / "cv_template.md")
    for p in candidates:
        if p.is_file():
            return {"filename": "cv_template.md", "content": p.read_text(encoding="utf-8")}
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Template file not found in the running image.",
    )


@router.get("/library/issues")
def library_issues(db: Session = Depends(get_db)) -> dict:
    """Audit the master library for parse garbage, near-dupes, and
    cross-source conflicts. Combines deterministic checks with an
    optional LLM pass that spots harder problems (timeline gaps,
    unsupported summary claims). Used by the UI to show a warning
    banner before the user renders a tailored CV from a bad master.
    """
    from app.services.library_quality import audit

    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None:
        return {"issues": [], "counts": {"total": 0}, "llm_used": False,
                "error": "No library yet."}
    library_out = _to_out(row)
    result = audit(library_out, use_llm=True)
    return result.model_dump()


@router.delete("/library", status_code=status.HTTP_204_NO_CONTENT)
def delete_library(db: Session = Depends(get_db)) -> None:
    """Drop the singleton library row. Useful when PDF auto-build
    seeded a messy library and the user wants a clean slate before
    uploading `cv.md`. CVs + Documents are untouched."""
    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None:
        return
    db.delete(row)
    db.commit()


@router.get("/llm-status")
def llm_status() -> dict:
    """Quick diagnostic — is the LLM polish layer reachable?

    Hits the configured chat-completion endpoint with a 1-token ping.
    Returns enabled / configured / reachable flags so the UI can show
    a clear "LLM ON" badge instead of silently falling back.
    """
    from app.services import llm_extraction_service as llm

    status_dict: dict = {
        "enabled": llm.is_enabled(),
        "configured": False,
        "reachable": False,
        "provider": "",
        "model": "",
        "base_url": "",
        "error": "",
    }
    if not llm.is_enabled():
        status_dict["error"] = (
            "LLM disabled — set USE_LLM_EXTRACTION=true and either "
            "OPENAI_API_KEY or ANTHROPIC_API_KEY in .env, then restart."
        )
        return status_dict

    cfg = llm._config()  # type: ignore[attr-defined]
    status_dict["configured"] = True
    status_dict["provider"] = cfg.get("provider", "")
    status_dict["model"] = cfg.get("model", "")
    status_dict["base_url"] = cfg.get("base_url", "")

    try:
        # 1-token ping. If this returns anything, the API key + URL work.
        reply = llm._chat_completion([  # type: ignore[attr-defined]
            # OpenAI requires the literal word "json" in messages when
            # response_format=json_object is set. Use a JSON-shaped ping.
            {"role": "system", "content": "Reply with the JSON object {\"ok\": true}."},
            {"role": "user", "content": "ping"},
        ])
        status_dict["reachable"] = bool((reply or "").strip())
        if not status_dict["reachable"]:
            status_dict["error"] = "Empty response from LLM."
    except Exception as exc:  # noqa: BLE001
        status_dict["error"] = f"LLM call failed: {exc}"
    return status_dict


@router.post("/convert-to-markdown")
def convert_to_markdown(payload: dict) -> dict:
    """Run the connected LLM over pasted CV text → return one cv.md string.

    Body: ``{"text": "<raw CV — pdf paste, linkedin export, free notes>"}``
    Returns: ``{"markdown": "...", "filename": "cv.md"}``

    Same prompt as ``docs/cv_to_markdown_prompt.md`` so the in-app flow
    and the copy-paste-to-Claude flow produce identical output shape.
    """
    text = (payload or {}).get("text", "")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Body must include non-empty 'text' field.",
        )
    try:
        md = convert_cv_text_to_markdown(text)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return {"markdown": md, "filename": "cv.md"}


@router.post("/library/from-markdown", response_model=CVLibraryOut)
async def upload_library_markdown(
    file: UploadFile | None = File(default=None, description="Markdown CV file (.md)"),
    db: Session = Depends(get_db),
) -> CVLibraryOut:
    """Replace the CV library by parsing an uploaded `cv.md` directly.

    This is the recommended ingest path: deterministic parsing, no PDF
    whitespace recovery, every field lands where it should. See the
    template at `docs/cv_template.md` — fill it in once and upload here.
    """
    if file is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No file uploaded. Use multipart form field 'file'.",
        )
    if not (file.filename or "").lower().endswith((".md", ".markdown", ".txt")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .md / .markdown / .txt files are accepted here.",
        )
    raw = await file.read()
    text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty file.",
        )

    payload = parse_cv_markdown(text)
    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None:
        row = CVLibrary(id=1)
        db.add(row)
    for k, v in payload.model_dump().items():
        setattr(row, k, v)
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.post("/library/rebuild", response_model=CVLibraryOut)
def rebuild_library_from_all(db: Session = Depends(get_db)) -> CVLibraryOut:
    """Aggregate every uploaded CV + Document into one merged library.

    Replaces the singleton row. Header takes the newest CV's contact
    info; skills / projects / experience / certifications / publications
    / languages are unioned and deduplicated across all sources. Each
    entry is auto-tagged with the canonical skills found in its text so
    the renderer ranks by JD overlap without manual curation.
    """
    payload = build_library_from_all(db)
    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None:
        row = CVLibrary(id=1)
        db.add(row)
    for k, v in payload.model_dump().items():
        setattr(row, k, v)
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.post("/library/from-cv/{cv_id}", response_model=CVLibraryOut)
def build_library_from_cv_id(
    cv_id: int,
    db: Session = Depends(get_db),
) -> CVLibraryOut:
    """Build / replace the CV library by parsing an uploaded CV row.

    Best-effort — the editor in the UI is the source of truth for the
    final shape. Auto-tags every entry with canonical skills found in
    its text so the renderer can rank by JD overlap immediately.
    """
    cv = db.query(CV).filter(CV.id == cv_id).first()
    if cv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"CV {cv_id} not found.",
        )
    payload = build_library_from_cv(cv)

    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None:
        row = CVLibrary(id=1)
        db.add(row)
    for k, v in payload.model_dump().items():
        setattr(row, k, v)
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
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

    # ---- Synthesise Core Competencies row via LLM when polish is on.
    # Career-ops methodology: 6–8 compound noun phrases at the
    # candidate × JD intersection. Falls back to the heuristic
    # (JD ∩ skills_groups single-token match) when LLM is off or fails.
    core_competencies_override: list[str] | None = None
    if payload.use_llm and job is not None:
        core_competencies_override = generate_competencies(
            library=library_out, job=job, want=8,
        )

    # ---- Pick section caps. Page target + (optional) LLM decide.
    plan = plan_sections(
        target_length=payload.target_length,
        library=library_out,
        job=job,
        user_max_selected=payload.max_selected_projects,
        user_max_additional=payload.max_additional_projects,
        user_max_experience=payload.max_experience,
    )

    def _render_and_score(lib_in):
        """Render once and return (result, covered, missing, coverage)."""
        r = render_cv(
            lib_in,
            job=job,
            max_selected_projects=plan.max_selected_projects,
            max_additional_projects=plan.max_additional_projects,
            max_experience=plan.max_experience,
            compile_pdf=False,  # only the final pass compiles PDF
            min_competency_rating=payload.min_competency_rating,
            core_competencies_override=core_competencies_override,
        )
        latex_low_local = r.latex.lower()
        cov_list: list[str] = []
        miss_list: list[str] = []
        for term in r.matched_skills:
            if term.lower() in latex_low_local:
                cov_list.append(term)
            else:
                miss_list.append(term)
        cov_ratio = (len(cov_list) / len(r.matched_skills)) if r.matched_skills else 0.0
        return r, cov_list, miss_list, cov_ratio

    result, covered, missing, coverage = _render_and_score(library_out)
    coverage_history: list[float] = [round(coverage, 3)]
    coverage_boost_log: list[str] = []
    iterations_done = 0

    # ---- Auto-boost loop: if coverage < target AND we're allowed to
    # use the LLM AND there's a JD to chase, ask the LLM to weave the
    # missing keywords into existing bullets, re-render, repeat. Each
    # round is bounded; we stop on first hit or no-progress.
    if (
        payload.use_llm
        and job is not None
        and payload.target_keyword_coverage > 0
        and payload.max_boost_iterations > 0
        and coverage < payload.target_keyword_coverage
        and missing
    ):
        for _ in range(payload.max_boost_iterations):
            if coverage >= payload.target_keyword_coverage or not missing:
                break
            boosted_lib, log = boost_coverage(
                library=library_out, job=job, missing_keywords=missing,
            )
            coverage_boost_log.extend(log)
            iterations_done += 1
            if boosted_lib is library_out:
                # Booster declined (LLM off / no-op). Don't loop forever.
                break
            library_out = boosted_lib
            result, covered, missing, coverage = _render_and_score(library_out)
            coverage_history.append(round(coverage, 3))
            # Stuck? Bail rather than burn LLM calls on a no-progress loop.
            if len(coverage_history) >= 2 and coverage_history[-1] <= coverage_history[-2]:
                coverage_boost_log.append("coverage_boost: no progress, stopping")
                break

    # If we boosted, we skipped PDF compilation each loop — compile now
    # on the final library so the user gets the latest content.
    if iterations_done > 0 and payload.compile_pdf:
        result, covered, missing, coverage = _render_and_score(library_out)
        # Re-run with compile_pdf=true.
        result = render_cv(
            library_out,
            job=job,
            max_selected_projects=plan.max_selected_projects,
            max_additional_projects=plan.max_additional_projects,
            max_experience=plan.max_experience,
            compile_pdf=True,
            min_competency_rating=payload.min_competency_rating,
            core_competencies_override=core_competencies_override,
        )
    elif iterations_done == 0 and payload.compile_pdf:
        # First-pass render skipped PDF; compile now.
        result = render_cv(
            library_out,
            job=job,
            max_selected_projects=plan.max_selected_projects,
            max_additional_projects=plan.max_additional_projects,
            max_experience=plan.max_experience,
            compile_pdf=True,
            min_competency_rating=payload.min_competency_rating,
            core_competencies_override=core_competencies_override,
        )

    # ---- Filename: cv-{first-name-kebab}-{company-kebab}-{YYYY-MM-DD}
    from datetime import date as _date
    import re as _re

    def _kebab(s: str) -> str:
        s = (s or "").strip().lower()
        s = _re.sub(r"[^a-z0-9]+", "-", s).strip("-")
        return s or "unknown"

    candidate = _kebab((library_out.header.name or "").split()[0] if library_out.header.name else "")
    company = _kebab(job.company if job and job.company else "")
    today = _date.today().isoformat()
    filename = f"cv-{candidate}-{company}-{today}".replace("--", "-").strip("-")

    return RenderCVResponse(
        latex=result.latex,
        pdf_b64=result.pdf_b64,
        compiled=result.compiled,
        compile_error=result.compile_error,
        sections_chosen=result.sections_chosen,
        matched_skills=result.matched_skills,
        used_llm=used_llm,
        llm_skip_reason=llm_skip_reason,
        keyword_coverage=round(coverage, 3),
        keywords_covered=covered,
        keywords_missing=missing,
        suggested_filename=filename or "tailored-cv",
        section_plan={
            "max_selected_projects": plan.max_selected_projects,
            "max_additional_projects": plan.max_additional_projects,
            "max_experience": plan.max_experience,
            "source": plan.source,
            "rationale": plan.rationale,
        },
        core_competencies=core_competencies_override or [],
        job_title=(job.job_title if job is not None else ""),
        job_company=(job.company if job is not None else ""),
        coverage_iterations=iterations_done,
        coverage_history=coverage_history,
        coverage_boost_log=coverage_boost_log,
    )
