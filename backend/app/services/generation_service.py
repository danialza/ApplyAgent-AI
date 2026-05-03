"""Deterministic generators for application artefacts.

Three outputs shipped in one call:

    * `cv_suggestions`    — prose version of the structured tailoring panel.
    * `cover_letter`      — short, calibrated to seniority + matched skills.
    * `linkedin_message`  — 3-sentence outreach blurb.

Everything is template-driven and deterministic. An optional LLM polish
step is available when `USE_LLM_EXTRACTION=true` and `OPENAI_API_KEY` is
set — same gating + fallback rules as the extraction layer
(`llm_extraction_service`). LLM failures fall back to the rule-based
output silently and log a warning; callers always get a usable string.

Privacy: only the CV + JD that the caller already has access to are sent
to the LLM. The user's API key never leaves the backend.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.models.schemas import JobParsed, MatchResult, TailoringSuggestion

logger = logging.getLogger("ai_job_cv_matcher.generation")


# ---------- Helpers ----------

def _join_human(items: list[str]) -> str:
    """Oxford-comma join — ('A', 'B', 'C') → 'A, B, and C'."""
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def _years_total(experience_entries: list[str]) -> int:
    """Best-effort years-of-experience estimate (mirrors scoring_service)."""
    total = 0
    for entry in experience_entries or []:
        years = sorted({int(y) for y in _YEAR_RE.findall(entry or "")})
        if len(years) >= 2:
            total += years[-1] - years[0]
    return total


def _level_phrase(experience_level: str, years: int) -> str:
    """Translate JD experience_level / years into a natural opener phrase."""
    level = (experience_level or "").lower()
    if level in {"senior", "principal", "lead"} or years >= 5:
        return f"With {years}+ years of professional experience"
    if level in {"mid-level", "mid", "intermediate"} or years >= 2:
        return f"With {years} years of hands-on experience"
    if level in {"junior", "internship", "intern"}:
        return "Early in my career but already shipping production work"
    return "I bring relevant hands-on experience"


def _greeting(company: str) -> str:
    if company.strip():
        return f"Dear hiring team at {company.strip()},"
    return "Dear hiring team,"


def _signature(cv_name: str) -> str:
    return cv_name.strip() if cv_name and cv_name.strip() else "Your candidate"


def _strongest_evidence(strongest: list[str], n: int = 1) -> list[str]:
    return [s.strip().rstrip(".") for s in (strongest or [])[:n] if s and s.strip()]


# ---------- Generators ----------

def generate_cv_suggestions(
    suggestion: TailoringSuggestion,
    job: JobParsed,
    cv_name: str,
) -> str:
    """Prose summary of the structured tailoring panel — one short paragraph
    per actionable category.
    """
    role = job.job_title or "this role"
    parts: list[str] = []

    if suggestion.skills_to_add:
        parts.append(
            f"Add the following missing skills if you have them: "
            f"{_join_human(suggestion.skills_to_add[:6])}."
        )
    if suggestion.skills_to_emphasize:
        parts.append(
            f"Emphasize {_join_human(suggestion.skills_to_emphasize[:4])} in your "
            f"summary or experience bullets — your skills section already lists "
            f"them, but recruiter keyword scans miss anything not mentioned in prose."
        )
    if suggestion.sections_to_add:
        parts.append("Sections to add: " + " ".join(suggestion.sections_to_add))
    if suggestion.bullets_to_rewrite:
        first = suggestion.bullets_to_rewrite[0]
        parts.append(
            f"Rewrite the bullet \"{first.original}\" to weave in "
            f"{_join_human(first.target_skills[:3])}."
        )
    if suggestion.summary_hint:
        parts.append(f"Summary template: \"{suggestion.summary_hint}\"")
    if suggestion.keywords_for_ats:
        parts.append(
            "ATS keywords (cover both Skills and a bullet): "
            + ", ".join(suggestion.keywords_for_ats[:8])
            + ("…" if len(suggestion.keywords_for_ats) > 8 else "")
        )

    if not parts:
        return (
            f"{cv_name or 'Your CV'} already aligns well with {role}. "
            "Quantify impact in your existing bullets to reinforce the match."
        )
    return "\n\n".join(parts)


def generate_cover_letter(
    job: JobParsed,
    match: MatchResult,
    cv_experience: list[str],
    cv_name: str,
) -> str:
    """Short, calibrated, deterministic cover letter."""
    role = job.job_title or "this role"
    company = job.company or ""
    years = _years_total(cv_experience)
    matched = list(match.matched_skills or [])
    primary_skills = matched[:3]
    evidence = _strongest_evidence(match.strongest_points, n=1)

    opener = _level_phrase(job.experience_level, years)
    greeting = _greeting(company)

    intro = f"I'm writing to apply for the {role} role"
    intro += f" at {company}." if company else "."

    if primary_skills:
        skill_phrase = (
            f"{opener} across {_join_human(primary_skills)}, "
            "I'm a strong match for what you've described."
        )
    else:
        skill_phrase = f"{opener}, I'm well-positioned for the responsibilities you've outlined."

    bullets: list[str] = []
    for skill in matched[:3]:
        bullets.append(
            f"- {skill}: documented in my CV and applied directly in my recent work."
        )
    if evidence:
        bullets.append(f"- Recent highlight: {evidence[0]}.")

    closing_skill = primary_skills[0] if primary_skills else ""
    closing = (
        f"I'd welcome the chance to discuss how my experience"
        + (f" with {closing_skill}" if closing_skill else "")
        + f" maps to the {role} role."
    )

    body = "\n".join([
        greeting,
        "",
        intro + " " + skill_phrase,
        "",
        "Specifically, what I bring that maps to your requirements:",
        *bullets,
        "",
        closing,
        "",
        "Best regards,",
        _signature(cv_name),
    ])
    return body


def generate_linkedin_message(
    job: JobParsed,
    match: MatchResult,
    cv_experience: list[str],
    cv_name: str,
) -> str:
    """3-sentence outreach message — short by design."""
    role = job.job_title or "the open role"
    company = job.company or ""
    years = _years_total(cv_experience)
    matched = list(match.matched_skills or [])
    primary = matched[0] if matched else ""

    opener = "Hi — I came across the {role}{at}".format(
        role=role,
        at=f" opening at {company}" if company else " opening",
    )
    bridge_bits: list[str] = []
    if years:
        bridge_bits.append(f"{years}+ years")
    if primary:
        bridge_bits.append(f"of {primary}")
    bridge = (
        " and " + " ".join(bridge_bits) + " makes me think it could be a strong fit"
        if bridge_bits else ""
    )
    sentence_1 = opener + bridge + "."

    if len(matched) >= 2:
        sentence_2 = (
            f"My background covers {_join_human(matched[:3])} — happy to share concrete examples."
        )
    elif matched:
        sentence_2 = f"I've worked extensively with {primary} and would love to share examples."
    else:
        sentence_2 = "Happy to share my CV and concrete examples of recent work."

    sentence_3 = "Would you be open to a 15-minute chat next week?"
    sig = _signature(cv_name)
    return "\n".join([sentence_1, sentence_2, sentence_3, "", f"Thanks, {sig}"])


# ---------- Optional LLM polish ----------

@dataclass
class GenerationContext:
    """Bundle of inputs an LLM polish prompt needs."""
    job: JobParsed
    match: MatchResult
    cv_name: str
    cv_summary: str
    cv_experience: list[str]


_LLM_SYSTEM = (
    "You are a careful career coach. Rewrite the user's draft into a more "
    "natural, recruiter-friendly version. Keep every concrete claim; do NOT "
    "invent skills, employers, or achievements. Keep length similar to the "
    "draft. Output plain text only — no markdown, no headers, no JSON."
)

_LLM_USER_TEMPLATE = """\
Polish this draft. Use the structured context to keep claims grounded.
Do not add new technical skills the candidate didn't list.

KIND: {kind}

CONTEXT
- Candidate: {name}
- Role: {role}
- Company: {company}
- Matched skills: {matched_skills}
- Recent highlight: {highlight}

DRAFT
\"\"\"
{draft}
\"\"\"
"""


def _polish_via_llm(kind: str, draft: str, ctx: GenerationContext) -> str | None:
    """Optional LLM polish. Returns None when the LLM layer is disabled or
    fails; callers fall back to the deterministic draft.
    """
    from app.services import llm_extraction_service as llm

    if not llm.is_enabled():
        return None

    try:
        highlight = (ctx.match.strongest_points or [""])[0]
        messages = [
            {"role": "system", "content": _LLM_SYSTEM},
            {
                "role": "user",
                "content": _LLM_USER_TEMPLATE.format(
                    kind=kind,
                    name=ctx.cv_name or "the candidate",
                    role=ctx.job.job_title or "the role",
                    company=ctx.job.company or "the company",
                    matched_skills=", ".join((ctx.match.matched_skills or [])[:5]) or "none",
                    highlight=highlight or "n/a",
                    draft=draft,
                ),
            },
        ]
        # Reuse the LLM client; response_format JSON isn't useful here, so we
        # call the same chat-completion seam but expect plain text in `content`.
        text = llm._chat_completion(messages)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM polish failed for %s: %s", kind, exc)
        return None

    cleaned = (text or "").strip()
    if not cleaned:
        return None
    # Strip stray markdown fences if the model added them despite the prompt.
    cleaned = re.sub(r"^```\w*\s*|\s*```$", "", cleaned, flags=re.MULTILINE).strip()
    return cleaned or None


# ---------- Public entry point ----------

@dataclass
class GenerationBundle:
    cv_suggestions: str = ""
    cover_letter: str = ""
    linkedin_message: str = ""
    used_llm: bool = False


def generate_artefacts(
    *,
    kinds: list[str],
    job: JobParsed,
    match: MatchResult,
    suggestions: TailoringSuggestion,
    cv_name: str,
    cv_experience: list[str],
    polish_with_llm: bool = True,
) -> GenerationBundle:
    """Produce the requested set of artefacts.

    `kinds` is a subset of {"cv_suggestions", "cover_letter", "linkedin_message"}.
    Unknown kinds are ignored. `polish_with_llm` is best-effort — set it to
    `False` to force the deterministic path.
    """
    bundle = GenerationBundle()
    ctx = GenerationContext(
        job=job, match=match, cv_name=cv_name,
        cv_summary="", cv_experience=cv_experience,
    )

    if "cv_suggestions" in kinds:
        bundle.cv_suggestions = generate_cv_suggestions(suggestions, job, cv_name)

    if "cover_letter" in kinds:
        draft = generate_cover_letter(job, match, cv_experience, cv_name)
        if polish_with_llm:
            polished = _polish_via_llm("cover_letter", draft, ctx)
            if polished:
                draft = polished
                bundle.used_llm = True
        bundle.cover_letter = draft

    if "linkedin_message" in kinds:
        draft = generate_linkedin_message(job, match, cv_experience, cv_name)
        if polish_with_llm:
            polished = _polish_via_llm("linkedin_message", draft, ctx)
            if polished:
                draft = polished
                bundle.used_llm = True
        bundle.linkedin_message = draft

    return bundle
