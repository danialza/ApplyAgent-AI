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


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
