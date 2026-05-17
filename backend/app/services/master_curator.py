"""LLM-driven curation pass over the entire master library.

Runs ONE LLM call that returns curation decisions for every section:

  projects        — group near-duplicates, drop noise (profile repos,
                    forks, empty entries).
  experience      — collapse near-dupe role+company entries, drop
                    placeholder titles.
  education       — collapse corrupt rows for the same school, prefer
                    cleanest institution + degree spelling.
  certifications  — group same credential mentioned by different names.
  publications    — group same paper across sources.
  summary         — pick the strongest existing summary OR rewrite a
                    tighter one ONLY when current is empty / very weak.
  header          — fill missing email / linkedin / github when another
                    source has them.

Falls back to the input library on any LLM failure. Never raises.
Skill dedup is deterministic (handled separately in
skill_categorizer) since "ROS 2" / "ROS2" are obvious.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.models.schemas import (
    CertificationEntry,
    CVLibraryBase,
    EducationEntry,
    ExperienceEntryLib,
    ProjectEntry,
    PublicationEntry,
)

logger = logging.getLogger("ai_job_cv_matcher.master_curator")

MAX_ENTRIES_PER_SECTION = 80


# ---------- LLM schema ----------

class _Group(BaseModel):
    canonical_title: str
    member_indices: list[int] = Field(default_factory=list)
    reason: str = ""


class _SectionDecision(BaseModel):
    groups: list[_Group] = Field(default_factory=list)
    drop_indices: list[int] = Field(default_factory=list)
    drop_reasons: dict[str, str] = Field(default_factory=dict)


class _CuratorFullOutput(BaseModel):
    projects: _SectionDecision = Field(default_factory=_SectionDecision)
    experience: _SectionDecision = Field(default_factory=_SectionDecision)
    education: _SectionDecision = Field(default_factory=_SectionDecision)
    certifications: _SectionDecision = Field(default_factory=_SectionDecision)
    publications: _SectionDecision = Field(default_factory=_SectionDecision)
    summary_rewrite: str = ""        # empty → keep existing
    header_patches: dict[str, str] = Field(default_factory=dict)


# ---------- Public entry ----------

def curate_projects(library: CVLibraryBase) -> CVLibraryBase:
    """Backwards-compatible name. Now curates the WHOLE library."""
    return curate_library(library)


def curate_library(library: CVLibraryBase) -> CVLibraryBase:
    from app.services import llm_extraction_service as llm

    if not llm.is_enabled():
        logger.info("Master curator: LLM disabled, returning library unchanged")
        return library

    # Aggregate per-section views into a single digest.
    digest, items_by_section = _build_full_digest(library)
    if not any(items_by_section.values()):
        return library

    parsed = _llm_call(digest)
    if parsed is None:
        return library

    new_lib = library.model_copy(deep=True)

    # PROJECTS — selected + additional combined into one index space.
    proj_items = items_by_section["projects"]
    if proj_items:
        new_lib.selected_projects, new_lib.additional_projects = _apply_project_decision(
            proj_items, parsed.projects,
        )

    # EXPERIENCE / EDUCATION / CERTIFICATIONS / PUBLICATIONS — single bucket each.
    new_lib.experience = _apply_simple_section(
        items_by_section["experience"], parsed.experience, ExperienceEntryLib,
        title_field="title", merge_company=True,
    )
    new_lib.education = _apply_simple_section(
        items_by_section["education"], parsed.education, EducationEntry,
        title_field="institution", merge_company=False,
    )
    new_lib.certifications = _apply_simple_section(
        items_by_section["certifications"], parsed.certifications, CertificationEntry,
        title_field="name", merge_company=False,
    )
    new_lib.publications = _apply_simple_section(
        items_by_section["publications"], parsed.publications, PublicationEntry,
        title_field="title", merge_company=False,
    )

    # SUMMARY — rewrite only when LLM offered something AND current is
    # weak (empty or < 80 chars). Never overwrite a strong existing.
    if parsed.summary_rewrite and len((library.summary or "").strip()) < 80:
        new_lib.summary = parsed.summary_rewrite.strip()

    # HEADER patches — fill empty fields only.
    if parsed.header_patches:
        for k, v in parsed.header_patches.items():
            if not v or not k:
                continue
            current = getattr(new_lib.header, k, None)
            if current is None:
                continue
            if not str(current).strip():
                try:
                    setattr(new_lib.header, k, v.strip())
                except Exception:  # noqa: BLE001
                    pass

    return new_lib


# ---------- Digest building ----------

def _build_full_digest(library: CVLibraryBase) -> tuple[dict, dict[str, list]]:
    """Returns (llm_digest, items_by_section) where items_by_section
    holds the live entry objects keyed in the same index space the
    LLM sees."""
    project_items: list[tuple[str, ProjectEntry]] = []
    for p in (library.selected_projects or []):
        project_items.append(("selected", p))
    for p in (library.additional_projects or []):
        project_items.append(("additional", p))
    project_items = project_items[:MAX_ENTRIES_PER_SECTION]

    experience_items = list(library.experience or [])[:MAX_ENTRIES_PER_SECTION]
    education_items = list(library.education or [])[:MAX_ENTRIES_PER_SECTION]
    cert_items = list(library.certifications or [])[:MAX_ENTRIES_PER_SECTION]
    pub_items = list(library.publications or [])[:MAX_ENTRIES_PER_SECTION]

    digest = {
        "projects": [
            {
                "idx": i,
                "bucket": bucket,
                "title": p.title,
                "period": p.period or "",
                "tags": list(p.tags or [])[:6],
                "sources": list(p.sources or []),
                "n_bullets": len(p.highlights or []),
                "sample_bullet": (p.highlights[0] if p.highlights else "")[:160],
            }
            for i, (bucket, p) in enumerate(project_items)
        ],
        "experience": [
            {
                "idx": i,
                "title": x.title,
                "company": x.company,
                "period": x.period or "",
                "tags": list(x.tags or [])[:6],
                "sources": list(x.sources or []),
                "n_bullets": len(x.highlights or []),
                "sample_bullet": (x.highlights[0] if x.highlights else "")[:160],
            }
            for i, x in enumerate(experience_items)
        ],
        "education": [
            {
                "idx": i,
                "institution": e.institution,
                "degree": e.degree,
                "period": e.period,
                "sources": list(e.sources or []),
            }
            for i, e in enumerate(education_items)
        ],
        "certifications": [
            {
                "idx": i,
                "issuer": c.issuer,
                "name": c.name,
                "sources": list(c.sources or []),
            }
            for i, c in enumerate(cert_items)
        ],
        "publications": [
            {
                "idx": i,
                "title": p.title,
                "status": p.status,
                "venue": p.venue,
                "sources": list(p.sources or []),
            }
            for i, p in enumerate(pub_items)
        ],
        "summary_current": (library.summary or "")[:600],
        "header_current": {
            "name": library.header.name,
            "email": library.header.email,
            "phone": library.header.phone,
            "linkedin": library.header.linkedin,
            "github": library.header.github,
            "website": library.header.website,
        },
    }

    return digest, {
        "projects": project_items,
        "experience": experience_items,
        "education": education_items,
        "certifications": cert_items,
        "publications": pub_items,
    }


# ---------- LLM call ----------

def _llm_call(digest: dict) -> _CuratorFullOutput | None:
    from app.services import llm_extraction_service as llm

    system = (
        "You curate a candidate's master CV library. For EACH section "
        "(projects, experience, education, certifications, "
        "publications), return curation decisions: which entries "
        "are near-duplicates that should merge into one, and which "
        "entries are noise that should drop entirely.\n\n"
        "Hard rules per section:\n\n"
        "PROJECTS:\n"
        "  * Group near-duplicates: semantic title match. "
        "'TalkingHeadAI' and 'talkinghead-ai' merge. "
        "'NED 3 Pro DRL Sim-to-Real Reaching' and "
        "'ned3-pro-drl-sim2real' merge.\n"
        "  * DROP only true noise: profile-readme repos, forks, "
        "event archives ('ai-event'), empty-title entries, "
        "personal blog repos.\n"
        "  * KEEP everything else — recruiters appreciate breadth.\n\n"
        "EXPERIENCE:\n"
        "  * Group entries with same (title, company) — even if "
        "company is spelled slightly differently. CV may have an "
        "entry the imported document re-states; merge both bullets.\n"
        "  * DROP entries with empty title or placeholder text.\n"
        "  * Flag conflicting periods in the reason — but merge "
        "anyway, the renderer picks the longer period.\n\n"
        "EDUCATION:\n"
        "  * Group entries for the same institution + degree, even "
        "when one row's institution field has garbage stuffed into "
        "it (PDF-parse artifact: 'MSc in AI & Robotics, University "
        "of Hertfordshire, Distinction, GPA 4.42/5.00' is ONE row, "
        "not multiple). For the canonical_title, pick the cleanest "
        "institution name (just the school).\n"
        "  * DROP truly empty rows.\n\n"
        "CERTIFICATIONS:\n"
        "  * Group same credential phrased differently (e.g. "
        "'CS50P – Introduction to Programming with Python' and "
        "'HarvardX CS50P'). Canonical_title is the cleanest credential "
        "name.\n"
        "  * DROP duplicate entries with no extra info.\n\n"
        "PUBLICATIONS:\n"
        "  * Group same paper title across sources.\n"
        "  * DROP entries with empty title.\n\n"
        "SUMMARY:\n"
        "  * Look at summary_current. If it's empty or shorter than "
        "two sentences, write a 3-4 line summary using ONLY facts "
        "visible in the digest sections (no invention). Otherwise "
        "return empty string to keep what's there.\n\n"
        "HEADER:\n"
        "  * For any header_current field that's EMPTY, propose a "
        "value from another source if any source has it. Don't "
        "overwrite non-empty fields. Skip if no candidate value.\n\n"
        "Reply with exactly this JSON shape:\n"
        '{\n'
        '  "projects": {"groups": [{"canonical_title": str, '
        '"member_indices": [int], "reason": str}], '
        '"drop_indices": [int], "drop_reasons": {"<idx>": str}},\n'
        '  "experience": {... same shape ...},\n'
        '  "education": {... same shape ...},\n'
        '  "certifications": {... same shape ...},\n'
        '  "publications": {... same shape ...},\n'
        '  "summary_rewrite": str,\n'
        '  "header_patches": {"email": str, "linkedin": str, "github": str, "website": str, "phone": str}\n'
        '}\n'
        "Indices in each section's groups / drops refer to that "
        "section's idx field — never cross-section."
    )

    user = json.dumps(digest, ensure_ascii=False)

    try:
        raw = llm._chat_completion(  # type: ignore[attr-defined]
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            json_mode=True,
        )
        data = json.loads(raw)
        parsed = _CuratorFullOutput.model_validate(data)
    except (json.JSONDecodeError, ValidationError, Exception) as exc:  # noqa: BLE001
        logger.warning("Master curator LLM failed: %s", exc)
        return None
    return parsed


# ---------- Apply ----------

def _resolve_groups(
    n: int, decision: _SectionDecision,
) -> tuple[set[int], list[tuple[list[int], str]]]:
    """Returns (drop_set, ordered_groups). Each group is (member_indices,
    canonical_title). Indices the LLM didn't mention land in singleton
    groups so they survive (defensive)."""
    drop: set[int] = {
        int(i) for i in (decision.drop_indices or [])
        if isinstance(i, int) and 0 <= i < n
    }
    group_of: dict[int, int] = {}
    canonical: list[str] = []
    order: list[list[int]] = []
    for g in (decision.groups or []):
        members = [
            i for i in (g.member_indices or [])
            if isinstance(i, int) and 0 <= i < n and i not in drop
        ]
        if not members:
            continue
        gid = len(canonical)
        canonical.append((g.canonical_title or "").strip())
        order.append(members)
        for m in members:
            group_of[m] = gid
    # Singletons for ungrouped + non-dropped.
    for i in range(n):
        if i in drop or i in group_of:
            continue
        gid = len(canonical)
        canonical.append("")
        order.append([i])
        group_of[i] = gid
    return drop, list(zip(order, canonical))


def _apply_project_decision(
    items: list[tuple[str, ProjectEntry]],
    decision: _SectionDecision,
) -> tuple[list[ProjectEntry], list[ProjectEntry]]:
    n = len(items)
    drop, groups = _resolve_groups(n, decision)
    selected: list[ProjectEntry] = []
    additional: list[ProjectEntry] = []
    logger.info("Curator: projects %d → %d (dropped=%d)", n, len(groups), len(drop))
    for members, canon in groups:
        first = items[members[0]][1]
        merged = ProjectEntry(
            title=canon or first.title,
            period=first.period,
            highlights=list(first.highlights or []),
            tags=list(first.tags or []),
            sources=list(first.sources or []),
        )
        for m in members[1:]:
            other = items[m][1]
            if other.period and len(other.period) > len(merged.period or ""):
                merged.period = other.period
            for b in (other.highlights or []):
                if b and b not in merged.highlights:
                    merged.highlights.append(b)
            for t in (other.tags or []):
                if t and t not in merged.tags:
                    merged.tags.append(t)
            for s in (other.sources or []):
                if s and s not in merged.sources:
                    merged.sources.append(s)
        any_selected = any(items[m][0] == "selected" for m in members)
        (selected if any_selected else additional).append(merged)
    return selected, additional


def _apply_simple_section(
    items: list[Any],
    decision: _SectionDecision,
    entry_cls: type,
    *,
    title_field: str,
    merge_company: bool,
) -> list[Any]:
    """Generic merge for experience / education / cert / publication."""
    n = len(items)
    if n == 0:
        return []
    drop, groups = _resolve_groups(n, decision)
    out: list[Any] = []
    logger.info(
        "Curator: %s %d → %d (dropped=%d)",
        entry_cls.__name__, n, len(groups), len(drop),
    )
    for members, canon in groups:
        first = items[members[0]]
        merged = first.model_copy(deep=True)
        # Override title-field with canonical when LLM provided one.
        if canon:
            setattr(merged, title_field, canon)
        for m in members[1:]:
            other = items[m]
            # Period (when present): longer wins.
            if hasattr(merged, "period") and hasattr(other, "period"):
                if (other.period or "") and len(other.period) > len(merged.period or ""):
                    merged.period = other.period
            # Company override on experience: fill if empty.
            if merge_company and hasattr(merged, "company"):
                if not (merged.company or "").strip() and (other.company or "").strip():
                    merged.company = other.company
            # Issuer (cert): keep longer.
            if hasattr(merged, "issuer") and hasattr(other, "issuer"):
                if (other.issuer or "") and len(other.issuer) > len(merged.issuer or ""):
                    merged.issuer = other.issuer
            # Status / venue (publications): fill empty.
            for field in ("status", "venue", "degree"):
                if hasattr(merged, field):
                    cur = getattr(merged, field, "")
                    inc = getattr(other, field, "")
                    if not (cur or "").strip() and (inc or "").strip():
                        setattr(merged, field, inc)
            # Highlights: union.
            if hasattr(merged, "highlights"):
                for b in (other.highlights or []):
                    if b and b not in merged.highlights:
                        merged.highlights.append(b)
            # Tags: union.
            if hasattr(merged, "tags"):
                for t in (other.tags or []):
                    if t and t not in merged.tags:
                        merged.tags.append(t)
            # Sources: union.
            if hasattr(merged, "sources"):
                for s in (other.sources or []):
                    if s and s not in merged.sources:
                        merged.sources.append(s)
        out.append(merged)
    return out
