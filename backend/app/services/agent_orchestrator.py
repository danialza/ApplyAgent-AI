"""End-to-end pipeline orchestrator.

Chains the existing services in order:

    Profile → Tags/Queries → Discovery → Ranking → CV Match → Tailoring

Design choices:
  * Each step records an `AgentStep("ok"/"skipped"/"error", detail)` so
    the UI can render a progress trace and surface partial results when
    something downstream fails.
  * State flows forward — the JD parsed during ranking is reused during
    tailoring, and the best CV per job is captured once and reused too.
    No duplicate `extract_job` / `match_cv_to_job` calls.
  * Pool selection mirrors `/api/jobs/rank` and `/api/tailor`.
  * If no profile exists yet but at least one CV does, the orchestrator
    auto-builds the profile so a fresh user can run `agent.run` after a
    single upload.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session

from app.models.db_models import CV, UserProfile
from app.models.schemas import (
    AgentRunResponse,
    AgentStep,
    DiscoveredJob,
    JobParsed,
    MatchResult,
    PortfolioLinks,
    QueryTagSet,
    RankJobInput,
    RankedJobResult,
    TailorResponse,
    TailoringSuggestion,
    UserProfileOut,
)
from app.services.extraction import extract_job
from app.services.job_discovery import discover_jobs
from app.services.matching_engine import match_cv_to_job
from app.services.profile_service import (
    build_profile_payload,
    upsert_user_profile,
)
from app.services.query_builder import build_query_payload
from app.services.tailoring_service import build_tailoring_suggestion

logger = logging.getLogger("ai_job_cv_matcher.agent")


# ---------- Profile → synthetic CV ----------

def _profile_to_synthetic_cv(profile: UserProfile) -> SimpleNamespace:
    """Same helper as `tailor_routes` / `job_routes` — kept local so
    this module has no router-level dependencies."""
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


# ---------- Internal state ----------

@dataclass
class _RankedItem:
    """In-memory record for one ranked job — keeps everything we need
    for tailoring without re-parsing."""
    job_input: RankJobInput
    job: JobParsed
    best_cv: Any  # CV row or SimpleNamespace
    match: MatchResult


@dataclass
class _OrchestratorResult:
    steps: list[AgentStep] = field(default_factory=list)
    profile: UserProfile | None = None
    queries: list[str] = field(default_factory=list)
    tags: dict[str, Any] = field(default_factory=dict)
    discovered: list[DiscoveredJob] = field(default_factory=list)
    ranked: list[_RankedItem] = field(default_factory=list)
    tailored: list[TailorResponse] = field(default_factory=list)
    used_profile_fallback: bool = False
    fatal_error: str = ""


# ---------- Pool resolution ----------

def _resolve_cv_pool(
    db: Session,
    *,
    cv_ids: list[int] | None,
    use_profile_fallback: bool,
    profile: UserProfile | None,
) -> tuple[list[Any], bool, str]:
    """Return (pool, used_profile_fallback, error). Empty pool + non-empty
    error means the caller should bail with that message."""
    if cv_ids:
        cvs = db.query(CV).filter(CV.id.in_(cv_ids)).all()
        missing = set(cv_ids) - {cv.id for cv in cvs}
        if missing:
            return [], False, f"CV id(s) not found: {sorted(missing)}"
        return list(cvs), False, ""

    cvs = db.query(CV).all()
    if cvs:
        return list(cvs), False, ""

    if use_profile_fallback and profile is not None:
        return [_profile_to_synthetic_cv(profile)], True, ""

    return [], False, "No CVs uploaded and no unified profile to fall back on."


# ---------- Orchestrator ----------

def run_agent(
    db: Session,
    *,
    sources: list[str] | None = None,
    max_discover: int = 30,
    max_rank: int = 15,
    max_tailor: int = 5,
    cv_ids: list[int] | None = None,
    use_profile_fallback: bool = True,
    queries_override: list[str] | None = None,
    tags_override: dict[str, Any] | None = None,
) -> _OrchestratorResult:
    """Run the full Profile → Tailoring pipeline. Never raises.

    A fatal error short-circuits the rest of the pipeline but still
    returns a populated `_OrchestratorResult` with the steps recorded
    so far — the caller can decide how to surface partial progress.
    """
    state = _OrchestratorResult()

    # ===== 1. Profile =====
    profile = db.query(UserProfile).first()
    if profile is None:
        cv_count = db.query(CV).count()
        if cv_count > 0:
            try:
                payload = build_profile_payload(db)
                profile = upsert_user_profile(db, payload)
                state.steps.append(AgentStep(
                    name="profile", status="ok",
                    detail=f"auto-built from {cv_count} CV(s)",
                ))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Agent: auto profile-build failed: %s", exc)
                state.steps.append(AgentStep(
                    name="profile", status="error",
                    detail=f"auto-build failed: {exc}",
                ))
                state.fatal_error = "Could not build a profile from existing CVs."
                return state
        else:
            state.steps.append(AgentStep(
                name="profile", status="error",
                detail="no profile and no CVs to build one from",
            ))
            state.fatal_error = (
                "No profile and no CVs uploaded. Upload a CV or call "
                "POST /api/profile/build first."
            )
            return state
    else:
        state.steps.append(AgentStep(name="profile", status="ok", detail="existing profile"))
    state.profile = profile

    # ===== 2. Queries / tags =====
    # When the caller supplies `queries_override` and/or `tags_override`,
    # they take precedence over the auto-derived values. Each missing
    # half is filled in from the profile so the dashboard can edit one
    # without having to re-supply the other.
    derived: dict[str, Any] = {}
    if queries_override is None or tags_override is None:
        try:
            derived = build_query_payload(profile)
        except Exception as exc:  # noqa: BLE001
            state.steps.append(AgentStep(
                name="queries", status="error", detail=str(exc),
            ))
            state.fatal_error = "Could not derive queries from profile."
            return state

    if queries_override is not None:
        state.queries = [q for q in queries_override if q]
    else:
        state.queries = list(derived.get("queries") or [])

    if tags_override is not None:
        state.tags = tags_override
    else:
        state.tags = derived.get("tags") or {}

    overrides_used = [
        name for name, used in (("queries", queries_override is not None),
                                  ("tags", tags_override is not None)) if used
    ]
    detail = f"{len(state.queries)} queries, {len(state.tags.get('roles') or [])} roles"
    if overrides_used:
        detail += f" (override: {', '.join(overrides_used)})"
    state.steps.append(AgentStep(name="queries", status="ok", detail=detail))

    # ===== 3. Discovery =====
    try:
        result = discover_jobs(
            queries=state.queries,
            tags=state.tags,
            sources=sources,
            max_per_source=max_discover,
            max_total=max_discover,
        )
    except Exception as exc:  # noqa: BLE001
        state.steps.append(AgentStep(name="discovery", status="error", detail=str(exc)))
        return state

    discovered_models: list[DiscoveredJob] = []
    for j in result.results:
        d = j.to_dict() if hasattr(j, "to_dict") else j
        discovered_models.append(DiscoveredJob(**d))
    state.discovered = discovered_models

    skipped_note = (
        f" (skipped: {', '.join(result.skipped_sources)})"
        if result.skipped_sources else ""
    )
    if not state.discovered:
        state.steps.append(AgentStep(
            name="discovery", status="skipped",
            detail=f"no jobs returned{skipped_note}",
        ))
        return state
    state.steps.append(AgentStep(
        name="discovery", status="ok",
        detail=f"{len(state.discovered)} jobs{skipped_note}",
    ))

    # ===== 4. Resolve CV pool =====
    pool, used_profile_fallback, pool_error = _resolve_cv_pool(
        db, cv_ids=cv_ids, use_profile_fallback=use_profile_fallback, profile=profile,
    )
    state.used_profile_fallback = used_profile_fallback
    if not pool:
        state.steps.append(AgentStep(
            name="ranking", status="error",
            detail=pool_error or "empty CV pool",
        ))
        state.fatal_error = pool_error or "Empty CV pool."
        return state

    # ===== 5. Ranking =====
    ranked_items: list[_RankedItem] = []
    rank_errors = 0
    for d_job in state.discovered[:max_rank]:
        # Build a JD blob from title + snippet (snippet alone is sometimes
        # too short for the parser to find structured fields).
        job_text = (d_job.title + "\n\n" + d_job.snippet).strip() or d_job.title
        if not job_text.strip():
            rank_errors += 1
            continue
        try:
            parsed = extract_job(job_text)
            job = JobParsed(**parsed.to_dict())
            scored = [(cv, match_cv_to_job(cv, job)) for cv in pool]
            scored.sort(key=lambda pair: (-pair[1].overall_score, -pair[1].skill_score, pair[1].cv_id))
            best_cv, best_match = scored[0]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Agent: ranking failed for '%s': %s", d_job.title[:60], exc)
            rank_errors += 1
            continue
        ranked_items.append(_RankedItem(
            job_input=RankJobInput(
                job_text=job_text,
                title=d_job.title,
                company=d_job.company,
                location=d_job.location,
                url=d_job.url,
                source=d_job.source,
            ),
            job=job,
            best_cv=best_cv,
            match=best_match,
        ))

    ranked_items.sort(key=lambda r: (-r.match.overall_score, -r.match.skill_score))
    state.ranked = ranked_items

    detail = f"{len(ranked_items)} ranked"
    if rank_errors:
        detail += f", {rank_errors} skipped"
    state.steps.append(AgentStep(name="ranking", status="ok", detail=detail))

    # ===== 6. Tailoring (top N) =====
    if not ranked_items:
        state.steps.append(AgentStep(
            name="tailoring", status="skipped", detail="no ranked jobs to tailor",
        ))
        return state

    for item in ranked_items[:max_tailor]:
        suggestions = build_tailoring_suggestion(item.best_cv, item.job, item.match)
        best_cv_id = item.match.cv_id if item.match.cv_id is not None and item.match.cv_id >= 0 else None
        state.tailored.append(TailorResponse(
            best_cv_id=best_cv_id,
            best_cv_name=item.match.cv_name or "",
            best_cv_filename=item.match.filename or "",
            job=item.job,
            match=item.match,
            suggestions=suggestions,
            used_profile_fallback=used_profile_fallback,
        ))
    state.steps.append(AgentStep(
        name="tailoring", status="ok",
        detail=f"{len(state.tailored)} tailoring bundles",
    ))

    return state


def to_response(state: _OrchestratorResult) -> AgentRunResponse:
    """Convert internal state to the wire response. The internal record
    holds rich Python objects; the wire response is the serialisable view."""
    return AgentRunResponse(
        steps=state.steps,
        profile=UserProfileOut.model_validate(state.profile) if state.profile is not None else None,
        queries=state.queries,
        tags=QueryTagSet(**state.tags) if state.tags else QueryTagSet(),
        discovered=state.discovered,
        ranked=[
            RankedJobResult(
                job=item.job_input,
                best_cv_id=item.match.cv_id if item.match.cv_id is not None and item.match.cv_id >= 0 else None,
                best_cv_name=item.match.cv_name or "",
                best_cv_filename=item.match.filename or "",
                overall_score=item.match.overall_score,
                skill_score=item.match.skill_score,
                semantic_score=item.match.semantic_score,
                experience_score=item.match.experience_score,
                education_score=item.match.education_score,
                project_score=item.match.project_score,
                matched_skills=item.match.matched_skills,
                missing_skills=item.match.missing_skills,
                strongest_points=item.match.strongest_points,
                explanation=item.match.explanation,
            )
            for item in state.ranked
        ],
        tailored=state.tailored,
        used_profile_fallback=state.used_profile_fallback,
        error=state.fatal_error,
    )
