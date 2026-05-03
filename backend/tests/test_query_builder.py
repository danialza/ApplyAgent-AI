"""Tests for the smart-query / tag-intelligence layer.

Covers `tag_engine.build_tags` and `query_builder.build_query_payload`
in isolation — duck-typed profile objects, no DB.

    python -m tests.test_query_builder
"""
from __future__ import annotations

from app.services.query_builder import build_query_payload
from app.services.tag_engine import build_tags


class _ProfileLike:
    """Minimal duck-typed object both functions accept."""

    def __init__(self, *, skills, tools, domains, work_experience):
        self.skills = skills
        self.tools_and_technologies = tools
        self.domains = domains
        self.work_experience = work_experience


def _ai_senior_profile() -> _ProfileLike:
    return _ProfileLike(
        skills=[
            {"name": "Python"},
            {"name": "Machine Learning"},
            {"name": "Retrieval Augmented Generation"},
            {"name": "PyTorch"},
            {"name": "Natural Language Processing"},
        ],
        tools=[{"name": "Docker"}, {"name": "AWS"}, {"name": "FAISS"}],
        domains=["AI/ML", "DevOps / Cloud"],
        # 6 years (2019 → 2025) → senior tier.
        work_experience=[{"start_year": 2019, "end_year": 2025}],
    )


# ---------- tag_engine ----------

def test_tags_include_role_and_skills() -> None:
    tags = build_tags(_ai_senior_profile())
    assert any("AI Engineer" in r for r in tags["roles"])
    assert "Python" in tags["skills"]
    assert "AI/ML" in tags["domains"]
    assert "Docker" in tags["tools"]


def test_seniority_prefix_changes_with_years() -> None:
    junior = _ProfileLike(
        skills=[{"name": "Python"}],
        tools=[],
        domains=["AI/ML"],
        # 1 year → junior.
        work_experience=[{"start_year": 2024, "end_year": 2025}],
    )
    tags = build_tags(junior)
    # Junior gets the explicit "Junior" prefix on at least one role.
    assert any(r.startswith("Junior ") for r in tags["roles"])


def test_platform_tags_split() -> None:
    tags = build_tags(_ai_senior_profile())
    pt = tags["platform_tags"]
    # LinkedIn rewards full role phrases — top roles must appear there.
    assert any("AI Engineer" in t for t in pt["linkedin"])
    # Indeed list contains short role + skill keywords.
    assert "Python" in pt["indeed"]
    # General is the lowest-common-denominator union.
    assert "AI/ML" in pt["general"]


def test_empty_profile_returns_empty_tags() -> None:
    empty = _ProfileLike(skills=[], tools=[], domains=[], work_experience=[])
    tags = build_tags(empty)
    assert tags["roles"] == []
    assert tags["skills"] == []
    assert tags["tools"] == []
    assert tags["domains"] == []
    assert tags["platform_tags"]["linkedin"] == []
    assert tags["platform_tags"]["indeed"] == []
    assert tags["platform_tags"]["general"] == []


# ---------- query_builder ----------

def test_query_builder_builds_role_skill_combinations() -> None:
    payload = build_query_payload(_ai_senior_profile())
    queries = payload["queries"]
    assert queries, "expected at least one query"
    # Each role × skill query embeds the role and at least one top skill.
    assert any("Python" in q for q in queries)
    # Senior prefix appears in the role-led queries.
    assert any("Senior" in q for q in queries)


def test_query_builder_emits_remote_variant() -> None:
    payload = build_query_payload(_ai_senior_profile())
    assert any("Remote" in q for q in payload["queries"])


def test_query_builder_returns_unique_queries() -> None:
    payload = build_query_payload(_ai_senior_profile())
    qs = payload["queries"]
    assert len(qs) == len(set(qs)), "queries must be unique"


def test_query_builder_handles_empty_profile() -> None:
    empty = _ProfileLike(skills=[], tools=[], domains=[], work_experience=[])
    payload = build_query_payload(empty)
    # No exceptions, empty arrays.
    assert payload["queries"] == []
    assert payload["tags"]["roles"] == []
    assert payload["tags"]["skills"] == []


def test_query_payload_shape_matches_spec() -> None:
    payload = build_query_payload(_ai_senior_profile())
    # Top-level shape.
    assert set(payload) == {"queries", "tags"}
    tags = payload["tags"]
    assert set(tags) == {"roles", "skills", "tools", "domains", "platform_tags"}
    assert set(tags["platform_tags"]) == {"linkedin", "indeed", "general"}


# ---------- runner ----------

def _run_all() -> None:
    tests = [
        test_tags_include_role_and_skills,
        test_seniority_prefix_changes_with_years,
        test_platform_tags_split,
        test_empty_profile_returns_empty_tags,
        test_query_builder_builds_role_skill_combinations,
        test_query_builder_emits_remote_variant,
        test_query_builder_returns_unique_queries,
        test_query_builder_handles_empty_profile,
        test_query_payload_shape_matches_spec,
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
