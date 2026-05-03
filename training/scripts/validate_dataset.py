"""Validate an instruction-tuning JSONL produced by `build_dataset.py`.

Checks (per record):
  * line is valid JSON
  * required top-level keys present (`instruction`, `input`, `output`)
  * `input` non-empty after strip
  * `output` is a JSON-decodable string
  * if the instruction looks like a CV/JD prompt, the parsed output dict
    contains the expected keys for that schema

Usage:
    python -m scripts.validate_dataset path/to/dataset.jsonl

Exits with code 1 if any record fails. Prints a per-file summary.
"""
from __future__ import annotations

import _paths  # noqa: F401  # side-effect: sys.path setup (kept for parity)

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED_KEYS = {"instruction", "input", "output"}

CV_REQUIRED = {
    "name", "summary", "skills", "education", "experience",
    "projects", "certifications", "languages", "contact",
}
CV_CONTACT_REQUIRED = {"email", "phone", "linkedin", "github", "website"}

JOB_REQUIRED = {
    "job_title", "company", "location", "salary",
    "employment_type", "remote_type",
    "required_skills", "preferred_skills", "responsibilities",
    "qualifications", "experience_level", "education_requirements",
    "technologies", "soft_skills",
}


@dataclass
class Report:
    total: int = 0
    ok: int = 0
    errors: list[str] = field(default_factory=list)

    def fail(self, line_no: int, message: str) -> None:
        self.errors.append(f"line {line_no}: {message}")


def _classify(instruction: str) -> str:
    low = (instruction or "").lower()
    if "cv" in low:
        return "cv"
    if "job" in low:
        return "job"
    return ""


def _validate_record(line_no: int, raw: str, report: Report) -> None:
    try:
        record = json.loads(raw)
    except json.JSONDecodeError as e:
        report.fail(line_no, f"line is not valid JSON ({e.msg})")
        return

    if not isinstance(record, dict):
        report.fail(line_no, "record is not a JSON object")
        return

    missing = REQUIRED_KEYS - set(record)
    if missing:
        report.fail(line_no, f"missing top-level keys: {sorted(missing)}")
        return

    if not isinstance(record["input"], str) or not record["input"].strip():
        report.fail(line_no, "`input` is empty or non-string")
        return
    if not isinstance(record["output"], str) or not record["output"].strip():
        report.fail(line_no, "`output` is empty or non-string")
        return
    if not isinstance(record["instruction"], str) or not record["instruction"].strip():
        report.fail(line_no, "`instruction` is empty or non-string")
        return

    try:
        output_obj = json.loads(record["output"])
    except json.JSONDecodeError as e:
        report.fail(line_no, f"`output` string is not valid JSON ({e.msg})")
        return

    if not isinstance(output_obj, dict):
        report.fail(line_no, "`output` is JSON but not an object")
        return

    kind = _classify(record["instruction"])
    if kind == "cv":
        missing_cv = CV_REQUIRED - set(output_obj)
        if missing_cv:
            report.fail(line_no, f"CV output missing keys: {sorted(missing_cv)}")
            return
        contact = output_obj.get("contact")
        if not isinstance(contact, dict):
            report.fail(line_no, "`contact` is missing or not an object")
            return
        missing_c = CV_CONTACT_REQUIRED - set(contact)
        if missing_c:
            report.fail(line_no, f"CV contact missing keys: {sorted(missing_c)}")
            return
    elif kind == "job":
        missing_j = JOB_REQUIRED - set(output_obj)
        if missing_j:
            report.fail(line_no, f"job output missing keys: {sorted(missing_j)}")
            return

    report.ok += 1


def validate_file(path: Path) -> Report:
    report = Report()
    if not path.exists():
        report.errors.append(f"file not found: {path}")
        return report

    with path.open("r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            report.total += 1
            _validate_record(line_no, stripped, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()

    any_fail = False
    for path in args.paths:
        report = validate_file(path)
        print(f"\n=== {path} ===")
        print(f"records: {report.total}, ok: {report.ok}, errors: {len(report.errors)}")
        for err in report.errors[:25]:
            print(f"  - {err}")
        if len(report.errors) > 25:
            print(f"  … and {len(report.errors) - 25} more")
        if report.errors:
            any_fail = True

    if any_fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
