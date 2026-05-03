"""Compose optimised job-search queries from a `UserProfile`.

Pure logic — depends on `tag_engine` for the role / skill / domain
breakdown, then assembles human-readable query strings tuned for typical
job-board search bars.

The output shape matches the spec:

    {
      "queries": [...],
      "tags": {
        "roles": [...],
        "skills": [...],
        "tools": [...],
        "domains": [...],
        "platform_tags": {
          "linkedin": [...],
          "indeed": [...],
          "general": [...]
        }
      }
    }
"""
from __future__ import annotations

from typing import Any

from app.services.tag_engine import build_tags

# Maximum queries returned. The first batch is always the most specific.
_MAX_QUERIES = 10

# Skills paired with each role to make the queries actionable.
# Keep this short — over-stuffed queries get fewer hits, not more.
_SKILLS_PER_QUERY = 3


def _ensure_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        s = (v or "").strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _compose(role: str, skills: list[str]) -> str:
    """Format one query line: 'Senior AI Engineer (Python, FastAPI, RAG)'."""
    role = role.strip()
    skill_chunk = ", ".join(s for s in skills[:_SKILLS_PER_QUERY] if s)
    return f"{role} ({skill_chunk})" if skill_chunk else role


def build_query_payload(profile: Any) -> dict[str, Any]:
    """Return the full {queries, tags} dict for a `UserProfile`-shaped object."""
    tags = build_tags(profile)
    roles: list[str] = tags["roles"]
    skills: list[str] = tags["skills"]
    domains: list[str] = tags["domains"]
    tools: list[str] = tags["tools"]

    queries: list[str] = []

    # 1. Role × top-skills queries — the meat of the result.
    for role in roles[:5]:
        queries.append(_compose(role, skills))

    # 2. Domain × top skills — broader fallback for sites that don't
    #    parse role phrases well (Indeed-style keyword search).
    for domain in domains[:2]:
        if skills:
            queries.append(f"{domain} {' '.join(skills[:_SKILLS_PER_QUERY])}")

    # 3. Remote-friendly variants — flagged as a separate query because
    #    most boards have a remote toggle, but a literal "Remote" keyword
    #    still helps surface remote-first companies in free-text search.
    for role in roles[:2]:
        queries.append(f"{role} Remote")

    # 4. Tools-led query for portfolio sites that index by tech stack.
    if tools and roles:
        queries.append(f"{roles[0]} {tools[0]} {tools[1] if len(tools) > 1 else ''}".strip())

    queries = _ensure_unique(queries)[:_MAX_QUERIES]

    return {
        "queries": queries,
        "tags": tags,
    }
