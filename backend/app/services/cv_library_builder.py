"""Best-effort builder: uploaded CV (parsed via `cv_parser`) → CVLibrary payload.

The CV upload pipeline produces a fairly unstructured list of skills /
experience / projects strings. The CV library needs structured records
(title, company, period, tags, highlights). This builder does the
best-effort mapping; the user is expected to review + refine in the
library editor.

Heuristics:
  * Skills → grouped by `skill_dictionary` category when matchable, else
    a single "Skills" bucket.
  * Education entries → split on em-dash / hyphen / "—" between
    institution and degree; trailing year tokens become the period.
  * Experience entries → split "Title — Company (period)" or "Title at
    Company (period)" patterns; remaining text becomes the highlight.
  * Projects → first phrase before "." becomes the title; remaining
    sentences become highlights; year tokens detected as period.
  * Tags → canonical skills found via `find_technical_skills` inside
    each entry's text (auto-tagging for ranking).

Anything ambiguous gets dumped into `highlights` so no information is
lost — the editor can clean up the shape.
"""
from __future__ import annotations

import re
from typing import Any

from app.models.db_models import CV as CVRow
from app.models.schemas import (
    CertificationEntry,
    CVHeader,
    CVLibraryBase,
    EducationEntry,
    ExperienceEntryLib,
    ProjectEntry,
    PublicationEntry,
    SkillGroup,
)
from app.utils.skill_dictionary import (
    AI_ML_SKILLS,
    ROBOTICS_SKILLS,
    WEB_ECOMMERCE_SKILLS,
    find_technical_skills,
)

_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_PERIOD_RE = re.compile(
    r"((?:19|20)\d{2}\s*[-–—]\s*(?:(?:19|20)\d{2}|present|current|now))",
    re.IGNORECASE,
)
_ROLE_SPLIT_RE = re.compile(r"\s+[—–-]\s+|\s+at\s+|\s+@\s+", re.IGNORECASE)


# ---------- Skill bucketing ----------

def _category_for(skill: str) -> str:
    s = skill.strip().lower()
    if any(s == a or s in a for aliases in AI_ML_SKILLS.values() for a in aliases):
        return "LLM / Applied AI"
    if any(s == a or s in a for aliases in ROBOTICS_SKILLS.values() for a in aliases):
        return "Robotics / Control"
    if any(s == a or s in a for aliases in WEB_ECOMMERCE_SKILLS.values() for a in aliases):
        return "Web / E-commerce"
    if s in {"python", "sql", "javascript", "php", "dart", "rust", "java", "c++", "go"}:
        return "Languages"
    return "Other"


def _group_skills(skills: list[str]) -> list[SkillGroup]:
    buckets: dict[str, list[str]] = {}
    seen: set[str] = set()
    for s in skills or []:
        s = (s or "").strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        buckets.setdefault(_category_for(s), []).append(s)
    # Ordered: Languages first, then LLM/Applied AI, then the rest.
    order = ["Languages", "LLM / Applied AI", "Robotics / Control",
             "Web / E-commerce", "Other"]
    groups: list[SkillGroup] = []
    for label in order:
        if buckets.get(label):
            groups.append(SkillGroup(label=label, items=buckets[label]))
    # Catch any extra category that slipped in (defensive).
    for label, items in buckets.items():
        if label not in order:
            groups.append(SkillGroup(label=label, items=items))
    return groups


# ---------- Period + tag extraction ----------

def _extract_period(text: str) -> tuple[str, str]:
    """Return (period_string, residual_text_with_period_removed)."""
    m = _PERIOD_RE.search(text or "")
    if not m:
        return "", text
    period = m.group(1)
    cleaned = (text[: m.start()] + text[m.end():]).strip(" ,()|;–—-")
    return period, cleaned


def _tags_for(text: str) -> list[str]:
    return find_technical_skills(text)


# ---------- Per-section transformers ----------

def _build_education(entries: list[str]) -> list[EducationEntry]:
    out: list[EducationEntry] = []
    for e in entries or []:
        period, residual = _extract_period(e)
        parts = _ROLE_SPLIT_RE.split(residual, maxsplit=1)
        if len(parts) == 2:
            institution, degree = parts[0].strip(" ,"), parts[1].strip(" ,")
        else:
            institution, degree = residual.strip(), ""
        out.append(EducationEntry(
            institution=institution[:140],
            degree=degree[:140],
            period=period,
            highlights=[],
        ))
    return out


def _build_experience(entries: list[str]) -> list[ExperienceEntryLib]:
    """Group consecutive bullets under the most recent header line.

    Heuristic: a line with a year/period is treated as a header. Lines
    after it until the next header become its highlights.
    """
    if not entries:
        return []
    groups: list[tuple[str, list[str]]] = []
    for line in entries:
        line = (line or "").strip()
        if not line:
            continue
        if _PERIOD_RE.search(line) and len(line) < 200:
            groups.append((line, []))
        else:
            if not groups:
                groups.append(("Experience", []))
            groups[-1][1].append(line)

    out: list[ExperienceEntryLib] = []
    for header, bullets in groups:
        period, residual = _extract_period(header)
        parts = _ROLE_SPLIT_RE.split(residual, maxsplit=1)
        if len(parts) == 2:
            title, company = parts[0].strip(" ,"), parts[1].strip(" ,()")
        else:
            title, company = residual.strip(), ""
        body = " ".join(bullets)
        out.append(ExperienceEntryLib(
            title=title[:140],
            company=company[:140],
            period=period,
            highlights=bullets if bullets else [body] if body else [],
            tags=_tags_for(header + " " + body),
        ))
    return out


def _build_projects(entries: list[str]) -> list[ProjectEntry]:
    """One project per parsed entry. Title = first phrase, rest = highlights."""
    out: list[ProjectEntry] = []
    for e in entries or []:
        e = (e or "").strip()
        if not e:
            continue
        period, residual = _extract_period(e)
        # Split on first ':' or first '. ' for title vs body.
        m = re.match(r"(.{4,80}?)[:\.]\s+(.+)$", residual, re.DOTALL)
        if m:
            title, body = m.group(1).strip(), m.group(2).strip()
        else:
            title, body = residual.strip()[:80], residual.strip()
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]
        out.append(ProjectEntry(
            title=title,
            period=period,
            highlights=sentences[:5] if sentences else [body],
            tags=_tags_for(e),
        ))
    return out


def _build_certifications(entries: list[str]) -> list[CertificationEntry]:
    out: list[CertificationEntry] = []
    for e in entries or []:
        e = (e or "").strip()
        if not e:
            continue
        # Try "Issuer: Name" first, then "Name — Issuer".
        m = re.match(r"([^:]+):\s+(.+)$", e)
        if m:
            issuer, name = m.group(1).strip(), m.group(2).strip()
        else:
            parts = _ROLE_SPLIT_RE.split(e, maxsplit=1)
            if len(parts) == 2:
                name, issuer = parts[0].strip(), parts[1].strip()
            else:
                name, issuer = e, ""
        out.append(CertificationEntry(issuer=issuer[:80], name=name[:200], tags=_tags_for(e)))
    return out


def _build_publications_from_summary(summary_text: str) -> list[PublicationEntry]:
    """Best-effort: surface 'Under Submission'/'Published' lines from summary
    or experience text. Most uploads don't include publications explicitly."""
    out: list[PublicationEntry] = []
    for line in (summary_text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r"(under submission|published|accepted)\s*[:\-]\s*(.+)",
                     s, re.IGNORECASE)
        if m:
            out.append(PublicationEntry(
                status=m.group(1).title(),
                title=m.group(2).strip(),
                venue="",
                tags=_tags_for(m.group(2)),
            ))
    return out


# ---------- Header ----------

def _website_url(portfolio: str) -> str:
    p = (portfolio or "").strip()
    if not p:
        return ""
    return p if p.startswith(("http://", "https://")) else f"https://{p}"


# ---------- Public ----------

def build_library_from_cv(cv: CVRow, *, location: str = "") -> CVLibraryBase:
    """Project a CV row into a CVLibraryBase ready for upsert.

    `location` is optional — CVs rarely store a clean city/country; the
    caller can pass one explicitly if the existing library has it.
    """
    header = CVHeader(
        name=(cv.name or "").strip(),
        location=location,
        email=(cv.email or "").strip(),
        phone=(cv.phone or "").strip(),
        website=_website_url(cv.portfolio or ""),
        linkedin=(cv.linkedin or "").strip(),
        github=(cv.github or "").strip(),
    )

    skills_groups = _group_skills(list(cv.skills or []))

    projects_all = _build_projects(list(cv.projects or []))
    # First half → "Selected", remainder → "Additional". Keeps the editor
    # layout sane until the user re-curates.
    half = max(1, len(projects_all) // 2)
    selected = projects_all[:half]
    additional = projects_all[half:]

    return CVLibraryBase(
        header=header,
        summary=(cv.summary or "").strip(),
        skills_groups=skills_groups,
        education=_build_education(list(cv.education or [])),
        selected_projects=selected,
        additional_projects=additional,
        experience=_build_experience(list(cv.experience or [])),
        publications=_build_publications_from_summary(cv.raw_text or ""),
        certifications=_build_certifications(list(cv.certifications or [])),
        languages=list(cv.languages or []),
    )
