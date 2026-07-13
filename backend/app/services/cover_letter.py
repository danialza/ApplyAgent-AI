"""Cover-letter generator — plain text, grounded in the master library.

Called after a tailored render: given the JD (and optionally the very
CV that was just rendered), produce a short, specific cover letter the
user can paste into an application form or email. Text only — no file.

Grounding rules mirror the polish layer: never invent employers, tools,
metrics, or experience not present in the library; reference the actual
company/role from the JD; keep it tight (recruiters skim ~250 words).
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger("ai_job_cv_matcher.cover_letter")

_LENGTH_WORDS = {"short": 160, "standard": 250, "long": 380}


def generate_cover_letter(
    *,
    library,
    job_text: str,
    tone: str = "professional",
    length: str = "standard",
    extra_notes: str = "",
) -> tuple[str, str]:
    """Return (cover_letter_text, error). Empty text on failure."""
    from app.services import llm_extraction_service as llm

    if not llm.is_enabled():
        return "", "LLM is not configured."
    job_text = (job_text or "").strip()
    if not job_text:
        return "", "No job description provided."

    words = _LENGTH_WORDS.get(length, 250)
    lib_json = library.model_dump_json(
        include={"header", "summary", "skills_groups", "selected_projects",
                 "additional_projects", "experience", "education"},
    )

    system = (
        "You write cover letters that get replies. Rules:\n"
        "1. GROUNDED: every claim must come from the candidate library — "
        "never invent employers, tools, metrics, or experience.\n"
        "2. SPECIFIC: name the company and role from the JD; connect 2-3 "
        "of the candidate's most relevant, concrete achievements to the "
        "JD's actual needs. No generic filler ('I am a passionate…').\n"
        "3. STRUCTURE: hook (why this company/role, one sentence) → "
        "proof (2-3 achievement sentences mapped to their needs) → "
        "close (availability, call to action). No address block, no "
        "date — this is pasted into a form or email.\n"
        f"4. LENGTH: about {words} words. Tone: {tone}.\n"
        "5. Output PLAIN TEXT only — no markdown, no headers, no "
        "placeholders like [Company]. Start with 'Dear' and end after "
        "the sign-off with the candidate's name."
    )
    user = json.dumps({
        "job_description": job_text[:6000],
        "candidate_library": json.loads(lib_json),
        "extra_notes_from_candidate": (extra_notes or "").strip()[:1000],
    }, ensure_ascii=False)

    try:
        text = llm.chat_text([
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ])
    except Exception as exc:  # noqa: BLE001
        logger.warning("cover letter generation failed: %s", exc)
        return "", f"LLM call failed: {exc}"

    text = (text or "").strip()
    # Strip accidental markdown fences / headers.
    if text.startswith("```"):
        text = text.strip("`").strip()
    if not text:
        return "", "LLM returned empty output."
    return text, ""
