"""Sample / unit-test-style checks for the JD parser.

Run without pytest:
    python -m tests.test_job_parser

Run with pytest:
    pytest backend/tests
"""
from __future__ import annotations

from app.services.job_parser import parse_job_text


SAMPLE_AI_JD = """\
Job Title: Senior AI Engineer
Company: Cortex Labs
Location: Berlin, Germany (Hybrid)
Salary: €80,000 - €110,000 per year
Employment: Full-time

About the role
We're building production RAG systems on top of LLMs. You'll own the retrieval
pipeline end-to-end — from chunking and embeddings to evaluation.

What you'll do
- Design and ship retrieval-augmented generation pipelines using LangChain and FAISS.
- Train and fine-tune transformer models with PyTorch and Hugging Face.
- Build FastAPI services and deploy on AWS via Docker.
- Mentor junior engineers and collaborate with product.

Required skills
- 5+ years of professional Python experience.
- Strong background in Machine Learning, Deep Learning and NLP.
- Hands-on with LLMs, RAG, and vector databases (FAISS or Chroma).
- Experience with PyTorch, TensorFlow, SQL, and Docker.

Preferred qualifications
- Familiarity with TypeScript / Next.js for internal tooling.
- Prior MLOps experience on AWS or Azure.
- Open-source contributions.

Education
- Master's or PhD in Computer Science, Mathematics, or a related field.
"""


SAMPLE_WORDPRESS_JD = """\
WordPress Developer
Company: Pixel & Co
Location: Remote (UK)
Pay range: £40,000 to £55,000

What we're looking for
- 3+ years of WordPress and WooCommerce development.
- Strong PHP, JavaScript, HTML and CSS skills.
- SEO best practices and Google Analytics experience.
- Bonus: Shopify experience.
"""


SAMPLE_ROBOTICS_JD = """\
Robotics Engineer (Junior)
We're hiring a junior robotics engineer for an on-site role in Munich.
Responsibilities
- Develop ROS2 nodes for autonomous mobile robots.
- Tune PID controllers and validate in Gazebo simulation.
- Prototype control systems in MATLAB / Simulink.
Requirements
- BSc in Robotics, Mechatronics, or Electrical Engineering.
- Experience with ROS, C++, and Python.
"""


def test_ai_jd_parses_metadata() -> None:
    p = parse_job_text(SAMPLE_AI_JD)
    assert p.job_title == "Senior AI Engineer"
    assert p.company == "Cortex Labs"
    assert "Berlin" in p.location
    assert "80,000" in p.salary and "110,000" in p.salary
    assert p.employment_type == "full-time"
    assert p.remote_type == "hybrid"
    assert p.experience_level == "senior"


def test_ai_jd_skills() -> None:
    p = parse_job_text(SAMPLE_AI_JD)
    expected_required = {"Python", "Machine Learning", "Deep Learning", "NLP",
                         "LLM", "RAG", "FAISS", "Chroma", "PyTorch",
                         "TensorFlow", "SQL", "Docker"}
    assert expected_required.issubset(set(p.required_skills)), (
        f"missing: {expected_required - set(p.required_skills)}"
    )
    expected_preferred = {"TypeScript", "Next.js", "AWS", "Azure", "MLOps"}
    assert expected_preferred.issubset(set(p.preferred_skills)), (
        f"missing: {expected_preferred - set(p.preferred_skills)}"
    )
    # Tech list is the union seen anywhere in the JD.
    assert "FastAPI" in p.technologies
    assert "LangChain" in p.technologies
    assert "Hugging Face" in p.technologies


def test_ai_jd_responsibilities_and_education() -> None:
    p = parse_job_text(SAMPLE_AI_JD)
    assert any("RAG" in r or "retrieval" in r.lower() for r in p.responsibilities)
    assert any("Master" in e or "PhD" in e for e in p.education_requirements)


def test_ai_jd_soft_skills() -> None:
    p = parse_job_text(SAMPLE_AI_JD)
    assert "Mentoring" in p.soft_skills
    assert "Collaboration" in p.soft_skills


def test_wordpress_jd() -> None:
    p = parse_job_text(SAMPLE_WORDPRESS_JD)
    assert p.job_title.startswith("WordPress")
    assert p.remote_type == "remote"
    assert "40,000" in p.salary
    skills = set(p.required_skills) | set(p.technologies)
    for s in ("WordPress", "WooCommerce", "PHP", "JavaScript",
              "HTML", "CSS", "SEO", "Google Analytics"):
        assert s in skills, f"{s} not detected"
    assert "Shopify" in p.preferred_skills


def test_robotics_jd() -> None:
    p = parse_job_text(SAMPLE_ROBOTICS_JD)
    assert p.experience_level == "junior"
    assert p.remote_type == "on-site"
    assert any("ROS2" in r or "PID" in r or "Gazebo" in r for r in p.responsibilities)
    skills = set(p.required_skills) | set(p.technologies)
    for s in ("ROS", "ROS2", "PID", "Gazebo", "MATLAB", "Simulink", "C++", "Python"):
        assert s in skills, f"{s} not detected"


def test_empty_input_does_not_crash() -> None:
    p = parse_job_text("")
    assert p.job_title == "" and p.required_skills == []
    assert p.technologies == [] and p.soft_skills == []


def _run_all() -> None:
    tests = [
        test_ai_jd_parses_metadata,
        test_ai_jd_skills,
        test_ai_jd_responsibilities_and_education,
        test_ai_jd_soft_skills,
        test_wordpress_jd,
        test_robotics_jd,
        test_empty_input_does_not_crash,
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
