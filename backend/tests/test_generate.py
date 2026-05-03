"""Tests for the generator + `POST /api/generate`.

Pure unit tests for the deterministic templates, plus integration tests
through FastAPI's TestClient. LLM polish is monkeypatched off (and once
mocked on) so no real network calls happen.

    python -m tests.test_generate
"""
from __future__ import annotations

import os
import tempfile

# Temp file DB before any backend import.
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="generate_test_")
os.close(_DB_FD)
os.environ["APP_DB_URL"] = f"sqlite:///{_DB_PATH}"

from fastapi.testclient import TestClient  # noqa: E402

from app.db.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.db_models import CV  # noqa: E402
from app.models.schemas import (  # noqa: E402
    BulletRewriteSuggestion,
    JobParsed,
    MatchResult,
    TailoringSuggestion,
)
from app.services import llm_extraction_service as llm  # noqa: E402
from app.services.cv_parser import parse_cv_text  # noqa: E402
from app.services.generation_service import (  # noqa: E402
    generate_artefacts,
    generate_cover_letter,
    generate_cv_suggestions,
    generate_linkedin_message,
)


# ---------- Fixtures ----------

ALICE = """\
Alice Strong
alice@example.com

Skills
Python, FastAPI, Machine Learning, RAG, FAISS, Docker, AWS

Experience
- Senior AI Engineer — Acme (2019 - 2025). Built RAG pipelines on AWS using FastAPI.

Education
M.Sc. Computer Science
"""

JD_TEXT = (
    "Senior AI Engineer\n"
    "Company: Cortex Labs\n"
    "Required: Python, Machine Learning, RAG, FAISS, Docker, AWS.\n"
    "Education: Master's in Computer Science.\n"
)


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


def _ensure_llm_disabled() -> None:
    """Make sure the LLM polish path is gated off across tests."""
    os.environ.pop("USE_LLM_EXTRACTION", None)
    os.environ.pop("OPENAI_API_KEY", None)


# ---------- Direct generator tests ----------

def _fake_match(matched: list[str], strongest: list[str]) -> MatchResult:
    return MatchResult(
        cv_id=1, cv_name="Alice Strong", filename="alice.txt",
        overall_score=80, skill_score=90, semantic_score=70,
        experience_score=80, education_score=100, project_score=60,
        matched_skills=matched, missing_skills=["TypeScript"],
        strongest_points=strongest,
        improvement_suggestions=["Quantify your impact."],
        explanation="Strong match.",
    )


def _fake_job() -> JobParsed:
    return JobParsed(
        job_title="Senior AI Engineer",
        company="Cortex Labs",
        location="Berlin",
        experience_level="senior",
        required_skills=["Python", "Machine Learning", "RAG", "FAISS"],
        preferred_skills=["TypeScript"],
        technologies=["Python", "FastAPI", "Docker", "AWS"],
        raw_text=JD_TEXT,
    )


def test_cover_letter_includes_role_skills_and_signature() -> None:
    job = _fake_job()
    match = _fake_match(["Python", "Machine Learning", "RAG"], ["Built RAG pipelines on AWS"])
    text = generate_cover_letter(
        job=job, match=match,
        cv_experience=["Senior AI Engineer — Acme (2019 - 2025)"],
        cv_name="Alice Strong",
    )
    assert "Cortex Labs" in text
    assert "Senior AI Engineer" in text
    assert "Python" in text
    assert "Built RAG pipelines on AWS" in text
    assert text.rstrip().endswith("Alice Strong")


def test_linkedin_message_is_short_and_personal() -> None:
    job = _fake_job()
    match = _fake_match(["Python", "Machine Learning", "RAG"], ["RAG pipelines"])
    msg = generate_linkedin_message(
        job=job, match=match,
        cv_experience=["Senior AI Engineer — Acme (2019 - 2025)"],
        cv_name="Alice Strong",
    )
    # Three sentences + signature line.
    sentences = [s for s in msg.split("\n") if s.strip()]
    assert len(sentences) == 4
    assert "Senior AI Engineer" in msg
    assert "Cortex Labs" in msg
    assert "Python" in msg
    assert msg.rstrip().endswith("Alice Strong")


def test_cv_suggestions_summarises_panel() -> None:
    suggestions = TailoringSuggestion(
        skills_to_add=["TypeScript"],
        skills_to_emphasize=["RAG"],
        keywords_for_ats=["Python", "Machine Learning", "RAG"],
        sections_to_add=["Add a Projects section."],
        bullets_to_rewrite=[BulletRewriteSuggestion(
            original="Led the team.", target_skills=["Python", "RAG"],
            rationale="Weave in matched skills.",
        )],
        summary_hint="Senior-level engineer focused on Python, RAG.",
        generic_tips=["Quantify impact."],
    )
    text = generate_cv_suggestions(suggestions, _fake_job(), "Alice Strong")
    assert "TypeScript" in text
    assert "RAG" in text
    assert "Projects" in text
    assert "Senior-level engineer" in text


def test_cv_suggestions_handles_already_good_match() -> None:
    """No outstanding suggestions → produce a positive paragraph, not blank."""
    empty = TailoringSuggestion()
    text = generate_cv_suggestions(empty, _fake_job(), "Alice Strong")
    assert text  # non-empty
    assert "align" in text.lower() or "match" in text.lower()


def test_generate_artefacts_skips_unrequested_kinds() -> None:
    _ensure_llm_disabled()
    bundle = generate_artefacts(
        kinds=["linkedin_message"],
        job=_fake_job(),
        match=_fake_match(["Python"], ["Recent highlight"]),
        suggestions=TailoringSuggestion(),
        cv_name="Alice Strong",
        cv_experience=["Engineer (2020-2025)"],
        polish_with_llm=False,
    )
    assert bundle.linkedin_message
    assert bundle.cover_letter == ""
    assert bundle.cv_suggestions == ""
    assert bundle.used_llm is False


# ---------- LLM polish path ----------

def test_generate_artefacts_uses_llm_when_enabled() -> None:
    os.environ["USE_LLM_EXTRACTION"] = "true"
    os.environ["OPENAI_API_KEY"] = "test-key"

    polished_text = "Polished cover letter text from the LLM."
    original = llm._chat_completion
    llm._chat_completion = lambda messages: polished_text  # type: ignore[assignment]
    try:
        bundle = generate_artefacts(
            kinds=["cover_letter"],
            job=_fake_job(),
            match=_fake_match(["Python"], ["Highlight"]),
            suggestions=TailoringSuggestion(),
            cv_name="Alice Strong",
            cv_experience=["Engineer (2020-2025)"],
            polish_with_llm=True,
        )
        assert bundle.cover_letter == polished_text
        assert bundle.used_llm is True
    finally:
        llm._chat_completion = original  # type: ignore[assignment]
        _ensure_llm_disabled()


def test_generate_artefacts_falls_back_when_llm_fails() -> None:
    os.environ["USE_LLM_EXTRACTION"] = "true"
    os.environ["OPENAI_API_KEY"] = "test-key"

    original = llm._chat_completion

    def boom(_messages):
        raise RuntimeError("simulated LLM failure")

    llm._chat_completion = boom  # type: ignore[assignment]
    try:
        bundle = generate_artefacts(
            kinds=["cover_letter"],
            job=_fake_job(),
            match=_fake_match(["Python"], ["Highlight"]),
            suggestions=TailoringSuggestion(),
            cv_name="Alice Strong",
            cv_experience=["Engineer (2020-2025)"],
            polish_with_llm=True,
        )
        assert bundle.cover_letter  # deterministic fallback present
        assert bundle.used_llm is False
    finally:
        llm._chat_completion = original  # type: ignore[assignment]
        _ensure_llm_disabled()


# ---------- HTTP route ----------

def test_generate_endpoint_full_response() -> None:
    _reset()
    _ensure_llm_disabled()
    with SessionLocal() as db:
        _add_cv(db, "alice.txt", ALICE)

    with TestClient(app) as client:
        resp = client.post(
            "/api/generate",
            json={
                "job_text": JD_TEXT,
                "kinds": ["cv_suggestions", "cover_letter", "linkedin_message"],
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["best_cv_filename"] == "alice.txt"
    assert body["cv_suggestions"]
    assert body["cover_letter"]
    assert body["linkedin_message"]
    assert body["used_llm"] is False
    # Cover letter mentions the role and the matched skills.
    assert "Senior AI Engineer" in body["cover_letter"]


def test_generate_endpoint_400_on_unknown_kind() -> None:
    _reset()
    with SessionLocal() as db:
        _add_cv(db, "alice.txt", ALICE)
    with TestClient(app) as client:
        resp = client.post(
            "/api/generate",
            json={"job_text": JD_TEXT, "kinds": ["essay"]},
        )
    assert resp.status_code == 400


def test_generate_endpoint_404_when_no_cvs() -> None:
    _reset()
    with TestClient(app) as client:
        resp = client.post(
            "/api/generate",
            json={"job_text": JD_TEXT},
        )
    assert resp.status_code == 404


# ---------- runner ----------

def _run_all() -> None:
    tests = [
        test_cover_letter_includes_role_skills_and_signature,
        test_linkedin_message_is_short_and_personal,
        test_cv_suggestions_summarises_panel,
        test_cv_suggestions_handles_already_good_match,
        test_generate_artefacts_skips_unrequested_kinds,
        test_generate_artefacts_uses_llm_when_enabled,
        test_generate_artefacts_falls_back_when_llm_fails,
        test_generate_endpoint_full_response,
        test_generate_endpoint_400_on_unknown_kind,
        test_generate_endpoint_404_when_no_cvs,
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
