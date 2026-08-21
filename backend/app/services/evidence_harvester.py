"""Turn a "claimed but unproven" skill into real, evidenced experience.

The scorecard separates skills that are *evidenced* (they appear inside a
project/experience bullet) from ones merely *mentioned* in the skills
line. A mention with nothing behind it is what a recruiter reads as
padding — they look for it in your bullets and don't find it.

The fix is not to invent a bullet. It is to ask the candidate where they
actually used the tool, and write THAT into the master CV, so every
future tailored CV can evidence it truthfully. Same pattern as the
metrics harvester: ask once, persist as a user-patch, remember the
answer so the question is never asked twice.

Hard rule enforced in the prompt and re-checked in code: the bullet may
only contain what the candidate's answer supports. If the answer does
not describe real hands-on use, nothing is written.
"""
from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

logger = logging.getLogger("ai_job_cv_matcher.evidence_harvester")

MAX_QUESTIONS = 8

# Answers that mean "I have not actually done this" — never write a
# bullet for these, no matter what the model proposes.
_NEGATIVE = re.compile(
    r"^\s*(no|nope|none|never|n/?a|-|skip|haven'?t|have not|don'?t|do not|"
    r"not really|no experience|never used)\b", re.I)


class _Entry(BaseModel):
    section: str          # selected_projects | additional_projects | experience
    index: int
    title: str = ""


class _Proposal(BaseModel):
    section: str
    index: int
    bullet: str


def _entry_catalogue(row) -> list[dict]:
    """Every entry the harvester may attach a bullet to."""
    out: list[dict] = []
    for section in ("selected_projects", "additional_projects", "experience"):
        for i, e in enumerate(getattr(row, section, None) or []):
            title = e.get("title") if isinstance(e, dict) else getattr(e, "title", "")
            hl = e.get("highlights") if isinstance(e, dict) else getattr(e, "highlights", [])
            out.append({
                "section": section,
                "index": i,
                "title": title or "",
                "bullets": len(hl or []),
                "sample": (hl or [""])[0][:120] if hl else "",
            })
    return out


def generate_questions(db: Session, skills: list[str]) -> list[dict]:
    """One targeted question per unevidenced skill, skipping any already
    answered. Deterministic wording — no LLM call needed."""
    from app.models.db_models import CVLibrary
    from app.services import candidate_facts as facts

    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None:
        return []
    known = facts.all_facts(db)
    entries = _entry_catalogue(row)

    out: list[dict] = []
    for s in skills or []:
        s = (s or "").strip()
        if not s:
            continue
        key = facts.normalise_key(f"evidence_{s}")
        if key in known:
            continue
        out.append({
            "skill": s,
            "key": key,
            "question": (
                f"Where did you actually use {s}? Name the project or job and "
                f"what you built or did with it. If you have not used it "
                f"hands-on, write \"no\"."
            ),
            "entries": entries,
        })
        if len(out) >= MAX_QUESTIONS:
            break
    return out


def apply_answer(db: Session, *, skill: str, key: str, question: str, answer: str) -> dict:
    """Write the candidate's real usage into the master CV as a bullet.

    Returns {"status": ..., "entry": ..., "bullet": ...}. Statuses:
      written  — a bullet was added to an existing entry
      declined — the answer says they have not used it (nothing written)
      skipped  — LLM off, unusable answer, or no safe target entry
    """
    from app.models.db_models import CVLibrary
    from app.services import candidate_facts as facts
    from app.services import llm_extraction_service as llm
    from app.services.text_guard import clean_bullet, has_meta
    from app.services.user_patches import apply_action

    answer = (answer or "").strip()
    if not answer:
        return {"status": "skipped", "reason": "empty answer"}
    if _NEGATIVE.match(answer):
        # Remember the "no" too, so we stop asking about this skill.
        facts.upsert_fact(db, key, question, answer)
        return {"status": "declined", "skill": skill}

    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None:
        return {"status": "skipped", "reason": "no library"}
    entries = _entry_catalogue(row)
    if not entries:
        return {"status": "skipped", "reason": "no entries to attach to"}
    if not llm.is_enabled():
        return {"status": "skipped", "reason": "LLM disabled"}

    system = (
        "The candidate is adding a real, previously-unrecorded piece of "
        "experience to their CV. Given their answer, pick the ONE existing "
        "entry it belongs to and write ONE new CV bullet for it.\n"
        "ABSOLUTE RULES:\n"
        "1. The bullet may state ONLY what the candidate's answer supports. "
        "Invent nothing — no metrics, no scale, no tools, no outcomes they "
        "did not mention.\n"
        "2. Open with a strong past-tense action verb (Built, Designed, "
        "Automated, Integrated, Deployed, Wired, Migrated).\n"
        "3. Name the tool explicitly so it is searchable.\n"
        "4. Under 220 characters, one sentence, no first-person pronouns.\n"
        "5. Pick the entry the work genuinely belongs to. If the answer "
        "names a project not in the list, choose the closest one it was "
        "part of.\n"
        "6. If the answer does not describe real hands-on use, return "
        'an empty bullet.\n'
        'Reply JSON only: {"section": str, "index": int, "bullet": str}'
    )
    user = json.dumps({
        "skill": skill,
        "question_asked": question,
        "candidate_answer": answer,
        "existing_entries": entries,
    }, ensure_ascii=False)

    try:
        raw = llm._chat_completion(  # type: ignore[attr-defined]
            [{"role": "system", "content": system},
             {"role": "user", "content": user + "\n\nReturn valid JSON only."}],
            json_mode=True,
        )
        data = llm._coerce_json(raw)  # type: ignore[attr-defined]
        prop = _Proposal.model_validate(data)
    except (json.JSONDecodeError, ValidationError, Exception) as exc:  # noqa: BLE001
        logger.warning("evidence harvester failed for %s: %s", skill, exc)
        return {"status": "skipped", "reason": str(exc)[:120]}

    bullet = clean_bullet("", (prop.bullet or "").strip())
    if not bullet or has_meta(bullet) or len(bullet) < 25:
        return {"status": "skipped", "reason": "model returned no usable bullet"}

    # Sanity: the target must exist, and the bullet must actually name
    # the skill — otherwise it evidences nothing.
    valid = {(e["section"], e["index"]) for e in entries}
    if (prop.section, prop.index) not in valid:
        return {"status": "skipped", "reason": "model chose a non-existent entry"}
    if skill.split()[0].lower() not in bullet.lower():
        logger.info("evidence bullet for %s did not name the skill; skipping", skill)
        return {"status": "skipped", "reason": "bullet does not name the skill"}

    lst = list(getattr(row, prop.section, None) or [])
    entry = dict(lst[prop.index])
    highlights = list(entry.get("highlights") or []) + [bullet]
    payload = {"section": prop.section, "index": prop.index,
               "field": "highlights", "value": highlights}
    apply_action(row, "set_field", payload)
    patches = list(getattr(row, "user_patches", None) or [])
    patches.append({"kind": "set_field", "payload": payload})
    row.user_patches = patches
    facts.upsert_fact(db, key, question, answer)
    db.commit()

    title = next((e["title"] for e in entries
                  if e["section"] == prop.section and e["index"] == prop.index), "")
    logger.info("evidence harvester added %s bullet to %s", skill, title)
    return {"status": "written", "skill": skill, "entry": title, "bullet": bullet}


# ---------- proposal mode: draft it, the candidate approves ----------

class _Metric(BaseModel):
    text: str = ""
    # from_master      -> this number already appears in the CV; safe to reuse
    # needs_your_number-> a placeholder showing WHAT to measure; user must fill
    source: str = "needs_your_number"


class _Draft(BaseModel):
    skill: str
    section: str
    index: int
    usage: str = ""
    metric: _Metric = Field(default_factory=_Metric)
    bullet: str = ""


# A draft is never saved while it still carries a blank the candidate
# has not filled in. Deterministic — not a matter of the model behaving.
_PLACEHOLDER = re.compile(r"(_{2,}|\[[^\]]*\]|<[^>]*>|\bTODO\b|\bN/?A\b|\bXX+\b|\?\?+)", re.I)


def has_placeholder(text: str) -> bool:
    return bool(_PLACEHOLDER.search(text or ""))


def propose(db: Session, skills: list[str]) -> list[dict]:
    """For each unevidenced skill, draft a bullet for the candidate to
    approve: which project it belongs to, how it was probably used, and
    a metric.

    The metric is either lifted from a number already in the master (so
    it is true by construction) or returned as an explicit placeholder
    the candidate has to fill. The model is never asked to guess a
    figure and present it as fact.
    """
    from app.models.db_models import CVLibrary
    from app.services import candidate_facts as facts
    from app.services import llm_extraction_service as llm

    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None or not llm.is_enabled():
        return []
    wanted = [s.strip() for s in (skills or []) if s and s.strip()]
    known = facts.all_facts(db)
    wanted = [s for s in wanted if facts.normalise_key(f"evidence_{s}") not in known]
    if not wanted:
        return []

    entries = _entry_catalogue(row)
    # Full bullet text so the model can ground usage AND reuse real numbers.
    detail = []
    for e in entries:
        src = getattr(row, e["section"], None) or []
        item = src[e["index"]]
        hl = item.get("highlights") if isinstance(item, dict) else getattr(item, "highlights", [])
        detail.append({**{k: e[k] for k in ("section", "index", "title")},
                       "bullets": list(hl or [])})

    system = (
        "The candidate has skills a job asks for that their CV does not "
        "evidence. For EACH skill, draft one bullet they can approve.\n"
        "For each skill return:\n"
        "  section/index — the existing entry this work most plausibly "
        "belongs to, chosen from the list.\n"
        "  usage — a short, concrete guess at how they used the tool ON "
        "THAT project, consistent with what the project already does.\n"
        "  metric — a number for the bullet. If a suitable figure ALREADY "
        'appears in that entry\'s bullets, reuse it verbatim and set '
        'source to "from_master". Otherwise DO NOT invent one: set '
        'source to "needs_your_number" and put a placeholder showing '
        'what to measure, e.g. "~___ enquiries/month" or "cut handling '
        'time ___%".\n'
        "  bullet — the full draft, opening with a strong past-tense verb, "
        "naming the tool explicitly, under 220 characters, no first-person "
        "pronouns, embedding the metric text exactly as given.\n"
        "NEVER state a figure as fact unless it came from the entry's own "
        "bullets. The placeholder is the honest answer when you do not "
        "know.\n"
        'Reply JSON only: {"drafts": [{"skill": str, "section": str, '
        '"index": int, "usage": str, "metric": {"text": str, "source": '
        'str}, "bullet": str}]}'
    )
    user = json.dumps({"skills_to_evidence": wanted, "entries": detail},
                      ensure_ascii=False)

    try:
        raw = llm._chat_completion(  # type: ignore[attr-defined]
            [{"role": "system", "content": system},
             {"role": "user", "content": user + "\n\nReturn valid JSON only."}],
            json_mode=True,
        )
        data = llm._coerce_json(raw) or {}  # type: ignore[attr-defined]
        drafts = [_Draft.model_validate(d) for d in (data.get("drafts") or [])]
    except (json.JSONDecodeError, ValidationError, Exception) as exc:  # noqa: BLE001
        logger.warning("evidence proposal failed: %s", exc)
        return []

    valid = {(e["section"], e["index"]): e["title"] for e in entries}
    out: list[dict] = []
    for d in drafts:
        if (d.section, d.index) not in valid:
            continue
        bullet = (d.bullet or "").strip()
        if not bullet:
            continue
        # A "from_master" claim only counts if the figure really is in
        # that entry — otherwise downgrade it so the UI asks for it.
        src_bullets = " ".join(
            next((x["bullets"] for x in detail
                  if x["section"] == d.section and x["index"] == d.index), []))
        metric_text = (d.metric.text or "").strip()
        source = d.metric.source
        if source == "from_master" and metric_text:
            nums = re.findall(r"\d[\d,\.]*", metric_text)
            if nums and not all(n in src_bullets for n in nums):
                source = "needs_your_number"
        out.append({
            "skill": d.skill,
            "key": facts.normalise_key(f"evidence_{d.skill}"),
            "section": d.section,
            "index": d.index,
            "entry_title": valid[(d.section, d.index)],
            "usage": (d.usage or "").strip(),
            "metric_text": metric_text,
            "metric_source": source,
            "bullet": bullet,
            "needs_number": source != "from_master" or has_placeholder(bullet),
        })
    return out


def apply_bullet(db: Session, *, skill: str, key: str, section: str,
                 index: int, bullet: str, question: str = "") -> dict:
    """Save a bullet the candidate reviewed and approved.

    Refuses anything still carrying a placeholder — an unfilled blank
    means they have not supplied the real number yet.
    """
    from app.models.db_models import CVLibrary
    from app.services import candidate_facts as facts
    from app.services.text_guard import clean_bullet, has_meta
    from app.services.user_patches import apply_action

    bullet = clean_bullet("", (bullet or "").strip())
    if not bullet or len(bullet) < 25 or has_meta(bullet):
        return {"status": "skipped", "skill": skill, "reason": "empty or unusable bullet"}
    if has_placeholder(bullet):
        return {"status": "needs_number", "skill": skill,
                "reason": "still contains a blank — fill in the real figure first"}
    if skill.split()[0].lower() not in bullet.lower():
        return {"status": "skipped", "skill": skill,
                "reason": "bullet does not name the skill"}

    row = db.query(CVLibrary).filter(CVLibrary.id == 1).first()
    if row is None:
        return {"status": "skipped", "reason": "no library"}
    lst = list(getattr(row, section, None) or [])
    if not (0 <= index < len(lst)):
        return {"status": "skipped", "skill": skill, "reason": "entry not found"}

    entry = dict(lst[index])
    highlights = list(entry.get("highlights") or []) + [bullet]
    payload = {"section": section, "index": index,
               "field": "highlights", "value": highlights}
    apply_action(row, "set_field", payload)
    patches = list(getattr(row, "user_patches", None) or [])
    patches.append({"kind": "set_field", "payload": payload})
    row.user_patches = patches
    facts.upsert_fact(db, key, question or f"evidence for {skill}", bullet)
    db.commit()
    return {"status": "written", "skill": skill,
            "entry": entry.get("title", ""), "bullet": bullet}
