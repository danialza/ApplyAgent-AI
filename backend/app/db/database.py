"""SQLite engine + session factory.

Uses a relative path so the project is portable. The DB file lives at
`backend/data/app.db` by default; override with the APP_DB_URL env var.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# Resolve a project-relative default DB path (no hardcoded absolute paths).
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB_DIR = _BACKEND_ROOT / "data"
_DEFAULT_DB_DIR.mkdir(parents=True, exist_ok=True)
_DEFAULT_DB_PATH = _DEFAULT_DB_DIR / "app.db"

DB_URL = os.getenv("APP_DB_URL", f"sqlite:///{_DEFAULT_DB_PATH}")

# `check_same_thread=False` lets FastAPI's threadpool reuse connections safely.
engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """Declarative base shared by all ORM models."""


def init_db() -> None:
    """Create tables. Called once on FastAPI startup."""
    # Import here to ensure models are registered with `Base.metadata`.
    # Side-effect import: registers all SQLAlchemy models with Base.metadata.
    from app.models import db_models  # noqa: F401  pylint: disable=unused-import,import-outside-toplevel

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """Tiny inline migration for SQLite: `CREATE TABLE` is idempotent
    but adding columns to an EXISTING table is not. For each new column
    we ship, check `PRAGMA table_info` and `ALTER TABLE ADD COLUMN`
    when missing. Prevents users from having to `docker-clean` (and
    lose CVs/Documents) every time the library schema grows.

    Only runs against SQLite — other backends should use Alembic.
    """
    if not DB_URL.startswith("sqlite"):
        return
    from sqlalchemy import text

    # (table, column, ddl-type, default literal for ALTER)
    additions: list[tuple[str, str, str, str]] = [
        ("cv_library", "core_competencies", "JSON", "'[]'"),
        ("cv_library", "manually_edited_at", "TIMESTAMP", "NULL"),
        ("cv_library", "ignored_issues", "JSON", "'[]'"),
        ("cv_library", "user_patches", "JSON", "'[]'"),
        ("applications", "cv_latex", "TEXT", "''"),
        ("applications", "cv_pdf_b64", "TEXT", "''"),
        ("applications", "cv_filename", "VARCHAR(255)", "''"),
    ]
    # web_sources table is a new entity in this build; create_all
    # handles it. Nothing to ALTER for now.
    with engine.begin() as conn:
        for table, column, ddl_type, default in additions:
            existing = {
                row[1]
                for row in conn.execute(text(f"PRAGMA table_info({table})"))
            }
            if column in existing:
                continue
            # SQLite ALTER doesn't accept DEFAULT NULL syntax — emit
            # column without DEFAULT when default is the literal 'NULL'.
            if default.upper() == "NULL":
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
            else:
                conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type} "
                    f"DEFAULT {default}"
                ))


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
