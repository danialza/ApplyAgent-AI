"""Audit the master CV library and surface issues.

Two passes:

  1. Deterministic checks — fast, always run. Catch garbage entries
     (overlong institution fields, melted titles, missing highlights),
     near-duplicates (same project title spelled differently), and
     hard conflicts (same role + company with different periods).

  2. LLM checks — optional, gated on llm_extraction_service.is_enabled.
     Send a compact view of the library, ask Claude to spot
     inconsistencies a recruiter would notice (timeline overlap,
     contradicting summaries, missing experience for a claimed skill).

The endpoint returns a list of `Issue` rows with severity, scope, and
one-line fix-it-by hint. The UI shows them as a banner above the
master preview so the user can decide whether to render anyway.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from app.models.schemas import CVLibraryOut

logger = logging.getLogger("ai_job_cv_matcher.library_quality")

Severity = Literal["error", "warning", "info"]


class FixAction(BaseModel):
    """Machine-applicable patch the UI's `Apply` button posts to
    /api/cv/library/apply-fix. The `kind` discriminates how the
    backend mutates the library; payload fields depend on kind.

    Supported kinds:
      drop_entry        — {section: 'projects'|'experience'|'education'|
                            'certifications'|'publications', index: int}
      set_field         — {section: ..., index: int, field: str, value: str}
      set_summary       — {value: str}
      set_header_field  — {field: str, value: str}
      truncate_field    — {section: ..., index: int, field: str, max_chars: int}
    """
    kind: str
    payload: dict = Field(default_factory=dict)
    preview: str = ""        # human-readable before→after summary for UI


class Issue(BaseModel):
    severity: Severity
    scope: str            # e.g. "education[0]", "projects", "experience[1]"
    title: str            # one-liner shown in UI
    detail: str = ""      # longer explanation, surfaces in tooltip
    fix_hint: str = ""    # suggested action (free text)
    fix_action: Optional["FixAction"] = None  # machine-applicable patch (optional)
    # Stable category — "future_date" / "run_together_words" /
    # "summary_truncated" etc. Used in the fingerprint so LLM-
    # rephrased near-dupes of the same complaint collide and a
    # one-time Ignore stays sticky.
    topic: str = ""
    # Stable hash of (scope, topic) when topic present, fallback
    # (scope, title.lower) otherwise.
    fingerprint: str = ""


class IssuesResponse(BaseModel):
    issues: list[Issue] = Field(default_factory=list)
    counts: dict = Field(default_factory=dict)
    llm_used: bool = False


# ---------- Public entry ----------

def audit(
    library: CVLibraryOut,
    *,
    use_llm: bool = True,
    ignored_fingerprints: set[str] | None = None,
) -> IssuesResponse:
    issues: list[Issue] = []
    issues.extend(_check_education(library))
    issues.extend(_check_projects(library))
    issues.extend(_check_experience(library))
    issues.extend(_check_header(library))
    issues.extend(_check_skills(library))

    llm_used = False
    if use_llm:
        llm_issues = _llm_audit(library)
        if llm_issues is not None:
            issues.extend(llm_issues)
            llm_used = True

    # Stamp fingerprint on every issue (topic-first, title fallback)
    # + filter ignored.
    for iss in issues:
        iss.fingerprint = fingerprint(iss.scope, iss.topic or iss.title)
    if ignored_fingerprints:
        issues = [i for i in issues if i.fingerprint not in ignored_fingerprints]

    counts = {
        "error": sum(1 for i in issues if i.severity == "error"),
        "warning": sum(1 for i in issues if i.severity == "warning"),
        "info": sum(1 for i in issues if i.severity == "info"),
        "total": len(issues),
    }
    return IssuesResponse(issues=issues, counts=counts, llm_used=llm_used)


def fingerprint(scope: str, title: str) -> str:
    """Stable hash of an issue's identity. LLM-rephrased near-dupes
    collide when (scope, title) is the same — good enough for ignore
    lists. Lower-cases title; preserves scope exactly."""
    import hashlib
    raw = f"{scope}|{title.strip().lower()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# ---------- Deterministic checks ----------

_DEGREE_RE = re.compile(r"^(MSc|BSc|PhD|BA|MA|MEng|BEng|MBA|MD)\b", re.IGNORECASE)
_INST_RE = re.compile(r"^(University|Institute|College|School)\b", re.IGNORECASE)


def _check_education(library: CVLibraryOut) -> list[Issue]:
    out: list[Issue] = []
    seen_inst: dict[str, int] = {}
    for i, e in enumerate(library.education or []):
        inst = (e.institution or "").strip()
        deg = (e.degree or "").strip()
        scope = f"education[{i}]"

        if not inst and not deg:
            out.append(Issue(
                severity="error", scope=scope, topic="empty_education_row",
                title="Empty education row",
                detail="Both institution and degree are blank.",
                fix_hint="Remove this entry from the source or re-upload a clean cv.md.",
                fix_action=FixAction(
                    kind="drop_entry",
                    payload={"section": "education", "index": i},
                    preview=f"Delete education[{i}]",
                ),
            ))
            continue
        if len(inst) > 80 and "," in inst:
            # Split heuristically — institution before first comma, anything
            # after starting with MSc/BSc/PhD becomes the degree.
            parts = [p.strip() for p in inst.split(",")]
            inst_re = re.compile(r"^(University|Institute|College|School)\b", re.IGNORECASE)
            deg_re = re.compile(r"^(MSc|BSc|PhD|BA|MA|MEng|BEng|MBA)\b", re.IGNORECASE)
            new_inst = next((p for p in parts if inst_re.match(p)), parts[1] if len(parts) > 1 else "")
            new_deg = next((p for p in parts if deg_re.match(p)), parts[0])
            out.append(Issue(
                severity="warning", scope=scope, topic="overlong_institution",
                title=f"Overlong institution: {inst[:60]}…",
                detail="Parser stuffed the whole row into institution. Click "
                       "Apply to split into clean institution + degree fields.",
                fix_hint="Apply auto-split, or upload cv.md.",
                fix_action=FixAction(
                    kind="split_education",
                    payload={"section": "education", "index": i,
                             "new_institution": new_inst, "new_degree": new_deg},
                    preview=f"institution → {new_inst!r}; degree → {new_deg!r}",
                ),
            ))
        if deg and (len(deg) > 180 or re.search(r"\.\s+[A-Z]", deg)):
            out.append(Issue(
                severity="warning", scope=scope, topic="degree_prose",
                title="Degree field looks like prose",
                detail=f"Degree text: {deg[:80]}…",
                fix_hint="Edit library JSON or upload clean cv.md.",
            ))
        # Duplicate institution → flag for conflict review.
        key = inst.lower()
        if key:
            if key in seen_inst:
                prev = seen_inst[key]
                out.append(Issue(
                    severity="warning", scope=scope, topic="duplicate_institution",
                    title=f"Duplicate institution: {inst[:60]}",
                    detail=f"Also appears at education[{prev}]. Check the "
                           "degree/period fields differ intentionally.",
                    fix_hint="Merge the two rows or remove one source.",
                ))
            seen_inst[key] = i
    return out


def _check_projects(library: CVLibraryOut) -> list[Issue]:
    out: list[Issue] = []
    seen: dict[str, str] = {}
    all_projects = list(library.selected_projects or []) + list(library.additional_projects or [])
    for i, p in enumerate(all_projects):
        scope = f"projects[{i}]"
        title = (p.title or "").strip()
        # Index-into-master: selected first then additional.
        n_sel = len(library.selected_projects or [])
        if i < n_sel:
            sec, real_idx = "selected_projects", i
        else:
            sec, real_idx = "additional_projects", i - n_sel
        if not title:
            out.append(Issue(
                severity="error", scope=scope, topic="project_no_title",
                title="Project with no title",
                detail="Renderer drops these silently. Likely an LLM-polish artifact.",
                fix_hint="Click Apply to drop, or edit library JSON.",
                fix_action=FixAction(
                    kind="drop_entry",
                    payload={"section": sec, "index": real_idx},
                    preview=f"Delete {sec}[{real_idx}]",
                ),
            ))
            continue
        if title.lower() in {"project", "untitled", "unknown"}:
            out.append(Issue(
                severity="error", scope=scope, topic="project_placeholder_title",
                title=f"Placeholder title: {title!r}",
                detail="Looks like a corrupt entry.",
                fix_hint="Click Apply to drop.",
                fix_action=FixAction(
                    kind="drop_entry",
                    payload={"section": sec, "index": real_idx},
                    preview=f"Delete {sec}[{real_idx}]",
                ),
            ))
        if not p.highlights:
            out.append(Issue(
                severity="info", scope=scope, topic="project_no_bullets",
                title=f"Project '{title}' has no bullets",
                detail="Renderer will show the title and date but no content.",
                fix_hint="Add bullets in the source CV / cv.md.",
            ))
        # Near-duplicate detection: ignore casing + spaces.
        norm = re.sub(r"[^a-z0-9]+", "", title.lower())
        if norm in seen:
            out.append(Issue(
                severity="warning", scope=scope, topic="project_near_duplicate",
                title=f"Near-duplicate of {seen[norm]!r}",
                detail=f"Both projects normalise to {norm!r}. Merge into one entry.",
                fix_hint="Delete the smaller source or unify titles in cv.md.",
            ))
        else:
            seen[norm] = title
    return out


def _check_experience(library: CVLibraryOut) -> list[Issue]:
    out: list[Issue] = []
    seen: dict[str, tuple[int, str]] = {}
    for i, x in enumerate(library.experience or []):
        scope = f"experience[{i}]"
        title = (x.title or "").strip()
        company = (x.company or "").strip()
        if not title:
            out.append(Issue(
                severity="error", scope=scope, topic="experience_no_title",
                title="Experience entry with no role title",
                fix_hint="Drop or fix in source CV.",
            ))
            continue
        key = (title.lower(), company.lower())
        if key in seen:
            prev_i, prev_period = seen[key]
            if (x.period or "") != prev_period:
                out.append(Issue(
                    severity="warning", scope=scope, topic="experience_period_conflict",
                    title=f"Conflicting periods for {title} @ {company}",
                    detail=f"experience[{prev_i}] says {prev_period!r}; "
                           f"this one says {x.period!r}.",
                    fix_hint="Keep the more accurate period and delete the other.",
                ))
        else:
            seen[key] = (i, x.period or "")
        # Hint: bullets running over 250 chars often have melted spacing.
        for b in (x.highlights or []):
            if "  " not in b and re.search(r"[a-z][A-Z]", b):
                out.append(Issue(
                    severity="info", scope=scope, topic="run_together_words",
                    title=f"Bullet on {title!r} has run-together words",
                    detail=f"Sample: {b[:80]}",
                    fix_hint="PDF parse artifact — upload cv.md to fix.",
                ))
                break  # one hint per entry
    return out


def _check_header(library: CVLibraryOut) -> list[Issue]:
    out: list[Issue] = []
    h = library.header
    if not (h.name or "").strip():
        out.append(Issue(
            severity="error", scope="header", topic="missing_name",
            title="Missing candidate name",
            fix_hint="Add it to your cv.md or library JSON.",
        ))
    if not (h.email or "").strip():
        out.append(Issue(
            severity="warning", scope="header", topic="missing_email",
            title="Missing email",
            fix_hint="Recruiters need a contact path.",
        ))
    return out


def _check_skills(library: CVLibraryOut) -> list[Issue]:
    out: list[Issue] = []
    total_items = sum(len(g.items or []) for g in (library.skills_groups or []))
    if total_items > 80:
        out.append(Issue(
            severity="info", scope="skills", topic="skills_dense",
            title=f"Skills list is dense ({total_items} items)",
            detail="Recruiters skim. Consider trimming to 50–60 high-signal tokens.",
            fix_hint="Edit cv.md or library JSON.",
        ))
    if not (library.skills_groups or []):
        out.append(Issue(
            severity="warning", scope="skills", topic="skills_empty",
            title="No skills extracted",
            fix_hint="Check your CV / docs actually list skills.",
        ))
    return out


# ---------- LLM check ----------

class _LLMFixAction(BaseModel):
    kind: str
    payload: dict = Field(default_factory=dict)
    preview: str = ""


class _LLMIssue(BaseModel):
    severity: Severity = "warning"
    scope: str = ""
    title: str
    detail: str = ""
    fix_hint: str = ""
    topic: str = ""
    fix_action: Optional[_LLMFixAction] = None


class _LLMOutput(BaseModel):
    issues: list[_LLMIssue] = Field(default_factory=list)


def _llm_audit(library: CVLibraryOut) -> list[Issue] | None:
    """Ask the LLM to spot conflicts / inconsistencies. Returns None
    on any failure — caller falls back to deterministic-only result."""
    from app.services import llm_extraction_service as llm
    if not llm.is_enabled():
        return None

    system = (
        "You audit a candidate's master CV library for issues a "
        "recruiter would notice. Return a JSON object:\n"
        '  {"issues": [{"severity": "error|warning|info", '
        '"scope": "education[0] | selected_projects[1] | ...", '
        '"topic": "<stable_category_slug>", '
        '"title": "<short one-liner>", "detail": "<longer explanation>", '
        '"fix_hint": "<one short action>", "fix_action": null | '
        '{"kind": "...", "payload": {...}, "preview": "..."}}, ...]}\n\n'
        "TOPIC is critical — it MUST be a short stable slug like "
        "'future_date', 'period_conflict', 'summary_truncated', "
        "'experience_overlap', 'overlong_field', 'run_together_words', "
        "'duplicate_entry', 'unbacked_skill_claim', 'date_gap'. The "
        "ignore-list keys off (scope, topic) — if you change the topic "
        "string between runs, a user's Ignore won't stick. Same issue "
        "type ALWAYS gets the same topic slug, even if the title text "
        "varies. Use snake_case. Prefer reusing slugs over inventing "
        "new ones.\n\n"
        "What to flag (limit total to 8 highest-impact issues):\n"
        "1. Same role/company spanning conflicting dates.\n"
        "2. Same project described differently in two entries.\n"
        "3. Education that contradicts itself across rows.\n"
        "4. Summary claims a skill no project/experience backs.\n"
        "5. Date gaps > 12 months between experience entries.\n"
        "6. Parse garbage (overlong fields, run-together words).\n"
        "7. Duplicate-looking entries with slightly different titles.\n"
        "8. Periods in the future (e.g. project dated 2026 in past tense).\n\n"
        "fix_action — INCLUDE one whenever the fix is mechanical. "
        "Supported kinds + payloads:\n"
        '  drop_entry        → {section, index}\n'
        '  set_field         → {section, index, field, value} — for fixing periods, titles, bullets\n'
        '  truncate_field    → {section, index, field, max_chars}\n'
        '  set_summary       → {value} — for rewriting the summary\n'
        '  set_header_field  → {field, value}\n'
        "Section must be one of: selected_projects, additional_projects, "
        "experience, education, certifications, publications. Indices are the "
        "scope's index. preview is a one-line human description of the change.\n"
        "Examples:\n"
        '  Issue: project period is 2026 (future).\n'
        '  → fix_action: {"kind": "set_field", "payload": {"section": '
        '"selected_projects", "index": 1, "field": "period", "value": '
        '"2024 – 2025"}, "preview": "period: 2026 → 2024 – 2025"}\n\n'
        '  Issue: summary text truncated mid-word.\n'
        '  → fix_action: {"kind": "set_summary", "payload": {"value": '
        '"<full cleaned summary>"}, "preview": "rewrite summary, 4 lines"}\n\n'
        "Omit fix_action only when the fix needs human judgment "
        "(e.g. timeline conflict you can't auto-resolve). Don't invent "
        "problems. Empty issues array when library looks clean."
    )

    # Compact view — drop bulk text fields the audit doesn't need.
    compact = {
        "header": {
            "name": library.header.name,
            "email": library.header.email,
        },
        "summary": (library.summary or "")[:400],
        "education": [
            {"institution": e.institution, "degree": e.degree, "period": e.period}
            for e in (library.education or [])
        ],
        "selected_projects": [
            {"title": p.title, "period": p.period,
             "first_bullet": (p.highlights or [""])[0][:120],
             "n_bullets": len(p.highlights or [])}
            for p in (library.selected_projects or [])
        ],
        "additional_projects": [
            {"title": p.title, "period": p.period}
            for p in (library.additional_projects or [])
        ],
        "experience": [
            {"title": x.title, "company": x.company, "period": x.period,
             "n_bullets": len(x.highlights or [])}
            for x in (library.experience or [])
        ],
        "publications_count": len(library.publications or []),
        "certifications_count": len(library.certifications or []),
    }

    try:
        raw = llm._chat_completion(  # type: ignore[attr-defined]
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(compact, ensure_ascii=False)},
            ],
            json_mode=True,
        )
        data = json.loads(raw)
        parsed = _LLMOutput.model_validate(data)
    except (json.JSONDecodeError, ValidationError, Exception) as exc:  # noqa: BLE001
        logger.warning("Library LLM audit failed: %s", exc)
        return None

    out: list[Issue] = []
    for ll in parsed.issues[:8]:
        action = None
        if ll.fix_action and ll.fix_action.kind:
            action = FixAction(
                kind=ll.fix_action.kind,
                payload=ll.fix_action.payload or {},
                preview=ll.fix_action.preview or "",
            )
        out.append(Issue(
            severity=ll.severity, scope=ll.scope, title=ll.title,
            detail=ll.detail, fix_hint=ll.fix_hint, fix_action=action,
            topic=(ll.topic or "").strip(),
        ))
    return out
