"""Project the unified CV library into a duck-typed `CV`-shaped object.

The matcher (`match_cv_to_job`, `rank_cvs`, agent ranking, tailor) was
originally written to iterate per-CV-row. Now that the library is the
canonical merged view of every upload, downstream features should rank
*one* composite candidate, not iterate over raw files.

This module exposes:

    library_to_candidate(library) -> SimpleNamespace

returning an object that satisfies every attribute the matcher reads:
`id`, `name`, `filename`, `summary`, `skills`, `experience`, `projects`,
`education`, `certifications`, `languages`, `raw_text`. `id` is set to
`-1` so it never collides with a real `cvs.id` from the DB.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.models.schemas import CVLibraryOut


def library_to_candidate(library: CVLibraryOut) -> SimpleNamespace:
    """Synthesise a CV-shaped object from the merged library.

    All matcher consumers (skill, semantic, experience, education,
    project sub-scores) need flat string lists; the library stores rich
    nested objects. Flatten consistently so the matcher's existing logic
    works unchanged.
    """
    # Skills: union across all groups (matcher expects flat list[str]).
    skills: list[str] = []
    seen: set[str] = set()
    for g in library.skills_groups or []:
        for item in g.items or []:
            key = (item or "").strip().lower()
            if key and key not in seen:
                seen.add(key)
                skills.append(item.strip())

    # Experience: each entry → "Title — Company (Period). Bullets joined."
    experience: list[str] = []
    for x in library.experience or []:
        head = " — ".join(filter(None, [x.title, x.company]))
        if x.period:
            head = f"{head} ({x.period})"
        body = " ".join(x.highlights or [])
        experience.append(f"{head}. {body}".strip())
        # Also surface each highlight on its own line so the matcher's
        # bullet-level scoring (strongest_points) has granular text.
        for h in (x.highlights or []):
            if h.strip():
                experience.append(h.strip())

    # Projects: include both selected + additional in one flat list.
    projects: list[str] = []
    for p in (library.selected_projects or []) + (library.additional_projects or []):
        head = p.title or ""
        if p.period:
            head = f"{head} ({p.period})"
        body = " ".join(p.highlights or [])
        projects.append(f"{head}. {body}".strip())

    # Education / certifications / languages: flat strings.
    education = [
        " — ".join(filter(None, [e.institution, e.degree, e.period]))
        for e in (library.education or [])
    ]
    certifications = [
        f"{c.issuer}: {c.name}" if c.issuer else c.name
        for c in (library.certifications or [])
    ]
    languages = list(library.languages or [])

    # `raw_text` is what the semantic scorer + BoW fallback hash on, so
    # it needs every signal-bearing token. Concatenate everything.
    raw_chunks: list[str] = [
        (library.header.name or ""),
        (library.summary or ""),
        ", ".join(skills),
        "\n".join(experience),
        "\n".join(projects),
        "\n".join(education),
        "\n".join(certifications),
        "\n".join(p.title for p in (library.publications or [])),
        ", ".join(languages),
    ]
    raw_text = "\n".join(filter(None, raw_chunks))

    return SimpleNamespace(
        id=-1,
        filename="(unified library)",
        name=library.header.name or "",
        summary=library.summary or "",
        skills=skills,
        experience=experience,
        projects=projects,
        education=education,
        certifications=certifications,
        languages=languages,
        raw_text=raw_text,
    )


def _library_to_out(row: Any) -> CVLibraryOut:
    """ORM-row → Pydantic shim — matches the helper in cv_render_routes."""
    from datetime import datetime as _dt

    return CVLibraryOut(
        id=int(row.id),
        header=row.header or {},
        summary=row.summary or "",
        skills_groups=row.skills_groups or [],
        education=row.education or [],
        selected_projects=row.selected_projects or [],
        additional_projects=row.additional_projects or [],
        experience=row.experience or [],
        publications=row.publications or [],
        certifications=row.certifications or [],
        languages=row.languages or [],
        updated_at=row.updated_at or _dt.utcnow(),
    )


def get_unified_candidate(db) -> SimpleNamespace | None:
    """Convenience: pull the library row and project it. None if no library."""
    from app.models.db_models import CVLibrary

    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None:
        return None
    return library_to_candidate(_library_to_out(row))
