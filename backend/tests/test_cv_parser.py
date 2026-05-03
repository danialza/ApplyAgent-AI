"""Sample / unit-test-style checks for the CV parser.

Runs without pytest:
    python -m tests.test_cv_parser

Runs with pytest:
    pytest backend/tests
"""
from __future__ import annotations

from app.services.cv_parser import SAMPLE_CV, parse_cv_text
from app.utils.text_cleaning import (
    classify_url,
    extract_contacts,
    is_section_header,
    normalize_header,
    split_csv_like,
)


# ---------- helpers / utils ----------

def test_normalize_header() -> None:
    assert normalize_header("PROFESSIONAL SUMMARY:") == "professional summary"
    assert normalize_header("  Skills  ") == "skills"
    assert normalize_header("Work-Experience") == "work experience"


def test_is_section_header_variants() -> None:
    assert is_section_header("Skills") == "skills"
    assert is_section_header("TECHNICAL SKILLS") == "skills"
    assert is_section_header("Professional Summary:") == "summary"
    assert is_section_header("Work Experience") == "experience"
    assert is_section_header("Languages") == "languages"
    assert is_section_header("Random Heading") is None
    assert is_section_header("Built a thing that does Skills magic for users") is None


def test_classify_url() -> None:
    assert classify_url("https://linkedin.com/in/jane") == "linkedin"
    assert classify_url("github.com/jane/proj") == "github"
    assert classify_url("janedoe.dev/blog") == "portfolio"


def test_split_csv_like() -> None:
    out = split_csv_like("Python, Go; FastAPI | Docker\nKubernetes")
    assert out == ["Python", "Go", "FastAPI", "Docker", "Kubernetes"]


def test_extract_contacts_minimal() -> None:
    text = "Email: a@b.io | Phone: +1 (415) 555-0199 | github.com/foo | linkedin.com/in/foo"
    c = extract_contacts(text)
    assert c["email"] == "a@b.io"
    assert "415" in c["phone"]  # type: ignore[operator]
    assert "linkedin.com/in/foo" in c["linkedin"]  # type: ignore[operator]
    assert "github.com/foo" in c["github"]  # type: ignore[operator]


# ---------- end-to-end against SAMPLE_CV ----------

def test_parse_sample_cv() -> None:
    p = parse_cv_text(SAMPLE_CV)

    assert p.name == "Jane Doe"
    assert "backend engineer" in p.summary.lower()

    assert {"Python", "Go", "FastAPI", "Kubernetes"}.issubset(set(p.skills))

    assert any("Acme" in e for e in p.experience)
    assert any("Globex" in e for e in p.experience)

    assert any("Berkeley" in e for e in p.education)
    assert any("OpenObserve" in e for e in p.projects)
    assert any("AWS" in c for c in p.certifications)
    assert any("English" in lang for lang in p.languages)

    assert p.email == "jane.doe@example.com"
    assert "415" in p.phone
    assert "linkedin.com/in/janedoe" in p.linkedin
    assert "github.com/janedoe" in p.github
    assert "janedoe.dev" in p.portfolio


def test_parse_empty_text_does_not_crash() -> None:
    p = parse_cv_text("")
    assert p.name == "" and p.summary == ""
    assert p.skills == [] and p.experience == []
    assert p.email == "" and p.phone == ""


def test_parse_messy_cv() -> None:
    """No section headers, only contact line. Parser should not crash."""
    messy = "Some Person\nrandom@x.com\nworked at things, did stuff"
    p = parse_cv_text(messy)
    assert p.email == "random@x.com"
    # Skills/experience can legitimately stay empty.
    assert p.skills == []
    assert p.experience == []


# ---------- runner ----------

def _run_all() -> None:
    tests = [
        test_normalize_header,
        test_is_section_header_variants,
        test_classify_url,
        test_split_csv_like,
        test_extract_contacts_minimal,
        test_parse_sample_cv,
        test_parse_empty_text_does_not_crash,
        test_parse_messy_cv,
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
