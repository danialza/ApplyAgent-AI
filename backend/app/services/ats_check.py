"""ATS-parseability check for the rendered CV PDF.

Applicant-tracking systems re-extract plain text from the PDF. If that
extraction loses the contact line, mangles section headers, or produces
garbled glyphs, the application is silently filtered before a human ever
sees it. This module runs the same kind of extraction (pdfminer) against
the freshly compiled PDF and scores what survived.

Deterministic, no LLM. Returns (score 0-100, issues list). Never raises —
a failure to check returns (-1, [reason]) and the render proceeds.
"""
from __future__ import annotations

import base64
import logging
import re
from io import BytesIO

logger = logging.getLogger("ai_job_cv_matcher.ats_check")

# Section headers the template emits — an ATS keys its parsing on these.
_EXPECTED_SECTIONS = [
    "professional experience",
    "education",
    "projects",
    "skills",
]


def check_pdf_b64(pdf_b64: str, *, header: dict | None = None) -> tuple[int, list[str]]:
    """Score how well an ATS would re-extract this PDF.

    `header` is the library header dict (name/email/phone) so we can
    verify the contact line survives extraction.
    """
    try:
        raw = base64.b64decode(pdf_b64 or "")
        if not raw:
            return -1, ["No PDF bytes to check."]
    except Exception:  # noqa: BLE001
        return -1, ["PDF payload was not valid base64."]

    try:
        from pdfminer.high_level import extract_text
        text = extract_text(BytesIO(raw)) or ""
    except Exception as exc:  # noqa: BLE001
        return 0, [f"Text extraction failed entirely ({exc}) — most ATS would reject this file."]

    text_low = text.lower()
    issues: list[str] = []
    score = 100

    # 1. Enough text at all? A 2-page CV should extract well over 1500 chars.
    if len(text.strip()) < 800:
        score -= 40
        issues.append(
            f"Only {len(text.strip())} characters extracted — the PDF may be image-based or badly encoded."
        )

    # 2. Contact details survive extraction.
    h = header or {}
    name = (h.get("name") or "").strip()
    email = (h.get("email") or "").strip()
    phone = re.sub(r"[^\d+]", "", h.get("phone") or "")
    if name and name.lower() not in text_low:
        score -= 20
        issues.append(f"Name '{name}' not found in extracted text.")
    if email and email.lower() not in text_low:
        score -= 15
        issues.append(f"Email '{email}' not found in extracted text.")
    if phone and phone not in re.sub(r"[^\d+]", "", text):
        score -= 10
        issues.append("Phone number not found in extracted text.")

    # 3. Section headers survive (ATS anchors parsing on them).
    missing_sections = [s for s in _EXPECTED_SECTIONS if s not in text_low]
    if missing_sections:
        score -= 8 * len(missing_sections)
        issues.append("Section header(s) not extracted: " + ", ".join(missing_sections) + ".")

    # 4. Glyph garbage ratio — ligature/encoding failures show up as
    # replacement chars or (cid:NN) artifacts in pdfminer output.
    cid_hits = len(re.findall(r"\(cid:\d+\)", text))
    repl_hits = text.count("�")
    if cid_hits + repl_hits > 5:
        score -= 25
        issues.append(
            f"{cid_hits + repl_hits} unmapped glyphs — an ATS will see garbage where those characters are."
        )

    # 5. Reasonable word count (catches columns collapsing into soup).
    words = re.findall(r"[a-zA-Z]{2,}", text)
    if len(words) < 150:
        score -= 15
        issues.append(f"Only {len(words)} words extracted — layout may be collapsing under extraction.")

    return max(0, min(100, score)), issues
