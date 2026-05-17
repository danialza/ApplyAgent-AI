"""SQLAlchemy ORM models.

JSON columns hold list/dict fields so the MVP avoids extra join tables.
SQLite supports JSON natively via SQLAlchemy's generic JSON type.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class CV(Base):
    __tablename__ = "cvs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # List[str] fields stored as JSON arrays.
    skills: Mapped[list[Any]] = mapped_column(JSON, default=list)
    education: Mapped[list[Any]] = mapped_column(JSON, default=list)
    experience: Mapped[list[Any]] = mapped_column(JSON, default=list)
    projects: Mapped[list[Any]] = mapped_column(JSON, default=list)
    certifications: Mapped[list[Any]] = mapped_column(JSON, default=list)
    languages: Mapped[list[Any]] = mapped_column(JSON, default=list)

    # Contact info — empty string when not present in the CV.
    email: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    phone: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    linkedin: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    github: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    portfolio: Mapped[str] = mapped_column(String(512), nullable=False, default="")

    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )


class Document(Base):
    """Free-form documents that feed into the unified profile.

    PDF / DOCX / TXT files supplementing the user's CVs — project notes,
    portfolio descriptions, transcripts, certificates, etc.
    """
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )


class UserProfile(Base):
    """Unified aggregate over CVs + Documents. Single-row for the MVP."""
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Each entry is a dict (skill name / weight / sources). Stored as JSON.
    skills: Mapped[list[Any]] = mapped_column(JSON, default=list)
    tools_and_technologies: Mapped[list[Any]] = mapped_column(JSON, default=list)
    work_experience: Mapped[list[Any]] = mapped_column(JSON, default=list)
    education: Mapped[list[Any]] = mapped_column(JSON, default=list)
    projects: Mapped[list[Any]] = mapped_column(JSON, default=list)
    certifications: Mapped[list[Any]] = mapped_column(JSON, default=list)
    domains: Mapped[list[Any]] = mapped_column(JSON, default=list)
    languages: Mapped[list[Any]] = mapped_column(JSON, default=list)

    # {linkedin, github, portfolio, websites: []}
    portfolio_links: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Provenance — which CVs and Documents were aggregated.
    source_cv_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    source_document_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class CVLibrary(Base):
    """User-curated library of CV material — beyond any single uploaded CV.

    The renderer picks subsets of this material per JD to produce a tailored
    LaTeX CV. Single-row table for the MVP (id fixed at 1).
    """
    __tablename__ = "cv_library"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)

    # Personal header — name + contact line.
    header: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Skills as ordered category buckets, e.g.
    #   [{"label": "Languages", "items": ["Python", "SQL", ...]}, ...]
    skills_groups: Mapped[list[Any]] = mapped_column(JSON, default=list)

    # User-curated stretch skills with 1..5 self-ratings. The renderer
    # injects matching items into the tailored output above the
    # Technical Skills grid. See schemas.CompetencyEntry.
    core_competencies: Mapped[list[Any]] = mapped_column(JSON, default=list)

    # All entries below are list[dict] with a stable shape — see schemas.py.
    education: Mapped[list[Any]] = mapped_column(JSON, default=list)
    selected_projects: Mapped[list[Any]] = mapped_column(JSON, default=list)
    additional_projects: Mapped[list[Any]] = mapped_column(JSON, default=list)
    experience: Mapped[list[Any]] = mapped_column(JSON, default=list)
    publications: Mapped[list[Any]] = mapped_column(JSON, default=list)
    certifications: Mapped[list[Any]] = mapped_column(JSON, default=list)
    languages: Mapped[list[Any]] = mapped_column(JSON, default=list)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # When set, the user hand-edited the library and auto-rebuilds
    # are skipped to preserve their changes. POST /api/cv/library/rebuild?force=true
    # overrides. Set automatically by PUT /api/cv/library and the
    # apply-fix endpoint; cleared by force-rebuild.
    manually_edited_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=None
    )

    # Audit issues the user has dismissed. Each entry is a SHA-1
    # fingerprint of "<scope>|<title.lower>" so LLM-rephrased
    # near-duplicates collide. Audit endpoint filters them out.
    ignored_issues: Mapped[list[Any]] = mapped_column(JSON, default=list)


class WebSource(Base):
    """External web artifact the user pointed us at (portfolio site,
    GitHub profile, individual repo, blog post). Lives alongside CV /
    Document rows as a first-class library source; the master builder
    folds it in on every rebuild.

    `kind` discriminates how the ingester populated `raw_text` and
    `extracted` (JSON snapshot of derived projects/skills the builder
    can fold in cheaply on subsequent rebuilds).
    """
    __tablename__ = "web_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    # "web"   = generic page (portfolio, blog) → trafilatura/bs4 + LLM extract
    # "github_user" = github.com/<user> → list public repos via API
    # "github_repo" = github.com/<user>/<repo> → README + metadata
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="web")
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # JSON: best-effort structured extract — list[ProjectEntry-ish dicts],
    # bio paragraph, skill hints. Lets the master builder fold this
    # source in without re-LLM-ing every rebuild.
    extracted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # "pending" while fetching/extracting, "done" on success,
    # "failed" with `error` populated.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class Application(Base):
    """Application tracker row — career-ops style spreadsheet.

    Columns mirror the user's existing Google Sheet:
      When / DeadLine / Where? / What? / Status / How / Link
    Plus jd_hash + jd_text for dedupe ("have I applied to this already?")
    and free-form notes.

    Status is free-text to allow user-custom values; the UI ships a
    suggested enum (To-Apply / Applied / Interview / Offer / Rejected /
    Skipped) but doesn't enforce.
    """
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # ISO-format YYYY-MM-DD strings so CSV export is trivial. Empty
    # string means "not set yet" — renders as "-" in the UI/CSV.
    apply_date: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    deadline: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    company: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="To-Apply")
    how: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    # SHA-1 of normalised JD text. Cheap fingerprint for "did I tailor
    # this exact JD before?" checks. URL match wins when both exist.
    jd_hash: Mapped[str] = mapped_column(String(40), nullable=False, default="", index=True)
    jd_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Tailored CV snapshot at the time the application was tracked.
    # Stored so the user can re-download .tex / .pdf months later from
    # the tracker without re-rendering. PDF kept as base64 to match
    # the render endpoint contract; ~200 KB per row is fine.
    cv_latex: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cv_pdf_b64: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cv_filename: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
