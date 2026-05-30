"""LLM-driven Technical Skills tailoring per JD.

Master library skills_groups are domain-generic — every JD gets the
same wall. Recruiters scan the top 1-2 groups and move on. For a
finance/data JD the candidate wants "SQL, Data Pipelines, Python"
at the top; for a robotics JD "MuJoCo, ROS, Sim-to-Real". Same
master, different output.

This service runs ONE LLM call per (JD, skills_groups) → ordered
groups with reordered items. JD-relevant items rise to the front.
Items absent from the master are NEVER added (no invention).

Cached by sha1(jd + flat_skills) so re-renders are free.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.models.schemas import SkillGroup

logger = logging.getLogger("ai_job_cv_matcher.skill_tailor")

_CACHE: dict[str, list[SkillGroup]] = {}


class _LLMGroup(BaseModel):
    label: str
    items: list[str] = Field(default_factory=list)


class _LLMOutput(BaseModel):
    groups: list[_LLMGroup] = Field(default_factory=list)


def tailor_skills(jd_text: str, groups: list[SkillGroup]) -> list[SkillGroup] | None:
    """Return JD-tailored ordered groups, or None on failure."""
    if not (jd_text or "").strip() or not groups:
        return None
    from app.services import llm_extraction_service as llm
    if not llm.is_enabled():
        return None

    # Cache key.
    flat = sorted(f"{g.label}:{i}" for g in groups for i in (g.items or []))
    key = hashlib.sha1((jd_text[:3000] + "||" + "|".join(flat)).encode("utf-8")).hexdigest()
    if key in _CACHE:
        return _CACHE[key]

    master_view = [
        {"label": g.label, "items": list(g.items or [])}
        for g in groups
    ]

    system = (
        "Tailor a CV's Technical Skills section to a specific job.\n\n"
        "Input: candidate's master skills_groups + job description.\n"
        "Output JSON: "
        '{"groups": [{"label": "...", "items": ["...", "..."]}]}\n\n'
        "Rules:\n"
        "1. ONLY use items present in the master input. NEVER invent.\n"
        "2. Reorder GROUPS so the most JD-relevant comes first. A "
        "finance / data JD → Languages + Data & Storage + Frameworks "
        "at top. A robotics JD → Robotics + AI/ML at top.\n"
        "3. Within each group, reorder ITEMS so JD-matched terms "
        "appear first (e.g. JD mentions SQL → list SQL first in "
        "Languages).\n"
        "4. DROP a whole group when none of its items are even "
        "tangentially relevant. Drop individual items that are "
        "off-topic noise (Robotic 3D Design on a data-engineering "
        "JD).\n"
        "5. Aim for 4-7 final groups, 4-10 items per group. Recruiters "
        "skim — fewer high-signal items > many generic ones.\n"
        "6. Don't merge groups with different concerns. If master has "
        "'AI / ML & Data Science' and 'LLM & Agentic Systems' as "
        "separate groups, keep them separate when JD touches both.\n"
        "7. Group labels: keep master labels verbatim when reusing. "
        "Rename only if the JD's vocabulary is wildly different "
        "(rare).\n"
        "8. Generic groups (Domain Skills, Soft Skills) stay last "
        "unless JD explicitly values them (communication-heavy roles)."
    )
    user_payload = {
        "jd_excerpt": (jd_text or "")[:3000],
        "master_skills_groups": master_view,
    }
    user = json.dumps(user_payload, ensure_ascii=False)

    try:
        raw = llm._chat_completion(  # type: ignore[attr-defined]
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            json_mode=True,
        )
        data = json.loads(raw)
        parsed = _LLMOutput.model_validate(data)
    except (json.JSONDecodeError, ValidationError, Exception) as exc:  # noqa: BLE001
        logger.warning("Skill tailor LLM failed: %s", exc)
        return None

    # Validate: every output item must exist in master (case-insensitive).
    master_items = {i.lower(): i for g in groups for i in (g.items or [])}
    out: list[SkillGroup] = []
    seen: set[str] = set()
    for g in parsed.groups:
        clean_items: list[str] = []
        for it in g.items:
            low = (it or "").strip().lower()
            if not low or low in seen:
                continue
            if low not in master_items:
                logger.info("Skill tailor dropped invented item: %r", it)
                continue
            seen.add(low)
            # Preserve master's canonical casing.
            clean_items.append(master_items[low])
        if clean_items:
            out.append(SkillGroup(label=(g.label or "").strip(), items=clean_items))

    if not out:
        return None

    _CACHE[key] = out
    if logger.isEnabledFor(logging.INFO):
        logger.info(
            "Skill tailor: master %d groups / %d items → tailored %d groups / %d items",
            len(groups), sum(len(g.items or []) for g in groups),
            len(out), sum(len(g.items) for g in out),
        )
    return out
