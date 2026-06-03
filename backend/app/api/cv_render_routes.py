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
        project_links=getattr(row, "project_links", None) or {},
        updated_at=row.updated_at or datetime.utcnow(),
        manually_edited_at=getattr(row, "manually_edited_at", None),
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
    """Audit the master library. Filters out issues the user has
    previously dismissed (stored on cv_library.ignored_issues)."""
    from app.services.library_quality import audit

    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None:
        return {"issues": [], "counts": {"total": 0}, "llm_used": False,
                "error": "No library yet."}
    library_out = _to_out(row)
    ignored = set(getattr(row, "ignored_issues", None) or [])
    result = audit(library_out, use_llm=True, ignored_fingerprints=ignored)
    payload = result.model_dump()
    payload["ignored_count"] = len(ignored)
    return payload


@router.post("/library/issues/ignore")
def ignore_issue(payload: dict, db: Session = Depends(get_db)) -> dict:
    """Add an issue fingerprint to the ignore list. Body:
    ``{"fingerprint": "<sha1>"}`` OR ``{"scope": "...", "title": "..."}``.
    Same issue (across LLM rephrasings of nearly-identical title) stays
    hidden on future audits."""
    from app.services.library_quality import fingerprint as _fp
    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None:
        raise HTTPException(status_code=404, detail="No library yet.")
    fp = (payload or {}).get("fingerprint", "")
    if not fp:
        scope = (payload or {}).get("scope", "")
        title = (payload or {}).get("title", "")
        if not scope and not title:
            raise HTTPException(status_code=400, detail="Provide fingerprint or scope+title.")
        fp = _fp(scope, title)
    current = list(getattr(row, "ignored_issues", None) or [])
    if fp not in current:
        current.append(fp)
    row.ignored_issues = current
    db.commit()
    return {"fingerprint": fp, "ignored_count": len(current)}


@router.delete("/library/issues/ignore")
def unignore_all(db: Session = Depends(get_db)) -> dict:
    """Clear the entire ignore list — next audit re-surfaces everything."""
    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None:
        raise HTTPException(status_code=404, detail="No library yet.")
    row.ignored_issues = []
    db.commit()
    return {"ignored_count": 0}


@router.post("/library/apply-fix", response_model=CVLibraryOut)
def apply_fix(payload: dict, db: Session = Depends(get_db)) -> CVLibraryOut:
    """Apply a structured FixAction from the library audit.

    Body: ``{"kind": "...", "payload": {...}}``. The action is
    applied to the live library AND appended to user_patches so
    subsequent source-rebuilds replay it (your edit wins).
    Does NOT lock the library — auto-rebuilds from new sources keep
    flowing in, with your patches applied on top.
    """
    from app.services.user_patches import apply_action, validate_action

    kind = (payload or {}).get("kind", "")
    p = (payload or {}).get("payload", {}) or {}
    if not kind:
        raise HTTPException(status_code=400, detail="Missing 'kind'.")
    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None:
        raise HTTPException(status_code=404, detail="No library.")
    try:
        validate_action(kind, p)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        apply_action(row, kind, p)
    except (IndexError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Apply failed: {exc}") from exc
    # Record so rebuilds replay.
    patches = list(getattr(row, "user_patches", None) or [])
    patches.append({"kind": kind, "payload": p})
    row.user_patches = patches
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.post("/library/add-project", response_model=CVLibraryOut)
def add_project(payload: dict, db: Session = Depends(get_db)) -> CVLibraryOut:
    """Enrich + append a project to the master library.

    Body:
      {
        "title": str (required),
        "url": str (optional, GitHub/paper/demo/web),
        "period": str (optional),
        "notes": str (optional, free-form prose),
        "tag_hints": [str] (optional),
        "section": "selected_projects" | "additional_projects"
                   (default: selected_projects),
        "position": "end" | "start" | int (default: end),
        "jd_text": str (optional — bias bullets toward this JD)
      }

    Persists as an ``add_entry`` patch so source-rebuilds replay it.
    """
    from app.services.project_enricher import enrich_project
    from app.services.user_patches import apply_action, validate_action

    p = payload or {}
    title = (p.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title required")
    section = (p.get("section") or "selected_projects").strip()
    if section not in ("selected_projects", "additional_projects"):
        raise HTTPException(
            status_code=400,
            detail="section must be selected_projects or additional_projects",
        )

    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None:
        raise HTTPException(status_code=404, detail="No library.")

    entry, fetched_url, enrich_err = enrich_project(
        title=title,
        url=(p.get("url") or "").strip(),
        period=(p.get("period") or "").strip(),
        notes=(p.get("notes") or "").strip(),
        tag_hints=list(p.get("tag_hints") or []),
        jd_text=(p.get("jd_text") or "").strip(),
    )

    patch_payload = {
        "section": section,
        "entry": entry.model_dump(),
        "position": p.get("position", "end"),
    }
    try:
        validate_action("add_entry", patch_payload)
        apply_action(row, "add_entry", patch_payload)
    except (ValueError, IndexError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Apply failed: {exc}") from exc

    patches = list(getattr(row, "user_patches", None) or [])
    patches.append({"kind": "add_entry", "payload": patch_payload})
    row.user_patches = patches
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    if enrich_err:
        import logging as _log
        _log.getLogger("ai_job_cv_matcher.cv_render_routes").info(
            "add-project enrichment note: %s", enrich_err
        )
    return _to_out(row)


@router.post("/library/unlock", response_model=CVLibraryOut)
def unlock_library(db: Session = Depends(get_db)) -> CVLibraryOut:
    """Clear the manual-edit lock. Library content stays the same
    but the next source upload / delete will auto-rebuild it again."""
    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None:
        raise HTTPException(status_code=404, detail="No library to unlock.")
    row.manually_edited_at = None
    db.commit()
    db.refresh(row)
    return _to_out(row)


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


@router.post("/llm-model")
def set_llm_model(payload: dict) -> dict:
    """Switch the active LLM model at runtime.

    Body: ``{"model": "claude-sonnet-4-5"}``. Sets the right env var
    for the active provider (ANTHROPIC_MODEL for anthropic,
    LLM_MODEL_NAME for openai). Returns updated llm_status.
    """
    import os as _os
    model = (payload or {}).get("model", "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="model required.")
    # Route by detected provider — set the matching env so next
    # _config() call picks it up.
    provider = (_os.getenv("LLM_PROVIDER") or "").strip().lower()
    if provider == "anthropic" or model.startswith("claude-"):
        _os.environ["ANTHROPIC_MODEL"] = model
    elif provider == "openai" or model.startswith(("gpt-", "o1-", "o3-")):
        _os.environ["LLM_MODEL_NAME"] = model
    else:
        _os.environ["ANTHROPIC_MODEL"] = model
        _os.environ["LLM_MODEL_NAME"] = model
    return llm_status()


# Known model IDs the UI dropdown offers. Free-text input still
# allowed via the API for unreleased / private models.
_KNOWN_MODELS = {
    "anthropic": [
        "claude-sonnet-4-6",      # default
        "claude-sonnet-4-5",
        "claude-haiku-4-5",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-opus-4-1",        # legacy opus
    ],
    "openai": [
        "gpt-5",
        "gpt-5-mini",
        "gpt-4o",
        "gpt-4o-mini",
        "o3-mini",
    ],
}


@router.get("/llm-models")
def list_llm_models() -> dict:
    """Return known model IDs per provider for the UI dropdown."""
    return _KNOWN_MODELS


@router.post("/llm-provider")
def set_llm_provider(payload: dict) -> dict:
    """Switch the active LLM provider at runtime.

    Body: ``{"provider": "claude_code" | "anthropic" | "openai"}``.
    Sets os.environ['LLM_PROVIDER'] in-process so the next call to
    `_detect_provider()` picks it up. No container restart needed.

    Note: only persists for the lifetime of this process. Restart →
    reverts to whatever the .env / compose file specifies.
    """
    import os as _os
    provider = (payload or {}).get("provider", "").strip().lower()
    if provider not in {"claude_code", "anthropic", "openai"}:
        raise HTTPException(
            status_code=400,
            detail=f"provider must be one of: claude_code, anthropic, openai (got {provider!r}).",
        )
    _os.environ["LLM_PROVIDER"] = provider
    return llm_status()


@router.get("/llm-status")
def llm_status() -> dict:
    """Quick diagnostic — is the LLM polish layer reachable?

    Hits the configured chat-completion endpoint with a 1-token ping.
    Returns enabled / configured / reachable flags so the UI can show
    a clear "LLM ON" badge instead of silently falling back.
    """
    from app.services import llm_extraction_service as llm

    import os as _os
    # Which providers have a usable key configured. The UI dropdown
    # only offers these so the user can't pick a dead provider.
    available: list[str] = []
    if _os.getenv("ANTHROPIC_API_KEY"):
        available.append("anthropic")
    if _os.getenv("OPENAI_API_KEY"):
        available.append("openai")

    status_dict: dict = {
        "enabled": llm.is_enabled(),
        "configured": False,
        "reachable": False,
        "provider": "",
        "model": "",
        "base_url": "",
        "error": "",
        "available_providers": available,
    }
    if not llm.is_enabled():
        active = (_os.getenv("LLM_PROVIDER") or "").strip().lower()
        if active == "openai" and "openai" not in available:
            status_dict["error"] = (
                "OpenAI selected but no OPENAI_API_KEY configured. "
                "Switch to Anthropic, or add OPENAI_API_KEY to .env "
                "and restart."
            )
        elif active == "anthropic" and "anthropic" not in available:
            status_dict["error"] = (
                "Anthropic selected but no ANTHROPIC_API_KEY configured. "
                "Add it to .env and restart."
            )
        else:
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

    # Persist the raw markdown as a Document source so subsequent
    # rebuilds (triggered by notes / URL adds) can re-parse it as the
    # library base. Without this the markdown was a one-shot write to
    # cv_library and the next source change rebuilt from an empty
    # sources table — wiping all cv.md content.
    from app.models.db_models import Document as _Doc
    fname = (file.filename or "cv.md").strip()
    # Mark the document so the builder identifies it as the structured
    # CV base. Filename prefix is enough — builder matches on it.
    if not fname.startswith("cv-md:"):
        fname = f"cv-md:{fname}"
    # Replace any prior cv-md document so re-uploads don't pile up.
    db.query(_Doc).filter(_Doc.filename.like("cv-md:%")).delete()
    db.add(_Doc(filename=fname, raw_text=text))

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
def rebuild_library_from_all(
    force: bool = False,
    db: Session = Depends(get_db),
) -> CVLibraryOut:
    """Aggregate every uploaded CV + Document into one merged library.

    Respects the manual-edit lock: if `manually_edited_at` is set and
    `force=false`, returns the library unchanged (the user's hand
    edits stay intact). `force=true` ignores the lock AND clears it
    so future auto-rebuilds work normally again.
    """
    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is not None and getattr(row, "manually_edited_at", None) and not force:
        # Locked — refuse to overwrite. UI knows from the lock badge.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Library is hand-locked (manually_edited_at = "
                f"{row.manually_edited_at.isoformat()}). "
                "POST /api/cv/library/rebuild?force=true to override."
            ),
        )
    payload = build_library_from_all(db)
    if row is None:
        row = CVLibrary(id=1)
        db.add(row)
    for k, v in payload.model_dump().items():
        setattr(row, k, v)
    row.updated_at = datetime.utcnow()
    if force:
        row.manually_edited_at = None
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
    """Create or replace the singleton CV library.

    Sets `manually_edited_at` so subsequent auto-rebuilds from
    sources don't overwrite the user's hand edits. To unlock, POST
    /api/cv/library/rebuild?force=true.
    """
    data = payload.model_dump()
    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None:
        row = CVLibrary(id=1)
        db.add(row)
    for k, v in data.items():
        setattr(row, k, v)
    row.updated_at = datetime.utcnow()
    row.manually_edited_at = datetime.utcnow()
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
        polished, _bold_keywords, skip = polish_library_with_llm(
            library_out, job, enhance=bool(getattr(payload, "enhance_tailor", False))
        )
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
