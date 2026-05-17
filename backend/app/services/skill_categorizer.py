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
        "Bucket a CV's technical skills into 4-8 ordered groups for the "
        "Technical Skills section. Use these canonical labels when they "
        "fit; invent a new label only when the candidate's skills clearly "
        "demand it.\n\n"
        + "  " + " · ".join(CANONICAL_GROUPS) + "\n\n"
        "Hard rules:\n"
        "1. Every input skill MUST appear in exactly one group. No "
        "duplicates across groups, no skill dropped.\n"
        "2. Languages contains ONLY programming languages (Python, SQL, "
        "TypeScript, C++, Rust, etc.). Never tools like Docker or libraries.\n"
        "3. Frameworks & Libraries contains web/app frameworks and UI "
        "libraries (FastAPI, React, Next.js, Flask, Tailwind).\n"
        "4. AI / ML & Data Science: PyTorch, scikit-learn, transformers, "
        "RL, embeddings, fine-tuning, vector DBs that are AI-specific, "
        "LLM-platform names (OpenAI, Anthropic, Claude, GPT API, RAG, "
        "LangChain, Optuna).\n"
        "5. Data & Storage: PostgreSQL, Redis, SQLite, S3, generic vector "
        "databases not tied to an AI brand.\n"
        "6. Cloud & DevOps: Docker, AWS, GCP, Azure, CI/CD, Kubernetes, "
        "Terraform, GitHub Actions.\n"
        "7. Tools & Platforms: Git, Linux, Ubuntu, Notion, Zapier, Make, "
        "Airtable, Adobe Photoshop, n8n. Anything a non-engineer might "
        "use on adjacent workflow.\n"
        "8. Drop a group entirely if it has zero items. Don't pad.\n\n"
        "Reply with one JSON object:\n"
        '  {"groups": [{"label": "Languages", "items": ["Python", "SQL"]}, '
        '{"label": "Frameworks & Libraries", "items": [...]}, ...]}'
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
    surviving: set[str] = set()
    out: list[SkillGroup] = []
    for g in raw_groups:
        if not isinstance(g, dict):
            continue
        label = (g.get("label") or "").strip() or "Other"
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

    # ---- Backfill anything the LLM dropped.
    dropped = [s for s in skills if s.lower() not in surviving]
    if dropped:
        logger.info("Skill categorizer backfilling %d dropped items", len(dropped))
        # Try to slot them via the deterministic categorizer rather
        # than dumping into "Other" so the labels stay coherent.
        backfill = _fallback_categorise(dropped)
        # Merge backfill into existing groups by label, else append.
        by_label = {g.label.lower(): g for g in out}
        for g in backfill:
            existing = by_label.get(g.label.lower())
            if existing:
                existing.items.extend(g.items)
            else:
                out.append(g)

    # ---- Re-order: canonical groups first, custom labels after.
    out.sort(key=lambda g: _order_index(g.label))
    return out


def _order_index(label: str) -> int:
    for i, canon in enumerate(CANONICAL_GROUPS):
        if label.lower() == canon.lower():
            return i
    return len(CANONICAL_GROUPS) + 1  # custom labels last


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


def _fallback_categorise(skills: list[str]) -> list[SkillGroup]:
    buckets: OrderedDict[str, list[str]] = OrderedDict()
    for s in skills:
        label = _fallback_label(s)
        buckets.setdefault(label, []).append(s)
    out: list[SkillGroup] = []
    # Canonical order first, then anything custom.
    for canon in CANONICAL_GROUPS:
        if canon in buckets:
            out.append(SkillGroup(label=canon, items=buckets.pop(canon)))
    for label, items in buckets.items():
        out.append(SkillGroup(label=label, items=items))
    return out


def _dedup(skills: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in skills or []:
        clean = (s or "").strip()
        if not clean:
            continue
        low = clean.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(clean)
    return out
