"""LLM-driven skill bucketer for the master CV.

The dictionary-based `_group_skills` in cv_library_builder mis-bucketed
core languages (Python, SQL ended up in "LLM/Applied AI" because the
alias check ran before the Languages check) and dumped everything
unmatched into a giant "Other" group. When the candidate adds new
sources (a GitHub user, a portfolio scrape), fresh tokens like
"Anthropic", "Claude", "Make", "Notion AI" had no home.

This service replaces that with a one-shot LLM call:

  categorise(skills) → list[SkillGroup]

The LLM gets the full skill list and a strict instruction: bucket
every skill into 4-8 sensible groups labelled in career-ops style
(Languages, Frameworks & Libraries, AI / ML & Data Science, Cloud &
DevOps, etc.). Falls back to a deterministic categorizer when the LLM
is off or fails — and the fallback now checks Languages FIRST so the
Python-in-LLM bug stays fixed.
"""
from __future__ import annotations

import json
import logging
from collections import OrderedDict

from app.models.schemas import SkillGroup

logger = logging.getLogger("ai_job_cv_matcher.skill_categorizer")

# Canonical bucket labels in display order — both the LLM and the
# fallback aim for this taxonomy so the master CV always reads the same.
CANONICAL_GROUPS: tuple[str, ...] = (
    "Languages",
    "Frameworks & Libraries",
    "AI / ML & Data Science",
    "Data & Storage",
    "Cloud & DevOps",
    "Infrastructure & Observability",
    "Robotics / Control",
    "Tools & Platforms",
    "Web / E-commerce",
    "Domain Skills",
)


def categorise(skills: list[str]) -> list[SkillGroup]:
    """Bucket `skills` into ordered SkillGroups. Never raises."""
    skills = _dedup(skills)
    if not skills:
        return []
    llm_groups = _llm_categorise(skills)
    if llm_groups is not None:
        return llm_groups
    return _fallback_categorise(skills)


# ---------- LLM path ----------

def _llm_categorise(skills: list[str]) -> list[SkillGroup] | None:
    from app.services import llm_extraction_service as llm

    if not llm.is_enabled():
        return None

    system = (
        "Bucket a CV's technical skills into ordered groups for the "
        "Technical Skills section. Aim for 4-12 groups total.\n\n"
        "Preferred starting labels (use when they fit naturally):\n"
        + "  " + " · ".join(CANONICAL_GROUPS) + "\n\n"
        "You are NOT limited to these. Add a new group whenever the "
        "candidate's skills genuinely cluster around a theme not "
        "covered above. Good examples of custom groups recruiters "
        "recognise: 'Mobile Development', 'Creative & Design', "
        "'Marketing & SEO', 'Productivity Automation', 'Game Dev', "
        "'Hardware & Embedded', 'Security', 'Testing & QA', "
        "'Architecture & Patterns', 'Methodologies'.\n\n"
        "Hard rules:\n"
        "1. Every input skill SHOULD appear in exactly one group when "
        "a coherent group exists. If a skill is truly ambiguous and "
        "doesn't fit any group cleanly (yours or canonical), DROP it "
        "rather than dumping it into a generic 'Other' bucket.\n"
        "2. Never emit a group literally labelled 'Other' or "
        "'Miscellaneous' — invent a better name or omit the skill.\n"
        "3. Languages contains ONLY programming languages (Python, "
        "SQL, TypeScript, C++, Rust, etc.). Never tools/libraries.\n"
        "4. Frameworks & Libraries: web/app frameworks and UI libs "
        "(FastAPI, React, Next.js, Flask, Tailwind, jQuery).\n"
        "5. AI / ML & Data Science: PyTorch, scikit-learn, transformers, "
        "RL, embeddings, fine-tuning, LLM-platform names (OpenAI, "
        "Anthropic, Claude, GPT API, RAG, LangChain, Optuna).\n"
        "6. Data & Storage: PostgreSQL, Redis, SQLite, S3, generic "
        "vector databases not tied to an AI brand.\n"
        "7. Cloud & DevOps: Docker, AWS, GCP, Azure, CI/CD, "
        "Kubernetes, Terraform, GitHub Actions.\n"
        "8. Group order matters — most technically-impressive groups "
        "first (Languages, Frameworks, AI/ML), then infra/tools, then "
        "domain/soft groups at the end.\n"
        "9. Drop an empty group entirely. Don't pad.\n\n"
        "Reply with one JSON object:\n"
        '  {"groups": [{"label": "Languages", "items": ["Python", "SQL"]}, '
        '{"label": "Mobile Development", "items": ["iOS Development", "Android Development"]}, ...]}'
    )
    user = json.dumps({"skills": skills}, ensure_ascii=False)

    try:
        raw = llm._chat_completion(  # type: ignore[attr-defined]
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            json_mode=True,
        )
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Skill LLM categorise failed: %s", exc)
        return None

    raw_groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(raw_groups, list) or not raw_groups:
        logger.warning("Skill LLM returned malformed groups: %r", raw_groups)
        return None

    # ---- Validate: every input skill survives, no duplicates.
    # HARD reject any group labelled "Other" / "Miscellaneous" — the
    # prompt forbids them but the model sometimes ignores. Items
    # inside get dropped (not pooled elsewhere) per user spec.
    surviving: set[str] = set()
    out: list[SkillGroup] = []
    BANNED_LABELS = {"other", "miscellaneous", "misc", "general", ""}
    for g in raw_groups:
        if not isinstance(g, dict):
            continue
        label = (g.get("label") or "").strip()
        if label.lower() in BANNED_LABELS:
            logger.info(
                "Skill categorizer dropped banned group %r with %d items",
                label, len(g.get("items") or []),
            )
            # Still mark these items as "surviving" so the backfill
            # doesn't try to put them back in via the deterministic
            # path — user wants them gone, not relocated.
            for it in (g.get("items") or []):
                if isinstance(it, str) and it.strip():
                    surviving.add(it.strip().lower())
            continue
        items_in = g.get("items") or []
        if not isinstance(items_in, list):
            continue
        clean: list[str] = []
        for it in items_in:
            s = (it or "").strip() if isinstance(it, str) else ""
            if not s:
                continue
            low = s.lower()
            if low in surviving:
                continue
            surviving.add(low)
            clean.append(s)
        if clean:
            out.append(SkillGroup(label=label, items=clean))

    # ---- Backfill anything the LLM dropped — but ONLY via the
    # deterministic categorizer's CANONICAL labels. Anything the
    # fallback would have labelled "Other" we drop on the floor:
    # user wants ungroupable skills hidden rather than dumped into a
    # noisy catch-all.
    dropped = [s for s in skills if s.lower() not in surviving]
    if dropped:
        # Strict mode: deterministic categoriser drops anything that
        # would have landed in "Other" — when LLM is in the pipeline
        # we trust strict groups over completeness.
        backfill = _fallback_categorise(dropped, include_other=False)
        by_label = {g.label.lower(): g for g in out}
        kept, discarded = 0, 0
        for g in backfill:
            if g.label.lower() == "other":
                # Skip — user prefers losing the skill over polluting groups.
                discarded += len(g.items)
                continue
            existing = by_label.get(g.label.lower())
            if existing:
                existing.items.extend(g.items)
            else:
                out.append(g)
            kept += len(g.items)
        logger.info(
            "Skill categorizer backfill: kept=%d discarded=%d (ungroupable)",
            kept, discarded,
        )

    # ---- Re-order: canonical groups in their canonical order; custom
    # labels preserve the LLM-returned order so the model's judgment
    # about what comes first (most impressive) survives.
    canon_lookup = {c.lower(): i for i, c in enumerate(CANONICAL_GROUPS)}
    enumerated = list(enumerate(out))

    def _key(item):
        idx, g = item
        canon_idx = canon_lookup.get(g.label.lower())
        if canon_idx is not None:
            # Canonical group: sort by canonical index, position tiebreak.
            return (0, canon_idx, idx)
        # Custom group: sort to the end, preserve LLM-returned order.
        return (1, idx, idx)

    enumerated.sort(key=_key)
    return [g for _, g in enumerated]


# ---------- Deterministic fallback (fixed-order checks) ----------

# Strict Languages set — checked FIRST so Python never lands in AI/ML.
_LANGUAGES: set[str] = {
    "python", "sql", "javascript", "typescript", "ts", "js",
    "php", "dart", "rust", "java", "c", "c++", "c#", "go", "golang",
    "ruby", "swift", "kotlin", "scala", "r", "matlab", "bash", "shell",
    "perl", "haskell", "elixir", "html", "css",
}

# AI/ML and related (libraries, platforms, techniques).
_AI_ML: set[str] = {
    "pytorch", "tensorflow", "scikit-learn", "sklearn",
    "transformers", "huggingface", "hugging face",
    "numpy", "pandas", "matplotlib", "seaborn",
    "reinforcement learning", "rl", "deep learning", "dl",
    "machine learning", "ml", "computer vision", "cv",
    "natural language processing", "nlp",
    "cnns", "rnns", "lstm", "gan", "vae",
    "retrieval-augmented generation", "rag", "embeddings",
    "vector databases", "faiss", "qdrant", "chromadb", "pinecone",
    "langchain", "openai", "anthropic", "claude", "gpt", "gpt api",
    "llm", "llms", "llm apis", "llm workflows",
    "prompt engineering", "ai agents", "agentic workflows",
    "semantic search", "information retrieval",
    "fine-tuning", "optuna", "neural networks", "neural network",
    "text classification", "policy evaluation", "reward shaping",
    "multi-agent systems", "marl", "ppo", "sac", "ddpg",
    "sentence-transformers",
}

# Robotics / simulation.
_ROBOTICS: set[str] = {
    "ros", "ros 1", "ros 2", "ros1", "ros2",
    "mujoco", "gazebo", "unity", "robotics", "robot learning",
    "sim-to-real", "isaac sim", "isaac gym",
}

# Cloud / DevOps.
_CLOUD_DEVOPS: set[str] = {
    "docker", "kubernetes", "k8s", "aws", "gcp", "azure",
    "terraform", "ansible", "ci/cd", "github actions", "gitlab ci",
    "jenkins", "helm",
}

# Data & storage (non-AI vector DBs).
_DATA_STORAGE: set[str] = {
    "postgresql", "postgres", "mysql", "mongodb", "redis", "sqlite",
    "elasticsearch", "s3", "bigquery", "snowflake", "clickhouse",
}

# Frameworks / libraries (web/app).
_FRAMEWORKS: set[str] = {
    "fastapi", "flask", "django", "react", "next.js", "nextjs",
    "vue", "svelte", "tailwind", "express", "rest apis",
}

# Tools/platforms (workflow + creative).
_TOOLS: set[str] = {
    "git", "linux", "ubuntu", "linux shell scripting",
    "notion", "notion ai", "zapier", "make", "integromat", "airtable",
    "clay", "instantly", "n8n",
    "adobe photoshop", "adobe premiere pro", "after effects",
    "autocad", "icdl",
    "google sheets automation", "google ads", "google webmaster tools",
    "google analytics", "seo", "technical seo", "e-commerce seo",
    "data enrichment", "etl", "csv processing", "web scraping",
    "structured extraction",
}

# Web / e-commerce stack.
_WEB_ECOM: set[str] = {
    "wordpress", "woocommerce", "shopify", "cms development",
    "website building", "web development", "jquery", "bootstrap",
    "lpic", "ux optimization", "algorithm development",
}


def _fallback_label(skill: str) -> str:
    """Order-sensitive bucket lookup. Languages FIRST so Python doesn't
    land in AI/ML."""
    s = skill.strip().lower()
    if s in _LANGUAGES:
        return "Languages"
    if s in _AI_ML:
        return "AI / ML & Data Science"
    if s in _ROBOTICS:
        return "Robotics / Control"
    if s in _CLOUD_DEVOPS:
        return "Cloud & DevOps"
    if s in _DATA_STORAGE:
        return "Data & Storage"
    if s in _FRAMEWORKS:
        return "Frameworks & Libraries"
    if s in _TOOLS:
        return "Tools & Platforms"
    if s in _WEB_ECOM:
        return "Web / E-commerce"
    return "Other"


def _fallback_categorise(skills: list[str], *, include_other: bool = True) -> list[SkillGroup]:
    """Deterministic categoriser. `include_other=False` drops items
    that hit the catch-all bucket (used when caller wants strict
    grouping)."""
    buckets: OrderedDict[str, list[str]] = OrderedDict()
    for s in skills:
        label = _fallback_label(s)
        buckets.setdefault(label, []).append(s)
    out: list[SkillGroup] = []
    # Canonical order first, then anything custom. "Other" goes last
    # if kept, gets dropped entirely if include_other=False.
    for canon in CANONICAL_GROUPS:
        if canon in buckets:
            out.append(SkillGroup(label=canon, items=buckets.pop(canon)))
    for label, items in buckets.items():
        if label == "Other" and not include_other:
            continue
        out.append(SkillGroup(label=label, items=items))
    return out


def _dedup(skills: list[str]) -> list[str]:
    """Two-pass dedup: case-insensitive first (drops "Python" vs
    "python"), then whitespace/punctuation-insensitive (drops
    "ROS 2" vs "ROS2", "C++" vs "C ++").
    Keeps the first occurrence's spelling."""
    import re as _re
    seen_low: set[str] = set()
    seen_norm: set[str] = set()
    out: list[str] = []
    for s in skills or []:
        clean = (s or "").strip()
        if not clean:
            continue
        low = clean.lower()
        if low in seen_low:
            continue
        norm = _re.sub(r"[\s\-_./]+", "", low)
        if norm and norm in seen_norm:
            continue
        seen_low.add(low)
        if norm:
            seen_norm.add(norm)
        out.append(clean)
    return out
