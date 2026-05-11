"""LLM polish layer for the tailored CV renderer.

Mirrors the **career-ops** methodology (santifer/career-ops) — same
keyword extraction, summary rewrite, and bullet reformulation — but
returns structured JSON that plugs into the existing rule-based
template renderer (`cv_renderer.render_cv`). The template itself is
never LLM-generated; only the textual content is.

Hard guarantees:
  * LLM returns JSON only (Pydantic-validated).
  * Number of bullets per section is preserved (no fabrication, no
    silent truncation; if the LLM returns a wrong count we fall back).
  * No new skill names appear in `bold_keywords` that aren't in the
    library's skills or the JD's required/preferred/technologies lists.
  * Any failure (no API key, network, schema violation) returns
    `(None, reason)` and the caller silently uses the rule-based path.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.models.schemas import CVLibraryOut, JobParsed
from app.services import llm_extraction_service as llm

logger = logging.getLogger("ai_job_cv_matcher.cv_polish")


# ---------- LLM response schema ----------

class _LLMProjectRewrite(BaseModel):
    title: str  # used to match against library entries
    highlights: list[str] = Field(default_factory=list)


class _LLMExperienceRewrite(BaseModel):
    title: str
    company: str = ""
    highlights: list[str] = Field(default_factory=list)


class _LLMPolish(BaseModel):
    summary: str = ""
    bold_keywords: list[str] = Field(default_factory=list)
    selected_projects: list[_LLMProjectRewrite] = Field(default_factory=list)
    additional_projects: list[_LLMProjectRewrite] = Field(default_factory=list)
    experience: list[_LLMExperienceRewrite] = Field(default_factory=list)


# ---------- Prompts ----------

_SYSTEM = (
    "You are a senior CV editor following the career-ops methodology "
    "(github.com/santifer/career-ops). Your job: tailor ONE CV to ONE "
    "job description, step by step. "
    "HARD RULES (non-negotiable): "
    "(1) NEVER invent skills, employers, dates, numbers, or projects "
    "the candidate doesn't already list in the library. "
    "(2) ONLY REFORMULATE existing claims using the JD's exact "
    "vocabulary. Reformulation = same meaning, JD wording. "
    "(3) Output STRICT JSON — no markdown fences, no prose, no "
    "comments. First char `{`, last char `}`."
)

_USER_TEMPLATE = """\
TAILOR THIS CV TO THIS JOB. Run the 7-step career-ops pipeline exactly:

  STEP 1 — Extract 15-20 canonical keywords from the JD (skill nouns +
           role terms). Use exact form (e.g. "Reinforcement Learning",
           not "RL paraphrase").
  STEP 2 — Detect archetype: Applied AI Engineer / RL Researcher /
           MLOps / RAG Engineer / NLP Engineer / Robotics / Full-stack
           AI / Backend AI / Data Engineer. Open the Summary with the
           candidate's strongest credential for that archetype.
  STEP 3 — Rewrite the Professional Summary in 3-4 lines, NO first-
           person pronouns. Thread in the top 5 JD keywords by
           paraphrasing existing library claims. End with what the
           candidate ships / builds / improves.
  STEP 4 — For each project in selected_projects + additional_projects:
           KEEP THE SAME NUMBER OF BULLETS as the library (so the
           template renders cleanly). Reorder JD-relevant bullets to
           front. Reword each bullet using JD vocab when an equivalent
           meaning already exists.
  STEP 5 — For each experience entry: reorder + reword bullets the
           same way. Keep bullet count identical to the library.
  STEP 6 — bold_keywords: list canonical skill/role nouns that appear
           in your rewritten text AND in the JD. Only terms grounded
           in the library's skills + the JD's required/preferred/
           technologies. The renderer wraps these in \\textbf{{}}.
  STEP 7 — Self-check before emitting:
             * Per-entry bullet count == library bullet count.
             * No new skill names introduced.
             * Output is a valid JSON object.
             * Title (and company) match library EXACTLY (case + spacing).

OUTPUT SCHEMA (all keys required; empty arrays when nothing applies):

{{
  "summary": "<rewritten Professional Summary, 3-4 lines>",
  "bold_keywords": ["Python", "RAG", "FastAPI", "..."],
  "selected_projects": [
    {{ "title": "<EXACT title>", "highlights": ["<bullet 1>", "<bullet 2>"] }}
  ],
  "additional_projects": [
    {{ "title": "<EXACT title>", "highlights": ["..."] }}
  ],
  "experience": [
    {{
      "title": "<EXACT title>",
      "company": "<EXACT company>",
      "highlights": ["<bullet 1>", "<bullet 2>"]
    }}
  ]
}}

JOB DESCRIPTION:
\"\"\"
{job}
\"\"\"

CANDIDATE LIBRARY (JSON):
{library_json}
"""


# ---------- Validators ----------

_ALLOWED_LEN_RATIO = (0.4, 2.0)  # rewritten bullet must be within 40%-200% of original length


def _bullets_compatible(original: list[str], rewritten: list[str]) -> bool:
    """Reject obviously-bad rewrites that drop or duplicate content."""
    if not original:
        return not rewritten
    if len(rewritten) != len(original):
        return False
    for o, r in zip(original, rewritten):
        if not r or not r.strip():
            return False
        olen = max(1, len(o))
        rlen = len(r)
        if rlen / olen < _ALLOWED_LEN_RATIO[0] or rlen / olen > _ALLOWED_LEN_RATIO[1]:
            return False
    return True


def _scrub_bold_keywords(
    candidate: list[str],
    library: CVLibraryOut,
    job: JobParsed | None,
) -> list[str]:
    """Drop keywords that aren't grounded in the library or the JD."""
    grounded: set[str] = set()
    for g in library.skills_groups:
        for s in g.items:
            grounded.add(s.strip().lower())
    if job is not None:
        for s in (job.required_skills or []):
            grounded.add(s.strip().lower())
        for s in (job.preferred_skills or []):
            grounded.add(s.strip().lower())
        for s in (job.technologies or []):
            grounded.add(s.strip().lower())
    out: list[str] = []
    seen: set[str] = set()
    for k in candidate or []:
        k = (k or "").strip()
        if not k:
            continue
        kl = k.lower()
        if kl in seen:
            continue
        # Accept if any grounded term is substring-equal or word-overlap.
        if any(kl == g or kl in g or g in kl for g in grounded):
            seen.add(kl)
            out.append(k)
    return out


# ---------- Public API ----------

def polish_library_with_llm(
    library: CVLibraryOut,
    job: JobParsed | None,
) -> tuple[CVLibraryOut | None, list[str], str]:
    """Return (polished_library, bold_keywords, error_reason).

    `polished_library` is `None` when polish is skipped or fails — the
    caller should fall back to the rule-based path. `bold_keywords` is
    the scrubbed list of terms the template renderer should bold.
    """
    if not llm.is_enabled():
        return None, [], "LLM disabled (set USE_LLM_EXTRACTION + OPENAI_API_KEY)"
    if job is None:
        return None, [], "No JD provided"

    # Build the prompt body. Library JSON is sent verbatim — small file,
    # fits comfortably in any model's context.
    library_json = library.model_dump_json(exclude={"id", "updated_at"})
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _USER_TEMPLATE.format(
            job=(job.raw_text or "").strip()[:6000],
            library_json=library_json,
        )},
    ]

    # Hit the OpenAI-compatible endpoint via the existing thin wrapper.
    try:
        raw = llm._chat_completion(messages)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        logger.warning("CV polish LLM call failed: %s", exc)
        return None, [], f"LLM call failed: {exc}"

    payload = _coerce_json(raw)
    if payload is None:
        return None, [], "LLM returned non-JSON output"
    try:
        polished = _LLMPolish.model_validate(payload)
    except ValidationError as e:
        logger.warning("CV polish schema invalid: %s", e.errors()[:3])
        return None, [], f"Schema invalid: {e.errors()[0].get('msg', 'unknown')}"

    # Merge polished content back into a COPY of the library. Library is
    # the canonical source of fact — we only ever rewrite bullets, never
    # add or delete entries.
    merged = library.model_copy(deep=True)
    if polished.summary.strip():
        merged.summary = polished.summary.strip()

    _merge_projects(merged.selected_projects, polished.selected_projects, "selected_projects")
    _merge_projects(merged.additional_projects, polished.additional_projects, "additional_projects")
    _merge_experience(merged.experience, polished.experience)

    bold_keywords = _scrub_bold_keywords(polished.bold_keywords, library, job)
    return merged, bold_keywords, ""


# ---------- Merge helpers ----------

def _merge_projects(library_entries: list, llm_entries: list[_LLMProjectRewrite], where: str) -> None:
    by_title = {p.title.strip().lower(): p for p in library_entries}
    for rewrite in llm_entries:
        key = (rewrite.title or "").strip().lower()
        target = by_title.get(key)
        if target is None:
            logger.info("CV polish: project '%s' (in %s) not found in library; skipping",
                         rewrite.title, where)
            continue
        if not _bullets_compatible(target.highlights, rewrite.highlights):
            logger.info("CV polish: project '%s' bullets incompatible (count/length); keeping originals",
                         rewrite.title)
            continue
        target.highlights = list(rewrite.highlights)


def _merge_experience(library_entries: list, llm_entries: list[_LLMExperienceRewrite]) -> None:
    def k(title: str, company: str) -> str:
        return f"{(title or '').strip().lower()}|{(company or '').strip().lower()}"

    by_key = {k(e.title, e.company): e for e in library_entries}
    for rewrite in llm_entries:
        target = by_key.get(k(rewrite.title, rewrite.company))
        if target is None:
            # Fall back to title-only match.
            tlow = (rewrite.title or "").strip().lower()
            target = next(
                (e for e in library_entries if e.title.strip().lower() == tlow),
                None,
            )
        if target is None:
            logger.info("CV polish: experience '%s @ %s' not found; skipping",
                         rewrite.title, rewrite.company)
            continue
        if not _bullets_compatible(target.highlights, rewrite.highlights):
            logger.info("CV polish: experience '%s' bullets incompatible; keeping originals",
                         rewrite.title)
            continue
        target.highlights = list(rewrite.highlights)


# ---------- JSON coercion ----------

def _coerce_json(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    # Direct.
    try:
        return _ensure_dict(json.loads(text))
    except json.JSONDecodeError:
        pass
    # Strip ```json fences.
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        try:
            return _ensure_dict(json.loads(fence.group(1)))
        except json.JSONDecodeError:
            return None
    return None


def _ensure_dict(obj: Any) -> dict[str, Any] | None:
    return obj if isinstance(obj, dict) else None
