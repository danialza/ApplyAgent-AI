"""Enrich a project entry from a URL + free-form notes via LLM.

User supplies: title, optional URL (GitHub repo / paper / demo / web),
optional period, optional notes, optional tag hints. We:

  1. Fetch the URL via the existing `web_ingest` machinery (handles
     GitHub repos, generic web pages, gracefully degrades on 404).
  2. Compose a single LLM call combining title + fetched text + notes.
  3. Return a fully populated ProjectEntry (highlights, tags, period,
     url) ready to append to the master library.

Returns (entry, source_url_used, error). On LLM-off / failure we still
return a best-effort entry built from the user input alone so the
endpoint never hard-fails — the user can edit afterward.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.models.schemas import ProjectEntry

logger = logging.getLogger("ai_job_cv_matcher.project_enricher")


class _LLMProjectEnriched(BaseModel):
    title: str
    period: str = ""
    highlights: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


def enrich_project(
    *,
    title: str,
    url: str = "",
    period: str = "",
    notes: str = "",
    tag_hints: list[str] | None = None,
    jd_text: str = "",
) -> tuple[ProjectEntry, str, str]:
    """Return (entry, fetched_url, error). `error` empty on success.

    `jd_text` is optional — when supplied, the LLM is told to bias
    bullet vocabulary toward that JD's archetype (skill nouns, role
    terms). Empty string disables JD-biasing.
    """
    title = (title or "").strip()
    if not title:
        raise ValueError("title required")

    fetched_text = ""
    fetched_url = ""
    fetch_error = ""
    if (url or "").strip():
        try:
            from app.services.web_ingest import ingest
            _kind, raw, _ext, err = ingest(url.strip())
            fetched_text = (raw or "").strip()[:8000]
            fetched_url = url.strip()
            fetch_error = err or ""
        except Exception as exc:  # noqa: BLE001
            fetch_error = f"web fetch failed: {exc}"
            logger.warning("project enricher: %s", fetch_error)

    from app.services import llm_extraction_service as llm
    if not llm.is_enabled():
        # No LLM — return a minimal entry built from raw inputs.
        return (
            _fallback_entry(title, period, notes, tag_hints, fetched_url),
            fetched_url,
            "LLM disabled — returned raw entry",
        )

    system = (
        "Extract a single structured CV project entry from the user's "
        "inputs (title, optional URL content, optional free-form notes, "
        "optional tag hints). Return JSON:\n"
        '{"title": str, "period": str, "highlights": [str, ...], '
        '"tags": [str, ...]}\n\n'
        "Rules:\n"
        "1. title: keep the user's title verbatim (do not paraphrase).\n"
        "2. highlights: 3-5 strong-verb bullets ('Built', 'Designed', "
        "'Trained', 'Shipped', 'Benchmarked'). Anchor each on a "
        "concrete tool / model / outcome from the fetched text or "
        "notes. NEVER invent metrics, employers, or dates not in the "
        "inputs.\n"
        "3. tags: 5-10 specific tech/domain nouns mined from the "
        "inputs (frameworks, models, techniques). Prefer canonical "
        "multi-word forms (\"retrieval-augmented generation\", "
        "\"vision-language model\").\n"
        "4. period: use the year(s) from the inputs if present, else "
        "echo the user-supplied period, else empty string.\n"
        "5. Output STRICT JSON — no markdown fences, no prose, no "
        "comments. First char `{`, last char `}`."
    )
    if jd_text.strip():
        system += (
            "\n6. JD CONTEXT (bias bullet vocab toward this JD's "
            "archetype where the project's actual stack supports it; "
            "do not invent claims):\n" + jd_text.strip()[:1500]
        )

    user_blob = _compose_user_blob(
        title=title,
        url=fetched_url,
        period=period,
        notes=notes,
        tag_hints=tag_hints or [],
        fetched_text=fetched_text,
        fetch_error=fetch_error,
    )

    try:
        raw = llm._chat_completion(  # type: ignore[attr-defined]
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user_blob[:12000]},
            ],
            json_mode=True,
        )
        data = json.loads(raw)
        parsed = _LLMProjectEnriched.model_validate(data)
    except (json.JSONDecodeError, ValidationError, Exception) as exc:  # noqa: BLE001
        logger.warning("project enricher LLM failed: %s", exc)
        return (
            _fallback_entry(title, period, notes, tag_hints, fetched_url),
            fetched_url,
            f"LLM failed: {exc}",
        )

    entry = ProjectEntry(
        title=parsed.title.strip() or title,
        period=(parsed.period or period).strip(),
        highlights=[h.strip() for h in parsed.highlights if h and h.strip()],
        tags=[t.strip() for t in parsed.tags if t and t.strip()][:10],
        url=fetched_url,
    )
    logger.info(
        "project enricher: '%s' → %d highlights, %d tags (url=%s)",
        entry.title, len(entry.highlights), len(entry.tags),
        fetched_url or "-",
    )
    return entry, fetched_url, ""


def _compose_user_blob(
    *,
    title: str,
    url: str,
    period: str,
    notes: str,
    tag_hints: list[str],
    fetched_text: str,
    fetch_error: str,
) -> str:
    lines = [f"TITLE: {title}"]
    if period:
        lines.append(f"PERIOD: {period}")
    if url:
        lines.append(f"URL: {url}")
    if tag_hints:
        lines.append(f"TAG HINTS: {', '.join(tag_hints)}")
    if notes:
        lines.append("\nUSER NOTES:")
        lines.append(notes.strip())
    if fetched_text:
        lines.append("\nFETCHED CONTENT (from URL):")
        lines.append(fetched_text)
    if fetch_error and not fetched_text:
        lines.append(f"\n[URL fetch failed: {fetch_error}]")
    return "\n".join(lines)


def _fallback_entry(
    title: str,
    period: str,
    notes: str,
    tag_hints: list[str] | None,
    url: str,
) -> ProjectEntry:
    highlights = []
    if notes.strip():
        # Split notes into sentence-ish bullets so the entry isn't a
        # single wall of text. Trims to 5.
        for chunk in notes.replace("\r", "").split("\n"):
            chunk = chunk.strip(" -•*")
            if chunk:
                highlights.append(chunk[:240])
            if len(highlights) >= 5:
                break
    return ProjectEntry(
        title=title,
        period=period.strip(),
        highlights=highlights,
        tags=[t.strip() for t in (tag_hints or []) if t.strip()][:10],
        url=url,
    )
