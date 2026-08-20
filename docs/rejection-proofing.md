# Rejection-proofing: the recruiter scorecard

Nine upgrades aimed at the two gates that actually reject applications —
the ATS/keyword gate and the human 7-second scan. Validated on the
isolated stack at **3300 / 8300** (`make rp-up`), which has its own
database so the metrics harvester can never write to the master CV you
use daily.

## What each one does

| # | Upgrade | Where |
|---|---|---|
| 1 | **Metric gate** — master-CV quantification health with a target (40%); warns before you tailor anything | `GET /api/cv/metrics/density`, Master CV banner |
| 2 | **Evidence-graded coverage** — every JD skill is `evidenced` (proven in a bullet), `mentioned` (skills-line only), or `missing` | `cv_scorecard.coverage_breakdown` |
| 3 | **Hard gates** — the JD's `qualifications` / `education_requirements` (years, degree) are finally *used*, not discarded | `cv_scorecard.hard_gates` |
| 4 | **Required vs preferred** — split, because missing a required skill is fatal and missing a preferred one is not | scorecard `coverage` |
| 5 | **Verb / hedge lint** — flags weak openers and "familiar with"-style phrasing; the polish prompt now also *prevents* them | `cv_scorecard.bullet_audit` + polish rule |
| 6 | **7-second recruiter scan** — an LLM reads ONLY the top of page one and returns read-on / unsure / bin plus concrete fixes | `cv_scorecard.recruiter_scan` |
| 7 | **Fit score + verdict** — weighted 0-100 with a go/no-go call | `cv_scorecard._fit` |
| 8 | **Seniority calibration** — does the CV's language read at the level the JD asks for? | `cv_scorecard.seniority_check` |
| 9 | **Cover-letter AI-tell cleanup** — bans "excited to", "passionate about", "delve", "leverage my", … | `cover_letter.py` |

## Why coverage changed

The old number was `keyword in latex.lower()` — a keyword sitting in the
skills line scored exactly like one backed by an achievement. You could
hit 100% by listing words. Recruiters read the skills line, look for
proof in the bullets, and reject what they can't find. The scorecard now
separates the two, and the fit score weights **evidenced** required
skills at 40 points versus 10 for merely naming them.

## JD steering — what the tailor may and may not do

The polish layer is explicitly allowed to angle real facts at the JD:
choose *which* true metric to surface, restate a true number in the
employer's unit (`300/month` → `~3.6K/year`), lead with the facet the JD
values, reorder bullets, adopt the JD's vocabulary.

It is explicitly forbidden from changing the magnitude of a real number,
inventing a metric, or implying scale/seniority the evidence doesn't
support. If a number would have to change to impress an employer, the
instruction is to drop the number and lead with the qualitative claim.

## Running it

```bash
make rp-seed   # copy the live DB into this stack's own volume
make rp-up     # build + start on 3300 / 8300
```

Open http://localhost:3300. The scorecard appears under the render
result; the metric gate sits in the Master CV panel.
