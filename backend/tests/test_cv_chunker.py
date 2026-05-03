"""Sample / unit-test-style checks for the CV chunker.

Runs without numpy / sentence-transformers / faiss.

    python -m tests.test_cv_chunker
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services.cv_chunker import chunk_cv


def _fake_cv():
    return SimpleNamespace(
        id=42,
        name="Jane Doe",
        filename="jane.pdf",
        summary="Backend engineer with 6 years building distributed systems.",
        skills=["Python", "FastAPI", "AWS"],
        experience=[
            "Senior Engineer — Acme (2019-2024). Built FastAPI services on AWS.",
            "Engineer — Globex (2016-2019). Migrated monolith to microservices.",
        ],
        projects=["RAG demo using FAISS + Python"],
        education=["B.Sc. Computer Science"],
        certifications=["AWS Solutions Architect"],
        languages=["English", "Spanish"],
    )


def test_chunk_cv_kinds_and_counts() -> None:
    chunks = chunk_cv(_fake_cv())
    kinds = [c.kind for c in chunks]
    # one each for summary/skills, two experience, one project, one education,
    # one cert, two language entries.
    assert kinds.count("summary") == 1
    assert kinds.count("skills") == 1
    assert kinds.count("experience") == 2
    assert kinds.count("project") == 1
    assert kinds.count("education") == 1
    assert kinds.count("certification") == 1
    assert kinds.count("languages") == 2
    assert len(chunks) == 9


def test_chunk_meta_payload() -> None:
    chunks = chunk_cv(_fake_cv())
    skills_chunk = next(c for c in chunks if c.kind == "skills")
    meta = skills_chunk.to_meta()
    # Skills are joined into a single chunk for a recall-friendly vector.
    assert meta["text"] == "Python, FastAPI, AWS"
    assert meta["cv_id"] == 42
    assert meta["cv_name"] == "Jane Doe"
    assert meta["filename"] == "jane.pdf"
    assert meta["idx"] == 0


def test_empty_cv_returns_no_chunks() -> None:
    cv = SimpleNamespace(
        id=1, name="", filename="empty.pdf", summary="",
        skills=[], experience=[], projects=[], education=[],
        certifications=[], languages=[],
    )
    assert chunk_cv(cv) == []


def test_chunks_skip_empty_entries() -> None:
    cv = SimpleNamespace(
        id=1, name="X", filename="x.pdf", summary="",
        skills=["Python"],
        experience=["", "Real entry"],
        projects=[], education=[], certifications=[], languages=[],
    )
    chunks = chunk_cv(cv)
    # One skills chunk + one experience chunk (the empty entry is skipped).
    assert len(chunks) == 2
    assert any(c.kind == "experience" and c.text == "Real entry" for c in chunks)


def _run_all() -> None:
    tests = [
        test_chunk_cv_kinds_and_counts,
        test_chunk_meta_payload,
        test_empty_cv_returns_no_chunks,
        test_chunks_skip_empty_entries,
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
