"""Post-render CV scorecard — "would a recruiter bin this?".

Runs after a tailored render and grades the CV the way the two gates
that actually reject people work:

  * the ATS / keyword gate  → is each JD skill EVIDENCED, or merely
    listed in a skills line with nothing behind it?
  * the human 7-second scan → do bullets open with action verbs, carry
    real numbers, and avoid hedge language? does the CV read at the
    seniority the JD asks for? are any hard gates (years, degree,
    authorisation) visibly unmet?

Everything deterministic lives here; the one optional LLM call is the
recruiter-simulation verdict, which degrades to None when the LLM is
off. Never raises — a scorecard failure must not fail a render.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.services.synonyms import canonical, group_key

logger = logging.getLogger("ai_job_cv_matcher.scorecard")


# ---------- lint vocabularies ----------

# Openers that read as ownership. Anything else in first position is a
# weak start for a CV bullet.
_STRONG_VERBS = {
    "architected", "authored", "automated", "benchmarked", "built", "created",
    "cut", "delivered", "deployed", "designed", "developed", "drove",
    "engineered", "established", "evaluated", "expanded", "fine-tuned",
    "grew", "implemented", "improved", "increased", "integrated", "introduced",
    "launched", "led", "migrated", "optimised", "optimized", "orchestrated",
    "productionised", "productionized", "published", "reduced", "refactored",
    "scaled", "shipped", "spearheaded", "streamlined", "trained", "transformed",
    "tuned",
}

# Hedge / passive phrasing that signals "I was near this work" rather
# than "I did this work". Recruiters read these as filler.
_HEDGE_PATTERNS = [
    (re.compile(r"\bfamiliar\w*\b", re.I), "familiar with"),
    (re.compile(r"\bexposure to\b", re.I), "exposure to"),
    (re.compile(r"\bworked on\b", re.I), "worked on"),
    (re.compile(r"\binvolved in\b", re.I), "involved in"),
    (re.compile(r"\bparticipated in\b", re.I), "participated in"),
    (re.compile(r"\bassisted (?:with|in)\b", re.I), "assisted with"),
    (re.compile(r"\bhelped (?:to )?\b", re.I), "helped"),
    (re.compile(r"\bgained \w+ (?:experience|knowledge|familiarity)\b", re.I), "gained experience"),
    (re.compile(r"\b(?:strong |practical |good )?(?:understanding|knowledge) of\b", re.I), "understanding of"),
    (re.compile(r"\bresponsible for\b", re.I), "responsible for"),
    (re.compile(r"\bsupported\b", re.I), "supported"),
    (re.compile(r"\bexperience (?:with|in)\b", re.I), "experience with"),
]

# A REAL impact metric — deliberately stricter than the renderer's
# bolding patterns, which also fire on version numbers ("NED3") and
# GPAs. Here we only count magnitudes a recruiter reads as impact.
_IMPACT_METRIC = re.compile(
    r"""(
        \d+(?:\.\d+)?\s*%                                   # 40%
      | \b\d+(?:\.\d+)?\s*x\b                               # 10x
      | [\$€£]\s?\d+(?:[.,]\d+)?\s*[KMB]?\b                 # $1.2M
      | \b\d[\d,\.]*\s*\+?\s*(?:users|customers|clients|requests|queries|
          documents|records|rows|images|sessions|applications|people|
          engineers|analysts|students|participants|downloads|installs|
          repos|models|experiments|emails|tickets|MAU|DAU|QPS|RPS)\b
      | \b\d+(?:\.\d+)?\s*(?:ms|milliseconds|seconds|minutes|hours|days|weeks|months)\b
      | \b\d+(?:\.\d+)?\s*(?:GB|TB|MB|k|K|M)\b
      | \bfrom\s+\d+[^.]{0,20}?\bto\s+\d+
    )""",
    re.I | re.X,
)

_SENIORITY_ORDER = ["internship", "junior", "mid-level", "senior", "lead", "principal"]


# ---------- helpers ----------

def _iter_bullets(library) -> list[tuple[str, str, str]]:
    """(section, entry_title, bullet) for every rendered-eligible bullet."""
    out: list[tuple[str, str, str]] = []
    for section in ("selected_projects", "additional_projects", "experience"):
        for e in (getattr(library, section, None) or []):
            title = getattr(e, "title", "") or ""
            for b in (getattr(e, "highlights", None) or []):
                if (b or "").strip():
                    out.append((section, title, b.strip()))
    return out


def _strip_latex(text: str) -> str:
    """Rendered LaTeX → the plain text a human/ATS actually reads.

    Cuts the preamble first: without that, \\usepackage and \\newcommand
    bodies survive as noise and the 7-second recruiter scan grades
    layout code instead of the CV.
    """
    t = text or ""
    i = t.find(r"\begin{document}")
    if i >= 0:
        t = t[i + len(r"\begin{document}"):]
    t = re.sub(r"(?<!\\)%.*", " ", t)                                  # comments
    t = re.sub(r"\\textbf\{([^}]*)\}", r"\1", t)
    t = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", t)
    t = re.sub(r"\\(?:section|subsection)\*?\{([^}]*)\}", r"\1. ", t)
    t = re.sub(r"\\(?:begin|end)\{[^}]*\}", " ", t)
    t = re.sub(r"\\item\b", " ", t)
    t = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", " ", t)
    t = t.replace("{", " ").replace("}", " ").replace("\\", " ")
    return re.sub(r"\s+", " ", t).strip()


def _first_word(bullet: str) -> str:
    m = re.match(r"\W*([A-Za-z][\w\-]*)", bullet or "")
    return (m.group(1) if m else "").lower()


# ---------- 1. evidence-based coverage ----------

def _evidence_index(library) -> dict[str, str]:
    """group_key → strongest evidence level found for that term.

    "evidenced" = appears inside a project/experience BULLET (real work)
    "mentioned" = only in a skills group / competency line
    """
    idx: dict[str, str] = {}

    def mark(text: str, level: str) -> None:
        for tok in re.findall(r"[A-Za-z][A-Za-z0-9\+\#\.\-]{1,}", text or ""):
            k = group_key(tok)
            if not k:
                continue
            if level == "evidenced" or idx.get(k) != "evidenced":
                idx[k] = level
        # also index whole multi-word phrases so "vector database" matches
        low = (text or "").lower()
        for n in (2, 3):
            words = re.findall(r"[a-z0-9\+\#\.\-]+", low)
            for i in range(len(words) - n + 1):
                k = group_key(" ".join(words[i:i + n]))
                if k and (level == "evidenced" or idx.get(k) != "evidenced"):
                    idx[k] = level

    for _sec, _title, bullet in _iter_bullets(library):
        mark(bullet, "evidenced")
    for _sec, title, _b in _iter_bullets(library):
        mark(title, "evidenced")
    if getattr(library, "summary", ""):
        mark(library.summary, "mentioned")
    for g in (getattr(library, "skills_groups", None) or []):
        for item in (getattr(g, "items", None) or []):
            mark(item, "mentioned")
    return idx


def _rendered_group_keys(latex: str) -> set[str]:
    """Group keys actually present in the rendered document text."""
    body = _strip_latex(latex).lower()
    keys: set[str] = set()
    words = re.findall(r"[a-z0-9\+\#\.\-]+", body)
    for w in words:
        k = group_key(w)
        if k:
            keys.add(k)
    for n in (2, 3):
        for i in range(len(words) - n + 1):
            k = group_key(" ".join(words[i:i + n]))
            if k:
                keys.add(k)
    return keys


def coverage_breakdown(library, job, latex: str = "") -> dict[str, Any]:
    """Split JD skills into required/preferred and grade each as
    evidenced / mentioned / missing.

    `latex` gates the result: a term is only credited when it survived
    into the RENDERED document. Without that gate a skill the coverage
    booster wove into a bullet that the page-fit loop later trimmed
    would score as evidenced while being absent from the PDF.
    """
    if job is None:
        return {}
    idx = _evidence_index(library)
    rendered = _rendered_group_keys(latex) if latex else None

    def grade(skills: list[str]) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        seen: set[str] = set()
        for s in skills or []:
            disp = canonical(s) or s
            k = group_key(disp)
            if not k or k in seen:
                continue
            seen.add(k)
            state = idx.get(k, "missing")
            if rendered is not None and k not in rendered:
                state = "missing"   # never made it onto the page
            rows.append({"skill": disp, "state": state})
        return rows

    required = grade(list(job.required_skills or []))
    # Technologies named in the JD body are de-facto required signals.
    preferred = grade(
        [s for s in (list(job.preferred_skills or []) + list(job.technologies or []))]
    )
    req_keys = {group_key(r["skill"]) for r in required}
    preferred = [p for p in preferred if group_key(p["skill"]) not in req_keys]

    def pct(rows: list[dict[str, str]], states: tuple[str, ...]) -> float:
        if not rows:
            return 1.0
        return round(sum(1 for r in rows if r["state"] in states) / len(rows), 3)

    return {
        "required": required,
        "preferred": preferred,
        "required_evidenced": pct(required, ("evidenced",)),
        "required_any": pct(required, ("evidenced", "mentioned")),
        "preferred_evidenced": pct(preferred, ("evidenced",)),
        "preferred_any": pct(preferred, ("evidenced", "mentioned")),
        "required_missing": [r["skill"] for r in required if r["state"] == "missing"],
        "required_unevidenced": [r["skill"] for r in required if r["state"] == "mentioned"],
    }


# ---------- 2. metric density + 3. bullet lint ----------

def bullet_audit(library) -> dict[str, Any]:
    bullets = _iter_bullets(library)
    total = len(bullets)
    if total == 0:
        return {"total": 0, "metric_density": 0.0, "weak_openers": [],
                "hedges": [], "strong_verb_ratio": 0.0}

    with_metric = 0
    weak: list[dict[str, str]] = []
    hedges: list[dict[str, str]] = []
    for section, title, b in bullets:
        if _IMPACT_METRIC.search(b):
            with_metric += 1
        if _first_word(b) not in _STRONG_VERBS:
            weak.append({"section": section, "title": title, "bullet": b[:160]})
        for pat, label in _HEDGE_PATTERNS:
            if pat.search(b):
                hedges.append({"section": section, "title": title,
                               "phrase": label, "bullet": b[:160]})
                break

    return {
        "total": total,
        "metric_density": round(with_metric / total, 3),
        "with_metric": with_metric,
        "strong_verb_ratio": round((total - len(weak)) / total, 3),
        "weak_openers": weak[:12],
        "weak_opener_count": len(weak),
        "hedges": hedges[:12],
        "hedge_count": len(hedges),
    }


# ---------- 4. hard gates ----------

_YEARS_RE = re.compile(r"(\d+)\s*\+?\s*(?:years?|yrs?)", re.I)


def hard_gates(job, library, latex: str) -> list[dict[str, str]]:
    """Deterministic checks on the JD's stated hard requirements.

    Only flags gates we can actually evaluate from the document — never
    guesses at things like visa status (that's the gap detector's job,
    answered by the user and stored in candidate_facts).
    """
    if job is None:
        return []
    gates: list[dict[str, str]] = []
    body = _strip_latex(latex).lower()

    # "PhD preferred" / "nice to have" is not a gate — flagging it as one
    # turns every ambitious application into a false no-go.
    soft = re.compile(
        r"\b(preferred|nice[- ]to[- ]have|bonus|a plus|desirable|ideally|"
        r"advantageous|would be great)\b", re.I)

    quals = list(job.qualifications or []) + list(job.education_requirements or [])
    seen_q: set[str] = set()
    for q in quals:
        ql = (q or "").strip()
        if not ql or soft.search(ql):
            continue
        norm = re.sub(r"\W+", " ", ql.lower()).strip()
        if norm in seen_q:
            continue
        seen_q.add(norm)
        # The JD extractor often strips the qualifier, turning
        # "PhD in ML preferred" into "PhD in Machine Learning". Look the
        # phrase back up in the raw posting and honour a soft marker
        # sitting next to it — otherwise every stretch role reads no-go.
        raw = (getattr(job, "raw_text", "") or "")
        if raw:
            head = re.escape(ql.split(",")[0][:40])
            m = re.search(head, raw, re.I)
            if m:
                # Only the REST OF THIS SENTENCE counts — a wider window
                # bleeds into the next requirement and would suppress a
                # genuine gate because the following line says "preferred".
                tail = raw[m.end(): m.end() + 90]
                tail = re.split(r"[.;\n\r•]", tail)[0]
                if soft.search(tail):
                    continue
        m = _YEARS_RE.search(ql)
        if m:
            need = int(m.group(1))
            # Does the CV state a comparable span anywhere?
            have = max((int(x) for x in _YEARS_RE.findall(body)), default=0)
            if have < need:
                gates.append({
                    "kind": "years",
                    "requirement": ql[:160],
                    "status": "unmet" if have == 0 else "weak",
                    "detail": (
                        f"JD asks for {need}+ years; the CV never states a "
                        f"comparable span." if have == 0 else
                        f"JD asks for {need}+ years; the strongest span stated is {have}."
                    ),
                })
        elif re.search(r"\b(phd|doctorate)\b", ql, re.I):
            if not re.search(r"\b(phd|doctorate)\b", body):
                gates.append({"kind": "education", "requirement": ql[:160],
                              "status": "unmet",
                              "detail": "JD asks for a PhD; the CV does not show one."})
        elif re.search(r"\b(master'?s|msc|ma\b|mba)\b", ql, re.I):
            if not re.search(r"\b(master|msc|m\.sc|mba)\b", body):
                gates.append({"kind": "education", "requirement": ql[:160],
                              "status": "unmet",
                              "detail": "JD asks for a Master's; the CV does not show one."})
    return gates[:8]


# ---------- 5. seniority calibration ----------

def seniority_check(job, library) -> dict[str, Any]:
    """Does the CV's language read at the level the JD asks for?"""
    want = (getattr(job, "experience_level", "") or "").strip().lower() if job else ""
    if want not in _SENIORITY_ORDER:
        return {}
    want_i = _SENIORITY_ORDER.index(want)

    lead_signals = re.compile(
        r"\b(led|leading|mentored|owned|ownership|architected|drove|"
        r"spearheaded|managed|co-founder|founded|principal|head of)\b", re.I)
    junior_signals = re.compile(
        r"\b(assisted|learning|coursework|student|intern|familiar\w*|"
        r"exposure to|participated)\b", re.I)

    lead = junior = 0
    for _s, _t, b in _iter_bullets(library):
        if lead_signals.search(b):
            lead += 1
        if junior_signals.search(b):
            junior += 1

    reads = "senior" if lead >= 3 and junior <= 2 else ("junior" if junior > lead else "mid-level")
    reads_i = _SENIORITY_ORDER.index(reads)
    delta = reads_i - want_i
    return {
        "jd_level": want,
        "cv_reads_as": reads,
        "aligned": abs(delta) <= 1,
        "lead_signals": lead,
        "junior_signals": junior,
        "note": (
            "CV language reads below the level this JD asks for — surface "
            "ownership/leadership bullets." if delta < -1 else
            "CV language reads above the JD's level — fine, but trim if it "
            "signals overqualification." if delta > 1 else
            "CV language matches the JD's level."
        ),
    }


# ---------- 6. overall fit + go/no-go ----------

def _fit(cov: dict, bullets: dict, gates: list, seniority: dict, ats_score: int) -> dict[str, Any]:
    """Weighted 0-100. Required-evidence dominates; hard gates penalise."""
    req_ev = cov.get("required_evidenced", 1.0)
    req_any = cov.get("required_any", 1.0)
    pref_ev = cov.get("preferred_evidenced", 1.0)
    metric = min(bullets.get("metric_density", 0.0) / 0.5, 1.0)   # 50% == full marks
    verbs = bullets.get("strong_verb_ratio", 0.0)
    hedge_pen = min(bullets.get("hedge_count", 0) / max(bullets.get("total", 1), 1), 1.0)

    score = (
        40 * req_ev
        + 10 * (req_any - req_ev)      # credit for at least naming it
        + 15 * pref_ev
        + 15 * metric
        + 8 * verbs
        + 12 * (1.0 - hedge_pen)
    )
    if ats_score >= 0:
        score = score * 0.85 + (ats_score * 0.15)
    for g in gates:
        score -= 12 if g.get("status") == "unmet" else 5
    if seniority and not seniority.get("aligned", True):
        score -= 6
    score = max(0, min(100, round(score)))

    unmet = [g for g in gates if g.get("status") == "unmet"]
    missing_req = cov.get("required_missing", [])
    if unmet or len(missing_req) >= 3:
        verdict = "no-go"
    elif score >= 72 and not missing_req:
        verdict = "strong"
    elif score >= 55:
        verdict = "worth-applying"
    else:
        verdict = "weak"
    return {"score": score, "verdict": verdict}


# ---------- 7. recruiter 7-second scan (LLM) ----------

def recruiter_scan(latex: str, job) -> dict[str, Any] | None:
    """Show ONLY what a recruiter sees in the first pass and ask for a
    blunt verdict. Returns None when the LLM is unavailable."""
    import json as _json
    from app.services import llm_extraction_service as llm

    if not llm.is_enabled() or job is None:
        return None
    text = _strip_latex(latex)
    head = " ".join(text.split()[:220])   # ~ the top third of page one

    system = (
        "You are a hiring recruiter screening a stack of CVs. You spend "
        "SEVEN SECONDS on the top of page one before deciding to read on "
        "or bin it. Be blunt and specific — this feedback is used to fix "
        "the CV. Reply JSON only:\n"
        '{"verdict": "read-on"|"unsure"|"bin", '
        '"reason": "<one sentence, concrete>", '
        '"first_impression_role": "<the job title this person appears to be, '
        'judged only from what you read>", '
        '"fixes": ["<specific fix>", "..."]}\n'
        "`fixes` = at most 3, each naming what to change. If the opening "
        "does not clearly match the target role, say so as fix #1."
    )
    user = _json.dumps({
        "target_role": job.job_title or "",
        "target_company": job.company or "",
        "what_the_recruiter_sees": head,
    }, ensure_ascii=False)

    try:
        raw = llm._chat_completion(  # type: ignore[attr-defined]
            [{"role": "system", "content": system},
             {"role": "user", "content": user + "\n\nReturn valid JSON only."}],
            json_mode=True,
        )
        data = llm._coerce_json(raw)  # type: ignore[attr-defined]
        if not isinstance(data, dict):
            return None
        return {
            "verdict": str(data.get("verdict", ""))[:20],
            "reason": str(data.get("reason", ""))[:300],
            "first_impression_role": str(data.get("first_impression_role", ""))[:120],
            "fixes": [str(f)[:200] for f in (data.get("fixes") or [])][:3],
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("recruiter scan failed: %s", exc)
        return None


# ---------- public entry point ----------

def build_scorecard(
    *,
    library,
    job,
    latex: str,
    ats_score: int = -1,
    use_llm: bool = True,
) -> dict[str, Any]:
    """Full scorecard. Never raises."""
    try:
        cov = coverage_breakdown(library, job, latex)
        bullets = bullet_audit(library)
        gates = hard_gates(job, library, latex)
        seniority = seniority_check(job, library)
        fit = _fit(cov, bullets, gates, seniority, ats_score)
        scan = recruiter_scan(latex, job) if use_llm else None
        return {
            "fit_score": fit["score"],
            "verdict": fit["verdict"],
            "coverage": cov,
            "bullets": bullets,
            "hard_gates": gates,
            "seniority": seniority,
            "recruiter_scan": scan,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("scorecard failed")
        return {"fit_score": -1, "verdict": "", "error": str(exc)[:200]}
