"""Dynamic synonym expansion for JD canonicals.

Static synonyms.py covers a fixed vocabulary the maintainer adds.
That doesn't scale to every domain a user applies to (finance, legal,
biotech, gaming, embedded, etc.). Instead of hand-curating endless
packs, we ask the LLM to look at each JD and return per-canonical
aliases on the fly.

Pipeline:
  1. extract_jd_aliases(jd_text) → {canonical: [aliases]}
  2. Renderer unions these into jd_groups so the project ranker
     scores "behaviour cloning" against "imitation learning",
     "alpha generation" against "factor models", etc., even when
     the static synonym table has no entry.

Per-JD cache (in-memory, keyed on sha1 of normalised JD) so repeated
renders of the same posting don't re-burn LLM tokens.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger("ai_job_cv_matcher.jd_synonyms")

MAX_CANONICALS = 25      # LLM cap — keep tokens bounded
MAX_ALIASES_PER = 6
_CACHE: dict[str, dict[str, list[str]]] = {}


class _LLMOutput(BaseModel):
    domain: str = ""
    canonicals: list[dict] = Field(default_factory=list)


def _cache_key(jd_text: str) -> str:
    norm = re.sub(r"\s+", " ", (jd_text or "")[:4096]).strip().lower()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def expand_jd_aliases(jd_text: str) -> dict[str, list[str]]:
    """Return ``{canonical_lowercased: [alias_lowercased, ...]}`` for
    every domain-specific term the LLM identifies in the JD.

    Empty dict on cache hit miss + LLM unavailable. Never raises."""
    if not (jd_text or "").strip():
        return {}

    key = _cache_key(jd_text)
    if key in _CACHE:
        return _CACHE[key]

    from app.services import llm_extraction_service as llm
    if not llm.is_enabled():
        return {}

    system = (
        "You read a job description and emit canonical domain terms + "
        "their common aliases / abbreviations / paraphrases the "
        "candidate might have used in their CV.\n\n"
        "Examples across domains:\n"
        "  Robotics:  'Vision-Language-Action' -> [VLA, VLM, "
        "vision language action models]\n"
        "  Finance:   'factor models' -> [risk factor models, "
        "Fama-French, factor investing]\n"
        "  Legal AI:  'contract review' -> [contract analysis, "
        "clause extraction, contract intelligence]\n"
        "  Web:       'Server-Side Rendering' -> [SSR, isomorphic "
        "rendering, hydration]\n"
        "  Data Eng:  'Apache Airflow' -> [airflow, workflow "
        "orchestration, DAG scheduling]\n"
        "  ML Res:    'multi-GPU training' -> [distributed training, "
        "DDP, FSDP, model parallelism]\n\n"
        "Output JSON:\n"
        '{"domain": "robotics|finance|legal|web|data_eng|ml_research|'
        'nlp|backend|...|other", "canonicals": [{"term": "...", '
        '"aliases": ["...", "..."]}, ...]}\n\n'
        "Hard rules:\n"
        f"1. Cap canonicals at {MAX_CANONICALS}. Pick the ones MOST "
        "central to the JD. Skip generic tokens (Python, Git, Docker, "
        "REST, AI, ML — the static dictionary handles those).\n"
        f"2. Cap aliases at {MAX_ALIASES_PER} per term. Lower-case "
        "everything (including the term). Include the original JD "
        "phrasing as one of the aliases.\n"
        "3. ONLY emit terms that genuinely appear in the JD or are "
        "tight paraphrases. Don't invent jargon.\n"
        "4. Prefer multi-word terms — these win the ranker."
    )

    try:
        raw = llm._chat_completion(  # type: ignore[attr-defined]
            [
                {"role": "system", "content": system},
                {"role": "user", "content": jd_text[:6000]},
            ],
            json_mode=True,
        )
        data = json.loads(raw)
        parsed = _LLMOutput.model_validate(data)
    except (json.JSONDecodeError, ValidationError, Exception) as exc:  # noqa: BLE001
        logger.warning("JD synonym expander failed: %s", exc)
        _CACHE[key] = {}
        return {}

    out: dict[str, list[str]] = {}
    for entry in parsed.canonicals[:MAX_CANONICALS]:
        if not isinstance(entry, dict):
            continue
        term = (entry.get("term") or "").strip().lower()
        if not term:
            continue
        aliases_raw = entry.get("aliases") or []
        if not isinstance(aliases_raw, list):
            continue
        aliases: list[str] = []
        for a in aliases_raw[:MAX_ALIASES_PER]:
            if isinstance(a, str):
                clean = a.strip().lower()
                if clean and clean != term and clean not in aliases:
                    aliases.append(clean)
        # Term itself always included so caller can iterate aliases AND
        # the canonical without an extra check.
        out[term] = [term] + aliases

    if parsed.domain:
        logger.info(
            "JD synonyms: domain=%s canonicals=%d aliases=%d",
            parsed.domain, len(out),
            sum(len(v) for v in out.values()),
        )
    _CACHE[key] = out
    return out


def expanded_jd_keys(jd_text: str) -> set[str]:
    """Flat set of all LLM-derived keys (canonical + aliases). Used by
    the renderer to enrich its `jd_groups` set."""
    keys: set[str] = set()
    for term, aliases in expand_jd_aliases(jd_text).items():
        keys.add(term)
        keys.update(aliases)
    return keys
