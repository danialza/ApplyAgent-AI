"""Tests for the profile aggregator.

Uses an in-memory SQLite DB so the tests don't depend on the dev DB.

    python -m tests.test_profile_service
"""
from __future__ import annotations

import os

# Must be set BEFORE importing app.* — picked up by app.db.database.
os.environ["APP_DB_URL"] = "sqlite:///:memory:"

from app.db.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models.db_models import CV, Document, UserProfile  # noqa: E402
from app.services.cv_parser import parse_cv_text  # noqa: E402
from app.services.profile_service import (  # noqa: E402
    _years_in,
    build_profile_payload,
    delete_user_profile,
    upsert_user_profile,
)


def _reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    init_db()


def _add_cv(db, filename: str, raw_text: str) -> CV:
    parsed = parse_cv_text(raw_text)
    cv = CV(
        filename=filename,
        name=parsed.name,
        summary=parsed.summary,
        skills=parsed.skills,
        education=parsed.education,
        experience=parsed.experience,
        projects=parsed.projects,
        certifications=parsed.certifications,
        languages=parsed.languages,
        email=parsed.email,
        phone=parsed.phone,
        linkedin=parsed.linkedin,
        github=parsed.github,
        portfolio=parsed.portfolio,
        raw_text=raw_text,
    )
    db.add(cv)
    db.commit()
    db.refresh(cv)
    return cv


def _add_doc(db, filename: str, raw_text: str) -> Document:
    doc = Document(filename=filename, raw_text=raw_text)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


CV1 = """\
Jane Doe
jane@example.com | github.com/janedoe | janedoe.dev

Summary
Senior AI engineer building RAG systems.

Skills
Python, FastAPI, Machine Learning, NLP, RAG, FAISS, Docker, AWS

Experience
Senior AI Engineer — Acme (2019 - Present)
- Built RAG pipelines with Python and FastAPI on AWS.

Education
M.Sc. Computer Science — UC Berkeley (2017 - 2019)

Projects
- OpenObserve dashboard built with Python and FAISS.

Certifications
- AWS Certified Solutions Architect (2022)

Languages
English (Native), Spanish (Conversational)
"""

# Older CV with overlapping skills + new one (PyTorch).
CV2 = """\
Jane Doe
jane@old.example.com

Skills
Python, ML, PyTorch, scikit-learn

Experience
ML Engineer — Globex (2014 - 2018)
- Built classifiers in Python and scikit-learn.

Education
B.Sc. Mathematics — UCSD (2010 - 2014)

Languages
English (Fluent)
"""

# Free-form portfolio doc — adds Hugging Face + project mention.
DOC1 = """\
Personal portfolio note.
I love working with Hugging Face transformers and LangChain.
Side project: built a Python service that uses LangChain to summarise PDFs.
"""


# ---------- helpers ----------

def test_years_in_basic() -> None:
    assert _years_in("2019 - 2021") == (2019, 2021)
    assert _years_in("Acme (2019 - Present)")[0] == 2019
    assert _years_in("Acme (2019 - Present)")[1] is not None
    assert _years_in("no years here") == (None, None)


# ---------- aggregator ----------

def test_build_profile_aggregates_cvs() -> None:
    _reset_db()
    with SessionLocal() as db:
        _add_cv(db, "jane1.pdf", CV1)
        _add_cv(db, "jane2.pdf", CV2)

        payload = build_profile_payload(db)

    assert payload["name"] == "Jane Doe"
    assert "Senior AI engineer" in payload["summary"]
    assert payload["source_cv_ids"] and len(payload["source_cv_ids"]) == 2
    assert payload["source_document_ids"] == []

    skill_names = {s["name"] for s in payload["skills"]}
    # Canonical names — RAG → "Retrieval Augmented Generation", NLP → "Natural Language Processing".
    expected = {
        "Python", "FastAPI", "Machine Learning",
        "Retrieval Augmented Generation", "FAISS", "AWS", "PyTorch",
    }
    assert expected.issubset(skill_names), f"missing: {expected - skill_names}"

    # Python appears in both CVs → count 2 and weight > entries with count 1.
    py = next(s for s in payload["skills"] if s["name"] == "Python")
    assert py["count"] == 2
    pytorch = next(s for s in payload["skills"] if s["name"] == "PyTorch")
    assert py["weight"] > pytorch["weight"]

    # FAISS is mentioned in a project → in_projects flag should be set.
    faiss = next(s for s in payload["skills"] if s["name"] == "FAISS")
    assert faiss["in_projects"] is True

    # Tools/technologies subset is non-empty and only contains technical skills.
    tt_names = {s["name"] for s in payload["tools_and_technologies"]}
    assert "Python" in tt_names and "FastAPI" in tt_names

    # Domains inferred from skill set.
    assert "AI/ML" in payload["domains"]
    assert "Backend" in payload["domains"] or "DevOps / Cloud" in payload["domains"]


def test_documents_contribute_skills_and_projects() -> None:
    _reset_db()
    with SessionLocal() as db:
        _add_cv(db, "jane1.pdf", CV1)
        _add_doc(db, "portfolio.txt", DOC1)
        payload = build_profile_payload(db)

    skill_names = {s["name"] for s in payload["skills"]}
    assert "Hugging Face" in skill_names or "LangChain" in skill_names
    assert payload["source_document_ids"] and len(payload["source_document_ids"]) == 1


def test_recency_weighting_prefers_recent_roles() -> None:
    _reset_db()
    with SessionLocal() as db:
        _add_cv(db, "jane1.pdf", CV1)  # role ends "Present"
        _add_cv(db, "jane2.pdf", CV2)  # role ends 2018
        payload = build_profile_payload(db)

    # The most-recent experience (Present) should rank above the older Globex role.
    work = payload["work_experience"]
    assert len(work) >= 2
    # First entry has the higher recency_score; recent should be > older.
    assert work[0]["recency_score"] >= work[-1]["recency_score"]


def test_languages_deduplicate_by_head_token() -> None:
    _reset_db()
    with SessionLocal() as db:
        _add_cv(db, "jane1.pdf", CV1)
        _add_cv(db, "jane2.pdf", CV2)
        payload = build_profile_payload(db)
    # "English (Native)" and "English (Fluent)" should collapse.
    eng = [l for l in payload["languages"] if l.lower().startswith("english")]
    assert len(eng) == 1


def test_portfolio_links_aggregated() -> None:
    _reset_db()
    with SessionLocal() as db:
        _add_cv(db, "jane1.pdf", CV1)
        payload = build_profile_payload(db)
    links = payload["portfolio_links"]
    assert "github.com/janedoe" in links["github"]
    assert "janedoe.dev" in links["portfolio"]


# ---------- upsert + delete ----------

def test_upsert_creates_then_replaces_single_row() -> None:
    _reset_db()
    with SessionLocal() as db:
        _add_cv(db, "jane1.pdf", CV1)
        payload = build_profile_payload(db)
        upsert_user_profile(db, payload)

        # Add a doc, rebuild — should still be a single profile row.
        _add_doc(db, "portfolio.txt", DOC1)
        payload2 = build_profile_payload(db)
        upsert_user_profile(db, payload2)

        rows = db.query(UserProfile).all()
        assert len(rows) == 1
        assert rows[0].source_document_ids


def test_delete_removes_profile_and_documents_but_keeps_cvs() -> None:
    _reset_db()
    with SessionLocal() as db:
        _add_cv(db, "jane1.pdf", CV1)
        _add_doc(db, "portfolio.txt", DOC1)
        upsert_user_profile(db, build_profile_payload(db))
        assert db.query(UserProfile).count() == 1
        assert db.query(Document).count() == 1
        assert db.query(CV).count() == 1

        delete_user_profile(db)

        assert db.query(UserProfile).count() == 0
        assert db.query(Document).count() == 0
        # CVs preserved per spec.
        assert db.query(CV).count() == 1


def _run_all() -> None:
    tests = [
        test_years_in_basic,
        test_build_profile_aggregates_cvs,
        test_documents_contribute_skills_and_projects,
        test_recency_weighting_prefers_recent_roles,
        test_languages_deduplicate_by_head_token,
        test_portfolio_links_aggregated,
        test_upsert_creates_then_replaces_single_row,
        test_delete_removes_profile_and_documents_but_keeps_cvs,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    _run_all()
