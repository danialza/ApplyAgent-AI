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
