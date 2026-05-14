"""Iteratively raise tailored-CV keyword coverage toward a target.

The renderer can only bold and surface what's in the library. When the
JD demands a keyword the CV doesn't mention anywhere, coverage drops.
Career-ops manual fix is to weave one short clause into the closest
project/experience bullet using the candidate's own evidence — never
inventing.

This service automates that step:

  1. Look at the list of missing keywords after a render.
  2. For each, ask the LLM "which existing bullet is the closest
     match, and how would you append a SHORT clause that adds this
     keyword while staying grounded in the candidate's other library
     entries?"
  3. Apply the rewrites in-place to a deep-copied library so the
     caller can re-render.
  4. Cap iterations and missing-list size so we never blow LaTeX up.

Falls back to a no-op (returns the unchanged library) when LLM is off
or the request fails.
"""
from __future__ import annotations

import json
import logging
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.models.schemas import CVLibraryOut, JobParsed

logger = logging.getLogger("ai_job_cv_matcher.coverage_booster")

# At most one LLM round per call. The route can loop if a single round
# isn't enough — we keep this single-shot so each call is bounded.
MAX_BULLETS_TOUCHED_PER_CALL = 6
MAX_KEYWORDS_PER_CALL = 8


class _BulletEdit(BaseModel):
    """One rewrite the LLM proposes."""
    section: str = Field(pattern=r"^(selected_projects|additional_projects|experience)$")
    index: int = Field(ge=0)
    bullet_index: int = Field(ge=0)
    new_text: str
    keyword: str


class _LLMOutput(BaseModel):
    edits: list[_BulletEdit] = Field(default_factory=list)


def boost_coverage(
    *,
    library: CVLibraryOut,
    job: JobParsed,
    missing_keywords: list[str],
) -> tuple[CVLibraryOut, list[str]]:
    """Return a deep-copied library with bullets nudged to cover more
    of `missing_keywords`. Also returns a per-edit log so the caller
    can surface it in the API response for debugging.

    Never raises — failures degrade to no-op.
    """
    from app.services import llm_extraction_service as llm

    log: list[str] = []
    if not missing_keywords:
        return library, log
    if not llm.is_enabled():
        return library, ["coverage_boost_skipped: LLM disabled"]

    keywords = missing_keywords[:MAX_KEYWORDS_PER_CALL]

    bullets_snapshot = _bullet_snapshot(library)
    if not bullets_snapshot:
        return library, ["coverage_boost_skipped: library has no bullets to edit"]

    system = (
        "You raise a tailored CV's keyword coverage by weaving missing "
        "JD keywords into existing bullets. Strict rules:\n"
        "1. For each missing keyword you can confidently place, return "
        "ONE edit naming the bullet to rewrite and the new text.\n"
        "2. The new text MUST be a polished rewrite of the existing "
        "bullet — keep its core claim, just add one short clause that "
        "names the keyword in the candidate's voice. Never fabricate "
        "tools, employers, dates, metrics, or experience the rest of "
        "the library doesn't already support.\n"
        "3. Skip any keyword where no bullet plausibly fits — don't "
        "force it. Better to leave coverage at 70% than invent.\n"
        f"4. Touch at most {MAX_BULLETS_TOUCHED_PER_CALL} bullets in total.\n"
        "5. Keep each rewritten bullet under 200 characters and one line.\n"
        "6. Reply with one JSON object:\n"
        "   {\"edits\": [{\"section\": \"selected_projects|"
        "additional_projects|experience\", \"index\": int, "
        "\"bullet_index\": int, \"new_text\": str, \"keyword\": str}, ...]}"
    )

    user = json.dumps({
        "job_title": job.job_title,
        "missing_keywords": keywords,
        "library_evidence": {
            "summary": (library.summary or "")[:600],
            "skills": _flat_skills(library),
            "bullets": bullets_snapshot,
        },
    }, ensure_ascii=False)

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
        logger.warning("Coverage booster LLM failed: %s", exc)
        return library, [f"coverage_boost_failed: {exc}"]

    if not parsed.edits:
        return library, ["coverage_boost: LLM proposed no edits"]

    # Apply edits to a deep-copied library so we never mutate the
    # caller's reference. Out-of-range or empty edits get logged + skipped.
    new_lib = library.model_copy(deep=True)
    applied = 0
    for edit in parsed.edits[:MAX_BULLETS_TOUCHED_PER_CALL]:
        target = _resolve_bullet_target(new_lib, edit.section, edit.index, edit.bullet_index)
        if target is None:
            log.append(
                f"edit_skipped: {edit.section}[{edit.index}].bullet[{edit.bullet_index}] out of range"
            )
            continue
        section_list, idx, bidx = target
        clean = (edit.new_text or "").strip()
        if not clean:
            log.append(f"edit_skipped: {edit.section}[{edit.index}] empty new_text")
            continue
        if len(clean) > 400:  # safety belt
            clean = clean[:400].rstrip() + "…"
        old = section_list[idx].highlights[bidx]
        section_list[idx].highlights[bidx] = clean
        applied += 1
        log.append(
            f"edit_applied[{edit.keyword}]: {edit.section}[{edit.index}].bullet[{edit.bullet_index}]"
        )
        logger.info(
            "Coverage booster rewrote %s[%d].bullet[%d] for keyword %r: %r → %r",
            edit.section, edit.index, edit.bullet_index, edit.keyword, old, clean,
        )

    if applied == 0:
        log.append("coverage_boost: all proposed edits were out of range")
    return new_lib, log


# ---------- helpers ----------

def _flat_skills(library: CVLibraryOut) -> list[str]:
    out: list[str] = []
    for g in library.skills_groups or []:
        out.extend(g.items or [])
    return out


def _bullet_snapshot(library: CVLibraryOut) -> list[dict]:
    """Compact view: every editable bullet keyed by section/index so
    the LLM can return a stable pointer back to the one it wants to
    rewrite."""
    out: list[dict] = []
    for i, p in enumerate(library.selected_projects or []):
        for b, text in enumerate(p.highlights or []):
            out.append({
                "section": "selected_projects",
                "index": i,
                "bullet_index": b,
                "title": p.title,
                "text": text,
            })
    for i, p in enumerate(library.additional_projects or []):
        for b, text in enumerate(p.highlights or []):
            out.append({
                "section": "additional_projects",
                "index": i,
                "bullet_index": b,
                "title": p.title,
                "text": text,
            })
    for i, x in enumerate(library.experience or []):
        for b, text in enumerate(x.highlights or []):
            out.append({
                "section": "experience",
                "index": i,
                "bullet_index": b,
                "title": f"{x.title} @ {x.company}",
                "text": text,
            })
    return out


def _resolve_bullet_target(
    library: CVLibraryOut, section: str, idx: int, bidx: int,
) -> tuple[list[Any], int, int] | None:
    if section == "selected_projects":
        lst = library.selected_projects
    elif section == "additional_projects":
        lst = library.additional_projects
    elif section == "experience":
        lst = library.experience
    else:
        return None
    if idx < 0 or idx >= len(lst):
        return None
    entry = lst[idx]
    bullets = entry.highlights or []
    if bidx < 0 or bidx >= len(bullets):
        return None
    return lst, idx, bidx
