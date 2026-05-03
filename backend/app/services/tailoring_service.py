"""CV tailoring suggestions.

Given a JD and the best-matching CV, produce **structured** tailoring
advice the UI can render in distinct panels:

  - `skills_to_add`        — required JD skills missing from the CV.
  - `skills_to_emphasize`  — matched skills that don't appear prominently
                             in the CV's summary / experience prose.
  - `keywords_for_ats`     — canonical JD skills/technologies, ordered.
  - `sections_to_add`      — Summary / Projects / Certifications etc.
                             when the JD signals they matter and the CV
                             lacks them.
  - `bullets_to_rewrite`   — up to 3 CV experience bullets that don't
                             yet mention any matched skill, paired with
                             the target skills they should weave in.
  - `summary_hint`         — a single-sentence template the user can
                             rewrite into their tailored summary.
  - `generic_tips`         — falls through to the matcher's existing
                             rule-based improvement_suggestions.

All output is deterministic. An LLM rewrite step can layer on top later
through the same return type.
"""
from __future__ import annotations

from typing import Any

from app.models.schemas import (
    BulletRewriteSuggestion,
    JobParsed,
    MatchResult,
    TailoringSuggestion,
)


_SECTION_HINTS: dict[str, str] = {
    "summary": "Add a 2-3 line professional summary tailored to this role.",
    "projects": "Add a Projects section featuring work that demonstrates the required skills.",
    "certifications": "Add relevant certifications or coursework matching the JD requirements.",
    "skills": "Add a dedicated Skills section listing the canonical names from the JD.",
}


# ---------- Helpers ----------

def _prose_blob(cv: Any) -> str:
    """Concatenate every text field where a skill mention should land."""
    parts = [
        getattr(cv, "summary", "") or "",
        " ".join(getattr(cv, "experience", []) or []),
        " ".join(getattr(cv, "projects", []) or []),
    ]
    return " ".join(p for p in parts if p).lower()


def _skills_to_emphasize(matched: list[str], cv: Any) -> list[str]:
    """Matched skills the CV holds but doesn't *mention* in its prose.

    Listed in the Skills column but absent from summary/experience/projects
    is exactly the kind of thing a recruiter's keyword scan misses — so we
    nudge the user to weave them into a bullet.
    """
    if not matched:
        return []
    blob = _prose_blob(cv)
    return [s for s in matched if s.lower() not in blob][:6]


def _keywords_for_ats(job: JobParsed) -> list[str]:
    """Canonical, deduped, JD-ordered list of skills + technologies.

    Required first, then preferred, then anything else only seen in
    `technologies` / `responsibilities`. ATS scanners care about presence
    and exact form — this preserves both.
    """
    seen: set[str] = set()
    out: list[str] = []
    for source in (job.required_skills, job.preferred_skills, job.technologies):
        for s in source or []:
            key = (s or "").strip()
            low = key.lower()
            if not key or low in seen:
                continue
            seen.add(low)
            out.append(key)
    return out


def _sections_to_add(cv: Any, job: JobParsed) -> list[str]:
    """Suggestions to add missing sections that this JD cares about."""
    out: list[str] = []
    if not getattr(cv, "summary", ""):
        out.append(_SECTION_HINTS["summary"])
    if not (getattr(cv, "projects", []) or []) and (job.technologies or job.required_skills):
        out.append(_SECTION_HINTS["projects"])
    if not (getattr(cv, "certifications", []) or []) and (job.education_requirements or []):
        out.append(_SECTION_HINTS["certifications"])
    if not (getattr(cv, "skills", []) or []) and (job.required_skills or []):
        out.append(_SECTION_HINTS["skills"])
    return out


def _bullets_to_rewrite(
    cv: Any,
    matched: list[str],
    missing: list[str],
) -> list[BulletRewriteSuggestion]:
    """Pick experience bullets that contain *no* matched skill yet, and
    pair them with target skills (matched first, then missing) the user
    should consider weaving in.
    """
    bullets = list(getattr(cv, "experience", []) or [])
    if not bullets:
        return []
    matched_low = [m.lower() for m in matched]
    missing_top = list(missing)[:3]

    candidates: list[BulletRewriteSuggestion] = []
    for bullet in bullets:
        low = bullet.lower()
        if any(m in low for m in matched_low):
            # Already mentions something matched — leave it alone.
            continue
        # Prefer matched skills the bullet doesn't have; pad with top
        # missing skills so users see what they could plausibly add.
        targets: list[str] = []
        for s in matched + missing_top:
            if s.lower() not in low and s not in targets:
                targets.append(s)
            if len(targets) >= 3:
                break
        if not targets:
            continue
        rationale = (
            "This bullet doesn't reference any of the JD's required skills. "
            "Rewrite it to highlight: " + ", ".join(targets) + "."
        )
        candidates.append(BulletRewriteSuggestion(
            original=bullet,
            target_skills=targets,
            rationale=rationale,
        ))
        if len(candidates) >= 3:
            break
    return candidates


def _summary_hint(job: JobParsed, matched: list[str]) -> str:
    """One-sentence template the user can adapt for their summary."""
    role = job.job_title or "this role"
    skills_phrase = ", ".join(matched[:4]) if matched else "the required skills"
    level = (job.experience_level or "").strip()
    if level in {"senior", "lead", "principal"}:
        prefix = f"{level.title()}-level"
    elif level in {"junior", "internship", "intern"}:
        prefix = "Early-career"
    else:
        prefix = "Experienced"
    return (
        f"{prefix} engineer focused on {skills_phrase}, with a track record of "
        f"shipping production work relevant to {role}."
    )


# ---------- Public API ----------

def build_tailoring_suggestion(
    cv: Any,
    job: JobParsed,
    match: MatchResult,
) -> TailoringSuggestion:
    """Compose the full structured suggestion payload for one (CV, JD) pair."""
    matched = list(match.matched_skills or [])
    missing = list(match.missing_skills or [])

    return TailoringSuggestion(
        skills_to_add=missing,
        skills_to_emphasize=_skills_to_emphasize(matched, cv),
        keywords_for_ats=_keywords_for_ats(job),
        sections_to_add=_sections_to_add(cv, job),
        bullets_to_rewrite=_bullets_to_rewrite(cv, matched, missing),
        summary_hint=_summary_hint(job, matched),
        generic_tips=list(match.improvement_suggestions or []),
    )
