"""Sample / unit-test-style checks for the matching engine.

Runs without pytest:
    python -m tests.test_matching_engine
"""
from __future__ import annotations

from types import SimpleNamespace

from app.models.schemas import JobParsed
from app.services.job_parser import parse_job_text
from app.services.matching_engine import match_cv_to_job, rank_cvs
from app.services.scoring_service import aggregate, education_score, skill_score
from app.services.synonyms import canonical, group_key


# ---------- fakes ----------

def _fake_cv(
    cv_id: int,
    name: str,
    skills: list[str],
    experience: list[str] | None = None,
    education: list[str] | None = None,
    projects: list[str] | None = None,
    certifications: list[str] | None = None,
    summary: str = "",
):
    """SimpleNamespace stand-in for the SQLAlchemy CV row.

    The matching engine only reads attributes off the object, so duck typing
    is enough — no DB session required for tests.
    """
    return SimpleNamespace(
        id=cv_id,
        filename=f"{name.lower()}.pdf",
        name=name,
        summary=summary,
        skills=skills,
        experience=experience or [],
        education=education or [],
        projects=projects or [],
        certifications=certifications or [],
        languages=[],
        raw_text=" ".join([summary] + (experience or []) + (projects or []) + skills),
    )


SAMPLE_JD = """\
Job Title: Senior AI Engineer
Required skills
- Python, Machine Learning, NLP, FastAPI, Docker, AWS
Preferred qualifications
- TypeScript, Next.js
Responsibilities
- Build RAG pipelines and ship FastAPI services on AWS.
Education
- Bachelor's in Computer Science or related field.
"""


# ---------- synonyms ----------

def test_synonyms_canonical() -> None:
    assert canonical("ML") == "Machine Learning"
    assert canonical("ts") == "TypeScript"
    assert canonical("WP") == "WordPress"
    assert canonical("LLMs") == "Large Language Models"
    assert canonical("GenAI") == "Generative AI"
    # Unknown skill: returned cleaned but not transformed.
    assert canonical("Rust") == "Rust"


def test_synonyms_group_key_equivalence() -> None:
    assert group_key("ML") == group_key("Machine Learning")
    assert group_key("JS") == group_key("javascript")
    assert group_key("k8s") == group_key("Kubernetes")
    # Different skills must NOT collide.
    assert group_key("Python") != group_key("PyTorch")


# ---------- skill scoring ----------

def test_skill_score_uses_synonyms() -> None:
    cv_skills = ["Python", "JS", "ML"]
    required = ["JavaScript", "Machine Learning", "Python"]
    score, matched, missing, _ = skill_score(cv_skills, required, [])
    assert score == 100.0
    assert set(matched) == {"JavaScript", "Machine Learning", "Python"}
    assert missing == []


def test_skill_score_partial_with_preferred() -> None:
    cv_skills = ["Python", "FastAPI"]
    required = ["Python", "Docker", "AWS"]
    preferred = ["TypeScript"]
    score, matched, missing, _ = skill_score(cv_skills, required, preferred)
    # 1/3 required hit (0.25), 0/1 preferred → 0.75 * 0.333 + 0.25 * 0 = 25.0
    assert score == 25.0
    assert "Python" in matched
    assert set(missing) == {"Docker", "AWS"}


# ---------- education scoring ----------

def test_education_field_recognition() -> None:
    cv = ["B.Sc. Computer Science, MIT 2018"]
    jd = ["Bachelor's in Computer Science"]
    assert education_score(cv, jd) == 100.0

    cv2 = ["B.Sc. History"]
    # Same degree level but field mismatch → small penalty applied.
    assert education_score(cv2, jd) < 100.0


# ---------- aggregator ----------

def test_aggregate_weights() -> None:
    # Sanity: aggregate(100, 100, 100, 100, 100) ≈ 100.
    assert aggregate(100, 100, 100, 100, 100) == 100.0
    # 0.40*80 + 0.25*60 + 0.20*70 + 0.10*50 + 0.05*40 = 33.0?
    expected = round(0.40 * 80 + 0.25 * 60 + 0.20 * 70 + 0.10 * 50 + 0.05 * 40, 2)
    assert aggregate(80, 60, 70, 50, 40) == expected


# ---------- end-to-end matching ----------

def test_match_strong_candidate() -> None:
    job = JobParsed(**parse_job_text(SAMPLE_JD).to_dict())
    cv = _fake_cv(
        1, "Alice Strong",
        skills=["Python", "ML", "NLP", "FastAPI", "Docker", "AWS", "TS", "Next.js"],
        experience=[
            "Senior AI Engineer — Acme (2019 - 2024). Built RAG pipelines and "
            "shipped FastAPI services on AWS using Python."
        ],
        education=["B.Sc. Computer Science"],
        projects=["RAG demo using FAISS and Python"],
    )
    result = match_cv_to_job(cv, job)
    assert result.skill_score == 100.0
    assert result.overall_score >= 75
    assert "Python" in result.matched_skills
    assert "Machine Learning" in result.matched_skills
    assert "strong match" in result.explanation.lower()
    assert len(result.strongest_points) >= 1


def test_match_weak_candidate_has_suggestions() -> None:
    job = JobParsed(**parse_job_text(SAMPLE_JD).to_dict())
    cv = _fake_cv(2, "Bob Weak", skills=["PHP", "WordPress"], experience=["WP dev 2019-2021"])
    result = match_cv_to_job(cv, job)
    assert result.overall_score < 50
    # Per-skill suggestions should reference missing required skills.
    suggestion_text = " ".join(result.improvement_suggestions)
    assert "Python" in suggestion_text
    assert "FastAPI" in suggestion_text
    assert "missing" in result.explanation.lower()


def test_rank_orders_by_overall_score() -> None:
    job_text = SAMPLE_JD
    strong = _fake_cv(
        1, "Strong",
        skills=["Python", "ML", "NLP", "FastAPI", "Docker", "AWS"],
        experience=["Built RAG with FastAPI on AWS for 5 years"],
        education=["B.Sc. Computer Science"],
    )
    weak = _fake_cv(2, "Weak", skills=["WordPress"], experience=["WP dev 2 years"])
    medium = _fake_cv(
        3, "Medium",
        skills=["Python", "FastAPI"],
        experience=["Backend dev 3 years"],
        education=["B.Sc. Mathematics"],
    )
    _, results = rank_cvs([weak, strong, medium], job_text)
    ordered_names = [r.cv_name for r in results]
    assert ordered_names[0] == "Strong"
    assert ordered_names[-1] == "Weak"


def test_match_is_deterministic() -> None:
    job = JobParsed(**parse_job_text(SAMPLE_JD).to_dict())
    cv = _fake_cv(1, "Alice", skills=["Python", "FastAPI"], experience=["Backend dev"])
    a = match_cv_to_job(cv, job)
    b = match_cv_to_job(cv, job)
    assert a.overall_score == b.overall_score
    assert a.matched_skills == b.matched_skills
    assert a.explanation == b.explanation


# ---------- runner ----------

def _run_all() -> None:
    tests = [
        test_synonyms_canonical,
        test_synonyms_group_key_equivalence,
        test_skill_score_uses_synonyms,
        test_skill_score_partial_with_preferred,
        test_education_field_recognition,
        test_aggregate_weights,
        test_match_strong_candidate,
        test_match_weak_candidate_has_suggestions,
        test_rank_orders_by_overall_score,
        test_match_is_deterministic,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    _run_all()
