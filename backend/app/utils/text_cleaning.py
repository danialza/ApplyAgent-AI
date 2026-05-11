"""Text utilities used by the parser and matcher.

Grouped helpers:
  * Whitespace / unicode normalization (`clean_text`, `strip_bullet`).
  * Tokenization for similarity scoring (`tokenize`).
  * Contact extraction (`extract_email`, `extract_phone`, `extract_urls`).
  * URL classification (`classify_url`).
  * Section-header utilities (`normalize_header`, `is_section_header`).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable

# ---------- whitespace / bullets ----------

_WS_RE = re.compile(r"[ \t\f\v]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")
_BULLET_RE = re.compile(r"^[\s\-\*•●▪○·–—►▶✓✔]+")


def clean_text(text: str) -> str:
    """Normalize unicode and collapse repeated whitespace.

    Preserves single newlines (used by section detection) but collapses runs.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WS_RE.sub(" ", ln).strip() for ln in text.split("\n")]
    text = "\n".join(lines)
    text = _MULTI_NL_RE.sub("\n\n", text)
    return text.strip()


def strip_bullet(line: str) -> str:
    """Remove leading bullet/dash markers from a line."""
    return _BULLET_RE.sub("", line).strip()


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, length >= 2. Used for overlap scores."""
    return [t for t in re.findall(r"[A-Za-z0-9\+\#\.]{2,}", text.lower())]


# ---------- contact ----------

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Phone: optional +, then groups of digits with separators, total 8-20 digits.
PHONE_RE = re.compile(
    r"""
    (?<!\w)                    # not in the middle of a word
    (\+?\d[\d\s().\-]{7,}\d)   # leading digit, separators, trailing digit
    (?!\w)
    """,
    re.VERBOSE,
)

URL_RE = re.compile(r"(https?://[^\s)]+|www\.[^\s)]+|[A-Za-z0-9\-]+\.(?:com|io|dev|me|net|org|ai|co)/[^\s)]+)")

# Bare-domain personal sites without a path, e.g. "janedoe.dev". Matched
# separately so we don't pollute generic URL extraction.
BARE_DOMAIN_RE = re.compile(
    r"(?<![A-Za-z0-9@.\-/])([A-Za-z0-9][A-Za-z0-9\-]{1,40}\.(?:dev|me|io|ai|co|com|net|org|app|tech|page|site))(?![A-Za-z0-9/])",
    re.IGNORECASE,
)
_BARE_DOMAIN_BLOCKLIST = {"linkedin.com", "github.com", "gmail.com", "outlook.com", "yahoo.com", "hotmail.com"}

LINKEDIN_RE = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/(?:in|pub)/[A-Za-z0-9_\-%./]+", re.IGNORECASE)
GITHUB_RE = re.compile(r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-.]*)?", re.IGNORECASE)


def extract_email(text: str) -> str:
    m = EMAIL_RE.search(text or "")
    return m.group(0) if m else ""


def extract_phone(text: str) -> str:
    """Return the first plausible phone number, with whitespace tidied."""
    if not text:
        return ""
    for m in PHONE_RE.finditer(text):
        candidate = m.group(1)
        digits = re.sub(r"\D", "", candidate)
        if 8 <= len(digits) <= 15:
            return re.sub(r"\s+", " ", candidate).strip()
    return ""


def extract_urls(text: str) -> list[str]:
    """Return de-duplicated list of URLs found in `text`."""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip(".,);")
        key = url.lower()
        if key not in seen:
            seen.add(key)
            out.append(url)
    return out


def _ensure_scheme(url: str) -> str:
    return url if url.lower().startswith(("http://", "https://")) else "https://" + url


def classify_url(url: str) -> str:
    """Return one of: 'linkedin', 'github', 'portfolio'."""
    low = url.lower()
    if "linkedin.com" in low:
        return "linkedin"
    if "github.com" in low:
        return "github"
    return "portfolio"


def extract_contacts(text: str) -> dict[str, object]:
    """Pull email/phone/LinkedIn/GitHub/portfolio links from a CV blob.

    Returns keys:
        email, phone, linkedin, github, portfolio (all str — empty if missing)
        websites: list[str] of any other URLs (kept for completeness).
    """
    email = extract_email(text)
    phone = extract_phone(text)

    linkedin = ""
    m = LINKEDIN_RE.search(text or "")
    if m:
        linkedin = _ensure_scheme(m.group(0))

    github = ""
    m = GITHUB_RE.search(text or "")
    if m:
        github = _ensure_scheme(m.group(0))

    portfolio = ""
    other: list[str] = []
    for url in extract_urls(text):
        kind = classify_url(url)
        if kind == "portfolio":
            full = _ensure_scheme(url)
            if not portfolio:
                portfolio = full
            else:
                other.append(full)

    # Fallback: bare domain like "janedoe.dev" with no path component.
    if not portfolio:
        for m in BARE_DOMAIN_RE.finditer(text or ""):
            domain = m.group(1)
            low = domain.lower()
            # Skip domains that come from email addresses (already captured).
            if email and email.lower().endswith("@" + low):
                continue
            if low in _BARE_DOMAIN_BLOCKLIST:
                continue
            portfolio = _ensure_scheme(domain)
            break

    return {
        "email": email,
        "phone": phone,
        "linkedin": linkedin,
        "github": github,
        "portfolio": portfolio,
        "websites": other,
    }


# ---------- section headers ----------

# Canonical name -> list of accepted header variants (lowercased, no colons).
SECTION_HEADERS: dict[str, list[str]] = {
    "summary": [
        "summary", "professional summary", "profile", "professional profile",
        "about me", "about", "objective", "career objective", "personal statement",
        "executive summary",
    ],
    "skills": [
        "skills", "technical skills", "core competencies", "competencies",
        "key skills", "areas of expertise", "technologies", "tech stack",
        "tools", "programming languages",
    ],
    "experience": [
        "experience", "work experience", "professional experience",
        "employment", "employment history", "work history", "career",
        "career history", "professional background",
    ],
    "education": [
        "education", "academic background", "academic qualifications",
        "qualifications", "educational background",
    ],
    "selected_projects": [
        "selected projects", "selected ai projects", "selected ai engineering projects",
        "key projects", "featured projects", "highlighted projects",
        "notable projects",
    ],
    "additional_projects": [
        "additional projects", "additional technical projects", "other projects",
        "side projects", "personal projects", "open source projects",
        "open-source projects",
    ],
    # Generic fallback for CVs that have only one Projects header.
    "projects": [
        "projects", "project experience",
        "ai projects", "automation projects",
        "ai automation projects", "ai engineering projects",
        "ai applications", "applied projects",
    ],
    "certifications": [
        "certifications", "certificates", "licenses",
        "courses", "training", "professional development",
    ],
    "publications": [
        "publications", "papers", "research", "research papers",
        "academic publications", "selected publications",
    ],
    "languages": [
        "languages", "language proficiency", "spoken languages",
    ],
}

# Reverse map: variant -> canonical, for O(1) lookups.
_HEADER_LOOKUP: dict[str, str] = {
    variant: canonical
    for canonical, variants in SECTION_HEADERS.items()
    for variant in variants
}


def normalize_header(line: str) -> str:
    """Lowercase, strip punctuation/colons, collapse spaces. Used pre-lookup."""
    if not line:
        return ""
    s = line.strip().lower().rstrip(":").strip()
    s = re.sub(r"[^a-z\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_section_header(line: str, max_len: int = 60) -> str | None:
    """Return canonical section name if `line` looks like a header, else None.

    A header line is short (<= max_len), on its own line, and matches a
    known variant either exactly or as a substring. The substring path
    catches real-world variants like:

      * "Selected AI, Robotics & Software Projects" → selected_projects
      * "AI & Automation Projects"                  → projects
      * "Work Experience & Career History"          → experience
      * "Additional Background & Hobbies"           → additional_projects
        (only when the variant is the dominant header noun)

    Hit-priority: exact match > longest-variant containment. The
    "longest first" tiebreaker prevents "projects" (5 chars) from
    shadowing "selected ai projects" (20 chars) in a header that
    contains both.
    """
    if not line or len(line) > max_len:
        return None
    norm = normalize_header(line)
    if not norm:
        return None
    # 1. Exact match first — fastest, no ambiguity.
    if norm in _HEADER_LOOKUP:
        return _HEADER_LOOKUP[norm]
    # 2. Token-subset match for multi-word variants, longest variant
    # first so {selected, ai, projects} beats {projects} when both fit.
    header_tokens = set(norm.split())
    for variant, canonical in sorted(_HEADER_LOOKUP.items(), key=lambda kv: -len(kv[0])):
        variant_tokens = set(variant.split())
        if len(variant_tokens) >= 2 and variant_tokens.issubset(header_tokens):
            return canonical
    # 3. Last resort: single-token variant exactly matches the line.
    if " " not in norm and norm in _HEADER_LOOKUP:
        return _HEADER_LOOKUP[norm]
    return None


def split_csv_like(block: str, max_item_len: int = 80) -> list[str]:
    """Split a comma/newline/pipe/semicolon block into a deduped list."""
    if not block:
        return []
    parts = re.split(r"[,\n;|/]+", block)
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        s = strip_bullet(p).strip(" .-")
        if not s or len(s) > max_item_len:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def split_entries(block: str) -> list[str]:
    """Split a multi-line section into bullet-style logical entries."""
    if not block:
        return []
    entries: list[str] = []
    buf: list[str] = []
    for ln in block.split("\n"):
        s = strip_bullet(ln).strip()
        if not s:
            if buf:
                entries.append(" ".join(buf).strip())
                buf = []
            continue
        if ln.lstrip().startswith(("-", "*", "•", "·", "–", "—")):
            if buf:
                entries.append(" ".join(buf).strip())
                buf = []
            buf.append(s)
        else:
            buf.append(s)
    if buf:
        entries.append(" ".join(buf).strip())
    return [e for e in entries if e]


def remove_contact_lines(lines: Iterable[str]) -> list[str]:
    """Drop lines that are mostly contact info — useful before name/summary fallback."""
    cleaned: list[str] = []
    for ln in lines:
        if EMAIL_RE.search(ln) or URL_RE.search(ln):
            continue
        if PHONE_RE.search(ln) and len(re.sub(r"[^A-Za-z]", "", ln)) < 5:
            # Mostly digits → phone line.
            continue
        cleaned.append(ln)
    return cleaned
