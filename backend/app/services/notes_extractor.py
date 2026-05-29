"""Extract structured CV entries from free-form notes.

The unified-uploader chatbox lets the user dump arbitrary prose
("I built Patina, an iOS AR app with a LangGraph backend…"). The
deterministic block parser only finds projects under explicit
`## Selected Projects` / `### Title` markers, so pure prose yields
nothing.

This service runs ONE LLM call per notes document → structured
projects / skills / experience / publications / certifications the
builder folds into the master library.

Cached by sha1(text) so repeated rebuilds don't re-burn tokens.
Returns empty CVLibraryBase on LLM-off / failure (builder then just
skips the notes — no crash).
"""
from __future__ import annotations

import hashlib
import json
import logging

from pydantic import BaseModel, Field, ValidationError

from app.models.schemas import (
    CertificationEntry,
    CVLibraryBase,
    ExperienceEntryLib,
    ProjectEntry,
    PublicationEntry,
)

logger = logging.getLogger("ai_job_cv_matcher.notes_extractor")

_CACHE: dict[str, CVLibraryBase] = {}


class _LLMProject(BaseModel):
    title: str
    period: str = ""
    highlights: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class _LLMExperience(BaseModel):
    title: str
    company: str = ""
    period: str = ""
    highlights: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class _LLMPublication(BaseModel):
    title: str
    status: str = ""
    venue: str = ""


class _LLMCert(BaseModel):
    issuer: str = ""
    name: str


class _LLMOutput(BaseModel):
    projects: list[_LLMProject] = Field(default_factory=list)
    experience: list[_LLMExperience] = Field(default_factory=list)
    publications: list[_LLMPublication] = Field(default_factory=list)
    certifications: list[_LLMCert] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


def extract_entries(text: str) -> CVLibraryBase:
    """Free-form notes → structured CVLibraryBase fragment. Empty on
    failure. Cached per text hash."""
    text = (text or "").strip()
    if not text:
        return CVLibraryBase()

    key = hashlib.sha1(text.encode("utf-8")).hexdigest()
    if key in _CACHE:
        return _CACHE[key]

    from app.services import llm_extraction_service as llm
    if not llm.is_enabled():
        return CVLibraryBase()

    system = (
        "Extract structured CV entries from free-form notes a "
        "candidate dumped about their work. Return JSON:\n"
        '{"projects": [{"title": str, "period": str, '
        '"highlights": [str, ...], "tags": [str, ...]}], '
        '"experience": [{"title": str, "company": str, "period": str, '
        '"highlights": [str], "tags": [str]}], '
        '"publications": [{"title": str, "status": str, "venue": str}], '
        '"certifications": [{"issuer": str, "name": str}], '
        '"skills": [str, ...]}\n\n'
        "Rules:\n"
        "1. A coherent named product / system / app = ONE project. "
        "Pull the name as title (e.g. 'Patina'). 3-5 highlight "
        "bullets, strong-verb first (Built, Designed, Integrated, "
        "Delivered). Keep the candidate's real claims — do not invent.\n"
        "2. tags = 5-8 specific tech/domain nouns FROM the text "
        "(frameworks, models, techniques). Prefer multi-word "
        "canonical forms (\"multi-agent pipeline\", \"retrieval-"
        "augmented generation\").\n"
        "3. If the notes contain a 'X-Y-Z' / accomplishment-formula "
        "block, fold those into the SAME project's highlights — don't "
        "make a duplicate project.\n"
        "4. skills = flat list of every tool / library / framework / "
        "technique named (SwiftUI, ARKit, LangGraph, BLIP-2, CLIP, "
        "Whisper, pgvector, etc.).\n"
        "5. Only emit experience/publications/certifications when the "
        "notes clearly describe a job / paper / credential. Empty "
        "arrays otherwise.\n"
        "6. period: use the year(s) if stated, else empty string."
    )

    try:
        raw = llm._chat_completion(  # type: ignore[attr-defined]
            [
                {"role": "system", "content": system},
                {"role": "user", "content": text[:6000]},
            ],
            json_mode=True,
        )
        data = json.loads(raw)
        parsed = _LLMOutput.model_validate(data)
    except (json.JSONDecodeError, ValidationError, Exception) as exc:  # noqa: BLE001
        logger.warning("Notes extractor failed: %s", exc)
        _CACHE[key] = CVLibraryBase()
        return _CACHE[key]

    out = CVLibraryBase(
        selected_projects=[
            ProjectEntry(
                title=p.title.strip(),
                period=p.period.strip(),
                highlights=[h for h in p.highlights if h.strip()],
                tags=[t for t in p.tags if t.strip()][:8],
            )
            for p in parsed.projects if p.title.strip()
        ],
        experience=[
            ExperienceEntryLib(
                title=x.title.strip(),
                company=x.company.strip(),
                period=x.period.strip(),
                highlights=[h for h in x.highlights if h.strip()],
                tags=[t for t in x.tags if t.strip()][:8],
            )
            for x in parsed.experience if x.title.strip()
        ],
        publications=[
            PublicationEntry(title=pb.title.strip(), status=pb.status.strip(), venue=pb.venue.strip())
            for pb in parsed.publications if pb.title.strip()
        ],
        certifications=[
            CertificationEntry(issuer=c.issuer.strip(), name=c.name.strip())
            for c in parsed.certifications if c.name.strip()
        ],
    )
    # Skills go into a single ad-hoc group; the skill_categorizer
    # re-buckets everything on the full library afterwards.
    skills = [s.strip() for s in parsed.skills if s.strip()]
    if skills:
        from app.models.schemas import SkillGroup
        out.skills_groups = [SkillGroup(label="From Notes", items=skills)]

    logger.info(
        "Notes extractor: %d projects, %d skills, %d experience from %d-char note",
        len(out.selected_projects), len(skills), len(out.experience), len(text),
    )
    _CACHE[key] = out
    return out
