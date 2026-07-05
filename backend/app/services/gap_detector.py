"""Detect what the batch autopilot must ASK the user before it can
confidently tailor a CV to a JD.

Given the JD, a compact candidate profile (skills + summary), and the
facts already known (from earlier answers), an LLM returns a short list
of clarifying questions whose answers are (a) genuinely needed and (b)
not derivable from the CV or the known facts. Each question carries a
stable `key` so the same gap is only ever asked once across all JDs.

Degrades to an empty list when the LLM is off or errors — the item then
renders without asking (best-effort, never blocks the batch).
"""
from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field, ValidationError

from app.services import candidate_facts as facts

logger = logging.getLogger("ai_job_cv_matcher.gap_detector")

MAX_QUESTIONS = 4


class _Q(BaseModel):
    key: str
    question: str


class _Out(BaseModel):
    questions: list[_Q] = Field(default_factory=list)


def detect_gaps(
    *,
    jd_text: str,
    candidate_profile: str,
    known_facts: dict[str, str],
) -> list[dict[str, str]]:
    """Return [{"key","question"}] for unanswered, needed gaps."""
    jd_text = (jd_text or "").strip()
    if not jd_text:
        return []

    from app.services import llm_extraction_service as llm
    if not llm.is_enabled():
        return []

    known_block = (
        "\n".join(f"- {k}: {v}" for k, v in known_facts.items())
        if known_facts else "(none yet)"
    )
    system = (
        "You screen a job application. Given a JD, the candidate's "
        "profile, and facts already known about them, list ONLY the "
        "clarifying questions that MUST be answered to decide fit or to "
        "tailor the CV, and that are NOT already answerable from the "
        "profile or the known facts. Focus on hard gates a recruiter "
        "checks: work authorization / visa, years of a SPECIFIC required "
        "technology the profile doesn't evidence, security clearance, "
        "on-site/relocation, or a required skill entirely absent from "
        "the profile. Do NOT ask about things the profile already shows. "
        "Do NOT ask soft/opinion questions. Return AT MOST "
        f"{MAX_QUESTIONS} questions — fewer is better, and an empty list "
        "is the right answer when nothing critical is missing.\n"
        "Return JSON: {\"questions\": [{\"key\": str, \"question\": str}]}. "
        "`key` is a short stable snake_case slug for the gap (e.g. "
        "\"work_auth_uk\", \"years_typescript\", \"clearance\") so the "
        "same gap is recognised across different jobs. `question` is a "
        "direct question to the candidate."
    )
    user = (
        f"JOB DESCRIPTION:\n{jd_text[:5000]}\n\n"
        f"CANDIDATE PROFILE:\n{candidate_profile[:3000]}\n\n"
        f"ALREADY KNOWN FACTS:\n{known_block}\n\n"
        "Return valid JSON only. First char {, last char }."
    )

    try:
        raw = llm._chat_completion(  # type: ignore[attr-defined]
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            json_mode=True,
        )
        data = json.loads(_strip_fences(raw))
        parsed = _Out.model_validate(data)
    except (json.JSONDecodeError, ValidationError, Exception) as exc:  # noqa: BLE001
        logger.warning("gap detector failed: %s", exc)
        return []

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for q in parsed.questions[:MAX_QUESTIONS]:
        key = facts.normalise_key(q.key)
        if not key or not (q.question or "").strip():
            continue
        if key in known_facts or key in seen:
            continue  # already answered / duplicate
        seen.add(key)
        out.append({"key": key, "question": q.question.strip()})
    return out


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        import re
        m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.DOTALL)
        if m:
            return m.group(1)
    return t
