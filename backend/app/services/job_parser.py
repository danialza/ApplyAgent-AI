"""Job description parser.

Heuristic-only (no LLM): regex + keyword section detection + a configurable
skill dictionary. Returns a `ParsedJob` dataclass with the full set of fields
required by the matching engine and frontend.

Pipeline:
  1. `clean_text` — normalize whitespace/unicode.
  2. Header line scan → `required` / `preferred` / `responsibilities` /
     `qualifications` / `education` blocks.
  3. Targeted regex passes for title/company/location/salary/employment type/
     remote type/experience level.
  4. Skill dictionary scan over the JD (and per-block) for required vs
     preferred technical skills, technologies, and soft skills.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from app.utils.skill_dictionary import find_soft_skills, find_technical_skills
from app.utils.text_cleaning import clean_text, split_csv_like


# ---------- Section header keywords ----------

# Lowercased exact-or-prefix match against the normalized header line.
_SECTION_KEYS: dict[str, list[str]] = {
    "required": [
        "required skills", "required qualifications",
        "requirements", "key requirements",
        "essential skills", "essential qualifications",
        "must have", "must-have",
        "what you'll need", "what you will need",
        "what we need", "what we're looking for",
        "minimum qualifications",
    ],
    "preferred": [
        "preferred skills", "preferred qualifications",
        "preferred", "nice to have", "nice-to-have",
        "bonus", "bonus points", "plus", "good to have",
    ],
    "responsibilities": [
        "responsibilities", "key responsibilities", "core responsibilities",
        "what you'll do", "what you will do", "what you'll be doing",
        "duties", "role overview", "the role", "your role",
        "day to day", "day-to-day",
        "about the role", "about the position", "job description",
    ],
    "qualifications": [
        "qualifications", "candidate profile", "who you are",
        "what we're looking for", "what we look for",
    ],
    "education": [
        "education", "education requirements", "educational requirements",
    ],
}

# Build a flat set of every header keyword for boundary detection.
_ALL_HEADER_KEYWORDS = {kw for kws in _SECTION_KEYS.values() for kw in kws}


# ---------- Inline metadata regex ----------

_TITLE_HINT_RE = re.compile(
    r"^(?:job\s*title|position|role)\s*[:\-]\s*(.+)$", re.IGNORECASE | re.MULTILINE
)
_COMPANY_HINT_RE = re.compile(
    r"^(?:company|employer|organization|organisation)\s*[:\-]\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_LOCATION_HINT_RE = re.compile(
    r"^(?:location|based in|office|city)\s*[:\-]\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_SALARY_HINT_RE = re.compile(
    r"^(?:salary|compensation|pay|pay range|salary range)\s*[:\-]\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)

# Salary patterns: $80,000 - $120,000 / 60K-90K USD / £40,000 / €50k+
_SALARY_RANGE_RE = re.compile(
    r"""
    (?P<cur>[\$£€]|USD|EUR|GBP|CAD|AUD)?\s*
    (?P<low>\d{1,3}(?:[,.]\d{3})*(?:\.\d+)?\s*[kK]?)
    \s*(?:-|to|–|—)\s*
    (?P<cur2>[\$£€]|USD|EUR|GBP|CAD|AUD)?\s*
    (?P<high>\d{1,3}(?:[,.]\d{3})*(?:\.\d+)?\s*[kK]?)
    (?:\s*(?P<cur3>USD|EUR|GBP|CAD|AUD))?
    (?P<period>\s*(?:per\s*(?:year|annum|hour|month)|/yr|/year|/hr|/hour|/month))?
    """,
    re.IGNORECASE | re.VERBOSE,
)
_SALARY_SINGLE_RE = re.compile(
    r"""
    (?P<cur>[\$£€]|USD|EUR|GBP|CAD|AUD)\s*
    (?P<amt>\d{1,3}(?:[,.]\d{3})*(?:\.\d+)?\s*[kK]?\+?)
    (?P<period>\s*(?:per\s*(?:year|annum|hour|month)|/yr|/year|/hr|/hour|/month))?
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Employment type — order matters when multiple match.
_EMPLOYMENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("internship", re.compile(r"\bintern(?:ship)?\b", re.IGNORECASE)),
    ("contract", re.compile(r"\b(?:contract|contractor|freelance)\b", re.IGNORECASE)),
    ("part-time", re.compile(r"\bpart[\s\-]?time\b", re.IGNORECASE)),
    ("temporary", re.compile(r"\btemporary\b", re.IGNORECASE)),
    ("full-time", re.compile(r"\bfull[\s\-]?time\b", re.IGNORECASE)),
]

# Remote type
_REMOTE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("hybrid", re.compile(r"\bhybrid\b", re.IGNORECASE)),
    ("remote", re.compile(r"\b(?:fully\s+)?remote\b|\bwork\s+from\s+home\b|\bwfh\b", re.IGNORECASE)),
    ("on-site", re.compile(r"\bon[\s\-]?site\b|\bonsite\b|\bin[\s\-]?office\b|\bin\s+the\s+office\b", re.IGNORECASE)),
]

# Experience level (canonical → patterns)
_LEVEL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("internship", re.compile(r"\bintern(?:ship)?\b", re.IGNORECASE)),
    ("principal", re.compile(r"\bprincipal\b", re.IGNORECASE)),
    ("lead", re.compile(r"\b(?:lead|tech\s*lead|team\s*lead|engineering\s*manager)\b", re.IGNORECASE)),
    ("senior", re.compile(r"\b(?:senior|sr\.?|staff)\b", re.IGNORECASE)),
    ("mid-level", re.compile(r"\b(?:mid[\s\-]?level|intermediate)\b", re.IGNORECASE)),
    ("junior", re.compile(r"\b(?:junior|jr\.?|entry[\s\-]?level|graduate)\b", re.IGNORECASE)),
]
_YEARS_RE = re.compile(r"(\d+)\+?\s*(?:to\s*\d+\s*)?years?", re.IGNORECASE)

_EDU_KEYWORDS = [
    "bachelor", "bsc", "b.sc", "b.s.",
    "master", "msc", "m.sc", "m.s.", "mba",
    "phd", "ph.d", "doctorate",
    "diploma", "associate",
]


# ---------- Result type ----------

@dataclass
class ParsedJob:
    job_title: str = ""
    company: str = ""
    location: str = ""
    salary: str = ""
    employment_type: str = ""
    remote_type: str = ""

    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    responsibilities: list[str] = field(default_factory=list)
    qualifications: list[str] = field(default_factory=list)

    experience_level: str = ""
    education_requirements: list[str] = field(default_factory=list)

    technologies: list[str] = field(default_factory=list)
    soft_skills: list[str] = field(default_factory=list)

    raw_text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------- Helpers ----------

def _normalize_header_line(line: str) -> str:
    s = line.strip().lower().rstrip(":").strip()
    return re.sub(r"\s+", " ", s)


def _section_block(text: str, keywords: list[str]) -> str:
    """Return the block of text following the first matching header.

    Stops at the next known header (any of `_ALL_HEADER_KEYWORDS`), so blocks
    don't bleed into one another.
    """
    capture = False
    captured: list[str] = []
    for ln in text.split("\n"):
        norm = _normalize_header_line(ln)
        if not norm:
            if capture:
                captured.append("")
            continue
        if any(norm == kw or norm.startswith(kw) for kw in keywords):
            capture = True
            continue
        if capture and any(norm == kw or norm.startswith(kw) for kw in _ALL_HEADER_KEYWORDS):
            break
        if capture:
            captured.append(ln)
    return "\n".join(captured).strip()


def _split_bullet_list(block: str) -> list[str]:
    """Split a section into bullet-style entries (one per line/bullet)."""
    if not block:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for ln in block.split("\n"):
        s = re.sub(r"^[\s\-\*•·–—►]+", "", ln).strip(" .;-")
        if not s or len(s) > 400:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _first_match(pattern: re.Pattern[str], text: str) -> str:
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


def _detect_employment_type(text: str) -> str:
    for canonical, pattern in _EMPLOYMENT_PATTERNS:
        if pattern.search(text):
            return canonical
    return ""


def _detect_remote_type(text: str) -> str:
    for canonical, pattern in _REMOTE_PATTERNS:
        if pattern.search(text):
            return canonical
    return ""


def _detect_experience_level(text: str) -> str:
    for canonical, pattern in _LEVEL_PATTERNS:
        if pattern.search(text):
            return canonical
    m = _YEARS_RE.search(text)
    if m:
        years = int(m.group(1))
        if years <= 1:
            return "junior"
        if years <= 4:
            return "mid-level"
        if years <= 7:
            return "senior"
        return "principal"
    return ""


def _detect_salary(text: str) -> str:
    """Try labelled salary line first, then ranges, then single amounts."""
    labelled = _first_match(_SALARY_HINT_RE, text)
    if labelled:
        return labelled

    m = _SALARY_RANGE_RE.search(text)
    if m:
        cur = m.group("cur") or m.group("cur2") or m.group("cur3") or ""
        period = (m.group("period") or "").strip()
        body = f"{m.group('low').strip()} - {m.group('high').strip()}"
        return f"{cur.strip()} {body} {period}".strip()

    m = _SALARY_SINGLE_RE.search(text)
    if m:
        period = (m.group("period") or "").strip()
        return f"{m.group('cur').strip()}{m.group('amt').strip()} {period}".strip()
    return ""


def _detect_title(text: str) -> str:
    """Prefer 'Job Title:' line, else first short non-sentence line."""
    labelled = _first_match(_TITLE_HINT_RE, text)
    if labelled:
        return labelled
    for ln in text.split("\n"):
        s = ln.strip()
        if not s:
            continue
        if 3 <= len(s) <= 80 and not s.endswith(".") and not s.endswith(":"):
            # Avoid matching contact/location lines that often start the doc.
            if any(c in s for c in "@/"):
                continue
            return s
    return ""


def _extract_education(text: str) -> list[str]:
    """Pull lines mentioning an education keyword; dedup, preserve order."""
    out: list[str] = []
    seen: set[str] = set()
    for ln in text.split("\n"):
        low = ln.lower()
        if any(kw in low for kw in _EDU_KEYWORDS):
            cleaned = re.sub(r"^[\s\-\*•·–—►]+", "", ln).strip(" .;-")
            if cleaned and cleaned.lower() not in seen:
                seen.add(cleaned.lower())
                out.append(cleaned)
    return out


# ---------- Public API ----------

def parse_job_text(text: str) -> ParsedJob:
    """Parse raw JD text into a `ParsedJob` dataclass."""
    cleaned = clean_text(text or "")
    parsed = ParsedJob(raw_text=cleaned)
    if not cleaned:
        return parsed

    # --- 1. Inline metadata
    parsed.job_title = _detect_title(cleaned)
    parsed.company = _first_match(_COMPANY_HINT_RE, cleaned)
    parsed.location = _first_match(_LOCATION_HINT_RE, cleaned)
    parsed.salary = _detect_salary(cleaned)
    parsed.employment_type = _detect_employment_type(cleaned)
    parsed.remote_type = _detect_remote_type(cleaned)
    parsed.experience_level = _detect_experience_level(cleaned)

    # --- 2. Section blocks
    req_block = _section_block(cleaned, _SECTION_KEYS["required"])
    pref_block = _section_block(cleaned, _SECTION_KEYS["preferred"])
    resp_block = _section_block(cleaned, _SECTION_KEYS["responsibilities"])
    qual_block = _section_block(cleaned, _SECTION_KEYS["qualifications"])
    edu_block = _section_block(cleaned, _SECTION_KEYS["education"])

    parsed.responsibilities = _split_bullet_list(resp_block)
    parsed.qualifications = _split_bullet_list(qual_block)
    parsed.education_requirements = (
        _split_bullet_list(edu_block) if edu_block else _extract_education(cleaned)
    )

    # --- 3. Skill extraction via dictionary
    # Inline "Bonus:" / "Nice to have:" lines inside the required/qualifications
    # blocks should feed preferred_skills, not required.
    inline_bonus_lines: list[str] = []
    cleaned_required_lines: list[str] = []
    inline_bonus_re = re.compile(
        r"^\s*(?:bonus|nice[\s\-]?to[\s\-]?have|plus|good\s+to\s+have)\s*[:\-]\s*(.+)$",
        re.IGNORECASE,
    )
    for source in (req_block, qual_block):
        for ln in source.split("\n"):
            m = inline_bonus_re.search(re.sub(r"^[\s\-\*•·–—►]+", "", ln))
            if m:
                inline_bonus_lines.append(m.group(1))
            else:
                cleaned_required_lines.append(ln)

    req_text = "\n".join(cleaned_required_lines).strip()
    pref_text = (pref_block + "\n" + "\n".join(inline_bonus_lines)).strip()

    required_from_block = find_technical_skills(req_text)
    preferred_from_block = find_technical_skills(pref_text)

    # Anything found across the whole JD becomes the technologies list.
    all_tech = find_technical_skills(cleaned)

    # If no required block was found, treat all extracted skills as required
    # so downstream scoring still has something to compare against. Otherwise
    # keep the required vs preferred split as detected.
    if required_from_block:
        parsed.required_skills = required_from_block
    else:
        # Skills found in responsibilities are usually still required-ish.
        parsed.required_skills = all_tech if not preferred_from_block else [
            s for s in all_tech if s not in preferred_from_block
        ]

    parsed.preferred_skills = [s for s in preferred_from_block if s not in parsed.required_skills]
    parsed.technologies = all_tech
    parsed.soft_skills = find_soft_skills(cleaned)

    # --- 4. Fallback: if structured blocks missed obvious comma lists in the
    # required block (e.g. "Skills: Python, FastAPI, SQL"), pull those too.
    if req_block and not parsed.required_skills:
        parsed.required_skills = split_csv_like(req_block)

    return parsed
