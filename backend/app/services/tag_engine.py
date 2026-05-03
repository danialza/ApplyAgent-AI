"""Generate role / skill / tool / domain / platform tags from a UserProfile.

Pure logic — takes a `UserProfile` ORM row (or any object with the same
attributes) and returns plain dicts that the `/api/profile/queries`
endpoint and the query builder both consume.
"""
from __future__ import annotations

from typing import Any

# Domain → role templates. The first entry in each list is the strongest
# match; later entries broaden the search.
_DOMAIN_ROLE_MAP: dict[str, list[str]] = {
    "AI/ML": [
        "AI Engineer", "Machine Learning Engineer", "NLP Engineer",
        "MLOps Engineer", "Data Scientist", "Applied Scientist",
    ],
    "Backend": [
        "Backend Engineer", "Python Engineer", "Software Engineer",
        "API Engineer",
    ],
    "Frontend": [
        "Frontend Engineer", "Full-stack Engineer", "React Developer",
    ],
    "Web / E-commerce": [
        "WordPress Developer", "WooCommerce Developer",
        "Full-stack Developer", "E-commerce Developer",
    ],
    "Robotics": [
        "Robotics Engineer", "Robotics Software Engineer",
        "Control Systems Engineer",
    ],
    "DevOps / Cloud": [
        "DevOps Engineer", "Cloud Engineer", "Platform Engineer",
        "Site Reliability Engineer",
    ],
}

# Limits — keep the output panel-sized rather than overwhelming.
_MAX_ROLES = 8
_MAX_SKILLS = 12
_MAX_TOOLS = 12

# Number of years considered "fresh" experience for level inference.
_LEVEL_THRESHOLDS = {
    "junior": (0, 2),
    "mid": (2, 5),
    "senior": (5, 8),
    "lead": (8, 999),
}


# ---------- Helpers ----------

def _experience_years(work_experience: list[dict[str, Any]]) -> int:
    """Best-effort total years from `work_experience` entries.

    Each entry can have `start_year` / `end_year` (the profile aggregator
    sets these). Falls back to 0 when nothing is parseable.
    """
    total = 0
    for entry in work_experience or []:
        start = entry.get("start_year")
        end = entry.get("end_year")
        if isinstance(start, int) and isinstance(end, int) and end >= start:
            total += end - start
    return total


def _seniority(level_years: int) -> str:
    if level_years < 2:
        return "junior"
    if level_years < 5:
        return "mid"
    if level_years < 8:
        return "senior"
    return "lead"


def _level_prefix(level: str) -> str:
    """Phrase added in front of role names. `mid` and `junior` add no prefix."""
    return {"senior": "Senior", "lead": "Lead", "junior": "Junior"}.get(level, "")


def _top_skill_names(skills: list[Any], n: int) -> list[str]:
    out: list[str] = []
    for s in skills or []:
        if isinstance(s, dict):
            name = s.get("name", "")
        else:
            name = str(s)
        if name and name not in out:
            out.append(name)
        if len(out) >= n:
            break
    return out


def _roles_for_domains(domains: list[str], level: str) -> list[str]:
    """Compose role labels from declared domains, optionally level-prefixed."""
    seen: set[str] = set()
    out: list[str] = []
    prefix = _level_prefix(level)
    for d in domains or []:
        for role in _DOMAIN_ROLE_MAP.get(d, []):
            label = f"{prefix} {role}".strip() if prefix else role
            if label.lower() in seen:
                continue
            seen.add(label.lower())
            out.append(label)
            if len(out) >= _MAX_ROLES:
                return out
    return out


# ---------- Public API ----------

def build_tags(profile: Any) -> dict[str, Any]:
    """Build the `tags` portion of the smart-query payload from a profile.

    `profile` may be a SQLAlchemy `UserProfile` row or a Pydantic
    `UserProfileOut` — both expose the same attribute names.
    """
    skills_raw = list(getattr(profile, "skills", []) or [])
    tools_raw = list(getattr(profile, "tools_and_technologies", []) or [])
    domains: list[str] = list(getattr(profile, "domains", []) or [])
    work_experience = list(getattr(profile, "work_experience", []) or [])

    skills = _top_skill_names(skills_raw, _MAX_SKILLS)
    tools = _top_skill_names(tools_raw, _MAX_TOOLS)

    level = _seniority(_experience_years(work_experience))
    roles = _roles_for_domains(domains, level)

    # Platform tags — calibrated to each platform's habits:
    #   - LinkedIn rewards full role phrases.
    #   - Indeed rewards short comma-style keywords.
    #   - "general" is the lowest common denominator.
    linkedin = roles[:5] + skills[:5]
    indeed = [t for t in (roles[:3] + skills[:6] + tools[:4]) if t]
    general = sorted(set(roles[:3] + skills[:5] + domains + tools[:3]),
                     key=lambda x: (roles[:3] + skills[:5] + domains + tools[:3]).index(x))

    return {
        "roles": roles,
        "skills": skills,
        "tools": tools,
        "domains": domains,
        "platform_tags": {
            "linkedin": linkedin,
            "indeed": indeed,
            "general": general,
        },
    }
