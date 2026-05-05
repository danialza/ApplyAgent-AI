"""Tests for the tailored-LaTeX-CV renderer.

Covers escaping, JD-driven section ranking, skill-bolding, and the HTTP
contract for /api/cv/library + /api/cv/render. PDF compilation is tested
only when `tectonic` or `pdflatex` is installed (skipped otherwise so CI
on minimal machines stays green).

    python -m tests.test_cv_renderer
"""
from __future__ import annotations

import os
import shutil
import tempfile

# Temp file DB before any backend import (FastAPI route tests need it).
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="cvrender_test_")
os.close(_DB_FD)
os.environ["APP_DB_URL"] = f"sqlite:///{_DB_PATH}"

from fastapi.testclient import TestClient  # noqa: E402

from app.db.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.db_models import CVLibrary  # noqa: E402
from app.models.schemas import (  # noqa: E402
    CVHeader,
    CVLibraryOut,
    CertificationEntry,
    EducationEntry,
    ExperienceEntryLib,
    JobParsed,
    ProjectEntry,
    PublicationEntry,
    SkillGroup,
)
from app.services.cv_renderer import latex_escape, render_cv  # noqa: E402


# ---------- Fixtures ----------

def _reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    init_db()


def _sample_library() -> CVLibraryOut:
    from datetime import datetime
    return CVLibraryOut(
        id=1,
        header=CVHeader(
            name="Danial Z.",
            location="London, UK",
            email="danial@example.com",
            phone="07304 152749",
            website="https://danielz.co.uk",
            github="https://github.com/danialza",
        ),
        summary=(
            "Applied AI engineer with hands-on Python, FastAPI, and RAG experience."
        ),
        skills_groups=[
            SkillGroup(label="Languages", items=["Python", "SQL", "JavaScript"]),
            SkillGroup(label="LLM / Applied AI", items=["RAG", "prompt engineering"]),
            SkillGroup(label="Web", items=["WordPress", "PHP"]),
        ],
        education=[
            EducationEntry(
                institution="University of Hertfordshire",
                degree="MSc AI & Robotics",
                period="2025--2026",
                highlights=["Distinction, GPA 4.42/5.00."],
            ),
        ],
        selected_projects=[
            ProjectEntry(
                title="AI Job-CV Matching Agent",
                period="2026",
                tags=["Python", "FastAPI", "FAISS", "RAG"],
                highlights=[
                    "Built RAG pipelines with FastAPI and FAISS.",
                    "Explainable scoring for candidate fit.",
                ],
            ),
            ProjectEntry(
                title="WordPress Plugin Builder",
                period="2024",
                tags=["PHP", "WordPress"],
                highlights=["Built a custom WordPress plugin."],
            ),
        ],
        additional_projects=[
            ProjectEntry(
                title="Persian Digit CNN",
                period="2025",
                tags=["PyTorch", "CNN"],
                highlights=["CNN classifier in PyTorch."],
            ),
        ],
        experience=[
            ExperienceEntryLib(
                title="Senior Systems Developer",
                company="Green Wing Co.",
                period="2020--2023",
                tags=["backend", "production"],
                highlights=["Worked on production platforms."],
            ),
        ],
        publications=[
            PublicationEntry(
                status="Under Submission",
                title="Reinforcement Learning Constrained Control",
                venue="",
                tags=["reinforcement learning", "control"],
            ),
        ],
        certifications=[
            CertificationEntry(
                issuer="Microsoft", name="Azure AI Fundamentals (AI-900)",
                tags=["Azure"],
            ),
            CertificationEntry(
                issuer="HarvardX", name="CS50P -- Python",
                tags=["Python"],
            ),
        ],
        languages=["English: Professional", "Farsi: Native"],
        updated_at=datetime.utcnow(),
    )


# ---------- LaTeX escaping ----------

def test_latex_escape_handles_meta_chars() -> None:
    assert latex_escape("R&D 100%") == r"R\&D 100\%"
    assert latex_escape("$10K bonus") == r"\$10K bonus"
    assert latex_escape("snake_case") == r"snake\_case"
    assert latex_escape("a~b^c#d") == r"a\textasciitilde{}b\textasciicircum{}c\#d"
    assert latex_escape("backslash\\here") == r"backslash\textbackslash{}here"


def test_latex_escape_idempotent_on_safe_text() -> None:
    safe = "Senior AI Engineer at Cortex Labs (Berlin)"
    assert latex_escape(safe) == safe


# ---------- Renderer ----------

def test_render_unfiltered_master_cv() -> None:
    """No JD → render everything in library order, no bolding."""
    lib = _sample_library()
    result = render_cv(lib, max_selected_projects=10, max_additional_projects=10)
    tex = result.latex
    # Core sections present.
    assert r"\section{Professional Summary" in tex
    assert r"\section{Technical Skills" in tex
    assert r"\section{Education" in tex
    assert r"\section{Selected AI Projects" in tex
    assert r"\section{Additional Technical Projects" in tex
    assert r"\section{Professional Experience" in tex
    assert r"\section{Certifications" in tex
    assert r"\section{Publications" in tex
    assert r"\section{Languages" in tex
    # Both projects survive (limit not hit).
    assert "AI Job-CV Matching Agent" in tex
    assert "WordPress Plugin Builder" in tex


def test_render_with_jd_ranks_and_bolds() -> None:
    """JD with Python/RAG: AI Matching project ranks first; tokens bolded."""
    lib = _sample_library()
    job = JobParsed(
        job_title="Senior AI Engineer",
        required_skills=["Python", "FastAPI", "RAG", "FAISS"],
        preferred_skills=["TypeScript"],
        technologies=["Python", "FastAPI", "RAG", "FAISS"],
        raw_text="Required: Python, FastAPI, RAG, FAISS.",
    )
    result = render_cv(lib, job=job, max_selected_projects=1)

    # Only top-ranked project survives the cap.
    assert "AI Job-CV Matching Agent" in result.latex
    assert "WordPress Plugin Builder" not in result.latex

    # Matched skills are bolded inside bullets.
    assert r"\textbf{Python}" in result.latex
    assert r"\textbf{FastAPI}" in result.latex
    assert r"\textbf{RAG}" in result.latex

    # `sections_chosen` reports the picks.
    assert result.sections_chosen["selected_projects"] == ["AI Job-CV Matching Agent"]
    assert "Python" in result.matched_skills


def test_render_skill_groups_reorder_by_relevance() -> None:
    """A WordPress JD pushes the Web group above Languages/LLM."""
    lib = _sample_library()
    job = JobParsed(
        job_title="WordPress Developer",
        required_skills=["WordPress", "PHP"],
        technologies=["WordPress", "PHP"],
        raw_text="Required: WordPress, PHP.",
    )
    result = render_cv(lib, job=job, max_selected_projects=2)
    web_pos = result.latex.find(r"\textbf{Web:}")
    lang_pos = result.latex.find(r"\textbf{Languages:}")
    assert 0 < web_pos < lang_pos, "Web group must come before Languages for a WP JD"


def test_render_caps_respected() -> None:
    lib = _sample_library()
    result = render_cv(lib, max_selected_projects=1, max_additional_projects=0)
    assert "Persian Digit CNN" not in result.latex  # additional cap = 0
    # Only one of the two selected projects.
    assert ("AI Job-CV Matching Agent" in result.latex) ^ \
           ("WordPress Plugin Builder" in result.latex)


def test_render_does_not_double_bold() -> None:
    """Skill term inside a bullet shouldn't end up wrapped twice."""
    lib = _sample_library()
    job = JobParsed(required_skills=["Python"], raw_text="Python")
    result = render_cv(lib, job=job)
    # No \textbf{\textbf{...}} sequence.
    assert r"\textbf{\textbf{" not in result.latex


# ---------- HTTP routes ----------

def test_library_get_404_when_unset() -> None:
    _reset_db()
    with TestClient(app) as client:
        resp = client.get("/api/cv/library")
    assert resp.status_code == 404


def test_library_put_then_get_then_render() -> None:
    _reset_db()
    payload = _sample_library().model_dump(exclude={"id", "updated_at"})

    with TestClient(app) as client:
        # PUT — create.
        resp = client.put("/api/cv/library", json=payload)
        assert resp.status_code == 200, resp.text

        # GET — round-trip.
        resp = client.get("/api/cv/library")
        assert resp.status_code == 200
        body = resp.json()
        assert body["header"]["name"] == "Danial Z."
        assert any(p["title"] == "AI Job-CV Matching Agent" for p in body["selected_projects"])

        # POST /render — without JD.
        resp = client.post("/api/cv/render", json={"job_text": ""})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["latex"].startswith(r"\documentclass")
        assert body["compiled"] is False
        assert body["matched_skills"] == []

        # POST /render — with JD that should bold + reorder.
        resp = client.post("/api/cv/render", json={
            "job_text": "Senior AI Engineer. Required: Python, FastAPI, RAG, FAISS.",
            "max_selected_projects": 1,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert "AI Job-CV Matching Agent" in body["latex"]
        assert r"\textbf{Python}" in body["latex"]
        assert "Python" in body["matched_skills"]


def test_render_404_when_library_unset() -> None:
    _reset_db()
    with TestClient(app) as client:
        resp = client.post("/api/cv/render", json={"job_text": "AI Engineer"})
    assert resp.status_code == 404


# ---------- PDF compilation (skipped when no compiler) ----------

def test_pdf_compile_when_available() -> None:
    if not (shutil.which("tectonic") or shutil.which("pdflatex")):
        print("  (skipped — no tectonic / pdflatex on PATH)")
        return
    _reset_db()
    payload = _sample_library().model_dump(exclude={"id", "updated_at"})
    with TestClient(app) as client:
        client.put("/api/cv/library", json=payload)
        resp = client.post("/api/cv/render", json={
            "job_text": "AI Engineer. Required: Python, RAG.",
            "compile_pdf": True,
        })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["compiled"] is True, body.get("compile_error", "")
    # Sanity-check the base64 looks like a PDF.
    import base64
    pdf_bytes = base64.b64decode(body["pdf_b64"])
    assert pdf_bytes.startswith(b"%PDF-")


# ---------- Runner ----------

def _run_all() -> None:
    tests = [
        test_latex_escape_handles_meta_chars,
        test_latex_escape_idempotent_on_safe_text,
        test_render_unfiltered_master_cv,
        test_render_with_jd_ranks_and_bolds,
        test_render_skill_groups_reorder_by_relevance,
        test_render_caps_respected,
        test_render_does_not_double_bold,
        test_library_get_404_when_unset,
        test_library_put_then_get_then_render,
        test_render_404_when_library_unset,
        test_pdf_compile_when_available,
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
