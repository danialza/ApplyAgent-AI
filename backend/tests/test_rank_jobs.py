"""Tests for `POST /api/jobs/rank`.

Exercises the multi-job ranking flow against an in-memory DB. We hit the
underlying `match_cv_to_job` directly via the route's helpers — no HTTP
round-trip required.

    python -m tests.test_rank_jobs
"""
from __future__ import annotations

import os
import tempfile

# Use a temp file for the test DB so the engine pool's connections all
# share the same database (a per-connection :memory: DB doesn't survive
# the FastAPI request scope).
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="rankjobs_test_")
os.close(_DB_FD)
os.environ["APP_DB_URL"] = f"sqlite:///{_DB_PATH}"

from fastapi.testclient import TestClient  # noqa: E402

from app.db.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.db_models import CV, UserProfile  # noqa: E402
from app.services.cv_parser import parse_cv_text  # noqa: E402
from app.services.profile_service import (  # noqa: E402
    build_profile_payload,
    upsert_user_profile,
)


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


# Two CVs with very different skill profiles.
ALICE_AI = """\
Alice Strong
alice@example.com

Summary
Senior AI engineer with 6 years of production RAG systems.

Skills
Python, FastAPI, Machine Learning, NLP, RAG, FAISS, PyTorch, Docker, AWS

Experience
Senior AI Engineer — Acme (2019 - Present). Built RAG pipelines on AWS.

Education
M.Sc. Computer Science — UC Berkeley (2017 - 2019)
"""

CAROL_WP = """\
Carol Web
carol@example.com

Summary
WordPress / WooCommerce developer with 5 years of e-commerce builds.

Skills
WordPress, WooCommerce, PHP, JavaScript, HTML, CSS, SEO, Google Analytics

Experience
WordPress Developer — Pixel (2019 - 2024). Built WooCommerce stores.

Education
Diploma in Web Development
"""

JD_AI = (
    "Senior AI Engineer\n"
    "Required skills: Python, Machine Learning, RAG, FAISS, AWS.\n"
    "Education: Master's in Computer Science.\n"
)
JD_WP = (
    "WordPress Developer\n"
    "What we're looking for:\n"
    "- WordPress, WooCommerce, PHP, JavaScript, HTML, CSS, SEO.\n"
)
JD_ROBOTICS = (
    "Robotics Engineer (Junior)\n"
    "Required: ROS2, PID, Gazebo, MATLAB, Simulink, C++, Python.\n"
)


# ---------- happy path ----------

def test_rank_picks_correct_best_cv_per_job() -> None:
    _reset()
    with SessionLocal() as db:
        _add_cv(db, "alice.txt", ALICE_AI)
        _add_cv(db, "carol.txt", CAROL_WP)

    with TestClient(app) as client:
        resp = client.post(
            "/api/jobs/rank",
            json={"jobs": [
                {"job_text": JD_AI, "title": "Senior AI Engineer"},
                {"job_text": JD_WP, "title": "WordPress Developer"},
                {"job_text": JD_ROBOTICS, "title": "Robotics Engineer"},
            ]},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_title = {r["job"]["title"]: r for r in body["results"]}

    assert by_title["Senior AI Engineer"]["best_cv_filename"] == "alice.txt"
    assert by_title["WordPress Developer"]["best_cv_filename"] == "carol.txt"
    # Robotics matches neither well — best score should be lower than AI's.
    assert by_title["Robotics Engineer"]["overall_score"] < by_title["Senior AI Engineer"]["overall_score"]

    # Results are sorted desc by overall_score.
    scores = [r["overall_score"] for r in body["results"]]
    assert scores == sorted(scores, reverse=True)


def test_rank_filters_to_explicit_cv_ids() -> None:
    _reset()
    with SessionLocal() as db:
        alice = _add_cv(db, "alice.txt", ALICE_AI)
        _add_cv(db, "carol.txt", CAROL_WP)
        alice_id = alice.id

    with TestClient(app) as client:
        resp = client.post(
            "/api/jobs/rank",
            json={
                "jobs": [{"job_text": JD_WP}],
                "cv_ids": [alice_id],
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    # Only Alice was in the pool; she's not a great WP fit but she's the only option.
    assert body["cv_pool_ids"] == [alice_id]
    assert body["results"][0]["best_cv_id"] == alice_id


def test_rank_404_when_explicit_cv_id_missing() -> None:
    _reset()
    with SessionLocal() as db:
        _add_cv(db, "alice.txt", ALICE_AI)

    with TestClient(app) as client:
        resp = client.post(
            "/api/jobs/rank",
            json={"jobs": [{"job_text": JD_AI}], "cv_ids": [999]},
        )
    assert resp.status_code == 404


def test_rank_falls_back_to_profile_when_no_cvs() -> None:
    _reset()
    with SessionLocal() as db:
        # No CVs — only a profile (built from a single Document via the
        # standard upsert flow).
        profile_payload = {
            "name": "Profile User",
            "summary": "AI engineer",
            "skills": [{"name": "Python", "weight": 1.5, "count": 1, "sources": ["doc:1"], "in_projects": False}],
            "tools_and_technologies": [{"name": "Docker", "weight": 1.0, "count": 1, "sources": [], "in_projects": False}],
            "work_experience": [{"text": "AI Engineer (2020-2025)", "start_year": 2020, "end_year": 2025, "recency_score": 1.0, "sources": []}],
            "education": ["M.Sc. CS"],
            "projects": ["RAG demo"],
            "certifications": [],
            "domains": ["AI/ML"],
            "languages": [],
            "portfolio_links": {"linkedin": "", "github": "", "portfolio": "", "websites": []},
            "source_cv_ids": [],
            "source_document_ids": [1],
        }
        upsert_user_profile(db, profile_payload)

    with TestClient(app) as client:
        resp = client.post(
            "/api/jobs/rank",
            json={"jobs": [{"job_text": JD_AI}]},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["used_profile_fallback"] is True
    # No real CV id — best_cv_id is None for the synthetic CV.
    assert body["results"][0]["best_cv_id"] is None
    assert body["results"][0]["overall_score"] > 0


def test_rank_404_when_no_cvs_and_no_profile() -> None:
    _reset()
    with TestClient(app) as client:
        resp = client.post(
            "/api/jobs/rank",
            json={"jobs": [{"job_text": JD_AI}]},
        )
    assert resp.status_code == 404


def test_rank_max_results_caps_output() -> None:
    _reset()
    with SessionLocal() as db:
        _add_cv(db, "alice.txt", ALICE_AI)

    with TestClient(app) as client:
        resp = client.post(
            "/api/jobs/rank",
            json={
                "jobs": [{"job_text": JD_AI}, {"job_text": JD_WP}, {"job_text": JD_ROBOTICS}],
                "max_results": 2,
            },
        )
    assert resp.status_code == 200
    assert len(resp.json()["results"]) == 2


# ---------- runner ----------

def _run_all() -> None:
    tests = [
        test_rank_picks_correct_best_cv_per_job,
        test_rank_filters_to_explicit_cv_ids,
        test_rank_404_when_explicit_cv_id_missing,
        test_rank_falls_back_to_profile_when_no_cvs,
        test_rank_404_when_no_cvs_and_no_profile,
        test_rank_max_results_caps_output,
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
        # Clean up the temp DB file regardless of test outcome.
        try:
            os.unlink(_DB_PATH)
        except OSError:
            pass
