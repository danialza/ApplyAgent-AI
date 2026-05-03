"""Tests for the job-discovery service.

Network-free: every upstream call is monkeypatched. Covers source
parsing, dedup, relevance scoring, and graceful per-source failure.

    python -m tests.test_job_discovery
"""
from __future__ import annotations

from app.services import job_discovery
from app.services.job_discovery import (
    DiscoveredJob,
    discover_jobs,
)


# ---------- Fakes ----------

REMOTEOK_PAYLOAD = [
    {"legal": "metadata blob, ignore"},  # first item is metadata.
    {
        "position": "Senior AI Engineer",
        "company": "Cortex Labs",
        "location": "Remote",
        "url": "https://remoteok.com/jobs/1",
        "description": "<p>Build RAG pipelines with Python and FastAPI on AWS.</p>",
        "tags": ["python", "rag", "llm"],
        "date": "2025-01-01",
    },
    {
        "position": "Frontend Engineer",
        "company": "Pixel & Co",
        "location": "Remote",
        "url": "https://remoteok.com/jobs/2",
        "description": "React, Next.js, TypeScript.",
        "tags": ["react", "next.js", "typescript"],
        "date": "2025-01-02",
    },
]

REMOTIVE_PAYLOAD = {
    "jobs": [
        {
            "title": "Machine Learning Engineer",
            "company_name": "Northwind",
            "candidate_required_location": "Worldwide",
            "url": "https://remotive.com/jobs/123",
            "description": "Build NLP models in PyTorch.",
            "tags": ["python", "pytorch", "nlp"],
            "publication_date": "2025-01-03",
        },
        {
            "title": "Senior AI Engineer",
            "company_name": "Cortex Labs",
            "candidate_required_location": "Europe",
            "url": "https://remoteok.com/jobs/1",   # ← duplicate of RemoteOK URL.
            "description": "RAG pipelines.",
            "tags": ["python", "rag"],
            "publication_date": "2025-01-04",
        },
    ]
}

HN_PAYLOAD = {
    "hits": [
        {
            "objectID": "100",
            "comment_text": "Acme Inc — Senior AI Engineer (Remote). We build RAG products with Python.",
            "created_at": "2025-01-05T00:00:00Z",
        },
        {
            "objectID": "101",
            "comment_text": "<p>Off-topic chatter about cooking and weekends.</p>",
            "created_at": "2025-01-05T01:00:00Z",
        },
    ]
}


def _make_fake_get():
    def fake_get(url: str, params=None):
        if "remoteok.com" in url:
            return REMOTEOK_PAYLOAD
        if "remotive.com" in url:
            return REMOTIVE_PAYLOAD
        if "hn.algolia.com" in url:
            return HN_PAYLOAD
        raise RuntimeError(f"unexpected URL {url}")
    return fake_get


def _patch(fn) -> None:
    job_discovery._http_get_json = fn  # type: ignore[assignment]
    # Skip throttle delays in tests.
    job_discovery._throttle = lambda host: None  # type: ignore[assignment]


# ---------- happy path ----------

def test_discover_dedupes_across_sources_and_scores() -> None:
    _patch(_make_fake_get())
    result = discover_jobs(
        queries=["Senior AI Engineer Python RAG"],
        tags={"skills": ["Python", "RAG", "LLM"], "roles": ["AI Engineer"], "domains": ["AI/ML"]},
        sources=["remoteok", "remotive"],
        max_per_source=10,
        max_total=10,
    )
    urls = [j.url for j in result.results]
    # The duplicate URL is dedup'd — only one entry for /jobs/1.
    assert urls.count("https://remoteok.com/jobs/1") == 1
    # AI engineer scores higher than the unrelated frontend role.
    titles = [j.title for j in result.results]
    assert titles[0].lower().startswith("senior ai") or "machine learning" in titles[0].lower()
    # Matched terms include some of the requested skills.
    top = result.results[0]
    assert any(t.lower() in {"python", "rag", "llm"} for t in (m.lower() for m in top.matched_terms))


def test_discover_includes_hn_filtered_to_hiring_text() -> None:
    _patch(_make_fake_get())
    result = discover_jobs(
        queries=["AI Engineer"],
        tags={"skills": ["Python"], "roles": ["AI Engineer"]},
        sources=["hn"],
        max_per_source=10,
        max_total=10,
    )
    assert len(result.results) == 1  # only the comment with "hiring" survived
    assert result.results[0].source == "hn-who-is-hiring"


def test_unknown_source_recorded_as_skipped() -> None:
    _patch(_make_fake_get())
    result = discover_jobs(
        queries=["x"],
        tags={"skills": []},
        sources=["nope"],
        max_per_source=5,
        max_total=5,
    )
    assert result.results == []
    assert "nope" in result.skipped_sources
    assert any(e.get("source") == "nope" for e in result.errors)


def test_failed_source_does_not_break_others() -> None:
    def boom_then_ok(url: str, params=None):
        if "remoteok.com" in url:
            raise RuntimeError("simulated upstream failure")
        return REMOTIVE_PAYLOAD
    _patch(boom_then_ok)
    result = discover_jobs(
        queries=["Engineer"],
        tags={"skills": ["Python"]},
        sources=["remoteok", "remotive"],
        max_per_source=10,
        max_total=10,
    )
    # Remotive results still came through.
    assert any(j.source == "remotive" for j in result.results)
    # Remoteok failure surfaced in errors.
    assert any(e["source"] == "remoteok" for e in result.errors)


def test_empty_tags_still_returns_results_with_zero_score() -> None:
    _patch(_make_fake_get())
    result = discover_jobs(
        queries=["whatever"],
        tags=None,
        sources=["remotive"],
        max_per_source=5,
        max_total=5,
    )
    assert result.results, "expected at least one job"
    assert all(j.relevance_score == 0.0 for j in result.results)


def test_max_total_caps_results() -> None:
    _patch(_make_fake_get())
    result = discover_jobs(
        queries=["x"],
        tags={"skills": ["python"]},
        sources=["remoteok", "remotive"],
        max_per_source=10,
        max_total=2,
    )
    assert len(result.results) == 2


# ---------- Runner ----------

def _run_all() -> None:
    tests = [
        test_discover_dedupes_across_sources_and_scores,
        test_discover_includes_hn_filtered_to_hiring_text,
        test_unknown_source_recorded_as_skipped,
        test_failed_source_does_not_break_others,
        test_empty_tags_still_returns_results_with_zero_score,
        test_max_total_caps_results,
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
    _run_all()
