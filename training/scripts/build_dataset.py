"""Build instruction-tuning JSONL files from the project's parsed structures.

Each record is a single JSON object on one line:

    {
      "instruction": "...",
      "input": "<raw text>",
      "output": "<valid JSON string>"
    }

Sources:
  * `cvs`        — read every row from the backend SQLite DB and emit one
                   record per CV (`raw_text` → structured JSON).
  * `jobs-csv`   — parse a CSV (same contract as `/api/match/batch-csv`)
                   and emit one record per row.
  * `synthetic`  — emit hard-coded synthetic samples bundled in this repo
                   (safe to publish; never contains real personal data).

Usage:
    python -m scripts.build_dataset cvs --output ../data/processed/cvs.jsonl
    python -m scripts.build_dataset jobs-csv path/to/jobs.csv \
        --output ../data/processed/jobs.jsonl
    python -m scripts.build_dataset synthetic --output ../data/processed/synthetic.jsonl

Run from the `training/scripts/` directory or via the `python -m` form
above (the helper module `_paths.py` adds `backend/` to `sys.path`).
"""
from __future__ import annotations

import _paths  # noqa: F401  # side-effect: sys.path += backend

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

# Imports from the backend package — available thanks to _paths.
from app.services.cv_parser import ParsedCV, parse_cv_text  # type: ignore
from app.services.job_csv_importer import parse_csv_bytes  # type: ignore
from app.services.job_parser import ParsedJob, parse_job_text  # type: ignore


CV_INSTRUCTION = "Extract structured information from this CV and return valid JSON."
JOB_INSTRUCTION = (
    "Extract structured information from this job description and return valid JSON."
)


# ---------- Output shaping ----------

def _cv_to_output_dict(parsed: ParsedCV) -> dict[str, Any]:
    """Project a `ParsedCV` into the same shape the LLM-extraction prompt asks for."""
    return {
        "name": parsed.name,
        "summary": parsed.summary,
        "skills": parsed.skills,
        "education": parsed.education,
        "experience": parsed.experience,
        "projects": parsed.projects,
        "certifications": parsed.certifications,
        "languages": parsed.languages,
        "contact": {
            "email": parsed.email,
            "phone": parsed.phone,
            "linkedin": parsed.linkedin,
            "github": parsed.github,
            "website": parsed.portfolio,
        },
    }


def _job_to_output_dict(parsed: ParsedJob) -> dict[str, Any]:
    out = asdict(parsed)
    # `raw_text` belongs in `input`, not `output`.
    out.pop("raw_text", None)
    return out


def _make_record(instruction: str, raw_text: str, output_obj: dict[str, Any]) -> dict[str, str]:
    return {
        "instruction": instruction,
        "input": raw_text,
        "output": json.dumps(output_obj, ensure_ascii=False),
    }


# ---------- Sources ----------

def _iter_cv_records_from_db() -> Iterable[dict[str, str]]:
    """Pull every CV row from the backend DB and yield records.

    Re-parses `raw_text` so the output reflects the *current* parser version
    rather than the historical snapshot stored in the row.
    """
    from app.db.database import SessionLocal  # type: ignore
    from app.models.db_models import CV  # type: ignore

    with SessionLocal() as db:
        for cv in db.query(CV).all():
            text = (cv.raw_text or "").strip()
            if not text:
                continue
            parsed = parse_cv_text(text)
            yield _make_record(CV_INSTRUCTION, text, _cv_to_output_dict(parsed))


def _iter_job_records_from_csv(path: Path) -> Iterable[dict[str, str]]:
    data = path.read_bytes()
    parsed = parse_csv_bytes(data)
    if parsed.fatal_error:
        raise SystemExit(f"CSV error: {parsed.fatal_error}")
    for row in parsed.rows:
        if not row.is_usable:
            continue
        text = row.to_jd_text()
        job = parse_job_text(text)
        yield _make_record(JOB_INSTRUCTION, text, _job_to_output_dict(job))


# ---------- Synthetic samples ----------

_SYNTHETIC_CVS: list[str] = [
    """\
Jane Doe
San Francisco, CA | jane.doe@example.com | +1 (415) 555-0199
linkedin.com/in/janedoe | github.com/janedoe | janedoe.dev

PROFESSIONAL SUMMARY
Backend engineer with 6 years building distributed systems in Python and Go.

TECHNICAL SKILLS
Python, Go, FastAPI, PostgreSQL, Redis, Docker, Kubernetes, AWS

WORK EXPERIENCE
Senior Backend Engineer — Acme Corp (2021 - Present)
- Designed a multi-tenant billing service handling 5M events/day.
- Cut p99 latency 40% by introducing async batching and read replicas.

EDUCATION
B.Sc. Computer Science — UC Berkeley (2014 - 2018)

CERTIFICATIONS
- AWS Certified Solutions Architect (2022)

LANGUAGES
English (Native), Spanish (Conversational)
""",
    """\
Alex Kim
Berlin, Germany · alex.kim@example.com · +49 30 555 12345
github.com/alexkim · alexkim.dev

About
Junior data scientist focused on NLP and applied ML.

Skills
Python, PyTorch, Hugging Face, NLP, scikit-learn, Pandas, NumPy, SQL

Experience
Data Science Intern — Northwind Labs (2023 - 2024)
- Fine-tuned a transformer for legal-text classification (F1 0.91).
- Built data pipelines in Python and Airflow.

Education
M.Sc. Artificial Intelligence — TU Berlin (2022 - 2024)
B.Sc. Mathematics — Seoul National University (2017 - 2021)

Languages
Korean (Native), English (Fluent), German (Basic)
""",
]

_SYNTHETIC_JOBS: list[str] = [
    """\
Job Title: Senior AI Engineer
Company: Cortex Labs
Location: Berlin, Germany (Hybrid)
Salary: €80,000 - €110,000 per year
Employment: Full-time

About the role
We're building production RAG systems on top of LLMs.

What you'll do
- Design and ship retrieval-augmented generation pipelines using LangChain and FAISS.
- Train and fine-tune transformer models with PyTorch and Hugging Face.
- Build FastAPI services and deploy on AWS via Docker.
- Mentor junior engineers and collaborate with product.

Required skills
- 5+ years of professional Python experience.
- Strong background in Machine Learning, Deep Learning and NLP.
- Hands-on with LLMs, RAG, and vector databases (FAISS or Chroma).

Preferred qualifications
- Familiarity with TypeScript / Next.js for internal tooling.
- Prior MLOps experience on AWS or Azure.

Education
- Master's or PhD in Computer Science, Mathematics, or a related field.
""",
    """\
WordPress Developer
Company: Pixel & Co
Location: Remote (UK)
Pay range: £40,000 to £55,000

What we're looking for
- 3+ years of WordPress and WooCommerce development.
- Strong PHP, JavaScript, HTML and CSS skills.
- SEO best practices and Google Analytics experience.
- Bonus: Shopify experience.
""",
    """\
Robotics Engineer (Junior)
We're hiring a junior robotics engineer for an on-site role in Munich.

Responsibilities
- Develop ROS2 nodes for autonomous mobile robots.
- Tune PID controllers and validate in Gazebo simulation.
- Prototype control systems in MATLAB / Simulink.

Requirements
- BSc in Robotics, Mechatronics, or Electrical Engineering.
- Experience with ROS, C++, and Python.
""",
]


def _iter_synthetic_records() -> Iterable[dict[str, str]]:
    for raw in _SYNTHETIC_CVS:
        parsed = parse_cv_text(raw)
        yield _make_record(CV_INSTRUCTION, raw.strip(), _cv_to_output_dict(parsed))
    for raw in _SYNTHETIC_JOBS:
        parsed = parse_job_text(raw)
        yield _make_record(JOB_INSTRUCTION, raw.strip(), _job_to_output_dict(parsed))


# ---------- IO ----------

def _write_jsonl(path: Path, records: Iterable[dict[str, str]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            count += 1
    return count


# ---------- CLI ----------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="source", required=True)

    p_cvs = sub.add_parser("cvs", help="Export every CV in the backend DB.")
    p_cvs.add_argument("--output", "-o", type=Path, required=True)

    p_jobs = sub.add_parser("jobs-csv", help="Convert a CSV of JDs to JSONL.")
    p_jobs.add_argument("csv_path", type=Path)
    p_jobs.add_argument("--output", "-o", type=Path, required=True)

    p_syn = sub.add_parser("synthetic", help="Emit the bundled synthetic samples.")
    p_syn.add_argument("--output", "-o", type=Path, required=True)

    args = parser.parse_args()

    if args.source == "cvs":
        records = _iter_cv_records_from_db()
    elif args.source == "jobs-csv":
        records = _iter_job_records_from_csv(args.csv_path)
    else:
        records = _iter_synthetic_records()

    n = _write_jsonl(args.output, records)
    print(f"Wrote {n} record(s) → {args.output}")


if __name__ == "__main__":
    main()
