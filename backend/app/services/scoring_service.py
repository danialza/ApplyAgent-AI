"""Sub-score functions and the weighted aggregator.

Each scorer returns a value in [0, 100]. Inputs are kept simple (lists/strings)
so individual scorers can be unit-tested without DB or HTTP fixtures.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.services.embedding_service import EmbeddingService
from app.services.synonyms import canonical, group_key

# Aggregator weights (per spec).
WEIGHTS = {
    "skill": 0.40,
    "semantic": 0.25,
    "experience": 0.20,
    "education": 0.10,
    "project": 0.05,
}


# ============================================================
# Skill score
# ============================================================

def _skill_index(skills: list[str]) -> dict[str, str]:
    """Return {group_key: canonical_display} for a list of skills."""
    out: dict[str, str] = {}
    for s in skills or []:
        if not s:
            continue
        out.setdefault(group_key(s), canonical(s))
    return out


def _fuzzy_match(needle_canonical: str, hay_canonicals: list[str], threshold: float = 0.88) -> bool:
    """Last-resort fuzzy match for skills outside the synonym dictionary."""
    n = needle_canonical.lower()
    for h in hay_canonicals:
        hl = h.lower()
        if n == hl or n in hl or hl in n:
            return True
        if SequenceMatcher(None, n, hl).ratio() >= threshold:
            return True
    return False


def skill_score(
    cv_skills: list[str],
    required: list[str],
    preferred: list[str],
) -> tuple[float, list[str], list[str], list[str]]:
    """Compute skill score with synonym-aware matching.

    Required hits are weighted 75% of the final score, preferred 25%.

    Returns:
        (score 0-100, matched_skills_canonical, missing_required_canonical,
         matched_preferred_canonical)
    """
    cv_idx = _skill_index(cv_skills)
    req_idx = _skill_index(required)
    pref_idx = _skill_index(preferred)
    cv_canonicals = list(cv_idx.values())

    matched_required: list[str] = []
    missing_required: list[str] = []
    for gkey, disp in req_idx.items():
        if gkey in cv_idx or _fuzzy_match(disp, cv_canonicals):
            matched_required.append(disp)
        else:
            missing_required.append(disp)

    matched_preferred: list[str] = []
    for gkey, disp in pref_idx.items():
        if gkey in cv_idx or _fuzzy_match(disp, cv_canonicals):
            matched_preferred.append(disp)

    r_hit = len(matched_required) / len(req_idx) if req_idx else 1.0
    if pref_idx:
        p_hit = len(matched_preferred) / len(pref_idx)
        raw = 0.75 * r_hit + 0.25 * p_hit
    else:
        # No preferred skills listed → don't penalize; use required hit alone.
        raw = r_hit
    score = round(raw * 100, 2)

    matched_all = matched_required + [s for s in matched_preferred if s not in matched_required]
    return score, matched_all, missing_required, matched_preferred


# ============================================================
# Semantic score
# ============================================================
# `EmbeddingService` is a thin abstraction so this stays a one-line swap when
# sentence-transformers replaces the BoW fallback in Phase 3.

def semantic_score(cv_text: str, jd_text: str, embedder: EmbeddingService) -> float:
    """Cosine similarity (0-1) → 0-100 percentage."""
    return round(embedder.similarity(cv_text or "", jd_text or "") * 100, 2)


# ============================================================
# Experience score
# ============================================================

_DURATION_RE = re.compile(r"(\d+)\+?\s*(?:to\s*\d+\s*)?years?", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")  # non-capturing — `findall` returns full year


def _estimate_cv_years(experience_entries: list[str]) -> float:
    """Estimate years of experience from CV experience strings."""
    total = 0.0
    for entry in experience_entries:
        m = _DURATION_RE.search(entry)
        if m:
            total += float(m.group(1))
            continue
        years = [int(y) for y in _YEAR_RE.findall(entry)]
        if len(years) >= 2:
            total += max(0, max(years) - min(years))
    return total


def _required_years(jd_text: str, level: str | None) -> float:
    m = _DURATION_RE.search(jd_text or "")
    if m:
        return float(m.group(1))
    return {
        "internship": 0, "intern": 0,
        "junior": 1,
        "mid": 3, "mid-level": 3, "intermediate": 3,
        "senior": 5,
        "lead": 8, "principal": 8,
    }.get(level or "", 2)


def _domain_overlap(cv_experience_text: str, jd_keywords: list[str]) -> float:
    """Fraction of JD keywords (skills + responsibility tokens) in CV experience text."""
    if not jd_keywords:
        return 0.0
    cv_low = cv_experience_text.lower()
    hits = sum(1 for kw in jd_keywords if kw.lower() in cv_low)
    return hits / len(jd_keywords)


def experience_score(
    cv_experience: list[str],
    jd_text: str,
    jd_level: str | None,
    jd_keywords: list[str] | None = None,
) -> float:
    """Blend years-based score with domain-keyword overlap.

    Years drive 60% of the final value, keyword overlap 40%.
    """
    cv_years = _estimate_cv_years(cv_experience)
    needed = _required_years(jd_text, jd_level)

    if needed <= 0:
        years_component = 100.0 if cv_experience else 50.0
    else:
        years_component = min(cv_years / needed, 1.0) * 100

    cv_text = " ".join(cv_experience or [])
    overlap_component = _domain_overlap(cv_text, jd_keywords or []) * 100

    if not jd_keywords:
        return round(years_component, 2)
    return round(0.6 * years_component + 0.4 * overlap_component, 2)


# ============================================================
# Education score
# ============================================================

_EDU_RANK = {
    "phd": 4, "doctorate": 4, "ph.d": 4,
    "master": 3, "msc": 3, "m.sc": 3, "m.s.": 3, "mba": 3,
    "bachelor": 2, "bsc": 2, "b.sc": 2, "b.s.": 2,
    "diploma": 1, "associate": 1,
}

# Field-of-study keywords recognised by the education scorer.
_EDU_FIELDS = [
    "computer science", "software engineering", "computer engineering",
    "artificial intelligence", "machine learning", "data science",
    "robotics", "mechatronics", "electrical engineering",
    "mechanical engineering", "engineering", "mathematics", "statistics",
    "information technology", "physics",
]


def _max_rank(entries: list[str]) -> int:
    best = 0
    for e in entries:
        low = e.lower()
        for kw, rank in _EDU_RANK.items():
            if kw in low and rank > best:
                best = rank
    return best


def _has_field(entries: list[str]) -> bool:
    text = " ".join(entries).lower()
    return any(field in text for field in _EDU_FIELDS)


def education_score(cv_education: list[str], jd_education: list[str]) -> float:
    """Compare CV education to JD requirements (degree level + field).

    - No JD requirement → reward any education (full marks if relevant field).
    - Otherwise: compare ranks; bonus when CV mentions a recognised field.
    """
    cv_rank = _max_rank(cv_education)
    jd_rank = _max_rank(jd_education)
    cv_field_match = _has_field(cv_education)
    jd_field_match = _has_field(jd_education)

    if jd_rank == 0:
        if cv_rank > 0:
            return 100.0 if cv_field_match else 80.0
        return 50.0

    if cv_rank >= jd_rank:
        base = 100.0
    elif cv_rank == 0:
        base = 0.0
    else:
        base = (cv_rank / jd_rank) * 100

    # Field-match bonus / penalty.
    if jd_field_match and not cv_field_match:
        base *= 0.85
    elif jd_field_match and cv_field_match:
        base = min(100.0, base + 5)

    return round(base, 2)


# ============================================================
# Project score
# ============================================================

def project_score(
    projects: list[str],
    certifications: list[str],
    technologies: list[str],
) -> float:
    """Reward projects/certs that reference the JD's technologies."""
    if not (projects or certifications):
        return 0.0
    if not technologies:
        return 60.0  # Has artefacts but JD lists no tech → soft credit.

    text = " ".join((projects or []) + (certifications or [])).lower()
    hits = sum(1 for t in technologies if t.lower() in text)
    ratio = hits / len(technologies)
    bonus = 0.2 if projects else 0.0  # Projects (vs only certs) score higher.
    return round(min(1.0, ratio + bonus) * 100, 2)


# ============================================================
# Aggregator
# ============================================================

def aggregate(
    skill: float,
    semantic: float,
    experience: float,
    education: float,
    project: float,
) -> float:
    overall = (
        WEIGHTS["skill"] * skill
        + WEIGHTS["semantic"] * semantic
        + WEIGHTS["experience"] * experience
        + WEIGHTS["education"] * education
        + WEIGHTS["project"] * project
    )
    return round(overall, 2)
