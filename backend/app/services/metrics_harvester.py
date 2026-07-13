"""Metrics harvester — turn activity bullets into impact bullets.

Recruiters rank quantified impact ("cut analyst time 40%", "served 2K
queries/day") far above bare activity ("built a RAG pipeline"). The
polish layer is forbidden from inventing numbers — correctly — so the
only honest source is the candidate.

This service:
  1. Scans the master library for bullets with NO quantification and
     asks the LLM to write ONE sharp question per project/experience
     entry that would elicit a real number (users, latency, time saved,
     accuracy, revenue, scale).
  2. Given the user's answers, rewrites the target bullet to include the
     number and records the edit as a `set_field` user-patch so it
     survives every master rebuild.

Questions are keyed `metric_<entry-slug>` and answers are ALSO stored in
candidate_facts, so the batch autopilot / future harvest runs never
re-ask the same thing.
"""
from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

logger = logging.getLogger("ai_job_cv_matcher.metrics_harvester")

MAX_QUESTIONS = 8

_NUM_RE = re.compile(r"\d")


class _Q(BaseModel):
    section: str          # selected_projects | additional_projects | experience
    index: int            # entry index within the section
    bullet_index: int     # bullet index within the entry
    key: str              # stable slug, e.g. metric_usta_users
    question: str


class _QOut(BaseModel):
    questions: list[_Q] = Field(default_factory=list)


def _entries_snapshot(library_row) -> list[dict]:
    """Flatten entries+bullets that contain no digits (unquantified)."""
    snap: list[dict] = []
    for section in ("selected_projects", "additional_projects", "experience"):
        entries = getattr(library_row, section, None) or []
        for ei, e in enumerate(entries):
            title = e.get("title") if isinstance(e, dict) else getattr(e, "title", "")
            highlights = e.get("highlights") if isinstance(e, dict) else getattr(e, "highlights", [])
            for bi, b in enumerate(highlights or []):
                if not _NUM_RE.search(b or ""):
                    snap.append({
                        "section": section, "index": ei, "bullet_index": bi,
                        "title": title, "bullet": b,
                    })
    return snap


def generate_questions(db: Session) -> list[dict]:
    """Return metric questions for unquantified bullets, skipping any
    already answered in candidate_facts."""
    from app.models.db_models import CVLibrary
    from app.services import candidate_facts as facts
    from app.services import llm_extraction_service as llm

    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None or not llm.is_enabled():
        return []

    snap = _entries_snapshot(row)
    if not snap:
        return []
    known = facts.all_facts(db)

    system = (
        "You help a candidate strengthen their CV with real numbers. "
        "Given bullets that lack ANY quantification, pick the ones where "
        "a number would most impress a recruiter and write ONE direct "
        "question each that elicits a concrete metric (users served, "
        "requests/day, % time saved, accuracy, latency, team size, "
        "revenue). Skip bullets where quantification wouldn't help "
        f"(e.g. certifications). AT MOST {MAX_QUESTIONS} questions, "
        "best-first. Reply JSON:\n"
        '{"questions": [{"section": str, "index": int, "bullet_index": '
        'int, "key": str, "question": str}]}\n'
        "`key` = short stable slug like \"metric_usta_users\" (derive "
        "from the entry title + what's measured). Copy section/index/"
        "bullet_index EXACTLY from the input list."
    )
    user = json.dumps({"bullets": snap[:40]}, ensure_ascii=False)

    try:
        raw = llm._chat_completion(  # type: ignore[attr-defined]
            [{"role": "system", "content": system},
             {"role": "user", "content": user + "\n\nReturn valid JSON only. First char {, last char }."}],
            json_mode=True,
        )
        parsed = _QOut.model_validate(json.loads(_strip_fences(raw)))
    except (json.JSONDecodeError, ValidationError, Exception) as exc:  # noqa: BLE001
        logger.warning("metrics harvester question-gen failed: %s", exc)
        return []

    out = []
    for q in parsed.questions[:MAX_QUESTIONS]:
        key = facts.normalise_key(q.key)
        if not key or key in known:
            continue
        out.append({
            "section": q.section, "index": q.index,
            "bullet_index": q.bullet_index, "key": key,
            "question": (q.question or "").strip(),
        })
    return out


def apply_answer(
    db: Session,
    *,
    section: str,
    index: int,
    bullet_index: int,
    key: str,
    question: str,
    answer: str,
) -> str | None:
    """Weave `answer`'s number into the target bullet, persist as a
    user-patch (survives rebuilds), and remember the fact. Returns the
    rewritten bullet, or None on failure."""
    from app.models.db_models import CVLibrary
    from app.services import candidate_facts as facts
    from app.services import llm_extraction_service as llm
    from app.services.text_guard import clean_bullet, has_meta

    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None:
        return None
    entries = list(getattr(row, section, None) or [])
    if not (0 <= index < len(entries)):
        return None
    entry = entries[index]
    highlights = list(entry.get("highlights") or []) if isinstance(entry, dict) else list(entry.highlights or [])
    if not (0 <= bullet_index < len(highlights)):
        return None
    original = highlights[bullet_index]

    rewritten = original
    if llm.is_enabled():
        system = (
            "Rewrite ONE CV bullet to include the candidate's real metric. "
            "Keep the bullet's claim and voice; weave the number in "
            "naturally; stay under 220 characters; do not invent anything "
            "beyond the provided answer. Reply JSON: {\"bullet\": str}."
        )
        user = json.dumps({
            "bullet": original,
            "metric_question": question,
            "candidate_answer": answer,
        }, ensure_ascii=False)
        try:
            raw = llm._chat_completion(  # type: ignore[attr-defined]
                [{"role": "system", "content": system},
                 {"role": "user", "content": user + "\n\nReturn valid JSON only."}],
                json_mode=True,
            )
            cand = json.loads(_strip_fences(raw)).get("bullet", "")
            cand = clean_bullet(original, cand)
            if cand and not has_meta(cand):
                rewritten = cand
        except Exception as exc:  # noqa: BLE001
            logger.warning("metrics harvester rewrite failed: %s", exc)

    if rewritten == original:
        # LLM off / failed — still record the fact; bullet unchanged.
        facts.upsert_fact(db, key, question, answer)
        return None

    # Apply live + persist as a replayable patch.
    from app.services.user_patches import apply_action
    highlights[bullet_index] = rewritten
    payload = {"section": section, "index": index, "field": "highlights", "value": highlights}
    apply_action(row, "set_field", payload)
    patches = list(getattr(row, "user_patches", None) or [])
    patches.append({"kind": "set_field", "payload": payload})
    row.user_patches = patches
    facts.upsert_fact(db, key, question, answer)
    db.commit()
    return rewritten


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.DOTALL)
        if m:
            return m.group(1)
    return t
