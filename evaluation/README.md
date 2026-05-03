# Evaluation

Two CLI evaluators measure how well the system does the two things that
matter for this project: **extracting structured data** from raw CV/JD
text, and **ranking CVs** against a job description.

## Why bother?

For a portfolio project this folder does double duty:

1. **Honest signal.** "It works on my machine" isn't credible. Numbers on
   a versioned gold set are.
2. **Regression catcher.** Re-run the evaluators after any parser /
   matcher change — a metric that drops tells you to look before you ship.
3. **Comparable runs.** Same gold set, same metrics, swap in the LLM
   extractor or the embedding-backed matcher to compare apples to apples.
4. **Documentation.** The Markdown reports under `reports/` double as a
   public-facing snapshot of current quality without requiring a reader
   to run the code.

If you're hiring someone and they show you a project that *parses CVs and
matches them to jobs* but can't tell you the **skill F1** or the **top-1
accuracy** of their matcher, you've learned something.

## Folder layout

```
evaluation/
├── _paths.py                   # adds backend/ to sys.path
├── evaluate_extraction.py      # extraction CLI
├── evaluate_matching.py        # matching CLI
├── sample_gold_data.jsonl      # synthetic CV + JD gold (extraction)
├── sample_matching_gold.jsonl  # synthetic match gold (matching)
├── reports/
│   ├── extraction.{json,md}    # written on each run
│   └── matching.{json,md}
└── README.md
```

`reports/` is regenerated on every run; both JSON and Markdown formats
are emitted side-by-side.

## Synthetic gold only ⚠️

All gold records in this folder are **synthetic** — invented names,
emails, locations. The system is **not** evaluated against private CVs.
If you build a private gold set, keep it outside the repo (the project's
`.gitignore` already covers `data/raw/` and `data/processed/`).

## Extraction evaluation

Compares predicted `ParsedCV` / `ParsedJob` against gold expectations.

### Gold record (CV)

```json
{
  "kind": "cv",
  "id": "cv-jane",
  "raw_text": "<full CV text>",
  "expected": {
    "name": "Jane Doe",
    "skills": ["Python", "FastAPI", "AWS"],
    "education": ["B.Sc. Computer Science"],
    "experience": ["Senior Backend Engineer"],
    "certifications": ["AWS Certified Solutions Architect"],
    "languages": ["English", "Spanish"],
    "email": "jane.doe@example.com"
  }
}
```

### Gold record (JD)

```json
{
  "kind": "job",
  "id": "job-ai",
  "raw_text": "<full JD text>",
  "expected": {
    "job_title": "Senior AI Engineer",
    "company": "Cortex Labs",
    "location": "Berlin",
    "experience_level": "senior",
    "required_skills": ["Python", "Machine Learning", "RAG", "FAISS"],
    "preferred_skills": ["TypeScript", "Next.js"]
  }
}
```

Only the fields present in `expected` are scored — leaving a field out
means "not under test". This makes it easy to grow the gold set without
defining every column.

### Metrics

For **list fields** (skills, experience, education, projects,
certifications, languages, required_skills, preferred_skills,
technologies, soft_skills, …):

- **Precision** — fraction of predicted items that appear in gold.
- **Recall** — fraction of gold items that appear in prediction.
- **F1** — harmonic mean of the two.

Skill-like fields are compared after canonicalisation through
`backend/app/services/synonyms.py`, so `JS` ≡ `JavaScript`, `ML` ≡
`Machine Learning`, etc. Other list fields use case-insensitive set
comparison.

For **scalar fields** (name, email, job_title, company, …):

- **Exact match** rate (case + whitespace insensitive).
- For `location` only: gold is allowed to be a **substring** of the
  prediction (so gold `"Berlin"` matches prediction `"Berlin, Germany"`).
- For `summary`: a separate **token-overlap** ratio is reported (set of
  3+ char tokens in common / tokens in gold). Exact match would be too
  brittle.

Plus:

- **Skill-only P/R/F1** — broken out separately because skill quality
  drives the matcher.
- **Missing required fields count** — per record, the number of fields
  the gold expected (non-empty) where the prediction came back empty.
  Required-by-default: `name` + `skills` for CVs, `job_title` +
  `required_skills` for JDs.

### Run it

```bash
cd evaluation
python evaluate_extraction.py
# or with explicit paths:
python evaluate_extraction.py \
  --gold sample_gold_data.jsonl \
  --json reports/extraction.json \
  --markdown reports/extraction.md
```

## Matching evaluation

Compares the matcher's predicted ranking against an expected ranking.

### Gold record

```json
{
  "id": "match-ai",
  "job_text": "<full JD text>",
  "cvs": [
    {"id": "alice", "text": "<CV text>"},
    {"id": "bob",   "text": "<CV text>"},
    {"id": "carol", "text": "<CV text>"}
  ],
  "expected_ranking": ["alice", "bob", "carol"],
  "constraints": {
    "top1_must_be": "alice",
    "min_top1_score": 70
  }
}
```

`constraints` is optional. Each declared constraint is checked
independently and contributes to `constraint_satisfaction_rate`.

### Metrics

- **Top-1 accuracy** — predicted top CV equals expected top CV.
- **Top-3 accuracy** — expected top CV appears anywhere in predicted top
  3.
- **Mean Reciprocal Rank (MRR)** — `1 / rank_of_expected_top` averaged
  across records. 1.0 = always first.
- **Constraint satisfaction rate** — fraction of records whose
  constraints all pass.
- **Average score gap** — average of `(top1_score − top2_score)`. A
  larger gap means the recommended CV is decisively ahead. Useful as a
  *separability* signal independent of correctness.

### Run it

```bash
cd evaluation
python evaluate_matching.py
# or:
python evaluate_matching.py \
  --gold sample_matching_gold.jsonl \
  --json reports/matching.json \
  --markdown reports/matching.md
```

## Reading the reports

`reports/extraction.md` and `reports/matching.md` are written in
human-friendly Markdown — open them in any viewer or paste into a PR
description. The matching JSON / MD example outputs from the bundled
synthetic set look like:

```json
{
  "records": 3,
  "errors": 0,
  "top1_accuracy": 1.0,
  "top3_accuracy": 1.0,
  "mrr": 1.0,
  "constraint_satisfaction_rate": 1.0,
  "avg_score_gap": 45.25
}
```

## Extending the gold set

Add lines to either JSONL — the evaluators iterate top to bottom and
report per-record metrics. To grow without touching real PII:

1. Hand-write synthetic CVs (`build_dataset.py synthetic` is a useful
   starter pool).
2. Run the parsers to produce a candidate `expected` block, then **edit**
   it down to what you want to assert. Don't hard-code current parser
   output verbatim — that turns the eval into a tautology.
3. For matching gold: pick a JD that should clearly favour one CV, and
   list 2–3 plausible competitors so ranking has meaningful signal.

## Limitations

- Synthetic data only — these scores describe behaviour on idealised
  CV/JD text, not real-world noise (PDF artefacts, multilingual mixed
  scripts, scanned-image CVs). For a real eval, build a private gold set
  and keep it out of the repo.
- "Field-level F1" is set-based and case-insensitive after
  canonicalisation. It does NOT measure ordering, fuzzy spelling, or
  partial-string overlap of long entries.
- Constraint checks are intentionally simple. Add new ones by extending
  `_evaluate_record` in `evaluate_matching.py`.
- The matching evaluator runs both rule-based parsing for CVs (so the
  duck-typed object matches what the matcher expects) AND the orchestrator
  for the JD (so it benefits from LLM extraction when enabled). If you
  want a strictly heuristic baseline, set `USE_LLM_EXTRACTION=false`
  before running.
