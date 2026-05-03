# Contributing

Thanks for taking the time to look. Even small PRs are welcome — a typo
fix, a new synonym, a test case for a CV format that the parser
mangles. The project is intentionally small enough that the whole thing
fits in one head, and I'd like to keep it that way.

## Ground rules

1. **Don't commit personal data.** No real CVs, no scraped job postings
   that contain identifying info, no `.env`, no `backend/data/`. The
   `.gitignore` already covers most of these — verify with
   `git status` before you push.
2. **Keep the rule-based path working.** The LLM and embedding layers
   are *augmentations*, not replacements. A change that breaks the
   heuristic parser when neural deps are missing will be rejected.
3. **Add a test for behaviour you care about.** Every public-ish module
   already has a `tests/test_*.py` file you can extend.
4. **Be conservative about new dependencies.** Each one is a future
   support cost. If you can solve it in 30 lines of stdlib, do that.

## Local setup

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Frontend
cd ../frontend
cp .env.local.example .env.local
npm install
```

Run everything in parallel:

```bash
make dev
```

## Running the tests

```bash
make test
```

This runs every `tests/test_*.py` in the backend. Each test file is
runnable on its own as `python -m tests.<name>`. CI uses the same
entry-points.

The evaluation suite is also runnable:

```bash
make eval
```

It writes JSON + Markdown reports under `evaluation/reports/`. Don't
commit those — they're regenerated on every run.

## Style

- **Python** — 3.11 idioms (`from __future__ import annotations`,
  built-in generics like `list[str]`, `match` only when it helps).
  No formatter pinned; keep diffs small.
- **TypeScript** — strict mode is on. Prefer plain functions and
  `interface`/`type` aliases. No global state libraries.
- **Comments** — explain the *why*, not the *what*. The codebase
  already trims commentary aggressively; new code should match.
- **Logs** — use `logging.getLogger("ai_job_cv_matcher.<area>")` so
  ops can grep them. Don't `print()`.

## Adding a new skill / synonym

1. Add the canonical name + aliases to
   `backend/app/utils/skill_dictionary.py` (categorised dicts).
2. If the skill has common short aliases (`JS`, `WP`, …) that should
   match across CV and JD even if the dictionary scan misses them,
   add a synonym group to `backend/app/services/synonyms.py`.
3. Add a test case to `tests/test_job_parser.py` so the new skill
   round-trips.

## Adding a new section header (e.g. "Volunteering")

1. Append the header variants to the relevant map in
   `backend/app/utils/text_cleaning.py` (CV) or
   `backend/app/services/job_parser.py` (JD).
2. Decide where the content lands — new field on `ParsedCV` /
   `ParsedJob`? Existing field? Nothing changes if it's "informational
   only".
3. Test in `tests/test_cv_parser.py` / `tests/test_job_parser.py`.

## Adding a new sub-score

1. Implement a function in
   `backend/app/services/scoring_service.py` that returns a value in
   `[0, 100]`.
2. Wire it into `match_cv_to_job` in `matching_engine.py`.
3. Update the weights in `WEIGHTS` so they still sum to 1.0.
4. Add the field to `MatchResult` in `app/models/schemas.py` and to
   the frontend `lib/types.ts` + `ScoreCard.tsx`.
5. Add a test in `tests/test_matching_engine.py`.

## Pull requests

- Keep PRs focused. One concept per PR.
- Reference an issue when relevant.
- Describe the *why*, list the visible behaviour change, and call out
  any new env vars or dependencies.
- Confirm `make test` and `make eval` still pass.

## Ethics

The job-URL scraper respects `robots.txt`, throttles per host, refuses
local / private addresses, and never bypasses logins, paywalls, or
anti-bot systems. PRs that weaken any of those guarantees won't be
accepted. If a target site doesn't want to be scraped, the right answer
is the manual-paste fallback that the UI already exposes.

This is a portfolio + personal-job-search tool. It is not a hiring
decision system, and PRs that frame it as one will be redirected.
