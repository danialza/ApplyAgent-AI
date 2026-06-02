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


class _LLMSkillGroupRewrite(BaseModel):
    label: str
    items: list[str] = Field(default_factory=list)


class _LLMPolish(BaseModel):
    summary: str = ""
    bold_keywords: list[str] = Field(default_factory=list)
    selected_projects: list[_LLMProjectRewrite] = Field(default_factory=list)
    additional_projects: list[_LLMProjectRewrite] = Field(default_factory=list)
    experience: list[_LLMExperienceRewrite] = Field(default_factory=list)
    # Enhance-mode only: extra skill groups the LLM proposes to merge
    # into the rendered Skills section. Ignored in strict mode.
    extra_skills: list[_LLMSkillGroupRewrite] = Field(default_factory=list)


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
    "comments. First char `{`, last char `}`. "
    "(4) NO RECYCLED FLUFF. Banned across multiple bullets: "
    "'production-grade', 'secure, scalable', 'intelligent insights', "
    "'enterprise-ready', 'at scale', 'end-to-end ownership', "
    "'observability and monitoring', 'platform engineering'. Use a "
    "phrase like this AT MOST ONCE in the entire CV — only when the "
    "JD explicitly demands it. Otherwise stick to concrete, specific "
    "language. "
    "(5) Every rewritten bullet MUST anchor on a concrete signal from "
    "the library: a named tool (FastAPI, MuJoCo, Qdrant), a measurable "
    "outcome, a named system, or a domain-specific verb (\"trained\", "
    "\"deployed\", \"benchmarked\", \"shipped\"). A bullet that only "
    "says \"built a production-grade system using best practices\" "
    "violates this rule. "
    "(6) Bullets that don't relate to the JD should be REWRITTEN "
    "MINIMALLY — closer to the original library text — not rewritten "
    "with generic AI-engineer vocabulary. "
    "(7) WRITE NATURAL PROSE. Avoid hyphen-chain modifiers that sound "
    "mechanical: 'CLIP-style encoders' → 'CLIP encoders' or "
    "'encoders like CLIP'; 'PaliGemma-inspired VLM extraction' → "
    "'VLM extraction using PaliGemma'; 'RAG-style pipelines' → "
    "'RAG pipelines'. Hyphenated compounds are fine when they're the "
    "actual term ('sim-to-real', 'real-hardware', 'multi-GPU'), but "
    "don't invent '-style', '-inspired', '-aware', '-friendly', "
    "'-oriented' modifiers to sound technical. Recruiters skim — "
    "natural sentences read faster than dash-glued chains."
)

_USER_TEMPLATE = """\
TAILOR THIS CV TO THIS JOB. Run the 7-step career-ops pipeline exactly:

  STEP 1 — Extract 15-20 canonical keywords from the JD (skill nouns +
           role terms). Use exact form (e.g. "Reinforcement Learning",
           not "RL paraphrase").
  STEP 2 — Detect archetype from JD keyword density. Common archetypes:
           Robot Learning Engineer (RL + manipulation + sim-to-real +
           VLA/diffusion + MuJoCo/Isaac)
           Applied AI Engineer (LLM apps + RAG + agents + backend)
           ML Research Engineer (publications + neural architectures +
           PyTorch + distributed training)
           MLOps Engineer (deployment + monitoring + pipelines + scale)
           Robotics Software Engineer (ROS + perception + real-hardware
           + control)
           Backend AI Engineer (FastAPI + LLM APIs + vector DBs)
           Data Engineer (pipelines + ETL + warehouses)
           NLP Engineer (transformers + tokenisation + LLM training)
           Full-stack AI (frontend + backend + ML)

           HARD: Open the Summary line with the EXACT archetype noun
           phrase from the JD's vocabulary. If JD title is "Senior
           Robot Learning Engineer", Summary MUST start with "Robot
           learning engineer" / "Senior robot learning engineer", NOT
           "Applied AI engineer" or "Software engineer". Misidentifying
           the archetype is the #1 reason recruiters skip a CV.
  STEP 3 — Rewrite the Professional Summary in 3-4 lines, NO first-
           person pronouns. After the archetype opener, thread in the
           top 5 JD keywords by paraphrasing existing library claims.
           End with what the candidate ships / builds / improves.
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


# ---------- Enhance-mode prompts ----------
#
# Enhance mode loosens the "never invent" rule. The LLM may:
#   * Add JD-relevant skills the library doesn't already list, when
#     they're plausibly inferable from the candidate's projects.
#   * Expand project descriptions with additional bullets / detail
#     drawn from JD vocabulary, anchored on the project's actual stack.
#   * Lightly rewrite Professional Experience bullets to weave in JD
#     terminology even when the original wording isn't a direct match.
#
# Trade-off: more JD coverage, more fabrication risk. User must opt
# in via the request flag — default stays OFF.
_ENHANCE_SYSTEM = (
    "You are a senior CV editor in ENHANCE mode. Your job: aggressively "
    "tailor ONE CV to ONE job description, maximising keyword coverage "
    "and JD-fit. "
    "ENHANCE RULES: "
    "(1) You MAY add skills, tools, frameworks, and techniques that the "
    "candidate's projects plausibly imply — even if not explicitly listed "
    "in the library — provided they are reasonable inferences from the "
    "project's stack or domain. Do NOT invent employers, dates, degrees, "
    "or company names. "
    "(2) You MAY expand project bullets with additional detail anchored "
    "on the project's actual technology stack. You MAY also CHANGE the "
    "bullet count per project (add up to 2 new bullets, or trim weak "
    "ones) to better fit the JD. "
    "(3) You MAY lightly rewrite Professional Experience bullets to "
    "weave in JD vocabulary, even when the original library wording "
    "isn't a direct match — but the role's core function must stay "
    "truthful. Keep bullet count flexible (±1 bullet allowed). "
    "(4) You MAY propose `extra_skills` — new skill groups (or items to "
    "append to existing groups) the rendered CV should display. "
    "(5) Output STRICT JSON — no markdown fences, no prose, no comments. "
    "First char `{`, last char `}`. "
    "(6) NO RECYCLED FLUFF. Banned across multiple bullets: "
    "'production-grade', 'secure, scalable', 'intelligent insights', "
    "'enterprise-ready', 'at scale'. Concrete language always wins. "
    "(7) Every bullet must anchor on a concrete signal: a named tool, "
    "a measurable outcome, a named system, or a domain-specific verb. "
    "(8) WRITE NATURAL PROSE. Avoid hyphen-chain modifiers ('CLIP-style', "
    "'PaliGemma-inspired') — use natural phrasing."
)

_ENHANCE_USER_TEMPLATE = """\
TAILOR THIS CV AGGRESSIVELY TO THIS JOB. You are in ENHANCE mode — you
may add plausible skills, expand projects, and tweak experience bullets
to maximise JD-fit. Run this pipeline:

  STEP 1 — Extract 20-25 canonical keywords from the JD (skills + tools
           + role terms + domain nouns). Use exact JD form.
  STEP 2 — Detect archetype from JD keyword density. Open the rewritten
           Summary with the EXACT archetype noun phrase from the JD.
  STEP 3 — Rewrite the Professional Summary in 3-5 lines, no first-
           person pronouns. Thread in top 7-8 JD keywords. End with
           what the candidate ships / builds / improves.
  STEP 4 — For each project: reorder JD-relevant bullets to front,
           reword using JD vocab, AND add up to 2 new bullets that
           anchor on the project's actual stack but bring in JD
           terminology the original bullets missed. You may also trim
           weak bullets. Bullet count is FLEXIBLE per project.
  STEP 5 — For each experience entry: reorder + lightly rewrite bullets
           to weave in JD vocab. Bullet count flexible (±1).
  STEP 6 — extra_skills: propose skill groups to ADD to the rendered
           Skills section. Either new groups (label + items) or items
           to append to a group label that already exists in the
           library. Only skills that are plausibly grounded in the
           candidate's projects or domain.
  STEP 7 — bold_keywords: canonical skill/role nouns that appear in
           your rewritten text AND in the JD. The renderer wraps these
           in \\textbf{{}}.
  STEP 8 — Self-check:
             * No new employers/companies/degrees/dates introduced.
             * Output is a valid JSON object.
             * Title (and company) match library EXACTLY (case + spacing).

OUTPUT SCHEMA (all keys required; empty arrays when nothing applies):

{{
  "summary": "<rewritten Professional Summary, 3-5 lines>",
  "bold_keywords": ["Python", "RAG", "FastAPI", "..."],
  "selected_projects": [
    {{ "title": "<EXACT title>", "highlights": ["<bullet 1>", "<bullet 2>", "<bullet 3>"] }}
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
  ],
  "extra_skills": [
    {{ "label": "Languages", "items": ["Rust", "Go"] }},
    {{ "label": "MLOps", "items": ["MLflow", "Kubeflow"] }}
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
    enhance: bool = False,
) -> list[str]:
    """Drop keywords that aren't grounded in the library or the JD.

    Enhance mode: JD-only grounding is accepted (the LLM is allowed to
    surface JD skills the library didn't originally list).
    """
    grounded: set[str] = set()
    for g in library.skills_groups:
        for s in g.items:
            grounded.add(s.strip().lower())
    jd_grounded: set[str] = set()
    if job is not None:
        for s in (job.required_skills or []):
            jd_grounded.add(s.strip().lower())
        for s in (job.preferred_skills or []):
            jd_grounded.add(s.strip().lower())
        for s in (job.technologies or []):
            jd_grounded.add(s.strip().lower())
    if enhance:
        grounded |= jd_grounded
    else:
        grounded |= jd_grounded
    out: list[str] = []
    seen: set[str] = set()
    for k in candidate or []:
        k = (k or "").strip()
        if not k:
            continue
        kl = k.lower()
        if kl in seen:
            continue
        if any(kl == g or kl in g or g in kl for g in grounded):
            seen.add(kl)
            out.append(k)
    return out


# ---------- Public API ----------

def polish_library_with_llm(
    library: CVLibraryOut,
    job: JobParsed | None,
    enhance: bool = False,
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
    sys_prompt = _ENHANCE_SYSTEM if enhance else _SYSTEM
    user_tpl = _ENHANCE_USER_TEMPLATE if enhance else _USER_TEMPLATE
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_tpl.format(
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

    _merge_projects(merged.selected_projects, polished.selected_projects, "selected_projects", enhance=enhance)
    _merge_projects(merged.additional_projects, polished.additional_projects, "additional_projects", enhance=enhance)
    _merge_experience(merged.experience, polished.experience, enhance=enhance)

    if enhance and polished.extra_skills:
        _merge_extra_skills(merged.skills_groups, polished.extra_skills)

    bold_keywords = _scrub_bold_keywords(polished.bold_keywords, library, job, enhance=enhance)
    return merged, bold_keywords, ""


def _merge_extra_skills(library_groups: list, extras: list[_LLMSkillGroupRewrite]) -> None:
    """Append items to existing labelled groups, or add new groups.

    De-dupes case-insensitively within each group.
    """
    by_label = {(g.label or "").strip().lower(): g for g in library_groups}
    for extra in extras:
        label = (extra.label or "").strip()
        if not label:
            continue
        items = [i.strip() for i in extra.items if i and i.strip()]
        if not items:
            continue
        target = by_label.get(label.lower())
        if target is None:
            # New group — append to library_groups in place.
            from app.models.schemas import SkillGroup  # local import: avoid cycle
            ng = SkillGroup(label=label, items=items)
            library_groups.append(ng)
            by_label[label.lower()] = ng
            continue
        existing_lower = {i.strip().lower() for i in target.items}
        for it in items:
            if it.lower() not in existing_lower:
                target.items.append(it)
                existing_lower.add(it.lower())


# ---------- Merge helpers ----------

def _merge_projects(library_entries: list, llm_entries: list[_LLMProjectRewrite], where: str, enhance: bool = False) -> None:
    by_title = {p.title.strip().lower(): p for p in library_entries}
    for rewrite in llm_entries:
        key = (rewrite.title or "").strip().lower()
        target = by_title.get(key)
        if target is None:
            logger.info("CV polish: project '%s' (in %s) not found in library; skipping",
                         rewrite.title, where)
            continue
        rewritten = [h.strip() for h in rewrite.highlights if h and h.strip()]
        if not rewritten:
            continue
        if enhance:
            # Enhance mode — accept any non-empty bullet list, only
            # guarding against runaway counts (cap at original + 2).
            cap = max(1, len(target.highlights)) + 2
            target.highlights = rewritten[:cap]
            continue
        if not _bullets_compatible(target.highlights, rewrite.highlights):
            logger.info("CV polish: project '%s' bullets incompatible (count/length); keeping originals",
                         rewrite.title)
            continue
        target.highlights = list(rewrite.highlights)


def _merge_experience(library_entries: list, llm_entries: list[_LLMExperienceRewrite], enhance: bool = False) -> None:
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
        rewritten = [h.strip() for h in rewrite.highlights if h and h.strip()]
        if not rewritten:
            continue
        if enhance:
            cap = max(1, len(target.highlights)) + 1
            target.highlights = rewritten[:cap]
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
