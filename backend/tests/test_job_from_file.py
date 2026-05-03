"""Tests for `POST /api/jobs/from-file` and `POST /api/match/from-file`.

    python -m tests.test_job_from_file
"""
from __future__ import annotations

import io
import os
import tempfile

# Temp file DB before any backend import.
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="jobfile_test_")
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
        filename=filename, name=parsed.name, summary=parsed.summary,
        skills=parsed.skills, experience=parsed.experience,
        education=parsed.education, projects=parsed.projects,
        certifications=parsed.certifications, languages=parsed.languages,
        raw_text=raw_text,
    )
    db.add(cv); db.commit(); db.refresh(cv)
    return cv


ALICE = """\
Alice Strong
alice@example.com

Skills
Python, FastAPI, Machine Learning, RAG, FAISS, Docker, AWS

Experience
- Senior AI Engineer — Acme (2019 - Present). Built RAG pipelines on AWS.

Education
M.Sc. Computer Science
"""

JD_TEXT = (
    "Senior AI Engineer\n\n"
    "Required skills: Python, Machine Learning, RAG, FAISS, Docker, AWS.\n"
    "Education: Master's in Computer Science.\n"
)


# ---------- /api/jobs/from-file ----------

def test_from_file_parses_txt_jd() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/api/jobs/from-file",
            files={"file": ("jd.txt", io.BytesIO(JD_TEXT.encode("utf-8")), "text/plain")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["filename"] == "jd.txt"
    assert "Senior AI Engineer" in body["extracted_text"]
    assert body["parsed_job"]["job_title"].startswith("Senior AI Engineer")
    assert "Python" in body["parsed_job"]["required_skills"]


def test_from_file_rejects_unknown_extension() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/api/jobs/from-file",
            files={"file": ("jd.weird", io.BytesIO(b"hi"), "application/octet-stream")},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "unsupported" in body["error"].lower()


def test_from_file_rejects_empty_file() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/api/jobs/from-file",
            files={"file": ("jd.txt", io.BytesIO(b""), "text/plain")},
        )
    body = resp.json()
    assert body["success"] is False
    assert "empty" in body["error"].lower()


# ---------- /api/match/from-file ----------

def test_match_from_file_ranks_cvs() -> None:
    _reset()
    with SessionLocal() as db:
        _add_cv(db, "alice.txt", ALICE)

    with TestClient(app) as client:
        resp = client.post(
            "/api/match/from-file",
            files={"file": ("jd.txt", io.BytesIO(JD_TEXT.encode("utf-8")), "text/plain")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["job"]["job_title"].startswith("Senior AI Engineer")
    assert body["results"]
    top = body["results"][0]
    assert top["filename"] == "alice.txt"
    assert top["overall_score"] > 50


def test_match_from_file_404_when_no_cvs() -> None:
    _reset()
    with TestClient(app) as client:
        resp = client.post(
            "/api/match/from-file",
            files={"file": ("jd.txt", io.BytesIO(JD_TEXT.encode("utf-8")), "text/plain")},
        )
    assert resp.status_code == 404


def test_match_from_file_400_on_bad_extension() -> None:
    _reset()
    with SessionLocal() as db:
        _add_cv(db, "alice.txt", ALICE)
    with TestClient(app) as client:
        resp = client.post(
            "/api/match/from-file",
            files={"file": ("jd.weird", io.BytesIO(b"hi"), "application/octet-stream")},
        )
    assert resp.status_code == 400


# ---------- runner ----------

def _run_all() -> None:
    tests = [
        test_from_file_parses_txt_jd,
        test_from_file_rejects_unknown_extension,
        test_from_file_rejects_empty_file,
        test_match_from_file_ranks_cvs,
        test_match_from_file_404_when_no_cvs,
        test_match_from_file_400_on_bad_extension,
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
