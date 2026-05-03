"""Skill synonym groups used by the matching engine.

Each group is a `frozenset` of lowercased aliases that all refer to the same
underlying skill. The first item in `_GROUPS` is treated as the canonical
display form.

Lookup is O(1) thanks to a precomputed alias → group_index map.
"""
from __future__ import annotations

import re

# (canonical, aliases...) — all lowercased except the canonical, which is
# kept in pretty form for display.
_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("JavaScript", ("javascript", "js", "ecmascript")),
    ("TypeScript", ("typescript", "ts")),
    ("Machine Learning", ("machine learning", "ml")),
    ("Artificial Intelligence", ("artificial intelligence", "ai")),
    ("Large Language Models", ("large language models", "llms", "llm",
                                "large language model")),
    ("Generative AI", ("generative ai", "genai", "gen ai")),
    ("WordPress", ("wordpress", "wp")),
    ("Natural Language Processing", ("natural language processing", "nlp")),
    ("Deep Learning", ("deep learning", "dl")),
    ("Retrieval Augmented Generation", ("retrieval augmented generation",
                                         "retrieval-augmented generation", "rag")),
    ("Kubernetes", ("kubernetes", "k8s")),
    ("Next.js", ("next.js", "nextjs", "next js")),
    ("Node.js", ("node.js", "nodejs", "node")),
    ("React", ("react", "react.js", "reactjs")),
    ("PostgreSQL", ("postgresql", "postgres")),
    ("AWS", ("aws", "amazon web services")),
    ("GCP", ("gcp", "google cloud", "google cloud platform")),
    ("Computer Vision", ("computer vision", "cv")),
    ("ROS2", ("ros2", "ros 2")),
    ("PID", ("pid", "pid controller", "pid control")),
]


def _normalize(s: str) -> str:
    """Lowercase, collapse whitespace. Used as the synonym lookup key."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


# Build alias -> group index for O(1) lookup, longest-first to avoid
# "ai" shadowing "artificial intelligence".
_ALIAS_TO_GROUP: dict[str, int] = {}
for idx, (_canonical, aliases) in enumerate(_GROUPS):
    for alias in aliases:
        _ALIAS_TO_GROUP[_normalize(alias)] = idx


def canonical(skill: str) -> str:
    """Return the canonical display name for a skill, or the input cleaned."""
    if not skill:
        return ""
    key = _normalize(skill)
    idx = _ALIAS_TO_GROUP.get(key)
    if idx is not None:
        return _GROUPS[idx][0]
    return skill.strip()


def group_key(skill: str) -> str:
    """Return a stable key shared by all aliases of `skill`.

    Two skills are equivalent under synonym rules iff `group_key(a) == group_key(b)`.
    """
    if not skill:
        return ""
    key = _normalize(skill)
    idx = _ALIAS_TO_GROUP.get(key)
    if idx is not None:
        return f"__group_{idx}__"
    return key  # unknown skill — its own group.
