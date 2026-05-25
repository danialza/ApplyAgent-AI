"""LLM-driven project re-ranker.

Tag-overlap ranking (cv_renderer._rank_entries) gives every matching
canonical the same weight, so a workshop project that tags
[LLM APIs, RAG, prompt engineering] ties with a production
agentic-workflow project that tags the same. JD intent breaks the
tie but the rule-based ranker can't read intent.

This service asks the LLM: "given THIS JD, rank these projects by
how directly they prove the candidate can do the job, weighted by
the JD's bias toward production / research / teaching / etc."

Returns an ordered list of input indices (most-relevant first) plus
optional reasons. Caller blends this with the tag-overlap score
(LLM picks the strong+weak order; tag overlap still drops zero-fit
entries before the LLM ever sees them).

Falls back to None on any failure → caller uses tag-overlap alone.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger("ai_job_cv_matcher.project_ranker")

MAX_PROJECTS_PER_CALL = 30
_CACHE: dict[str, list[int]] = {}


class _LLMItem(BaseModel):
    idx: int
    reason: str = ""


class _LLMOutput(BaseModel):
    ranking: list[_LLMItem] = Field(default_factory=list)


@dataclass
class _DigestEntry:
    idx: int
    title: str
    tags: list[str]
    period: str
    sample_bullet: str


def _digest(projects: list[Any]) -> list[_DigestEntry]:
    out: list[_DigestEntry] = []
    for i, p in enumerate(projects[:MAX_PROJECTS_PER_CALL]):
        bullets = list(getattr(p, "highlights", None) or [])
        out.append(_DigestEntry(
            idx=i,
            title=(getattr(p, "title", "") or "").strip(),
            tags=list(getattr(p, "tags", None) or [])[:6],
            period=(getattr(p, "period", "") or "").strip(),
            sample_bullet=(bullets[0][:160] if bullets else ""),
        ))
    return out


def _cache_key(jd_text: str, digests: list[_DigestEntry]) -> str:
    payload = jd_text[:4000] + "|" + "|".join(
        f"{d.title}::{','.join(d.tags)}" for d in digests
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def rank_projects(jd_text: str, projects: list[Any]) -> list[int] | None:
    """Return ordered indices (most-relevant first) or None when LLM
    is off / fails. Never raises."""
    if not (jd_text or "").strip() or not projects:
        return None

    from app.services import llm_extraction_service as llm
    if not llm.is_enabled():
        return None

    digests = _digest(projects)
    if not digests:
        return None

    key = _cache_key(jd_text, digests)
    if key in _CACHE:
        return _CACHE[key]

    system = (
        "You re-rank a candidate's projects against a specific job "
        "description. The hiring manager scans the top 3-4 projects "
        "on the CV — they decide whether to keep reading.\n\n"
        "Hard rules:\n"
        "1. Reorder by HOW DIRECTLY each project proves the candidate "
        "can do THIS JOB. Tag overlap is necessary but not sufficient. "
        "A workshop that tags 'LLM APIs, RAG, prompt engineering' is "
        "still a TEACHING project — rank it below a production system "
        "with the same tags when JD demands production.\n"
        "2. JD signals to read: 'production' / 'real workflows' / "
        "'end-to-end' / 'shipped' → prefer real systems; 'research' / "
        "'publications' / 'paper' → prefer research; 'agentic' / "
        "'workflow automation' / 'agents' → prefer tools/automation/"
        "agent projects; 'fintech' / 'finance' → prefer business "
        "workflow projects.\n"
        "3. NEVER drop a project — just reorder. Caller already "
        "filtered zero-fit entries. Return EVERY input idx exactly "
        "once.\n"
        "4. Workshop / talk / event / teaching projects rank LAST "
        "unless JD explicitly values communication / training / "
        "advocacy.\n"
        "5. Domain-mismatch (robotics project for a finance JD) ranks "
        "BELOW any plausibly-related project — robotics may still "
        "show transferable skills but recruiters skip when on-topic "
        "work exists.\n\n"
        "Reply JSON: "
        '{"ranking": [{"idx": int, "reason": "<one short clause>"}, ...]}\n'
        "Same idx values as input; cover all of them; most-relevant first."
    )
    user_payload = {
        "jd_excerpt": (jd_text or "")[:3000],
        "projects": [
            {
                "idx": d.idx,
                "title": d.title,
                "tags": d.tags,
                "period": d.period,
                "sample_bullet": d.sample_bullet,
            }
            for d in digests
        ],
    }
    user = json.dumps(user_payload, ensure_ascii=False)

    try:
        raw = llm._chat_completion(  # type: ignore[attr-defined]
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            json_mode=True,
        )
        data = json.loads(raw)
        parsed = _LLMOutput.model_validate(data)
    except (json.JSONDecodeError, ValidationError, Exception) as exc:  # noqa: BLE001
        logger.warning("Project relevance LLM call failed: %s", exc)
        return None

    valid = {d.idx for d in digests}
    seen: set[int] = set()
    ordered: list[int] = []
    for item in parsed.ranking:
        if item.idx in valid and item.idx not in seen:
            seen.add(item.idx)
            ordered.append(item.idx)
    # Backfill any indices the LLM omitted at the tail.
    for d in digests:
        if d.idx not in seen:
            ordered.append(d.idx)

    if len(ordered) != len(digests):
        logger.warning(
            "Project ranker produced %d ordered, expected %d — falling back",
            len(ordered), len(digests),
        )
        return None

    _CACHE[key] = ordered
    if logger.isEnabledFor(logging.INFO):
        names = [digests[i].title for i in ordered[:5]]
        logger.info("Project ranker top-5: %s", names)
    return ordered
