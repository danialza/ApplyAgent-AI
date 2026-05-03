"""Tests for `POST /api/tailor`.

Verifies the endpoint picks the right CV, returns the same explainable
breakdown as the matcher, and emits structured tailoring suggestions
(skills_to_add / skills_to_emphasize / keywords_for_ats / sections / etc.).

    python -m tests.test_tailor
"""
from __future__ import annotations

import os
import tempfile

# Temp file DB — see test_rank_jobs.py for why :memory: doesn't work
# behind FastAPI's per-request session.
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="tailor_test_")
os.close(_DB_FD)
os.environ["APP_DB_URL"] = f"sqlite:///{_DB_PATH}"

from fastapi.testclient import TestClient  # noqa: E402

from app.db.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.db_models import CV  # noqa: E402
from app.services.cv_parser import parse_cv_text  # noqa: E402


def _reset() -> None:
    Base.metadata.drop_all(bind=engine)
    init_db()


def _add_cv(db, filename: str, raw_text: str) -> CV:
    parsed = parse_cv_text(raw_text)
    cv = CV(
        filename=filename,
        name=parsed.name,
        summary=parsed.summary,
        skills=parsed.skills,
        experience=parsed.experience,
        education=parsed.education,
        projects=parsed.projects,
        certifications=parsed.certifications,
        languages=parsed.languages,
        raw_text=raw_text,
    )
    db.add(cv); db.commit(); db.refresh(cv)
    return cv


# Alice has a strong match for AI work but her bullets don't mention
# every required skill — perfect for testing tailoring suggestions.
ALICE = """\
Alice Strong
alice@example.com

Skills
Python, FastAPI, Machine Learning, RAG, FAISS, Docker, AWS

Experience
- Senior AI Engineer — Acme (2019 - Present). Led a small team and shipped production services.

- ML Engineer — Globex (2016 - 2019). Built classifiers in Python and scikit-learn.

Education
M.Sc. Computer Science — UC Berkeley
"""

# Bob has fewer matching skills overall.
BOB = """\
Bob Backend
bob@example.com

Skills
Python, FastAPI, PostgreSQL, Docker

Experience
Backend Engineer — Globex (2020 - 2024). Built billing services on AWS.

Education
B.Sc. Computer Science
"""

JD_AI = (
    "Senior AI Engineer\n"
    "Required skills: Python, Machine Learning, RAG, FAISS, NLP, Docker, AWS.\n"
    "Preferred: TypeScript.\n"
    "Education: Master's in Computer Science.\n"
)


# ---------- happy path ----------

def test_tailor_picks_best_cv_and_returns_suggestions() -> None:
    _reset()
    with SessionLocal() as db:
        _add_cv(db, "alice.txt", ALICE)
        _add_cv(db, "bob.txt", BOB)

    with TestClient(app) as client:
        resp = client.post("/api/tailor", json={"job_text": JD_AI})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Alice wins — she has more matched skills.
    assert body["best_cv_filename"] == "alice.txt"
    assert body["match"]["overall_score"] >= body["match"]["skill_score"] * 0.4

    sug = body["suggestions"]

    # NLP is required but not in Alice's skills → must show up in skills_to_add.
    # The parser canonicalises NLP → "Natural Language Processing" via the
    # synonym group; either form satisfies the assertion.
    assert any(s in sug["skills_to_add"] for s in ("NLP", "Natural Language Processing"))

    # ATS keywords echo the JD's required + preferred + technologies.
    for s in ("Python", "Machine Learning", "RAG", "FAISS", "Docker", "AWS"):
        assert s in sug["keywords_for_ats"]

    # Alice's first bullet ("Led a small team and shipped production services.")
    # mentions zero matched skills → must surface as a rewrite candidate.
    assert sug["bullets_to_rewrite"], "expected at least one bullet rewrite"
    rewrite = sug["bullets_to_rewrite"][0]
    assert "Led a small team" in rewrite["original"]
    assert rewrite["target_skills"], "rewrite suggestion must propose target skills"

    # Summary hint reads like a sentence, not an empty default.
    assert "engineer" in sug["summary_hint"].lower()


def test_tailor_emphasize_skills_only_in_skills_section() -> None:
    """Matched skills present in the Skills section but absent from
    summary/experience prose should be flagged for emphasis."""
    cv_text = (
        "Carla Codes\ncarla@example.com\n\n"
        "Skills\nPython, FastAPI, RAG, FAISS\n\n"
        "Experience\nEngineer — Co (2020-2024). Worked on internal tools.\n"
    )
    _reset()
    with SessionLocal() as db:
        _add_cv(db, "carla.txt", cv_text)

    with TestClient(app) as client:
        resp = client.post("/api/tailor", json={"job_text": JD_AI})
    body = resp.json()
    emph = body["suggestions"]["skills_to_emphasize"]
    # RAG/FAISS are in the skills list but never mentioned in prose.
    assert "RAG" in emph or "Retrieval Augmented Generation" in emph
    assert "FAISS" in emph


def test_tailor_404_when_explicit_cv_missing() -> None:
    _reset()
    with SessionLocal() as db:
        _add_cv(db, "alice.txt", ALICE)

    with TestClient(app) as client:
        resp = client.post("/api/tailor", json={"job_text": JD_AI, "cv_ids": [999]})
    assert resp.status_code == 404


def test_tailor_404_when_pool_empty_and_no_profile() -> None:
    _reset()
    with TestClient(app) as client:
        resp = client.post("/api/tailor", json={"job_text": JD_AI})
    assert resp.status_code == 404


def test_tailor_section_suggestion_when_summary_missing() -> None:
    """A CV without a Summary section should get the matching tip."""
    no_summary = (
        "Bob Plain\nbob@example.com\n\nSkills\nPython, Machine Learning, RAG\n\n"
        "Experience\nEngineer — Co (2021-Present).\n"
    )
    _reset()
    with SessionLocal() as db:
        _add_cv(db, "bob.txt", no_summary)
    with TestClient(app) as client:
        resp = client.post("/api/tailor", json={"job_text": JD_AI})
    sections = resp.json()["suggestions"]["sections_to_add"]
    assert any("summary" in s.lower() for s in sections)


# ---------- runner ----------

def _run_all() -> None:
    tests = [
        test_tailor_picks_best_cv_and_returns_suggestions,
        test_tailor_emphasize_skills_only_in_skills_section,
        test_tailor_404_when_explicit_cv_missing,
        test_tailor_404_when_pool_empty_and_no_profile,
        test_tailor_section_suggestion_when_summary_missing,
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
    try:
        _run_all()
    finally:
        try:
            os.unlink(_DB_PATH)
        except OSError:
            pass
