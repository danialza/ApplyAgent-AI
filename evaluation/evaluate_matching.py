"""Evaluate the CV-to-JD matching engine on synthetic gold pairs.

Gold record format (one per line):

    {
      "id": "match-ai",
      "job_text": "...",
      "cvs": [{"id": "alice", "text": "..."}, ...],
      "expected_ranking": ["alice", "bob", "carol"],
      "constraints": {
          "top1_must_be": "alice",
          "min_top1_score": 70
      }
    }

Metrics reported per record AND aggregated:

  - Top-1 accuracy: predicted top CV equals expected top CV.
  - Top-3 accuracy: expected top CV appears in predicted top 3.
  - Mean Reciprocal Rank (MRR) of the expected top CV.
  - Constraint satisfaction rate: fraction of records where every
    declared constraint passes (top1_must_be, min_top1_score).
  - Average match-score difference: |pred_top1 - pred_top2|, averaged.
    Larger = better separation between the recommended CV and runners-up.

CLI:

    python evaluate_matching.py \\
      --gold sample_matching_gold.jsonl \\
      --json reports/matching.json \\
      --markdown reports/matching.md
"""
from __future__ import annotations

import _paths  # noqa: F401  # side-effect: backend on sys.path

import argparse
import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.services.cv_parser import parse_cv_text  # type: ignore
from app.services.matching_engine import match_cv_to_job  # type: ignore
from app.services.extraction import extract_job  # type: ignore
from app.models.schemas import JobParsed  # type: ignore


# ---------- helpers ----------

def _build_fake_cv(cv_id: str, text: str, db_id: int) -> SimpleNamespace:
    """Run the rule-based CV parser and pack the result into the duck-typed
    object that `match_cv_to_job` expects (it only reads attributes)."""
    parsed = parse_cv_text(text)
    return SimpleNamespace(
        id=db_id,
        cv_id_str=cv_id,
        filename=f"{cv_id}.txt",
        name=parsed.name or cv_id,
        summary=parsed.summary,
        skills=parsed.skills,
        experience=parsed.experience,
        education=parsed.education,
        projects=parsed.projects,
        certifications=parsed.certifications,
        languages=parsed.languages,
        raw_text=text,
    )


def _rank_record(record: dict[str, Any]) -> tuple[list[str], list[float]]:
    """Run the matcher and return the predicted ranking + per-CV scores."""
    parsed_job = extract_job(record["job_text"])
    job = JobParsed(**parsed_job.to_dict())

    fakes = [
        _build_fake_cv(cv["id"], cv["text"], db_id=i + 1)
        for i, cv in enumerate(record["cvs"])
    ]
    results = []
    for cv in fakes:
        result = match_cv_to_job(cv, job)
        results.append((cv.cv_id_str, result.overall_score, result.skill_score, cv.id))
    results.sort(key=lambda t: (-t[1], -t[2], t[3]))
    ranked_ids = [t[0] for t in results]
    ranked_scores = [t[1] for t in results]
    return ranked_ids, ranked_scores


# ---------- core evaluation ----------

@dataclass
class RecordReport:
    id: str
    expected: list[str] = field(default_factory=list)
    predicted: list[str] = field(default_factory=list)
    predicted_scores: list[float] = field(default_factory=list)
    top1_correct: bool = False
    top3_correct: bool = False
    reciprocal_rank: float = 0.0
    score_gap: float = 0.0
    constraints_passed: bool = True
    constraint_violations: list[str] = field(default_factory=list)
    error: str = ""


def _evaluate_record(record: dict[str, Any]) -> RecordReport:
    rep = RecordReport(id=record.get("id", "?"))
    expected = record.get("expected_ranking") or []
    rep.expected = expected
    if not expected:
        rep.error = "missing expected_ranking"
        return rep

    try:
        predicted, scores = _rank_record(record)
    except Exception as e:  # noqa: BLE001
        rep.error = f"matcher failed: {e}"
        return rep

    rep.predicted = predicted
    rep.predicted_scores = scores

    target = expected[0]
    rep.top1_correct = predicted[:1] == [target]
    rep.top3_correct = target in predicted[:3]
    if target in predicted:
        rep.reciprocal_rank = round(1.0 / (predicted.index(target) + 1), 4)
    rep.score_gap = round(scores[0] - scores[1], 4) if len(scores) >= 2 else 0.0

    constraints = record.get("constraints") or {}
    if "top1_must_be" in constraints and predicted[0] != constraints["top1_must_be"]:
        rep.constraint_violations.append(
            f"top1_must_be={constraints['top1_must_be']}, got={predicted[0]}"
        )
    if "min_top1_score" in constraints and scores[0] < float(constraints["min_top1_score"]):
        rep.constraint_violations.append(
            f"min_top1_score={constraints['min_top1_score']}, got={scores[0]:.2f}"
        )
    rep.constraints_passed = not rep.constraint_violations
    return rep


# ---------- aggregation + reporting ----------

def _aggregate(reports: list[RecordReport]) -> dict[str, Any]:
    valid = [r for r in reports if not r.error]
    n = len(valid) or 1

    def avg(vs: list[float]) -> float:
        return round(statistics.mean(vs), 4) if vs else 0.0

    return {
        "records": len(reports),
        "errors": sum(1 for r in reports if r.error),
        "top1_accuracy": round(sum(r.top1_correct for r in valid) / n, 4),
        "top3_accuracy": round(sum(r.top3_correct for r in valid) / n, 4),
        "mrr": avg([r.reciprocal_rank for r in valid]),
        "constraint_satisfaction_rate": round(sum(r.constraints_passed for r in valid) / n, 4),
        "avg_score_gap": avg([r.score_gap for r in valid]),
    }


def _write_json(path: Path, aggregate: dict[str, Any], reports: list[RecordReport]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"aggregate": aggregate, "records": [r.__dict__ for r in reports]}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


def _write_markdown(path: Path, aggregate: dict[str, Any], reports: list[RecordReport]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# Matching evaluation report\n")
    lines.append(f"- Records evaluated: **{aggregate['records']}**")
    lines.append(f"- Errors: **{aggregate['errors']}**")
    lines.append(f"- Top-1 accuracy: **{aggregate['top1_accuracy']:.3f}**")
    lines.append(f"- Top-3 accuracy: **{aggregate['top3_accuracy']:.3f}**")
    lines.append(f"- Mean Reciprocal Rank: **{aggregate['mrr']:.3f}**")
    lines.append(f"- Constraint satisfaction rate: **{aggregate['constraint_satisfaction_rate']:.3f}**")
    lines.append(f"- Avg score gap (top1 − top2): **{aggregate['avg_score_gap']:.2f}**\n")

    lines.append("## Per-record\n")
    lines.append("| ID | Top1✓ | Top3✓ | RR | Predicted | Expected | Constraints |")
    lines.append("|---|:---:|:---:|---:|---|---|---|")
    for r in reports:
        if r.error:
            lines.append(f"| `{r.id}` | — | — | — | — | — | error: {r.error} |")
            continue
        violations = "ok" if r.constraints_passed else "; ".join(r.constraint_violations)
        lines.append(
            f"| `{r.id}` | {'✓' if r.top1_correct else '✗'} | "
            f"{'✓' if r.top3_correct else '✗'} | {r.reciprocal_rank:.3f} | "
            f"{r.predicted} | {r.expected} | {violations} |"
        )
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
    parser = argparse.ArgumentParser(description="Evaluate the matching engine.")
    parser.add_argument("--gold", type=Path, default=Path(__file__).parent / "sample_matching_gold.jsonl")
    parser.add_argument("--json", type=Path, default=Path(__file__).parent / "reports" / "matching.json")
    parser.add_argument("--markdown", type=Path, default=Path(__file__).parent / "reports" / "matching.md")
    args = parser.parse_args()

    records = _load_gold(args.gold)
    reports = [_evaluate_record(r) for r in records]
    aggregate = _aggregate(reports)
    _write_json(args.json, aggregate, reports)
    _write_markdown(args.markdown, aggregate, reports)

    print(json.dumps(aggregate, indent=2))
    print(f"\nReports written:\n  {args.json}\n  {args.markdown}")


if __name__ == "__main__":
    main()
