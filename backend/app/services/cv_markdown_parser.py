"""Parse the canonical CV markdown template into a CVLibraryBase.

Source-of-truth approach (career-ops style): the user maintains one
markdown file with explicit section headers, and we parse it directly
into the structured library. No PDF heuristics, no whitespace recovery,
no guessing — what you write is what you get.

Expected grammar (see `docs/cv_template.md`):

    # Full Name
    > Location | email | phone | website | github | linkedin

    ## Professional Summary
    <paragraph>

    ## Technical Skills
    - **Label**: item, item, item
    - **Other Label**: item, item

    ## Education
    ### Institution — Degree | Period
    - highlight 1
    - highlight 2

    ## Selected AI Projects
    ### Title | Period
    **Tags**: tag1, tag2
    - bullet
    - bullet

    ## Additional Technical Projects
    (same shape as Selected)

    ## Professional Experience
    ### Title — Company | Period
    **Tags**: tag1, tag2
    - bullet
    - bullet

    ## Certifications
    - **Issuer**: Name
    - **Issuer**: Name

    ## Publications
    - **Status**: Title (— optional venue) (tags: ...)

    ## Languages
    - **English**: Professional working proficiency
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.schemas import (
    CertificationEntry,
    CompetencyEntry,
    CVHeader,
    CVLibraryBase,
    EducationEntry,
    ExperienceEntryLib,
    ProjectEntry,
    PublicationEntry,
    SkillGroup,
)


# ---------- Helpers ----------

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _strip_bold(text: str) -> str:
    """Remove **bold** markdown markers but keep the inner text."""
    return _BOLD_RE.sub(r"\1", text)


def _split_csv(value: str) -> list[str]:
    return [v.strip() for v in re.split(r"[;,]", value) if v.strip()]


def _classify_contact(token: str) -> tuple[str, str]:
    """Return (field_name, normalised_value) for one contact-line token.

    field_name ∈ {email, phone, website, github, linkedin, location}.
    """
    t = token.strip()
    if not t:
        return "", ""
    low = t.lower()
    if "@" in t and "/" not in t:
        return "email", t
    if "linkedin.com" in low:
        return "linkedin", _ensure_scheme(t)
    if "github.com" in low:
        return "github", _ensure_scheme(t)
    if low.startswith(("http://", "https://")) or "://" in low:
        return "website", _ensure_scheme(t)
    # Bare-domain heuristic: contains a dot, no spaces.
    if "." in t and " " not in t and not t.startswith("+") and not any(c.isalpha() for c in t[:1] if c == "+"):
        return "website", _ensure_scheme(t)
    # Phone heuristic: mostly digits / +/spaces.
    digits = sum(c.isdigit() for c in t)
    if digits >= 7 and digits / max(1, len(t)) > 0.4:
        return "phone", t
    return "location", t


def _ensure_scheme(url: str) -> str:
    return url if url.startswith(("http://", "https://")) else "https://" + url


# ---------- Parser state machine ----------

@dataclass
class _State:
    section: str = ""
    sub_buf: dict[str, str] = None
    sub_lines: list[str] = None

    def reset_sub(self, title: str, period: str = "") -> None:
        self.sub_buf = {"title": title, "period": period, "tags": "", "bullets": []}
        self.sub_lines = []


def parse_cv_markdown(text: str) -> CVLibraryBase:
    """Parse markdown CV → CVLibraryBase. Deterministic, no heuristics."""
    out = CVLibraryBase()
    state = _State()

    # Buffers for entries inside a section.
    education: list[EducationEntry] = []
    selected: list[ProjectEntry] = []
    additional: list[ProjectEntry] = []
    experience: list[ExperienceEntryLib] = []
    certifications: list[CertificationEntry] = []
    publications: list[PublicationEntry] = []
    languages: list[str] = []
    skills_groups: list[SkillGroup] = []
    core_competencies: list[CompetencyEntry] = []
    summary_lines: list[str] = []

    current_entry: dict | None = None
    section = ""

    def _flush_entry() -> None:
        """Commit the in-progress sub-entry to the right section list."""
        nonlocal current_entry
        if current_entry is None:
            return
        title = current_entry.get("title", "").strip()
        period = current_entry.get("period", "").strip()
        tags = current_entry.get("tags") or []
        bullets = [b.strip() for b in current_entry.get("bullets", []) if b.strip()]

        if section == "education":
            # Title format: "Institution — Degree"
            inst, deg = _split_title(title, default_sep_first=True)
            education.append(EducationEntry(
                institution=inst, degree=deg, period=period, highlights=bullets,
            ))
        elif section == "selected_projects":
            selected.append(ProjectEntry(
                title=title, period=period, highlights=bullets, tags=tags,
                url=(current_entry.get("url") or "").strip(),
            ))
        elif section == "additional_projects":
            additional.append(ProjectEntry(
                title=title, period=period, highlights=bullets, tags=tags,
                url=(current_entry.get("url") or "").strip(),
            ))
        elif section == "experience":
            t, comp = _split_title(title)
            experience.append(ExperienceEntryLib(
                title=t, company=comp, period=period, highlights=bullets, tags=tags,
            ))
        current_entry = None

    for raw_line in (text or "").splitlines():
        line = raw_line.rstrip()

        # H1: candidate name.
        if line.startswith("# ") and not line.startswith("## "):
            out.header.name = line[2:].strip()
            continue

        # Blockquote line right after the name → contact line.
        if line.startswith("> ") and not out.header.email and not out.header.location:
            for tok in [t.strip() for t in line[2:].split("|")]:
                field, value = _classify_contact(tok)
                if not field:
                    continue
                if not getattr(out.header, field, ""):
                    setattr(out.header, field, value)
            continue

        # H2: section header.
        if line.startswith("## ") and not line.startswith("### "):
            _flush_entry()
            label = line[3:].strip().lower()
            section = _normalise_section(label)
            state.section = section
            continue

        # H3: sub-entry inside Projects / Education / Experience.
        if line.startswith("### "):
            _flush_entry()
            head = line[4:].strip()
            title, period = _split_period(head)
            current_entry = {"title": title, "period": period, "tags": [], "bullets": [], "url": ""}
            continue

        # Inside an entry: tags + bullets.
        if current_entry is not None:
            tags_match = re.match(r"\s*\*\*Tags?\*\*\s*:\s*(.+)$", line, re.IGNORECASE)
            if tags_match:
                current_entry["tags"] = _split_csv(tags_match.group(1))
                continue
            # **URL**: https://...  — populates ProjectEntry.url so the
            # renderer wraps the title in \href + UTM.
            url_match = re.match(
                r"\s*\*\*URL\*\*\s*:\s*(https?://\S+)\s*$",
                line, re.IGNORECASE,
            )
            if url_match:
                current_entry["url"] = url_match.group(1).rstrip(".,;)")
                continue
            if line.startswith("- "):
                current_entry["bullets"].append(_strip_bold(line[2:].strip()))
                continue
            if line.strip() == "":
                continue
            # Anything else inside an entry is appended to last bullet.
            if current_entry["bullets"]:
                current_entry["bullets"][-1] += " " + _strip_bold(line.strip())
            continue

        # Outside an entry: section-level content.
        if section == "summary":
            if line.strip():
                summary_lines.append(line.strip())

        elif section == "skills":
            m = re.match(r"\s*-\s*\*\*([^*]+?)\*\*\s*[:\-—]\s*(.+)$", line)
            if m:
                label = m.group(1).strip()
                items = _split_csv(m.group(2))
                skills_groups.append(SkillGroup(label=label, items=items))

        elif section == "certifications":
            m = re.match(r"\s*-\s*\*\*([^*]+?)\*\*\s*[:\-—]\s*(.+)$", line)
            if m:
                certifications.append(CertificationEntry(
                    issuer=m.group(1).strip(),
                    name=m.group(2).strip(),
                    tags=_tags_for_text(m.group(2)),
                ))
            elif line.startswith("- "):
                certifications.append(CertificationEntry(
                    issuer="", name=line[2:].strip(),
                    tags=_tags_for_text(line[2:]),
                ))

        elif section == "publications":
            if line.startswith("- "):
                body = line[2:].strip()
                # Try **Status**: Title format.
                m = re.match(r"\*\*([^*]+?)\*\*\s*[:\-—]\s*(.+)$", body)
                if m:
                    publications.append(PublicationEntry(
                        status=m.group(1).strip(),
                        title=_strip_bold(m.group(2).strip()),
                        venue="",
                        tags=_tags_for_text(m.group(2)),
                    ))
                else:
                    publications.append(PublicationEntry(
                        title=_strip_bold(body), status="", venue="",
                        tags=_tags_for_text(body),
                    ))

        elif section == "core_competencies":
            comp = _parse_competency_line(line)
            if comp is not None:
                core_competencies.append(comp)

        elif section == "languages":
            if line.startswith("- "):
                languages.append(_strip_bold(line[2:].strip()))

    _flush_entry()

    out.summary = " ".join(summary_lines).strip()
    out.skills_groups = skills_groups
    out.core_competencies = core_competencies
    out.education = education
    out.selected_projects = selected
    out.additional_projects = additional
    out.experience = experience
    out.certifications = certifications
    out.publications = publications
    out.languages = languages
    return out


# ---------- Section name → canonical key ----------

_SECTION_MAP: dict[str, str] = {
    "professional summary": "summary",
    "summary": "summary",
    "profile": "summary",
    "technical skills": "skills",
    "skills": "skills",
    "core competencies": "core_competencies",
    "stretch skills": "core_competencies",
    "aspirational skills": "core_competencies",
    "education": "education",
    "selected ai projects": "selected_projects",
    "selected projects": "selected_projects",
    "selected ai, robotics & software projects": "selected_projects",
    "additional technical projects": "additional_projects",
    "additional projects": "additional_projects",
    "other projects": "additional_projects",
    "professional experience": "experience",
    "experience": "experience",
    "work experience": "experience",
    "certifications": "certifications",
    "certificates": "certifications",
    "publications": "publications",
    "papers": "publications",
    "languages": "languages",
}


def _normalise_section(label: str) -> str:
    return _SECTION_MAP.get(label, label)


# ---------- Core Competencies line parser ----------

# Accepts any of:
#   - **Distributed systems**: 4/5 — designed event-driven services
#   - **Kubernetes**: 3/5
#   - **Rust** (2/5) — read code, write small CLIs
#   - Distributed systems: 4/5 — rationale
#   - Distributed systems (4) — rationale
_COMP_LINE_RE = re.compile(
    r"""^\s*-\s*
        (?:\*\*(?P<bold>[^*]+?)\*\*|(?P<plain>[^:()\-—]+?))
        \s*
        (?:[:\(]\s*|\s+)
        (?P<rating>[1-5])\s*(?:/\s*5)?\s*\)?
        \s*[:\-—]?\s*
        (?P<rationale>.*)$
    """,
    re.VERBOSE,
)


def _parse_competency_line(line: str) -> CompetencyEntry | None:
    """Return a CompetencyEntry from a single bullet, or None if the
    line doesn't match the `name + rating` shape. Lines without a
    rating are silently skipped — we don't guess; competencies the
    candidate hasn't graded shouldn't be auto-injected."""
    m = _COMP_LINE_RE.match(line)
    if not m:
        return None
    name = (m.group("bold") or m.group("plain") or "").strip()
    if not name:
        return None
    try:
        rating = int(m.group("rating"))
    except (TypeError, ValueError):
        return None
    rating = max(1, min(5, rating))
    rationale = (m.group("rationale") or "").strip().lstrip("—-:").strip()
    return CompetencyEntry(name=name, rating=rating, rationale=rationale)


# ---------- Title splitters ----------

def _split_period(head: str) -> tuple[str, str]:
    """Split 'Title | Period' on the LAST `|`."""
    if "|" in head:
        title, _, period = head.rpartition("|")
        return title.strip(), period.strip()
    return head.strip(), ""


def _split_title(title: str, default_sep_first: bool = False) -> tuple[str, str]:
    """Split 'Title — Company' or 'Title - Company' for experience.

    `default_sep_first=True` swaps the meaning for education where the
    pattern is 'Institution — Degree' (institution is the dominant noun).
    """
    for sep in (" — ", " – ", " - "):
        if sep in title:
            left, _, right = title.partition(sep)
            return left.strip(), right.strip()
    return title.strip(), ""


# ---------- Tag inference ----------

def _tags_for_text(text: str) -> list[str]:
    """Try the canonical skill dictionary first; fall back to noun-ish words."""
    try:
        from app.utils.skill_dictionary import find_technical_skills
        return find_technical_skills(text)
    except Exception:  # pragma: no cover
        return []
