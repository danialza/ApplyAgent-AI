"""Profile aggregator — combines CVs + Documents into a unified user profile.

The aggregation never modifies the source CV / Document rows; it produces
a `UserProfile` row (single-row table for the MVP) that re-runs the
parser over each source so the profile reflects the *current* parser
behaviour.

Design choices:

  - **Skills** are canonicalised through `synonyms.canonical()` so
    `JS`/`JavaScript` collapse into one entry. Each entry carries a
    `weight`, a `count` (number of distinct sources mentioning it), the
    `sources` themselves (`cv:<id>` / `doc:<id>`), and an `in_projects`
    flag set if the skill appears in any project entry.
  - **Recency boost** comes from the most-recent end year mentioned in
    work-experience strings. Roles ending in the current year score 1.0;
    each year older drops the contribution by ~10%.
  - **Domains** are inferred from the canonical skill set (AI/ML,
    Web/E-commerce, Robotics, Backend, DevOps, Data).
  - **Tools and technologies** are the subset of skills that appear in
    the project's technical skill dictionary (i.e. excludes things like
    soft skills / fictional bullets).
  - Lists (education, projects, certifications, languages, portfolio
    links) are deduplicated case-insensitively while preserving order.

This module is pure logic — no FastAPI, no HTTP. The router calls
`build_user_profile(db)` and the service returns a `UserProfileOut`-ready
dict that the route persists into the DB.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.db_models import CV, Document, UserProfile
from app.services.cv_parser import ParsedCV, parse_cv_text
from app.services.synonyms import canonical
from app.utils.skill_dictionary import all_technical_skills, find_technical_skills

logger = logging.getLogger("ai_job_cv_matcher.profile")

# Weighting knobs. Kept here so they're easy to tune from one place.
WEIGHT_PER_SOURCE = 1.0
WEIGHT_PROJECT_BOOST = 0.5
WEIGHT_RECENCY_FACTOR = 0.3
RECENCY_HALFLIFE_YEARS = 7.0
MAX_SUMMARY_LEN = 1500

_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_PRESENT_RE = re.compile(r"\b(present|current|now|today)\b", re.IGNORECASE)


# ---------- Source extraction ----------

def _parse_source(raw_text: str) -> ParsedCV:
    """Run the rule-based CV parser over any source text. Documents and CVs
    share the same parser — even unstructured docs surface useful skills /
    project / education mentions."""
    return parse_cv_text(raw_text or "")


def _gather_sources(db: Session) -> list[tuple[str, ParsedCV, str]]:
    """Return [(source_id, parsed, raw_text)] for every CV and Document."""
    sources: list[tuple[str, ParsedCV, str]] = []
    for cv in db.query(CV).order_by(CV.created_at.asc()).all():
        sources.append((f"cv:{cv.id}", _parse_source(cv.raw_text), cv.raw_text or ""))
    for doc in db.query(Document).order_by(Document.created_at.asc()).all():
        sources.append((f"doc:{doc.id}", _parse_source(doc.raw_text), doc.raw_text or ""))
    return sources


# ---------- Recency ----------

def _years_in(text: str) -> tuple[int | None, int | None]:
    """Pull (start_year, end_year) from a free-form experience string.

    `Present` / `Current` are treated as the current year. Returns
    `(None, None)` if no usable years are detected.
    """
    if not text:
        return None, None
    years = [int(y) for y in _YEAR_RE.findall(text)]
    has_present = bool(_PRESENT_RE.search(text))
    if not years and not has_present:
        return None, None
    current = datetime.utcnow().year
    if has_present:
        end = current
        start = min(years) if years else None
    else:
        start = min(years)
        end = max(years)
    return start, end


def _recency_score(end_year: int | None) -> float:
    """0..1, decaying with how long ago the role ended.

    Current year → 1.0. `RECENCY_HALFLIFE_YEARS` gap → ~0.5.
    """
    if end_year is None:
        return 0.0
    gap = max(0, datetime.utcnow().year - end_year)
    return round(0.5 ** (gap / RECENCY_HALFLIFE_YEARS), 4)


# ---------- Skills ----------

_TECH_CANONICAL: set[str] | None = None


def _technical_skill_canonicals() -> set[str]:
    """Lazy-built set of canonical skill names from the dictionary."""
    global _TECH_CANONICAL
    if _TECH_CANONICAL is None:
        _TECH_CANONICAL = {canonical(name) for name in all_technical_skills()}
    return _TECH_CANONICAL


def _aggregate_skills(
    sources: list[tuple[str, ParsedCV, str]],
    experience_recency: dict[str, float],
) -> list[dict[str, Any]]:
    """Collect canonical skills with weights, sources, and project flags."""
    table: dict[str, dict[str, Any]] = {}

    for source_id, parsed, raw_text in sources:
        # 1) Structured `skills` field — primary signal.
        for skill in parsed.skills:
            key = canonical(skill)
            if not key:
                continue
            entry = table.setdefault(
                key,
                {"name": key, "count": 0, "sources": [], "in_projects": False, "recency": 0.0},
            )
            if source_id not in entry["sources"]:
                entry["sources"].append(source_id)
                entry["count"] += 1

        # 2) Free-text scan via the technical skill dictionary — picks up
        #    skills mentioned in prose (e.g. portfolio docs without a Skills
        #    section, or experience bullets that mention a tool inline).
        for tech_skill in find_technical_skills(raw_text):
            key = canonical(tech_skill)
            if not key:
                continue
            entry = table.setdefault(
                key,
                {"name": key, "count": 0, "sources": [], "in_projects": False, "recency": 0.0},
            )
            if source_id not in entry["sources"]:
                entry["sources"].append(source_id)
                entry["count"] += 1

        # Project boost — skill mentioned in the body of a project line.
        if parsed.projects:
            joined_low = " ".join(parsed.projects).lower()
            for skill_key in list(table.keys()):
                if skill_key.lower() in joined_low:
                    table[skill_key]["in_projects"] = True

        # Recency boost — pull max recency from this source's experience.
        for entry in parsed.experience or []:
            _, end = _years_in(entry)
            r = _recency_score(end)
            if r <= 0:
                continue
            for skill_key, t in table.items():
                if skill_key.lower() in entry.lower():
                    t["recency"] = max(t["recency"], r)

    # Compose final weights.
    out: list[dict[str, Any]] = []
    for entry in table.values():
        weight = (
            WEIGHT_PER_SOURCE * entry["count"]
            + (WEIGHT_PROJECT_BOOST if entry["in_projects"] else 0.0)
            + WEIGHT_RECENCY_FACTOR * entry["recency"]
        )
        out.append({
            "name": entry["name"],
            "weight": round(weight, 3),
            "count": entry["count"],
            "sources": entry["sources"],
            "in_projects": entry["in_projects"],
        })
    out.sort(key=lambda s: (-s["weight"], -s["count"], s["name"]))
    return out


# ---------- Experience / education / projects / certs / languages ----------

def _dedupe_lines(values: list[str], lower_key_len: int = 80) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values or []:
        s = (v or "").strip()
        if not s:
            continue
        key = s.lower()[:lower_key_len]
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _aggregate_experience(sources: list[tuple[str, ParsedCV, str]]) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Dedup + add (start, end, recency) metadata. Also returns
    `experience_recency` for skill weighting upstream."""
    bucket: dict[str, dict[str, Any]] = {}
    recency_per_text: dict[str, float] = {}

    for source_id, parsed, _raw in sources:
        for entry in parsed.experience or []:
            text = entry.strip()
            if not text:
                continue
            key = text.lower()[:120]
            start, end = _years_in(text)
            r = _recency_score(end)
            recency_per_text[text] = max(recency_per_text.get(text, 0.0), r)
            existing = bucket.get(key)
            if existing:
                if source_id not in existing["sources"]:
                    existing["sources"].append(source_id)
                # Prefer richer (longer) text; keep best year info.
                if len(text) > len(existing["text"]):
                    existing["text"] = text
                if end and (not existing["end_year"] or end > existing["end_year"]):
                    existing["end_year"] = end
                if start and (not existing["start_year"] or start < existing["start_year"]):
                    existing["start_year"] = start
                existing["recency_score"] = max(existing["recency_score"], r)
            else:
                bucket[key] = {
                    "text": text,
                    "start_year": start,
                    "end_year": end,
                    "recency_score": r,
                    "sources": [source_id],
                }

    out = sorted(
        bucket.values(),
        key=lambda x: (-(x["recency_score"] or 0), -(x["end_year"] or 0)),
    )
    return out, recency_per_text


def _aggregate_languages(sources: list[tuple[str, ParsedCV, str]]) -> list[str]:
    """Languages are short — use first-token comparison so
    'English (Native)' and 'English (Fluent)' collapse."""
    seen: set[str] = set()
    out: list[str] = []
    for _src, parsed, _raw in sources:
        for lang in parsed.languages or []:
            head = re.split(r"[\s\(\[\-/]", lang.strip(), maxsplit=1)[0].lower()
            if head and head not in seen:
                seen.add(head)
                out.append(lang.strip())
    return out


# ---------- Portfolio links ----------

def _aggregate_portfolio(sources: list[tuple[str, ParsedCV, str]]) -> dict[str, Any]:
    """Take the first non-empty value for each link kind across CVs (docs
    rarely contain contact lines)."""
    links: dict[str, Any] = {"linkedin": "", "github": "", "portfolio": "", "websites": []}
    websites_seen: set[str] = set()
    for _src, parsed, _raw in sources:
        if parsed.linkedin and not links["linkedin"]:
            links["linkedin"] = parsed.linkedin
        if parsed.github and not links["github"]:
            links["github"] = parsed.github
        if parsed.portfolio:
            if not links["portfolio"]:
                links["portfolio"] = parsed.portfolio
            elif parsed.portfolio not in websites_seen and parsed.portfolio != links["portfolio"]:
                websites_seen.add(parsed.portfolio)
                links["websites"].append(parsed.portfolio)
    return links


# ---------- Domains ----------

# Each domain is satisfied if at least one trigger skill appears
# (canonical names so synonyms collapse beforehand).
_DOMAIN_TRIGGERS: list[tuple[str, set[str]]] = [
    ("AI/ML", {"Python", "Machine Learning", "Deep Learning", "Natural Language Processing",
               "Large Language Models", "Retrieval Augmented Generation", "PyTorch",
               "TensorFlow", "Hugging Face", "scikit-learn", "Computer Vision"}),
    ("Web / E-commerce", {"WordPress", "WooCommerce", "PHP", "JavaScript", "Shopify",
                          "Magento", "SEO", "Google Analytics"}),
    ("Robotics", {"ROS", "ROS2", "PID", "Gazebo", "MATLAB", "Simulink", "Robotics",
                  "Control Systems", "SLAM"}),
    ("Backend", {"FastAPI", "Node.js", "REST API", "GraphQL", "PostgreSQL", "SQL"}),
    ("DevOps / Cloud", {"Docker", "Kubernetes", "AWS", "Azure", "GCP", "MLOps"}),
    ("Frontend", {"React", "Next.js", "TypeScript", "JavaScript", "Tailwind CSS"}),
]


def _infer_domains(skill_entries: list[dict[str, Any]]) -> list[str]:
    skill_set = {s["name"] for s in skill_entries}
    return [name for name, triggers in _DOMAIN_TRIGGERS if skill_set & triggers]


# ---------- Summary ----------

def _pick_summary(sources: list[tuple[str, ParsedCV, str]]) -> str:
    """Choose the longest non-empty summary across CVs (docs rarely have one)."""
    best = ""
    for _src, parsed, _raw in sources:
        if parsed.summary and len(parsed.summary) > len(best):
            best = parsed.summary
    return best[:MAX_SUMMARY_LEN]


def _pick_name(sources: list[tuple[str, ParsedCV, str]]) -> str:
    for _src, parsed, _raw in sources:
        if parsed.name:
            return parsed.name
    return ""


# ---------- Public API ----------

def build_profile_payload(db: Session) -> dict[str, Any]:
    """Run the full aggregation and return a dict ready to upsert into
    the `user_profiles` table."""
    sources = _gather_sources(db)
    if not sources:
        logger.info("Profile build: no CVs or Documents to aggregate.")

    work_experience, exp_recency = _aggregate_experience(sources)
    skills = _aggregate_skills(sources, exp_recency)
    domains = _infer_domains(skills)
    tech_canonicals = _technical_skill_canonicals()
    tools_and_technologies = [s for s in skills if s["name"] in tech_canonicals]

    # Education / projects / certifications: simple deduped list across sources.
    education: list[str] = []
    projects: list[str] = []
    certifications: list[str] = []
    for _src, parsed, _raw in sources:
        education.extend(parsed.education or [])
        projects.extend(parsed.projects or [])
        certifications.extend(parsed.certifications or [])

    payload = {
        "name": _pick_name(sources),
        "summary": _pick_summary(sources),
        "skills": skills,
        "tools_and_technologies": tools_and_technologies,
        "work_experience": work_experience,
        "education": _dedupe_lines(education),
        "projects": _dedupe_lines(projects, lower_key_len=120),
        "certifications": _dedupe_lines(certifications),
        "domains": domains,
        "languages": _aggregate_languages(sources),
        "portfolio_links": _aggregate_portfolio(sources),
        "source_cv_ids": [int(sid.split(":")[1]) for sid, _p, _r in sources if sid.startswith("cv:")],
        "source_document_ids": [int(sid.split(":")[1]) for sid, _p, _r in sources if sid.startswith("doc:")],
    }
    return payload


def upsert_user_profile(db: Session, payload: dict[str, Any]) -> UserProfile:
    """Single-row upsert: replace the existing profile or insert the first one."""
    existing = db.query(UserProfile).first()
    if existing is None:
        existing = UserProfile()
        db.add(existing)
    for key, value in payload.items():
        setattr(existing, key, value)
    existing.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(existing)
    return existing


def delete_user_profile(db: Session) -> bool:
    """Delete the unified profile (if any) AND every Document. CVs are
    intentionally preserved — `Do not remove individual CVs.`"""
    profile = db.query(UserProfile).first()
    deleted = False
    if profile is not None:
        db.delete(profile)
        deleted = True
    db.query(Document).delete()
    db.commit()
    return deleted
