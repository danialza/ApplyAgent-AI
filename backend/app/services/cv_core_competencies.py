"""Generate the Core Competencies grid for a tailored CV.

Career-ops methodology says this row is the recruiter's 3-second scan
zone: 6–8 compound noun phrases that signal "this candidate sits at
the intersection of what we need." Three failure modes the naive
JD ∩ skills extractor hits:

  1. Single tokens only ("Python", "C++") instead of compounds
     ("Policy Optimisation & Reward Shaping").
  2. JD-verbatim only — misses phrases the candidate has clearly
     demonstrated but the JD doesn't spell out ("Sim-to-Real
     Transfer" for an RL role even when the JD says "transfer to
     real hardware").
  3. No abstraction tier mixing — recruiters want 2–3 high-level
     (Reinforcement Learning, Multi-Agent Systems), 3–4 specific
     tools/techniques (MuJoCo Simulation, PPO/SAC), and 1–2
     production signals (Real Hardware Validation, Python & C++).

This module asks the LLM to synthesise the row, then validates every
returned phrase against the candidate's library so we never invent.
Returns ``None`` when the LLM is off or fails — caller falls back to
the heuristic intersection in the renderer.
"""
from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel, Field, ValidationError

from app.models.schemas import CVLibraryOut, JobParsed

logger = logging.getLogger("ai_job_cv_matcher.core_competencies")

# Bounds — guard against pathological LLM output.
MIN_COMPETENCIES = 4
MAX_COMPETENCIES = 10
MAX_PHRASE_LEN = 60


class _LLMOutput(BaseModel):
    competencies: list[str] = Field(min_length=1, max_length=MAX_COMPETENCIES)


def generate_competencies(
    *,
    library: CVLibraryOut,
    job: JobParsed | None,
    want: int = 8,
) -> list[str] | None:
    """Return 6–8 polished Core Competency labels, or None on failure.

    Always validates: only keeps phrases backed by something in the
    candidate's library (skills, project tags, experience tags, project
    titles, summary text). If fewer than MIN_COMPETENCIES survive
    validation, returns None so the caller falls back.
    """
    from app.services import llm_extraction_service as llm

    if not llm.is_enabled():
        return None
    if job is None or not (job.raw_text or "").strip():
        # Without a JD we have nothing to tailor toward; the heuristic
        # path is fine for the unfiltered master CV.
        return None

    want = max(MIN_COMPETENCIES, min(MAX_COMPETENCIES, want))

    cv_evidence = _build_cv_evidence(library)
    jd_summary = _build_jd_summary(job)

    system = (
        "You write the Core Competencies row of a tailored CV using the "
        "career-ops methodology.\n\n"
        "Output a JSON object: {\"competencies\": [\"Phrase One\", "
        "\"Phrase Two\", ...]}\n\n"
        "Hard rules:\n"
        f"1. Return {want} items, ordered most-JD-relevant first.\n"
        "2. Each item is a 1–5 word noun phrase in Title Case. Examples: "
        "\"Reinforcement Learning\", \"Sim-to-Real Transfer\", "
        "\"Policy Optimisation & Reward Shaping\", \"Multi-Agent Systems\", "
        "\"Real Hardware Validation\", \"ROS 1 / ROS 2\", \"Python & C++\".\n"
        "3. Every item MUST be supported by the CV evidence below — pull "
        "words and phrases that actually appear in the candidate's "
        "skills, project tags, bullets, or summary. Never invent.\n"
        "4. Mix specificity: 2–3 high-level (broad fields), 3–4 specific "
        "tools/techniques, 1–2 production signals (languages, "
        "deployment, testing).\n"
        "5. Prefer compound phrases over single tokens. \"MuJoCo "
        "Simulation\" beats \"MuJoCo\". \"Real Hardware Validation\" "
        "beats \"Hardware\".\n"
        "6. Use the JD's vocabulary when the CV evidence backs it. "
        "Don't echo the JD verbatim if the candidate didn't actually do "
        "that thing.\n"
        "7. Don't include soft skills (communication, leadership) here."
    )
    user = json.dumps(
        {"job": jd_summary, "cv_evidence": cv_evidence},
        ensure_ascii=False,
    )

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
        logger.warning("Core competencies LLM call failed: %s", exc)
        return None

    # ---- Validate every phrase against the CV evidence corpus.
    # Tokenise the evidence into a set of lower-cased word stems so
    # multi-word phrases match when their content words show up
    # anywhere in the library (e.g. "Sim-to-Real Transfer" passes when
    # the CV mentions "sim-to-real" or "transfer learning").
    evidence_blob = _evidence_corpus(library)
    grounded: list[str] = []
    seen_keys: set[str] = set()
    for raw_phrase in parsed.competencies:
        phrase = _clean_phrase(raw_phrase)
        if not phrase or len(phrase) > MAX_PHRASE_LEN:
            continue
        key = phrase.lower()
        if key in seen_keys:
            continue
        if not _is_grounded(phrase, evidence_blob):
            logger.info("Dropping ungrounded competency: %r", phrase)
            continue
        grounded.append(phrase)
        seen_keys.add(key)
        if len(grounded) >= want:
            break

    if len(grounded) < MIN_COMPETENCIES:
        logger.warning(
            "Only %d grounded competencies (need ≥ %d); falling back",
            len(grounded), MIN_COMPETENCIES,
        )
        return None
    return grounded


# ---------- Helpers ----------

def _build_cv_evidence(library: CVLibraryOut) -> dict:
    """Compact view of the library the LLM can search through. Keep it
    small enough to keep token costs sane; large enough that compound
    phrases the candidate actually used surface."""
    skills_flat: list[str] = []
    for g in library.skills_groups or []:
        skills_flat.extend(g.items or [])

    project_blob: list[dict] = []
    for p in (library.selected_projects or []) + (library.additional_projects or []):
        project_blob.append({
            "title": p.title,
            "tags": list(p.tags or []),
            "bullets": [b for b in (p.highlights or [])[:3]],
        })

    experience_blob: list[dict] = []
    for x in library.experience or []:
        experience_blob.append({
            "role": x.title,
            "company": x.company,
            "tags": list(x.tags or []),
            "bullets": [b for b in (x.highlights or [])[:2]],
        })

    return {
        "summary": (library.summary or "")[:600],
        "skills": skills_flat,
        "competencies_user_curated": [
            {"name": c.name, "rating": c.rating}
            for c in (getattr(library, "core_competencies", None) or [])
        ],
        "projects": project_blob,
        "experience": experience_blob,
    }


def _build_jd_summary(job: JobParsed) -> dict:
    return {
        "title": job.job_title,
        "experience_level": job.experience_level,
        "required_skills": list(job.required_skills or []),
        "preferred_skills": list(job.preferred_skills or []),
        "technologies": list(job.technologies or []),
        "responsibilities_excerpt": list((job.responsibilities or [])[:6]),
        "raw_excerpt": (job.raw_text or "")[:1500],
    }


_WORD_RE = re.compile(r"[a-z0-9+]+")


# Acronym ↔ full-form expansions so a CV that writes "RL agents"
# still grounds a Core Competency phrased as "Reinforcement Learning".
# Keep the list tight — false positives here let hallucinated phrases
# through. CV→computer vision is intentionally OMITTED because
# "CV" everywhere means résumé.
_ACRONYM_EXPANSIONS: dict[str, str] = {
    "rl": "reinforcement learning",
    "marl": "multi-agent reinforcement learning",
    "mas": "multi-agent systems",
    "ml": "machine learning",
    "dl": "deep learning",
    "nlp": "natural language processing",
    "llm": "large language model llms",
    "rag": "retrieval augmented generation",
    "ros": "robot operating system",
    "k8s": "kubernetes",
    "iac": "infrastructure as code",
    "ci": "continuous integration",
    "cd": "continuous deployment",
    "ppo": "proximal policy optimization optimisation",
    "sac": "soft actor critic",
    "ddpg": "deep deterministic policy gradient",
    "dqn": "deep q network",
    "qa": "quality assurance",
}


def _evidence_corpus(library: CVLibraryOut) -> str:
    """One big lower-cased string of every CV signal we trust as
    evidence. The Core Competencies validator uses substring + token-
    overlap checks against this."""
    parts: list[str] = [library.summary or ""]
    for g in library.skills_groups or []:
        parts.extend(g.items or [])
    for c in (getattr(library, "core_competencies", None) or []):
        parts.append(c.name)
    for p in (library.selected_projects or []) + (library.additional_projects or []):
        parts.append(p.title)
        parts.extend(p.tags or [])
        parts.extend(p.highlights or [])
    for x in library.experience or []:
        parts.append(x.title)
        parts.append(x.company)
        parts.extend(x.tags or [])
        parts.extend(x.highlights or [])
    blob = " || ".join(s.lower() for s in parts if s)
    # Expand acronyms — a CV that writes "RL" gets its corpus
    # enriched with "reinforcement learning" so a Core Competency
    # phrased that way still validates.
    extras: list[str] = []
    for token in set(_WORD_RE.findall(blob)):
        expansion = _ACRONYM_EXPANSIONS.get(token)
        if expansion:
            extras.append(expansion)
    if extras:
        blob = blob + " || " + " ".join(extras)
    return blob


def _clean_phrase(phrase: str) -> str:
    """Trim trailing punctuation and collapse whitespace."""
    p = re.sub(r"\s+", " ", (phrase or "").strip())
    return p.strip(" ,;:.")


def _is_grounded(phrase: str, corpus: str) -> bool:
    """A phrase is grounded when at least one DISTINCTIVE content
    token (len ≥ 3, non-stopword) appears in the corpus, OR the phrase
    appears verbatim. Permissive on purpose — the LLM already sees the
    full library and is instructed not to invent. Validation here is
    the final guard against clearly off-topic phrases like "Quantum
    Computing" sneaking into an RL CV.

    The corpus is already acronym-expanded so "Reinforcement Learning"
    passes against a CV that only writes "RL".
    """
    p_low = phrase.lower()
    if p_low in corpus:
        return True
    tokens = [
        t for t in _WORD_RE.findall(p_low)
        if t not in _STOPWORDS and len(t) >= 3
    ]
    if not tokens:
        # All-acronym phrase like "C++". Fall back to substring check
        # using the original lower-cased form.
        return any(piece in corpus for piece in p_low.split() if piece)
    return any(t in corpus for t in tokens)


# Small stopword list — keep tight so we don't strip technical tokens.
_STOPWORDS: set[str] = {
    "a", "an", "and", "or", "of", "for", "in", "on", "to", "the",
    "with", "by", "at", "is", "as", "&",
}
