"""Decide how many Selected / Additional / Experience entries belong
in a tailored CV.

Three strategies:

  one_page   — junior / early-career caps. Fits a single LaTeX page
               in Danial's template at the default font size.
  two_page   — senior caps. Comfortable across two pages.
  auto       — asks the LLM "given this JD seniority and this many
               library entries, what caps fit?" Falls back to
               ``two_page`` whenever the LLM layer is off or errors.

Explicit non-negative `max_*` values from the request always win — the
planner never overrides a user-specified count. The MVP guard rail is
the page-target itself: even the LLM is constrained to plausible
ranges so a single rogue completion can't blow the CV up to 5 pages.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal

from app.models.schemas import CVLibraryOut, JobParsed

logger = logging.getLogger("ai_job_cv_matcher.planner")

TargetLength = Literal["auto", "one_page", "two_page"]


@dataclass
class SectionPlan:
    """Concrete caps the renderer should apply for each section."""
    max_selected_projects: int
    max_additional_projects: int
    max_experience: int
    source: str            # "user_override" | "one_page" | "two_page" | "llm" | "llm_fallback"
    rationale: str = ""


# Hard-coded defaults per page target. Tuned to Danial's onecolentry
# template at 10pt — adjust here once if the template changes width.
_PRESETS: dict[str, SectionPlan] = {
    "one_page": SectionPlan(
        max_selected_projects=2,
        max_additional_projects=1,
        max_experience=2,
        source="one_page",
        rationale="One-page preset (junior / early-career).",
    ),
    "two_page": SectionPlan(
        max_selected_projects=5,
        max_additional_projects=3,
        max_experience=4,
        source="two_page",
        rationale="Two-page preset (senior / mid).",
    ),
}

# Floors / ceilings the LLM is allowed to choose between. Prevents a
# bad completion from rendering an empty CV or a 4-page wall of text.
_LLM_BOUNDS = {
    "max_selected_projects": (1, 6),
    "max_additional_projects": (0, 4),
    "max_experience": (1, 6),
}


def plan_sections(
    *,
    target_length: TargetLength,
    library: CVLibraryOut,
    job: JobParsed | None,
    user_max_selected: int,
    user_max_additional: int,
    user_max_experience: int,
) -> SectionPlan:
    """Resolve every `max_*` value the renderer needs.

    ``user_max_*`` of -1 means "ask the planner"; anything ≥ 0 is an
    explicit user override and skips the planner for that field.
    """
    # Start from the preset. ``auto`` falls back to ``two_page`` when
    # the LLM is unavailable.
    if target_length == "one_page":
        base = _PRESETS["one_page"]
    elif target_length == "two_page":
        base = _PRESETS["two_page"]
    else:
        base = _llm_plan(library, job) or SectionPlan(
            **{k: v for k, v in vars(_PRESETS["two_page"]).items()
               if k != "rationale" and k != "source"},
            source="llm_fallback",
            rationale="LLM unavailable; using two-page preset.",
        )

    # Clamp against the candidate's actual library so we never claim
    # "show 5 projects" when only 2 exist.
    base.max_selected_projects = min(
        base.max_selected_projects, len(library.selected_projects)
    )
    base.max_additional_projects = min(
        base.max_additional_projects, len(library.additional_projects)
    )
    base.max_experience = min(base.max_experience, len(library.experience))

    # User overrides win field-by-field — leave the others to the plan.
    if user_max_selected >= 0:
        base.max_selected_projects = user_max_selected
        base.source = "user_override"
    if user_max_additional >= 0:
        base.max_additional_projects = user_max_additional
        base.source = "user_override"
    if user_max_experience >= 0:
        base.max_experience = user_max_experience
        base.source = "user_override"

    return base


# ---------- LLM path ----------

def _llm_plan(library: CVLibraryOut, job: JobParsed | None) -> SectionPlan | None:
    """Ask the LLM for section caps. Returns None on any failure so the
    caller can fall back cleanly. Never raises."""
    from app.services import llm_extraction_service as llm

    if not llm.is_enabled():
        return None

    library_summary = {
        "selected_projects_count": len(library.selected_projects),
        "additional_projects_count": len(library.additional_projects),
        "experience_count": len(library.experience),
        "skill_groups_count": len(library.skills_groups),
        "has_publications": bool(library.publications),
    }
    job_summary = _job_summary(job)

    system = (
        "You decide how many entries a tailored CV should keep per section "
        "so it fits a clean two-page maximum and matches the job's seniority. "
        "Reply with one JSON object of the shape:\n"
        '{"max_selected_projects": int, "max_additional_projects": int, '
        '"max_experience": int, "rationale": "one short sentence"}\n'
        "Bounds: selected 1..6, additional 0..4, experience 1..6.\n"
        "Heuristics: junior / first-job → fewer (2/1/2). Mid → 3/2/3. "
        "Senior / staff / principal → 5/3/4. Research / academic JDs that "
        "value publications → trim experience to 3, keep additional at 2."
    )
    user = json.dumps({
        "job": job_summary,
        "library": library_summary,
    }, ensure_ascii=False)

    try:
        raw = llm._chat_completion(  # type: ignore[attr-defined]
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            json_mode=True,
        )
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Section planner LLM call failed: %s", exc)
        return None

    try:
        sel = _clamp(int(data.get("max_selected_projects", 0)),
                     *_LLM_BOUNDS["max_selected_projects"])
        add = _clamp(int(data.get("max_additional_projects", 0)),
                     *_LLM_BOUNDS["max_additional_projects"])
        exp = _clamp(int(data.get("max_experience", 0)),
                     *_LLM_BOUNDS["max_experience"])
    except (TypeError, ValueError) as exc:
        logger.warning("Section planner returned bad ints: %s", exc)
        return None

    rationale = str(data.get("rationale") or "").strip()[:200]
    return SectionPlan(
        max_selected_projects=sel,
        max_additional_projects=add,
        max_experience=exp,
        source="llm",
        rationale=rationale or "LLM-decided caps.",
    )


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _job_summary(job: JobParsed | None) -> dict:
    """Compact view of the JD so the prompt stays cheap."""
    if job is None:
        return {"empty": True}
    return {
        "title": job.job_title,
        "experience_level": job.experience_level,
        "required_skills_count": len(job.required_skills),
        "preferred_skills_count": len(job.preferred_skills),
        "responsibilities_count": len(job.responsibilities),
    }
