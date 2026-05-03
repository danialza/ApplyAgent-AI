"""Evaluate the extraction layer (rule-based or LLM, transparent to this script).

Reads a gold JSONL where each line is one of:

    {"kind": "cv",  "id": "...", "raw_text": "...", "expected": {...}}
    {"kind": "job", "id": "...", "raw_text": "...", "expected": {...}}

For each record we run the orchestrator (`extract_cv` / `extract_job`)
and compare the structured output against the gold reference.

Metrics reported per record AND aggregated:

  - For *list* fields (skills, experience, education, …):
        precision / recall / F1 over canonical-form sets.
  - For *scalar* fields (name, summary, job_title, company, location):
        exact-match (case/whitespace insensitive). For `summary`, a
        loose token-overlap pass is also reported.
  - Skill-only P/R/F1 — separate, since "skills" is the matcher's most
    impactful field.
  - Missing required fields count — number of fields the gold expected
    but the prediction left empty.

CLI:

    python evaluate_extraction.py \\
      --gold sample_gold_data.jsonl \\
      --json reports/extraction.json \\
      --markdown reports/extraction.md
"""
from __future__ import annotations

import _paths  # noqa: F401  # side-effect: backend on sys.path

import argparse
import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.extraction import extract_cv, extract_job  # type: ignore
from app.services.synonyms import canonical  # type: ignore

CV_LIST_FIELDS = ["skills", "education", "experience", "projects", "certifications", "languages"]
CV_SCALAR_FIELDS = ["name", "email", "phone", "linkedin", "github", "portfolio", "summary"]

JOB_LIST_FIELDS = [
    "required_skills", "preferred_skills", "responsibilities",
    "qualifications", "education_requirements", "technologies", "soft_skills",
]
JOB_SCALAR_FIELDS = [
    "job_title", "company", "location", "salary",
    "employment_type", "remote_type", "experience_level",
]

REQUIRED_CV_FIELDS = ["name", "skills"]
REQUIRED_JOB_FIELDS = ["job_title", "required_skills"]


# ---------- helpers ----------

def _canon_set(values: list[str], use_synonyms: bool = True) -> set[str]:
    out: set[str] = set()
    for v in values or []:
        if not isinstance(v, str):
            continue
        s = v.strip()
        if not s:
            continue
        if use_synonyms:
            out.add(canonical(s).lower())
        else:
            out.add(" ".join(s.split()).lower())
    return out


def _overlap_set(text: str) -> set[str]:
    """Token set used for loose summary comparison."""
    if not text:
        return set()
    import re
    return {t for t in re.findall(r"[A-Za-z0-9]{3,}", text.lower())}


def _prf(pred_set: set[str], gold_set: set[str]) -> tuple[float, float, float]:
    if not gold_set and not pred_set:
        return 1.0, 1.0, 1.0
    if not pred_set:
        return 0.0, 0.0, 0.0
    if not gold_set:
        # No gold to compare against; precision is undefined → 1.0 if no preds, else 0.0
        return 0.0, 0.0, 0.0
    tp = len(pred_set & gold_set)
    precision = tp / len(pred_set) if pred_set else 0.0
    recall = tp / len(gold_set) if gold_set else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return round(precision, 4), round(recall, 4), round(f1, 4)


def _exact(pred: str, gold: str) -> int:
    return int((pred or "").strip().lower() == (gold or "").strip().lower())


# ---------- core evaluation ----------

@dataclass
class RecordReport:
    id: str
    kind: str
    list_metrics: dict[str, dict[str, float]] = field(default_factory=dict)
    scalar_exact: dict[str, int] = field(default_factory=dict)
    summary_overlap: float | None = None
    skill_prf: dict[str, float] = field(default_factory=dict)
    missing_required: int = 0
    error: str = ""


def _evaluate_cv(record: dict[str, Any]) -> RecordReport:
    rep = RecordReport(id=record.get("id", "?"), kind="cv")
    expected = record.get("expected", {}) or {}
    try:
        parsed = extract_cv(record.get("raw_text", "") or "")
    except Exception as e:  # noqa: BLE001
        rep.error = f"extraction failed: {e}"
        return rep

    pred = parsed.to_dict()

    for field_name in CV_LIST_FIELDS:
        pred_set = _canon_set(pred.get(field_name, []), use_synonyms=(field_name == "skills"))
        gold_set = _canon_set(expected.get(field_name, []), use_synonyms=(field_name == "skills"))
        p, r, f1 = _prf(pred_set, gold_set)
        rep.list_metrics[field_name] = {"precision": p, "recall": r, "f1": f1}

    for field_name in CV_SCALAR_FIELDS:
        if field_name == "summary":
            pred_tokens = _overlap_set(pred.get("summary", ""))
            gold_tokens = _overlap_set(expected.get("summary", ""))
            if gold_tokens:
                rep.summary_overlap = round(len(pred_tokens & gold_tokens) / len(gold_tokens), 4)
            continue
        if field_name in expected:
            rep.scalar_exact[field_name] = _exact(pred.get(field_name, ""), expected.get(field_name, ""))

    rep.skill_prf = rep.list_metrics.get("skills", {"precision": 0.0, "recall": 0.0, "f1": 0.0})

    for f_name in REQUIRED_CV_FIELDS:
        gold_val = expected.get(f_name)
        pred_val = pred.get(f_name)
        gold_present = bool(gold_val) if not isinstance(gold_val, list) else len(gold_val) > 0
        pred_present = bool(pred_val) if not isinstance(pred_val, list) else len(pred_val) > 0
        if gold_present and not pred_present:
            rep.missing_required += 1
    return rep


def _evaluate_job(record: dict[str, Any]) -> RecordReport:
    rep = RecordReport(id=record.get("id", "?"), kind="job")
    expected = record.get("expected", {}) or {}
    try:
        parsed = extract_job(record.get("raw_text", "") or "")
    except Exception as e:  # noqa: BLE001
        rep.error = f"extraction failed: {e}"
        return rep
    pred = parsed.to_dict()

    skill_like = {"required_skills", "preferred_skills", "technologies", "soft_skills"}
    for field_name in JOB_LIST_FIELDS:
        pred_set = _canon_set(pred.get(field_name, []), use_synonyms=field_name in skill_like)
        gold_set = _canon_set(expected.get(field_name, []), use_synonyms=field_name in skill_like)
        p, r, f1 = _prf(pred_set, gold_set)
        rep.list_metrics[field_name] = {"precision": p, "recall": r, "f1": f1}

    for field_name in JOB_SCALAR_FIELDS:
        if field_name in expected and expected[field_name]:
            # `location` is fuzzy: gold "Berlin" should match pred "Berlin, Germany".
            if field_name == "location":
                rep.scalar_exact[field_name] = int(
                    (expected[field_name] or "").strip().lower()
                    in (pred.get(field_name, "") or "").strip().lower()
                )
            else:
                rep.scalar_exact[field_name] = _exact(pred.get(field_name, ""), expected[field_name])

    rep.skill_prf = rep.list_metrics.get("required_skills", {"precision": 0.0, "recall": 0.0, "f1": 0.0})

    for f_name in REQUIRED_JOB_FIELDS:
        gold_val = expected.get(f_name)
        pred_val = pred.get(f_name)
        gold_present = bool(gold_val) if not isinstance(gold_val, list) else len(gold_val) > 0
        pred_present = bool(pred_val) if not isinstance(pred_val, list) else len(pred_val) > 0
        if gold_present and not pred_present:
            rep.missing_required += 1
    return rep


# ---------- aggregation + reporting ----------

def _aggregate(reports: list[RecordReport]) -> dict[str, Any]:
    def avg(values: list[float]) -> float:
        return round(statistics.mean(values), 4) if values else 0.0

    aggregate: dict[str, Any] = {
        "records": len(reports),
        "errors": sum(1 for r in reports if r.error),
        "missing_required_total": sum(r.missing_required for r in reports),
    }

    # Per-field list metrics — average across records that had that field.
    field_avg: dict[str, dict[str, float]] = {}
    for r in reports:
        for fname, m in r.list_metrics.items():
            d = field_avg.setdefault(fname, {"precision": [], "recall": [], "f1": []})
            d["precision"].append(m["precision"])
            d["recall"].append(m["recall"])
            d["f1"].append(m["f1"])
    aggregate["fields"] = {
        fname: {
            "precision": avg(vals["precision"]),
            "recall": avg(vals["recall"]),
            "f1": avg(vals["f1"]),
        }
        for fname, vals in field_avg.items()
    }

    # Skill-only macro across records.
    skill_p = [r.skill_prf["precision"] for r in reports if r.skill_prf]
    skill_r = [r.skill_prf["recall"] for r in reports if r.skill_prf]
    skill_f = [r.skill_prf["f1"] for r in reports if r.skill_prf]
    aggregate["skill"] = {
        "precision": avg(skill_p),
        "recall": avg(skill_r),
        "f1": avg(skill_f),
    }

    # Scalar exact-match rates.
    scalar_field_avg: dict[str, list[int]] = {}
    for r in reports:
        for k, v in r.scalar_exact.items():
            scalar_field_avg.setdefault(k, []).append(v)
    aggregate["scalar_exact_match"] = {
        k: round(sum(v) / len(v), 4) for k, v in scalar_field_avg.items() if v
    }
    return aggregate


def _write_json(path: Path, aggregate: dict[str, Any], reports: list[RecordReport]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "aggregate": aggregate,
        "records": [r.__dict__ for r in reports],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


def _write_markdown(path: Path, aggregate: dict[str, Any], reports: list[RecordReport]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# Extraction evaluation report\n")
    lines.append(f"- Records evaluated: **{aggregate['records']}**")
    lines.append(f"- Records with errors: **{aggregate['errors']}**")
    lines.append(f"- Missing required fields (total across records): **{aggregate['missing_required_total']}**\n")

    lines.append("## Field-level metrics (average across records)\n")
    lines.append("| Field | Precision | Recall | F1 |")
    lines.append("|---|---:|---:|---:|")
    for fname, m in sorted(aggregate.get("fields", {}).items()):
        lines.append(f"| `{fname}` | {m['precision']:.3f} | {m['recall']:.3f} | {m['f1']:.3f} |")

    lines.append("\n## Skills only\n")
    s = aggregate.get("skill", {})
    lines.append(f"- Precision: **{s.get('precision', 0):.3f}**")
    lines.append(f"- Recall: **{s.get('recall', 0):.3f}**")
    lines.append(f"- F1: **{s.get('f1', 0):.3f}**\n")

    lines.append("## Scalar exact-match rate\n")
    for k, v in sorted(aggregate.get("scalar_exact_match", {}).items()):
        lines.append(f"- `{k}`: **{v:.3f}**")
    lines.append("")

    lines.append("## Per-record\n")
    for r in reports:
        lines.append(f"### `{r.id}` ({r.kind})")
        if r.error:
            lines.append(f"- **error:** {r.error}\n")
            continue
        lines.append(f"- missing required fields: {r.missing_required}")
        if r.skill_prf:
            lines.append(
                f"- skills P/R/F1: {r.skill_prf['precision']:.3f} / "
                f"{r.skill_prf['recall']:.3f} / {r.skill_prf['f1']:.3f}"
            )
        if r.summary_overlap is not None:
            lines.append(f"- summary token overlap: {r.summary_overlap:.3f}")
        if r.scalar_exact:
            scalars = ", ".join(f"{k}={v}" for k, v in sorted(r.scalar_exact.items()))
            lines.append(f"- scalars: {scalars}")
        lines.append("")
    path.write_text("\n".join(lines))


# ---------- CLI ----------

def _load_gold(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            out.append(json.loads(ln))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the extraction layer.")
    parser.add_argument("--gold", type=Path, default=Path(__file__).parent / "sample_gold_data.jsonl")
    parser.add_argument("--json", type=Path, default=Path(__file__).parent / "reports" / "extraction.json")
    parser.add_argument("--markdown", type=Path, default=Path(__file__).parent / "reports" / "extraction.md")
    args = parser.parse_args()

    records = _load_gold(args.gold)
    reports: list[RecordReport] = []
    for r in records:
        kind = r.get("kind")
        if kind == "cv":
            reports.append(_evaluate_cv(r))
        elif kind == "job":
            reports.append(_evaluate_job(r))
        else:
            reports.append(RecordReport(id=r.get("id", "?"), kind=kind or "?", error="unknown kind"))

    aggregate = _aggregate(reports)
    _write_json(args.json, aggregate, reports)
    _write_markdown(args.markdown, aggregate, reports)

    print(json.dumps(aggregate, indent=2))
    print(f"\nReports written:\n  {args.json}\n  {args.markdown}")


if __name__ == "__main__":
    main()
