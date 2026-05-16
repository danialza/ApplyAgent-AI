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

from sqlalchemy.orm import Session

from app.models.db_models import CV as CVRow
from app.models.db_models import Document as DocumentRow
from app.models.db_models import WebSource as WebSourceRow
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
from app.services.cv_parser import extract_section_blocks, parse_cv_text
from app.utils.skill_dictionary import (
    AI_ML_SKILLS,
    ROBOTICS_SKILLS,
    WEB_ECOMMERCE_SKILLS,
    find_technical_skills,
)

_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
# Optional month prefix and suffix wrapping the year — captures
# "Oct 2017 – Dec 2024" cleanly while still matching bare "2017–2024".
_MONTH_RE = (
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"(?:uary|ruary|ch|il|e|y|ust|tember|ober|ember)?"
)
_PERIOD_RE = re.compile(
    rf"((?:{_MONTH_RE}\.?\s*)?(?:19|20)\d{{2}}"
    rf"\s*[-–—]\s*"
    rf"(?:(?:{_MONTH_RE}\.?\s*)?(?:19|20)\d{{2}}|present|current|now))",
    re.IGNORECASE,
)
# Permissive: catches anything between the year markers (months,
# spaces, dashes). Used for header *detection*.
_HEADER_PERIOD_RE = re.compile(
    rf"(?:{_MONTH_RE}\.?\s*)?(?:19|20)\d{{2}}.{{0,20}}?[-–—].{{0,20}}?"
    rf"(?:(?:{_MONTH_RE}\.?\s*)?(?:19|20)\d{{2}}|present|current|now)",
    re.IGNORECASE,
)
# Prefer em-dash / en-dash over hyphen so "Co-Founder – Company" splits
# at the right separator, not inside "Co-Founder".
_ROLE_SPLIT_EM = re.compile(r"\s*[—–]\s*")
_ROLE_SPLIT_AT = re.compile(r"\s+at\s+|\s+@\s+", re.IGNORECASE)
_ROLE_SPLIT_HYPHEN = re.compile(r"\s+-\s+")


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
    if m is None:
        m = _HEADER_PERIOD_RE.search(text or "")
    if not m:
        return "", text
    period = m.group(0) if m.lastindex is None else m.group(1) if hasattr(m, "group") else ""
    # Use the matched span directly so both regex variants work.
    period = (text[m.start():m.end()]).strip()
    cleaned = (text[: m.start()] + text[m.end():]).strip(" ,()|;–—-")
    return period, cleaned


def _tags_for(text: str) -> list[str]:
    return find_technical_skills(text)


# ---------- Block-level (raw section text) builders ----------

_BULLET_PREFIX = ("-", "*", "•", "·", "–", "—", "►", "▶", "✓", "✔")


def _walk_block_grouped(block: str) -> list[tuple[str, list[str]]]:
    """Yield (header_line, bullet_lines[]) groups from a raw section block.

    A "header line" has a year/period in it AND isn't a bullet. A
    bullet line starts with a bullet glyph or a hyphen. Non-bullet
    text between headers attaches to the most-recent header. Useful
    for projects, experience, and publications where roles/projects
    don't always have blank-line separators.
    """
    groups: list[tuple[str, list[str]]] = []
    for raw in (block or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        is_bullet = raw.lstrip().startswith(_BULLET_PREFIX)
        text = line.lstrip("".join(_BULLET_PREFIX) + " \t").strip()
        if not is_bullet and _HEADER_PERIOD_RE.search(line) and len(line) < 200:
            groups.append((line, []))
        else:
            if not groups:
                groups.append(("", []))
            groups[-1][1].append(text)
    return groups


def _projects_from_block(block: str) -> list[ProjectEntry]:
    """Header-aware project extraction from a raw section block."""
    out: list[ProjectEntry] = []
    for header, bullets in _walk_block_grouped(block):
        period, residual = _extract_period(header) if header else ("", "")
        title = (residual or header).strip(" ,—–-")[:140] or "Project"
        highlights: list[str] = []
        for b in bullets:
            for s in re.split(r"(?<=[.!?])\s+", b):
                s = s.strip()
                if s:
                    highlights.append(s)
        out.append(ProjectEntry(
            title=title,
            period=period,
            highlights=highlights[:8],
            tags=_tags_for(header + " " + " ".join(bullets)),
        ))
    return out


def _experience_from_block(block: str) -> list[ExperienceEntryLib]:
    """Header-aware experience extraction. Splits 'Title — Company Period'."""
    out: list[ExperienceEntryLib] = []
    for header, bullets in _walk_block_grouped(block):
        period, residual = _extract_period(header) if header else ("", "")
        # Try the separators in priority order: em/en-dash first, then
        # " at ", then bare hyphen (which is risky because role titles
        # often contain hyphens like "Co-Founder").
        parts: list[str] = [residual] if residual else [""]
        for splitter in (_ROLE_SPLIT_EM, _ROLE_SPLIT_AT, _ROLE_SPLIT_HYPHEN):
            cand = splitter.split(residual or "", maxsplit=1)
            if len(cand) == 2 and cand[0].strip() and cand[1].strip():
                parts = cand
                break
        if len(parts) == 2:
            title, company = parts[0].strip(" ,"), parts[1].strip(" ,()")
        else:
            # Fall back: comma split with the LAST chunk treated as company.
            chunks = [c.strip() for c in residual.split(",") if c.strip()]
            if len(chunks) >= 2:
                title, company = chunks[0], chunks[-1]
            else:
                title, company = (residual or header).strip(), ""
        if not title:
            continue
        highlights: list[str] = []
        for b in bullets:
            for s in re.split(r"(?<=[.!?])\s+", b):
                s = s.strip()
                if s:
                    highlights.append(s)
        out.append(ExperienceEntryLib(
            title=title[:140],
            company=company[:140],
            period=period,
            highlights=highlights[:8],
            tags=_tags_for(header + " " + " ".join(bullets)),
        ))
    return out


def _publications_from_block(block: str) -> list[PublicationEntry]:
    """Extract one PublicationEntry per non-empty line / bullet in the block."""
    out: list[PublicationEntry] = []
    for raw in (block or "").splitlines():
        line = raw.lstrip("".join(_BULLET_PREFIX) + " \t").strip()
        if not line or len(line) < 8:
            continue
        # "Under Submission: Title …" / "Published: Title …" / bare title.
        m = re.match(r"(under\s+submission|published|accepted|in\s+review)\s*[:\-]\s*(.+)",
                     line, re.IGNORECASE)
        if m:
            status = m.group(1).title()
            title = m.group(2).strip()
        else:
            status = ""
            title = line
        out.append(PublicationEntry(
            title=title[:240],
            status=status,
            venue="",
            tags=_tags_for(line),
        ))
    return out


# ---------- Per-section transformers ----------

def _build_education(entries: list[str]) -> list[EducationEntry]:
    out: list[EducationEntry] = []
    for e in entries or []:
        period, residual = _extract_period(e)
        parts = _ROLE_SPLIT_EM.split(residual, maxsplit=1)
        if len(parts) != 2:
            parts = _ROLE_SPLIT_HYPHEN.split(residual, maxsplit=1)
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
        parts = _ROLE_SPLIT_EM.split(residual, maxsplit=1)
        if len(parts) != 2:
            parts = _ROLE_SPLIT_HYPHEN.split(residual, maxsplit=1)
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
    """Group bullets under header lines.

    Real-world CVs structure projects as:

        Project Title 2025-Present
        - Did this thing
        - Then that thing

    `split_entries` yields each of those lines as a separate entry; we
    group them here by treating any line with a year/period (and no
    bullet glyph) as a header, then attaching following lines as
    highlights until the next header.
    """
    if not entries:
        return []
    groups: list[tuple[str, list[str]]] = []
    for line in entries:
        line = (line or "").strip()
        if not line:
            continue
        # Header: short-ish, has a year token, doesn't read like prose.
        looks_like_header = (
            len(line) < 140
            and _PERIOD_RE.search(line) is not None
            and "." not in line[:-1]  # full stops imply a sentence
        )
        if looks_like_header:
            groups.append((line, []))
        else:
            if not groups:
                groups.append(("Projects", []))
            groups[-1][1].append(line)

    out: list[ProjectEntry] = []
    for header, bullets in groups:
        period, residual = _extract_period(header)
        title = residual.strip(" ,—–-")[:140] or "Project"
        # Split bullet sentences into clean highlights when needed.
        highlights: list[str] = []
        for b in bullets:
            for s in re.split(r"(?<=[.!?])\s+", b):
                s = s.strip()
                if s:
                    highlights.append(s)
        out.append(ProjectEntry(
            title=title,
            period=period,
            highlights=highlights[:8],
            tags=_tags_for(header + " " + " ".join(bullets)),
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
            parts = _ROLE_SPLIT_EM.split(e, maxsplit=1)
            if len(parts) != 2:
                parts = _ROLE_SPLIT_HYPHEN.split(e, maxsplit=1)
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

# ---------- Merge helpers (smart dedup across sources) ----------
#
# When the same entry shows up in multiple sources (e.g. a project on
# both the CV and the candidate's portfolio site), we keep ONE entry
# and merge the new info in: union tags, append new highlights that
# aren't substring-duplicates of existing ones, keep the longer period
# string, and append the new source key to `sources`.

def _dedup_strings(existing: list[str], incoming: list[str]) -> list[str]:
    """Union of two ordered string lists, case-insensitive, preserving
    the first occurrence's casing. Substring duplicates (one bullet
    that's a prefix of another) collapse to the longer one."""
    out: list[str] = list(existing)
    seen_lower = {s.strip().lower(): i for i, s in enumerate(out)}
    for s in incoming:
        clean = (s or "").strip()
        if not clean:
            continue
        low = clean.lower()
        if low in seen_lower:
            # Existing match — replace with longer one if the incoming
            # is a strict superset.
            i = seen_lower[low]
            if len(clean) > len(out[i]):
                out[i] = clean
            continue
        # Check substring containment in either direction.
        absorbed = False
        for i, existing_s in enumerate(out):
            el = existing_s.lower()
            if el and (el in low or low in el):
                if len(clean) > len(existing_s):
                    out[i] = clean
                    seen_lower[el] = i
                absorbed = True
                break
        if not absorbed:
            out.append(clean)
            seen_lower[low] = len(out) - 1
    return out


def _record_source(entry, source_key: str) -> None:
    """Append `source_key` to `entry.sources` if it isn't already there.
    Used by every per-section merge helper below."""
    current = list(getattr(entry, "sources", None) or [])
    if source_key and source_key not in current:
        current.append(source_key)
    entry.sources = current


def _merge_education(target: EducationEntry, incoming: EducationEntry, source_key: str) -> None:
    target.period = target.period or incoming.period
    target.highlights = _dedup_strings(target.highlights or [], incoming.highlights or [])
    _record_source(target, source_key)


def _merge_experience(target: ExperienceEntryLib, incoming: ExperienceEntryLib, source_key: str) -> None:
    if incoming.period and len(incoming.period) > len(target.period or ""):
        target.period = incoming.period
    target.highlights = _dedup_strings(target.highlights or [], incoming.highlights or [])
    target.tags = _dedup_strings(target.tags or [], incoming.tags or [])
    _record_source(target, source_key)


def _merge_project(target: ProjectEntry, incoming: ProjectEntry, source_key: str) -> None:
    if incoming.period and len(incoming.period) > len(target.period or ""):
        target.period = incoming.period
    target.highlights = _dedup_strings(target.highlights or [], incoming.highlights or [])
    target.tags = _dedup_strings(target.tags or [], incoming.tags or [])
    _record_source(target, source_key)


def _merge_cert(target: CertificationEntry, incoming: CertificationEntry, source_key: str) -> None:
    # Issuer: keep the longer (more specific) string.
    if incoming.issuer and len(incoming.issuer) > len(target.issuer or ""):
        target.issuer = incoming.issuer
    target.tags = _dedup_strings(target.tags or [], incoming.tags or [])
    _record_source(target, source_key)


def _merge_pub(target: PublicationEntry, incoming: PublicationEntry, source_key: str) -> None:
    if incoming.status and not target.status:
        target.status = incoming.status
    if incoming.venue and len(incoming.venue) > len(target.venue or ""):
        target.venue = incoming.venue
    target.tags = _dedup_strings(target.tags or [], incoming.tags or [])
    _record_source(target, source_key)


def build_library_from_all(db: Session, *, location: str = "") -> CVLibraryBase:
    """Merge every uploaded CV + Document into one library.

    Header takes the most recent CV's contact info (newest upload usually
    reflects what the user wants displayed). Skills / projects /
    experience / certifications / publications / languages are *unioned*
    across all sources and deduplicated by canonical key. Each entry is
    auto-tagged with the canonical skills found in its full text so the
    renderer's JD-overlap ranking works out of the box.
    """
    cvs: list[CVRow] = (
        db.query(CVRow).order_by(CVRow.created_at.asc()).all()
    )
    docs: list[DocumentRow] = (
        db.query(DocumentRow).order_by(DocumentRow.created_at.asc()).all()
    )
    web_sources: list[WebSourceRow] = (
        db.query(WebSourceRow)
        .filter(WebSourceRow.status == "done")
        .order_by(WebSourceRow.created_at.asc())
        .all()
    )

    # Pre-compute parsed structures + raw section blocks for every source
    # up-front so every downstream pass can read them without redoing the
    # work. Block-aware parsing (header-grouped projects, experience,
    # publications) needs the raw section text, not the flattened entry
    # lists `cv.experience` / `cv.projects` carry.
    parsed_by_cv = {cv.id: parse_cv_text(cv.raw_text or "") for cv in cvs}
    parsed_by_doc = {d.id: parse_cv_text(d.raw_text or "") for d in docs}
    blocks_by_cv = {cv.id: extract_section_blocks(cv.raw_text or "") for cv in cvs}
    blocks_by_doc = {d.id: extract_section_blocks(d.raw_text or "") for d in docs}

    # ----- Header: newest CV wins for each field, but never overwrite a
    # populated value with an empty one from an older CV. -----
    header = CVHeader()
    for cv in cvs:
        if cv.name and not header.name:
            header.name = cv.name.strip()
        if cv.email and not header.email:
            header.email = cv.email.strip()
        if cv.phone and not header.phone:
            header.phone = cv.phone.strip()
        if cv.portfolio and not header.website:
            header.website = _website_url(cv.portfolio)
        if cv.linkedin and not header.linkedin:
            header.linkedin = cv.linkedin.strip()
        if cv.github and not header.github:
            header.github = cv.github.strip()
    if location:
        header.location = location

    # ----- Union skills across all sources, drop duplicates by canonical
    # key, then bucket via skill_dictionary categories. -----
    all_skills: list[str] = []
    seen_skill: set[str] = set()
    for cv in cvs:
        for s in (cv.skills or []):
            key = (s or "").strip().lower()
            if key and key not in seen_skill:
                seen_skill.add(key)
                all_skills.append(s.strip())
    # Also extract canonical skills from any Document text — TXT notes,
    # certificates, project READMEs often list extra skills the parser
    # missed in a CV.
    for d in docs:
        for s in find_technical_skills(d.raw_text or ""):
            key = s.lower()
            if key not in seen_skill:
                seen_skill.add(key)
                all_skills.append(s)
    # Web sources: pick up skills from extracted.skills and from
    # raw_text scrape (catches portfolio "Skills: …" lists).
    for w in web_sources:
        for s in (w.extracted or {}).get("skills") or []:
            key = (s or "").strip().lower()
            if key and key not in seen_skill:
                seen_skill.add(key)
                all_skills.append(s.strip())
        for s in find_technical_skills(w.raw_text or ""):
            key = s.lower()
            if key not in seen_skill:
                seen_skill.add(key)
                all_skills.append(s)
    skills_groups = _group_skills(all_skills)

    # ----- Education: merge across sources by institution+degree. -----
    # When several sources mention the same school, we UNION their
    # highlights instead of dropping later ones, and track every
    # contributing source on the merged entry. Same merge protocol
    # applies to experience, projects, certifications, publications.
    education: list[EducationEntry] = []
    edu_index: dict[str, int] = {}
    for cv in cvs:
        for e in _build_education(list(cv.education or [])):
            key = f"{e.institution.lower()}|{e.degree.lower()}"
            sk = f"cv:{cv.id}"
            if key in edu_index:
                _merge_education(education[edu_index[key]], e, sk)
            else:
                edu_index[key] = len(education)
                e.sources = [sk]
                education.append(e)

    # ----- Experience: merge by title+company. -----
    experience: list[ExperienceEntryLib] = []
    exp_index: dict[str, int] = {}
    for cv in cvs:
        block = blocks_by_cv[cv.id].get("experience") or ""
        sk = f"cv:{cv.id}"
        for x in _experience_from_block(block):
            key = f"{x.title.lower()}|{x.company.lower()}"
            if key in exp_index:
                _merge_experience(experience[exp_index[key]], x, sk)
            else:
                exp_index[key] = len(experience)
                x.sources = [sk]
                experience.append(x)

    # ----- Projects: merge by title across selected / additional /
    # generic buckets. Same project from CV + GitHub + portfolio should
    # appear ONCE with union of tags, highlights, and the longest
    # period string. -----
    selected_acc: list[ProjectEntry] = []
    additional_acc: list[ProjectEntry] = []
    generic_acc: list[ProjectEntry] = []
    proj_index: dict[str, tuple[list[ProjectEntry], int]] = {}

    def _add_proj(target: list[ProjectEntry], projects: list[ProjectEntry], source_key: str) -> None:
        for p in projects:
            key = (p.title or "").strip().lower()
            if not key:
                continue
            if key in proj_index:
                bucket, idx = proj_index[key]
                _merge_project(bucket[idx], p, source_key)
                continue
            p.sources = [source_key]
            proj_index[key] = (target, len(target))
            target.append(p)

    for cv in cvs:
        blocks = blocks_by_cv[cv.id]
        sk = f"cv:{cv.id}"
        _add_proj(selected_acc,    _projects_from_block(blocks.get("selected_projects", "")), sk)
        _add_proj(additional_acc,  _projects_from_block(blocks.get("additional_projects", "")), sk)
        _add_proj(generic_acc,     _projects_from_block(blocks.get("projects", "")), sk)
    for d in docs:
        blocks = blocks_by_doc[d.id]
        sk = f"document:{d.id}"
        _add_proj(selected_acc,    _projects_from_block(blocks.get("selected_projects", "")), sk)
        _add_proj(additional_acc,  _projects_from_block(blocks.get("additional_projects", "")), sk)
        _add_proj(generic_acc,     _projects_from_block(blocks.get("projects", "")), sk)

    # Web sources: LLM-extracted projects fold into the same merge map
    # so a "TalkingHeadAI" project from both the CV and the candidate's
    # portfolio shows up once with both sources listed.
    for w in web_sources:
        sk = f"web:{w.id}"
        for p in (w.extracted or {}).get("projects") or []:
            title = (p.get("title") or "").strip()
            if not title:
                continue
            summary = (p.get("summary") or "").strip()
            tags = [t for t in (p.get("tags") or []) if isinstance(t, str)]
            highlights: list[str] = []
            if summary:
                highlights.append(summary)
            if p.get("url"):
                highlights.append(f"Source: {p['url']}")
            _add_proj(additional_acc, [ProjectEntry(
                title=title, period="", highlights=highlights, tags=tags[:8],
            )], sk)

    if selected_acc or additional_acc:
        # CV provided explicit split — respect it. Generic projects join
        # `additional` so nothing is lost.
        selected = selected_acc
        additional = additional_acc + generic_acc
    else:
        # Fall back: split the generic bucket by tag-density.
        generic_acc.sort(key=lambda p: -len(p.tags))
        half = max(1, len(generic_acc) // 2) if generic_acc else 0
        selected = generic_acc[:half]
        additional = generic_acc[half:]

    # ----- Certifications: merge by name. -----
    certifications: list[CertificationEntry] = []
    cert_index: dict[str, int] = {}
    for cv in cvs:
        sk = f"cv:{cv.id}"
        for c in _build_certifications(list(cv.certifications or [])):
            key = c.name.lower()
            if key in cert_index:
                _merge_cert(certifications[cert_index[key]], c, sk)
            else:
                cert_index[key] = len(certifications)
                c.sources = [sk]
                certifications.append(c)

    # ----- Publications: merge by title across sources + fallback scrape. -----
    publications: list[PublicationEntry] = []
    pub_index: dict[str, int] = {}

    def _add_pub(p: PublicationEntry, source_key: str) -> None:
        key = (p.title or "").strip().lower()
        if not key:
            return
        if key in pub_index:
            _merge_pub(publications[pub_index[key]], p, source_key)
            return
        p.sources = [source_key]
        pub_index[key] = len(publications)
        publications.append(p)

    for cv in cvs:
        block = blocks_by_cv[cv.id].get("publications") or ""
        for p in _publications_from_block(block):
            _add_pub(p, f"cv:{cv.id}")
    for d in docs:
        block = blocks_by_doc[d.id].get("publications") or ""
        for p in _publications_from_block(block):
            _add_pub(p, f"document:{d.id}")
    # Fallback: scrape every raw_text for "Under Submission:" lines
    # that weren't under a Publications header.
    for cv in cvs:
        for p in _build_publications_from_summary(cv.raw_text or ""):
            _add_pub(p, f"cv:{cv.id}")
    for d in docs:
        for p in _build_publications_from_summary(d.raw_text or ""):
            _add_pub(p, f"document:{d.id}")

    # ----- Languages: union, dedup by head-token. -----
    languages: list[str] = []
    seen_lang: set[str] = set()
    for cv in cvs:
        for lang in (cv.languages or []):
            head = (lang or "").split(":")[0].split("(")[0].strip().lower()
            if head and head not in seen_lang:
                seen_lang.add(head)
                languages.append(lang)

    # ----- Summary: pick the longest non-empty summary across CVs.
    # Web-source bio acts as a fallback when no CV provided one. -----
    summary = ""
    for cv in cvs:
        if cv.summary and len(cv.summary) > len(summary):
            summary = cv.summary.strip()
    if not summary:
        for w in web_sources:
            bio = ((w.extracted or {}).get("bio") or "").strip()
            if bio and len(bio) > len(summary):
                summary = bio

    return CVLibraryBase(
        header=header,
        summary=summary,
        skills_groups=skills_groups,
        education=education,
        selected_projects=selected,
        additional_projects=additional,
        experience=experience,
        publications=publications,
        certifications=certifications,
        languages=languages,
    )


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
