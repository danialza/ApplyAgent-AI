"""Tests for `POST /api/agent/run` — the end-to-end pipeline.

Exercises the full chain end-to-end with a temp DB and a mocked HTTP
layer (so discovery doesn't actually hit RemoteOK / Remotive / HN).

    python -m tests.test_agent
"""
from __future__ import annotations

import os
import tempfile

# Temp file DB before any backend import.
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="agent_test_")
os.close(_DB_FD)
os.environ["APP_DB_URL"] = f"sqlite:///{_DB_PATH}"

from fastapi.testclient import TestClient  # noqa: E402

from app.db.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.db_models import CV  # noqa: E402
from app.services import job_discovery  # noqa: E402
from app.services.cv_parser import parse_cv_text  # noqa: E402


# ---------- Fixtures ----------

ALICE = """\
Alice Strong
alice@example.com

Skills
Python, FastAPI, Machine Learning, RAG, FAISS, PyTorch, Docker, AWS

Experience
- Senior AI Engineer — Acme (2019 - Present). Led production RAG pipelines on AWS.

- ML Engineer — Globex (2016 - 2019). Built classifiers in Python and PyTorch.

Education
M.Sc. Computer Science — UC Berkeley
"""

REMOTIVE_PAYLOAD = {
    "jobs": [
        {
            "title": "Senior AI Engineer",
            "company_name": "Cortex Labs",
            "candidate_required_location": "Worldwide",
            "url": "https://remotive.com/jobs/100",
            "description": "Build RAG pipelines with Python, FastAPI, and FAISS on AWS.",
            "tags": ["python", "rag", "faiss"],
            "publication_date": "2026-04-30",
        },
        {
            "title": "Machine Learning Engineer",
            "company_name": "Northwind",
            "candidate_required_location": "Europe",
            "url": "https://remotive.com/jobs/101",
            "description": "Train transformers with PyTorch.",
            "tags": ["python", "pytorch", "ml"],
            "publication_date": "2026-04-29",
        },
        {
            "title": "Frontend Engineer",
            "company_name": "Pixel",
            "candidate_required_location": "Remote",
            "url": "https://remotive.com/jobs/102",
            "description": "React, Next.js, TypeScript front-end work.",
            "tags": ["react", "next.js", "typescript"],
            "publication_date": "2026-04-28",
        },
    ]
}


def _reset() -> None:
    Base.metadata.drop_all(bind=engine)
    init_db()


def _seed_cv(db, filename: str, raw_text: str) -> CV:
    parsed = parse_cv_text(raw_text)
    cv = CV(
        filename=filename, name=parsed.name, summary=parsed.summary,
        skills=parsed.skills, experience=parsed.experience,
        education=parsed.education, projects=parsed.projects,
        certifications=parsed.certifications, languages=parsed.languages,
        raw_text=raw_text,
    )
    db.add(cv); db.commit(); db.refresh(cv)
    return cv


def _patch_discovery() -> None:
    """Make discovery deterministic + offline."""
    def fake_get(url: str, params=None):
        if "remotive.com" in url:
            return REMOTIVE_PAYLOAD
        # RemoteOK / HN return empty so only Remotive contributes.
        if "remoteok.com" in url:
            return [{"legal": "metadata"}]
        if "hn.algolia.com" in url:
            return {"hits": []}
        raise RuntimeError(f"unexpected URL {url}")
    job_discovery._http_get_json = fake_get  # type: ignore[assignment]
    job_discovery._throttle = lambda host: None  # type: ignore[assignment]


# ---------- happy path ----------

def test_agent_full_pipeline() -> None:
    _reset()
    with SessionLocal() as db:
        _seed_cv(db, "alice.txt", ALICE)
    _patch_discovery()

    with TestClient(app) as client:
        resp = client.post(
            "/api/agent/run",
            json={"max_discover": 20, "max_rank": 10, "max_tailor": 3},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["error"] == ""

    # Step trace covers every phase.
    step_names = [s["name"] for s in body["steps"]]
    for must in ("profile", "queries", "discovery", "ranking", "tailoring"):
        assert must in step_names, f"missing step: {must}"
    assert all(s["status"] in {"ok", "skipped"} for s in body["steps"])

    # Profile auto-built from the seeded CV.
    assert body["profile"] is not None
    assert body["profile"]["name"] == "Alice Strong"

    # Queries + tags non-empty.
    assert body["queries"], "expected at least one query"
    assert body["tags"]["roles"], "expected at least one role tag"

    # Discovery returned the mocked Remotive jobs.
    discovered_titles = [j["title"] for j in body["discovered"]]
    assert "Senior AI Engineer" in discovered_titles

    # Ranking sorted by overall_score desc; AI roles outrank frontend.
    assert body["ranked"], "expected ranked results"
    scores = [r["overall_score"] for r in body["ranked"]]
    assert scores == sorted(scores, reverse=True)
    top_titles = [r["job"]["title"] for r in body["ranked"][:2]]
    assert any("AI" in t or "Machine Learning" in t for t in top_titles)

    # Tailoring populated for top N.
    assert 1 <= len(body["tailored"]) <= 3
    first = body["tailored"][0]
    assert first["best_cv_filename"] == "alice.txt"
    assert first["suggestions"]["keywords_for_ats"]
    assert first["suggestions"]["summary_hint"]


def test_agent_uses_profile_fallback_when_no_cvs() -> None:
    """No CVs → no auto-build path → fatal error.

    The orchestrator only auto-builds the profile when at least one CV
    exists. With no CVs we expect a fatal error and a populated trace.
    """
    _reset()
    _patch_discovery()
    with TestClient(app) as client:
        resp = client.post("/api/agent/run", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]
    # Profile step is the one that errored.
    profile_step = next(s for s in body["steps"] if s["name"] == "profile")
    assert profile_step["status"] == "error"


def test_agent_empty_discovery_skips_downstream() -> None:
    _reset()
    with SessionLocal() as db:
        _seed_cv(db, "alice.txt", ALICE)

    # Override discovery to return zero results across all sources.
    def empty_get(url: str, params=None):
        if "remoteok.com" in url:
            return [{"legal": "metadata"}]
        if "remotive.com" in url:
            return {"jobs": []}
        if "hn.algolia.com" in url:
            return {"hits": []}
        raise RuntimeError(f"unexpected URL {url}")
    job_discovery._http_get_json = empty_get  # type: ignore[assignment]
    job_discovery._throttle = lambda host: None  # type: ignore[assignment]

    with TestClient(app) as client:
        resp = client.post("/api/agent/run", json={})
    assert resp.status_code == 200
    body = resp.json()
    statuses = {s["name"]: s["status"] for s in body["steps"]}
    assert statuses["discovery"] == "skipped"
    # Ranking + tailoring not even recorded since we early-returned.
    assert body["ranked"] == []
    assert body["tailored"] == []
    assert body["error"] == ""


def test_agent_uses_query_and_tag_overrides() -> None:
    """User-supplied queries/tags must take precedence over auto-derived ones."""
    _reset()
    with SessionLocal() as db:
        _seed_cv(db, "alice.txt", ALICE)
    _patch_discovery()

    overrides = {
        "queries": ["custom query about RAG and FAISS"],
        "tags": {
            "roles": ["RAG Engineer"],
            "skills": ["RAG", "FAISS", "Python"],
            "tools": ["FAISS"],
            "domains": ["AI/ML"],
            "platform_tags": {"linkedin": ["RAG Engineer"], "indeed": ["RAG"], "general": ["RAG"]},
        },
    }
    with TestClient(app) as client:
        resp = client.post(
            "/api/agent/run",
            json={"max_discover": 10, "max_rank": 5, "max_tailor": 2, **overrides},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["queries"] == ["custom query about RAG and FAISS"]
    assert body["tags"]["roles"] == ["RAG Engineer"]
    # The queries-step detail records that an override was used.
    queries_step = next(s for s in body["steps"] if s["name"] == "queries")
    assert "override" in (queries_step["detail"] or "").lower()


def test_agent_respects_max_tailor() -> None:
    _reset()
    with SessionLocal() as db:
        _seed_cv(db, "alice.txt", ALICE)
    _patch_discovery()

    with TestClient(app) as client:
        resp = client.post(
            "/api/agent/run",
            json={"max_discover": 20, "max_rank": 10, "max_tailor": 1},
        )
    body = resp.json()
    assert len(body["tailored"]) == 1


# ---------- runner ----------

def _run_all() -> None:
    tests = [
        test_agent_full_pipeline,
        test_agent_uses_profile_fallback_when_no_cvs,
        test_agent_empty_discovery_skips_downstream,
        test_agent_uses_query_and_tag_overrides,
        test_agent_respects_max_tailor,
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
