"""Shared PDF / DOCX / TXT byte-buffer → plain-text helper.

The profile aggregator and the JD-from-file endpoint both need this. Kept
in `utils` so neither depends on the other's router code.
"""
from __future__ import annotations

from app.services.cv_parser import extract_text_from_docx, extract_text_from_pdf
from app.utils.text_cleaning import clean_text


def extract_document_text(data: bytes, ext: str) -> str:
    """Return cleaned text for a PDF, DOCX, or TXT byte buffer.

    Raises `ValueError` for any other extension. Callers are expected to
    validate the extension first via `file_validation.ALLOWED_*` so this
    branch only fires on programmer error.
    """
    ext = ext.lower()
    if ext == ".pdf":
        return extract_text_from_pdf(data)
    if ext == ".docx":
        return extract_text_from_docx(data)
    if ext == ".txt":
        return clean_text(data.decode("utf-8", errors="replace"))
    raise ValueError(f"Unsupported document extension: {ext}")
