# AI Job-CV Matching Agent — Backend (Phase 1)

FastAPI service that ingests CVs (PDF/DOCX), parses job descriptions, and
ranks CVs against a JD using a transparent multi-factor score.

## Stack

- Python 3.10+
- FastAPI + Uvicorn
- SQLAlchemy 2.0 + SQLite
- Pydantic v2
- pdfplumber (PDF), python-docx (DOCX)
- Embedding service stub (BoW cosine fallback; `sentence-transformers` plugs in later)

## Project layout

```
backend/
├── app/
│   ├── main.py                # FastAPI app, CORS, router wiring, startup hook
│   ├── api/                   # HTTP layer (thin controllers)
│   │   ├── cv_routes.py
│   │   ├── job_routes.py
│   │   └── match_routes.py
│   ├── services/              # Domain logic (testable, no HTTP/DB coupling)
│   │   ├── cv_parser.py
│   │   ├── job_parser.py
│   │   ├── matching_engine.py
│   │   ├── scoring_service.py
│   │   └── embedding_service.py
│   ├── models/                # Pydantic DTOs + SQLAlchemy ORM
│   │   ├── schemas.py
│   │   └── db_models.py
│   ├── db/database.py         # Engine, session, Base, init_db
│   └── utils/
│       ├── text_cleaning.py
│       └── file_validation.py
├── data/                      # SQLite DB lives here (auto-created)
├── requirements.txt
└── README.md
```

## Run locally

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The API is then served at `http://127.0.0.1:8000` and interactive docs at
`http://127.0.0.1:8000/docs`.

### Environment variables

| Variable            | Default                             | Purpose                              |
|---------------------|-------------------------------------|--------------------------------------|
| `APP_DB_URL`        | `sqlite:///backend/data/app.db`     | SQLAlchemy connection string         |
| `APP_CORS_ORIGINS`  | `http://localhost:3000`             | Comma-separated allowed origins      |
| `APP_LOG_LEVEL`     | `INFO`                              | Root log level (`DEBUG` shows fallback paths) |
| `USE_LLM_EXTRACTION`| _(unset)_                           | `true` to enable LLM extraction (off by default) |
| `OPENAI_API_KEY`    | _(unset)_                           | API key — required when `USE_LLM_EXTRACTION=true` |
| `OPENAI_BASE_URL`   | `https://api.openai.com/v1`         | Override for OpenAI-compatible endpoints (e.g. local LLMs, Together, Groq) |
| `LLM_MODEL_NAME`    | `gpt-4o-mini`                       | Model name passed in the request     |
| `LLM_TIMEOUT_SECONDS` | `30`                              | Per-request timeout                  |

## API

Base path: `/api`

| Method | Path                    | Body / Params                          | Description                                |
|--------|-------------------------|----------------------------------------|--------------------------------------------|
| GET    | `/api/health`           | —                                      | Liveness check                             |
| POST   | `/api/cvs/upload`       | `multipart/form-data` field `files[]`  | Upload one or more PDF/DOCX CVs            |
| GET    | `/api/cvs`              | —                                      | List all CVs                               |
| GET    | `/api/cvs/{cv_id}`      | path                                   | Get a single CV                            |
| DELETE | `/api/cvs/{cv_id}`      | path                                   | Delete a CV                                |
| POST   | `/api/jobs/parse`       | `{ "text": "..." }`                    | Parse JD into structured fields            |
| POST   | `/api/match`            | `{ "job_text": "..." }`                | Rank all CVs against a JD                  |
| POST   | `/api/match/single`     | `{ "cv_id": 1, "job_text": "..." }`    | Score one CV against a JD                  |
| POST   | `/api/jobs/from-url`    | `{ "url": "https://…" }`               | Scrape a public JD URL → parsed JSON       |
| POST   | `/api/match/from-url`   | `{ "url": "https://…" }`               | Scrape a JD URL and rank all uploaded CVs  |
| POST   | `/api/jobs/from-file`   | `multipart/form-data` field `file`     | Extract a JD from a PDF/DOCX/TXT upload    |
| POST   | `/api/match/from-file`  | `multipart/form-data` field `file`     | Extract from PDF/DOCX/TXT and rank CVs     |
| POST   | `/api/jobs/import-csv`  | `multipart/form-data` field `file`     | Validate + parse a multi-job CSV (no match)|
| POST   | `/api/match/batch-csv`  | `multipart/form-data` field `file`     | Find the best CV for each row of the CSV   |
| POST   | `/api/embeddings/rebuild` | —                                    | Rebuild the FAISS index from all CVs       |
| POST   | `/api/search/semantic`  | `{ "query": "...", "top_k": 5 }`       | Free-text semantic search over CV chunks   |
| POST   | `/api/profile/build`    | `multipart/form-data` files (optional) | Aggregate every CV + Document into the unified profile (PDF/DOCX/TXT supplementary docs allowed) |
| GET    | `/api/profile`          | —                                      | Return the unified profile                 |
| DELETE | `/api/profile`          | —                                      | Drop the unified profile + supplementary documents (CVs untouched) |
| GET    | `/api/profile/queries`  | —                                      | Smart job-search queries + grouped tags + platform tags |
| POST   | `/api/jobs/discover`    | `{ "queries"?, "tags"?, "sources"?, "max_per_source"?, "max_total"? }` | Discover jobs from public, no-login APIs |
| POST   | `/api/jobs/rank`        | `{ "jobs": [...], "cv_ids"?, "use_profile_fallback"?, "max_results"? }` | Rank a batch of jobs against the CV pool / profile |
| POST   | `/api/tailor`           | `{ "job_text": "...", "cv_ids"?, "use_profile_fallback"? }` | Pick best CV + structured tailoring suggestions |
| POST   | `/api/agent/run`        | `{ "sources"?, "max_discover"?, "max_rank"?, "max_tailor"?, "cv_ids"?, "use_profile_fallback"?, "queries"?, "tags"? }` | End-to-end pipeline: profile → tags → discover → rank → tailor |
| POST   | `/api/generate`         | `{ "job_text": "...", "kinds": ["cv_suggestions","cover_letter","linkedin_message"], "cv_ids"?, "use_profile_fallback"?, "polish_with_llm"? }` | Produce CV suggestions, cover letter, LinkedIn message |
| GET    | `/api/cv/library`       | —                                      | Fetch the editable CV library (header, projects, publications, …) |
| PUT    | `/api/cv/library`       | `CVLibraryBase`                        | Replace the singleton CV library                                  |
| POST   | `/api/cv/render`        | `{ "job_text"?, "compile_pdf"?, "max_*"? }` | Render a tailored LaTeX CV (and PDF if `tectonic`/`pdflatex` available) |
| POST   | `/api/profile/build`    | `multipart/form-data` field `files[]` (optional) | Persist any uploaded PDF/DOCX/TXT documents, then build the unified profile across all CVs + Documents |
| GET    | `/api/profile`          | —                                      | Return the current unified profile (404 if not built) |
| DELETE | `/api/profile`          | —                                      | Delete the unified profile + supplementary docs (CVs preserved) |

### Sample requests

Upload CVs:

```bash
curl -X POST http://127.0.0.1:8000/api/cvs/upload \
  -F "files=@/path/to/alice.pdf" \
  -F "files=@/path/to/bob.docx"
```

Rank CVs against a JD:

```bash
curl -X POST http://127.0.0.1:8000/api/match \
  -H 'Content-Type: application/json' \
  -d '{"job_text": "Senior Python Engineer ... Required skills: Python, FastAPI ..."}'
```

## Scoring formula

All sub-scores are in `[0, 100]`. The aggregator uses the design weights:

```
overall = 0.40 * skill
        + 0.25 * semantic
        + 0.20 * experience
        + 0.10 * education
        + 0.05 * project
```

- **Skill**: required-vs-preferred hit rate with fuzzy matching (`difflib`).
- **Semantic**: bag-of-words cosine in Phase 1; swapped for sentence-transformers in Phase 3 (same `EmbeddingService.similarity()` API).
- **Experience**: parsed years from CV vs years implied by the JD level.
- **Education**: ranked degree comparison (PhD > Masters > Bachelor > ...).
- **Project**: projects/certifications referencing required skills.

## CV parser

The parser is purely heuristic — no LLM, no network calls. It supports the
common section headers below (case-insensitive, with or without colons):

| Canonical      | Variants accepted                                                                                          |
|----------------|------------------------------------------------------------------------------------------------------------|
| summary        | Summary, Profile, Professional Summary, About, About Me, Objective, Career Objective, Personal Statement   |
| skills         | Skills, Technical Skills, Core Competencies, Key Skills, Areas of Expertise, Technologies, Tech Stack, Tools |
| experience     | Experience, Work Experience, Professional Experience, Employment, Employment History, Career, Career History |
| education      | Education, Academic Background, Academic Qualifications, Qualifications, Educational Background            |
| projects       | Projects, Personal Projects, Selected Projects, Key Projects, Notable Projects, Side Projects              |
| certifications | Certifications, Certificates, Licenses, Courses, Training, Professional Development                        |
| languages      | Languages, Language Proficiency, Spoken Languages                                                          |

Contact extraction also pulls **email**, **phone**, **LinkedIn**, **GitHub**,
and a **portfolio** URL (including bare domains such as `janedoe.dev`).

### Run the parser tests / smoke demo

```bash
# Quick assertions over a sample CV (no pytest needed)
python -m tests.test_cv_parser

# Print the parsed sample as JSON
python -m app.services.cv_parser
```

### Example parsed JSON

For the sample CV bundled in `app/services/cv_parser.py`:

```json
{
  "name": "Jane Doe",
  "summary": "Backend engineer with 6 years building distributed systems in Python and Go. Passionate about developer tooling, observability, and clean APIs.",
  "skills": ["Python", "Go", "FastAPI", "PostgreSQL", "Redis", "Kafka", "Docker", "Kubernetes", "AWS", "Terraform"],
  "education": [
    "B.Sc. Computer Science — University of California, Berkeley (2014 - 2018)"
  ],
  "experience": [
    "Senior Backend Engineer — Acme Corp (2021 - Present)",
    "Designed a multi-tenant billing service handling 5M events/day.",
    "Cut p99 latency 40% by introducing async batching and read replicas.",
    "Backend Engineer — Globex (2018 - 2021)",
    "Built the core payments API in Python/FastAPI.",
    "Led migration from monolith to 6 microservices on Kubernetes."
  ],
  "projects": [
    "OpenObserve: open-source observability dashboard, 1.2k GitHub stars.",
    "pg-tuner: CLI that recommends Postgres config based on workload."
  ],
  "certifications": [
    "AWS Certified Solutions Architect — Associate (2022)",
    "Certified Kubernetes Administrator (CKA), 2021"
  ],
  "languages": ["English (Native)", "Spanish (Conversational)", "German (Basic)"],
  "email": "jane.doe@example.com",
  "phone": "+1 (415) 555-0199",
  "linkedin": "https://linkedin.com/in/janedoe",
  "github": "https://github.com/janedoe",
  "portfolio": "https://janedoe.dev"
}
```

> **Schema change**: the CV table now has `languages`, `email`, `phone`,
> `linkedin`, `github`, `portfolio` columns. If you ran a previous version,
> delete `backend/data/app.db` to recreate the schema.

## Job description parser

Heuristic parser with a configurable skill dictionary
(`app/utils/skill_dictionary.py`). Returns:

- `job_title`, `company`, `location`
- `salary` (range or single, multiple currencies/periods)
- `employment_type`: `full-time` / `part-time` / `contract` / `internship` / `temporary`
- `remote_type`: `remote` / `hybrid` / `on-site`
- `required_skills`, `preferred_skills`, `technologies` (canonical names)
- `responsibilities`, `qualifications`
- `experience_level`: `internship` / `junior` / `mid-level` / `senior` / `lead` / `principal`
- `education_requirements`
- `soft_skills`
- `raw_text`

Section headers detected (case-insensitive):

| Bucket          | Variants accepted                                                                                                                                  |
|-----------------|----------------------------------------------------------------------------------------------------------------------------------------------------|
| required        | Requirements / Required Skills / Essential Skills / Must Have / What You'll Need / What We're Looking For / Minimum Qualifications                 |
| preferred       | Preferred / Preferred Skills / Nice to Have / Bonus / Plus / Good to Have *(also detects inline "Bonus:" / "Nice to have:" lines inside required)* |
| responsibilities| Responsibilities / Key Responsibilities / What You'll Do / Duties / The Role / Day-to-Day / About the Role                                         |
| qualifications  | Qualifications / Candidate Profile / Who You Are / What We're Looking For                                                                          |
| education       | Education / Education Requirements                                                                                                                 |

Skill dictionary categories (extend in `skill_dictionary.py`):
**AI/ML**: Python, FastAPI, Machine Learning, Deep Learning, NLP, RAG, LLM,
LangChain, Vector Database, FAISS, Chroma, PyTorch, TensorFlow, Hugging Face,
scikit-learn, Pandas, NumPy, OpenAI, Anthropic, Computer Vision, MLOps, SQL,
Docker, Kubernetes, AWS, Azure, GCP, Git, React, Next.js, TypeScript, Node.js.
**Web/E-commerce**: WordPress, WooCommerce, PHP, JavaScript, SEO,
Google Analytics, Shopify, Magento, HTML, CSS, Tailwind CSS, REST API, GraphQL.
**Robotics**: ROS, ROS2, Robotics, Control Systems, PID, Gazebo, MATLAB,
Simulink, SLAM, OpenCV, C++, Embedded Systems.

### Run the JD parser tests

```bash
python -m tests.test_job_parser
```

### Example: AI engineering JD → parsed JSON

Input (excerpt):

```
Job Title: Senior AI Engineer
Company: Cortex Labs
Location: Berlin, Germany (Hybrid)
Salary: €80,000 - €110,000 per year
Employment: Full-time
...
Required skills
- 5+ years of professional Python experience.
- Strong background in Machine Learning, Deep Learning and NLP.
- Hands-on with LLMs, RAG, and vector databases (FAISS or Chroma).
Preferred qualifications
- Familiarity with TypeScript / Next.js for internal tooling.
- Prior MLOps experience on AWS or Azure.
```

Output:

```json
{
  "job_title": "Senior AI Engineer",
  "company": "Cortex Labs",
  "location": "Berlin, Germany (Hybrid)",
  "salary": "€80,000 - €110,000 per year",
  "employment_type": "full-time",
  "remote_type": "hybrid",
  "required_skills": [
    "Python", "Machine Learning", "Deep Learning", "NLP",
    "LLM", "RAG", "FAISS", "Chroma", "PyTorch", "TensorFlow",
    "SQL", "Docker"
  ],
  "preferred_skills": ["TypeScript", "Next.js", "MLOps", "AWS", "Azure"],
  "responsibilities": [
    "Design and ship retrieval-augmented generation pipelines using LangChain and FAISS.",
    "Train and fine-tune transformer models with PyTorch and Hugging Face.",
    "Build FastAPI services and deploy on AWS via Docker.",
    "Mentor junior engineers and collaborate with product."
  ],
  "qualifications": [],
  "experience_level": "senior",
  "education_requirements": [
    "Master's or PhD in Computer Science, Mathematics, or a related field."
  ],
  "technologies": [
    "Python", "FastAPI", "Machine Learning", "Deep Learning", "NLP",
    "RAG", "LLM", "LangChain", "FAISS", "Chroma", "PyTorch", "TensorFlow",
    "Hugging Face", "MLOps", "SQL", "Docker", "AWS", "Azure",
    "TypeScript", "Next.js"
  ],
  "soft_skills": ["Mentoring", "Collaboration"]
}
```

### Example: WordPress JD

```json
{
  "job_title": "WordPress Developer",
  "company": "Pixel & Co",
  "location": "Remote (UK)",
  "salary": "£40,000 to £55,000",
  "remote_type": "remote",
  "experience_level": "mid-level",
  "required_skills": ["WordPress", "WooCommerce", "PHP", "JavaScript", "HTML", "CSS", "SEO", "Google Analytics"],
  "preferred_skills": ["Shopify"],
  "qualifications": [
    "3+ years of WordPress and WooCommerce development",
    "Strong PHP, JavaScript, HTML and CSS skills",
    "SEO best practices and Google Analytics experience",
    "Bonus: Shopify experience"
  ]
}
```

### Example: Robotics JD

```json
{
  "job_title": "Robotics Engineer (Junior)",
  "remote_type": "on-site",
  "experience_level": "junior",
  "required_skills": ["ROS", "ROS2", "PID", "Gazebo", "MATLAB", "Simulink", "C++", "Python"],
  "responsibilities": [
    "Develop ROS2 nodes for autonomous mobile robots.",
    "Tune PID controllers and validate in Gazebo simulation.",
    "Prototype control systems in MATLAB / Simulink."
  ],
  "education_requirements": [
    "BSc in Robotics, Mechatronics, or Electrical Engineering."
  ]
}
```

## Matching engine

Compares one parsed JD against multiple parsed CVs and returns a deterministic
ranking. Lives in `app/services/matching_engine.py`; sub-scorers live in
`app/services/scoring_service.py`; synonym groups in
`app/services/synonyms.py`.

### Score breakdown (each 0-100)

```
overall = 0.40 * skill
        + 0.25 * semantic
        + 0.20 * experience
        + 0.10 * education
        + 0.05 * project
```

| Sub-score      | Logic                                                                                                                                                            |
|----------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **skill**      | Synonym-aware match. `0.75 * required_hit + 0.25 * preferred_hit`; if no preferred listed, falls back to `required_hit`. Fuzzy ratio ≥ 0.88 catches typos.       |
| **semantic**   | `EmbeddingService.similarity(cv, jd) * 100`. BoW cosine in Phase 1; sentence-transformers swaps in via the same interface in Phase 3.                            |
| **experience** | `0.6 * years_component + 0.4 * keyword_overlap`. Years from "X years" or year ranges in CV; keyword pool = required_skills + technologies (de-duped).            |
| **education**  | Degree-rank comparison (PhD > Masters > Bachelor > Diploma) plus field-of-study match (Computer Science, AI, Robotics, Engineering, Mathematics, Data Science…). |
| **project**    | Fraction of JD technologies referenced in CV `projects` + `certifications`, with a small bonus when projects (vs only certs) are present.                        |

### Synonym groups

Skills are matched after canonicalisation via `app/services/synonyms.py`:

| Canonical                       | Aliases                                                  |
|---------------------------------|----------------------------------------------------------|
| JavaScript                      | js, ecmascript                                           |
| TypeScript                      | ts                                                       |
| Machine Learning                | ml                                                       |
| Artificial Intelligence         | ai                                                       |
| Large Language Models           | llm, llms, large language model                          |
| Generative AI                   | genai, gen ai                                            |
| WordPress                       | wp                                                       |
| Natural Language Processing     | nlp                                                      |
| Deep Learning                   | dl                                                       |
| Retrieval Augmented Generation  | rag, retrieval-augmented generation                      |
| Kubernetes                      | k8s                                                      |
| Next.js / Node.js / React       | nextjs / nodejs / reactjs (variants)                     |
| AWS / GCP                       | amazon web services / google cloud                       |
| ROS2                            | ros 2                                                    |

Add groups by editing the `_GROUPS` list — first item is the canonical name.

### MatchResult fields

```json
{
  "cv_id": 1,
  "cv_name": "Alice Strong",
  "filename": "alice.pdf",
  "overall_score": 82.7,
  "skill_score": 100.0,
  "semantic_score": 57.5,
  "experience_score": 77.8,
  "education_score": 100.0,
  "project_score": 42.2,
  "matched_skills": ["Machine Learning", "FastAPI", "Python", "Docker", "Natural Language Processing", "AWS", "TypeScript", "Next.js"],
  "missing_skills": [],
  "strongest_points": [
    "Senior AI Engineer — Acme. Built RAG pipelines and shipped FastAPI services on AWS using Python."
  ],
  "improvement_suggestions": [
    "Strong alignment overall — consider quantifying impact in bullet points."
  ],
  "explanation": "This CV is a strong match for Senior AI Engineer because it includes Machine Learning, FastAPI, and Python, and there are no major skill gaps."
}
```

`improvement_suggestions` is template-driven and contextual — e.g. for a CV
missing Python it returns *"Add a bullet showing your experience with Python
if you have used it."*; for missing FastAPI it returns *"Mention FastAPI or
backend API experience if relevant."* See `_SUGGESTION_TEMPLATES` in
`matching_engine.py` to extend.

`explanation` is a single sentence summarising tier (strong / moderate /
weak), top matched skills, and notable gaps.

### Determinism

- No randomness anywhere in the pipeline.
- Stable sort: `(-overall_score, -skill_score, cv_id)`.
- Same `(JD, CVs)` → byte-identical `MatchResult`.

### Run the matching engine tests

```bash
python -m tests.test_matching_engine
```

## Semantic search (FAISS + sentence-transformers)

The matching engine combines **rule-based** scores (skill / experience /
education / project) with a **semantic** score driven by sentence-transformer
embeddings stored in a local FAISS index. Both signals are kept — the rule
based path is always on; the semantic path lights up when the neural deps
are installed.

### Pipeline

1. CV upload → `cv_chunker.chunk_cv()` splits the parsed CV into typed chunks
   (`summary`, `skills`, `experience`, `project`, `education`,
   `certification`, `languages`).
2. `EmbeddingService.encode()` runs each chunk through
   `sentence-transformers/all-MiniLM-L6-v2` (default, configurable via
   `APP_EMBEDDING_MODEL`). Vectors are L2-normalised so dot product == cosine.
3. `VectorStore` keeps the canonical numpy matrix + per-CV row map and
   mirrors it into a FAISS `IndexFlatIP` for global search.
4. Persistence: `data/index/cv_vectors.npy` + `cv_metas.json` (override dir
   with `APP_INDEX_DIR`). Loaded on startup, saved after upload / delete /
   rebuild.
5. Matching: for each CV, mean of top-3 cosine sims between the JD vector
   and the CV's chunk vectors → `semantic_score`. The same top chunks are
   returned as `top_semantic_matches` / `semantic_evidence` for the UI.

### Setup

```bash
pip install -r requirements.txt   # includes sentence-transformers, faiss-cpu, numpy
uvicorn app.main:app --reload
curl -X POST http://127.0.0.1:8000/api/embeddings/rebuild
```

The first `rebuild` call downloads the model (~90 MB) into the
HuggingFace cache. Subsequent runs reuse it.

### New endpoints

| Method | Path                         | Body                                          | Description                                        |
|--------|------------------------------|-----------------------------------------------|----------------------------------------------------|
| POST   | `/api/embeddings/rebuild`    | —                                             | Drops and re-builds the FAISS index from the DB    |
| POST   | `/api/search/semantic`       | `{ "query": "...", "top_k": 5 }`              | Returns the top-k CV chunks for a free-text query  |

`MatchResult` gains:

- `top_semantic_matches: list[SemanticMatch]` — full chunk metadata + score
- `semantic_evidence: list[str]` — convenience plain-text version for UI

### Graceful degradation

| State                                    | `/api/match`              | `/api/search/semantic`             | `/api/embeddings/rebuild`           |
|------------------------------------------|---------------------------|------------------------------------|-------------------------------------|
| Neural deps **not** installed            | works (BoW fallback)      | `503` with install instructions    | `200 status="disabled"`             |
| Installed but index empty                | works (BoW fallback)      | `404 "Index is empty"`             | `200 status="ok"` (counts returned) |
| Installed + index built                  | uses embeddings + FAISS   | works                              | works                               |

### Env vars

| Variable               | Default                                      | Purpose                                |
|------------------------|----------------------------------------------|----------------------------------------|
| `APP_EMBEDDING_MODEL`  | `sentence-transformers/all-MiniLM-L6-v2`     | Sentence-transformer model name        |
| `APP_INDEX_DIR`        | `backend/data/index`                         | Where vectors / metadata are persisted |

### Sample search

```bash
curl -X POST http://127.0.0.1:8000/api/search/semantic \
  -H 'Content-Type: application/json' \
  -d '{"query": "experience building RAG pipelines on AWS", "top_k": 5}'
```

```json
{
  "query": "experience building RAG pipelines on AWS",
  "results": [
    {
      "cv_id": 1, "cv_name": "Jane Doe", "filename": "jane.pdf",
      "kind": "experience", "idx": 0,
      "text": "Senior AI Engineer — Acme. Built RAG pipelines and shipped FastAPI services on AWS using Python.",
      "score": 0.7821
    }
  ]
}
```

### Run the new tests

```bash
python -m tests.test_cv_chunker
```

## Job URL scraping

The backend can fetch a public job-posting URL, extract the JD text, and
hand it to the existing parser/matcher.

### Pipeline

1. Validate URL — http(s) only; reject localhost / private / loopback /
   link-local addresses (basic SSRF guard).
2. Best-effort `robots.txt` check via `urllib.robotparser`. Refuse cleanly
   on explicit `Disallow`. Fail-open if `robots.txt` itself can't be loaded.
3. Per-host throttle: minimum 2 s between requests to the same host.
4. Fetch with `httpx`, custom UA, 12 s timeout, capped at 2 MB.
5. Extract metadata in this order:
   - JSON-LD `JobPosting` (best — structured `title`, `hiringOrganization`,
     `jobLocation`, `baseSalary`, `employmentType`, `description`).
   - OpenGraph / Twitter / standard `<meta>` tags.
   - `<title>` and first `<h1>` as fallbacks.
6. Extract body text via `trafilatura` (if installed) → falls back to a
   BeautifulSoup pass that strips `nav`, `footer`, `script`, `style`, etc.
7. Compose a labelled prefix (`Job Title: …\nCompany: …\nLocation: …\n`)
   and feed the whole blob into `parse_job_text`.

### Limitations (be honest about them)

- Many sites (LinkedIn, Indeed, BambooHR, etc.) require login, render the
  JD via JavaScript, or actively block scrapers. **Manual paste always
  works** and is the recommended fallback.
- This tool is for personal job search and portfolio demonstration.
  It does **not**:
  - bypass paywalls, logins, or CAPTCHAs;
  - run a headless browser to render JS;
  - ignore robots.txt or anti-bot challenges.
- Per-host rate limiting is in-process only — it doesn't survive restarts
  and isn't a substitute for proper crawler etiquette at scale.

### Safety hardening

The scraper applies multiple layers of safety on top of HTTP fetch:

| Layer | Behaviour |
|---|---|
| URL scheme | http / https only |
| SSRF guard | Refuses IP literals that resolve to private / loopback / link-local / reserved space |
| Domain block-list | Refuses `linkedin.com`, `indeed.com`, `glassdoor.com`, `ziprecruiter.com`, `monster.com`, `wellfound.com`/`angel.co`, `facebook.com`, `x.com`/`twitter.com` (+ subdomains). Override with `APP_SCRAPER_BLOCKLIST` (comma-separated). |
| robots.txt | `urllib.robotparser` honours explicit `Disallow`. Fail-open on robots-fetch errors. |
| Per-host throttle | 2 s minimum between requests to the same host (in-process). |
| Manual redirects | At most `MAX_REDIRECTS=5` hops; **each hop is re-validated** by `_validate_url`, so a redirect to `127.0.0.1` or a block-listed domain is refused mid-chain (defence-in-depth). |
| Response size | `Content-Length` header pre-checked when present; streamed body is hard-capped at 2 MB. |
| Content-type filter | Refuses anything that isn't `text/html` or `*/xml` family. |
| Charset handling | Decodes the body using the charset declared in `Content-Type`, with utf-8 fallback. |
| User-agent | Single descriptive UA so upstream services can identify and block us if they ever choose to. |

If any layer fires, `scrape_job_url` returns `success=False` with a
human-readable `error` — the route layer surfaces this so the frontend
can offer the manual-paste fallback.

### Examples

```bash
curl -X POST http://127.0.0.1:8000/api/jobs/from-url \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://example.com/careers/senior-ai-engineer"}'

curl -X POST http://127.0.0.1:8000/api/match/from-url \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://example.com/careers/senior-ai-engineer"}'
```

`from-url` returns `{success: false, error: "..."}` (HTTP 200) for
recoverable scraping issues so the frontend can offer the manual-paste
fallback. `match/from-url` returns 422 with the same message when there's
no usable text.

## CSV batch import

Upload a CSV with multiple jobs and get the best CV for each row in one
call. Useful for triaging a saved-jobs export from a job board or for
ranking a backlog of postings.

### CSV contract

| Column            | Required | Notes                                                |
|-------------------|----------|------------------------------------------------------|
| `description`     | ✅       | The JD body — fed to the rule-based parser           |
| `job_title`       | optional | Used as labelled prefix; also surfaced in the result |
| `company`         | optional |                                                      |
| `location`        | optional |                                                      |
| `url`             | optional | Echoed in the result so the user can re-open it      |
| `salary`          | optional |                                                      |
| `employment_type` | optional |                                                      |

- Header row required; column order doesn't matter.
- Header is normalised (lowercased, spaces → underscores) so
  `Job Title` / `JOB_TITLE` / `job title` all work.
- Extra columns are ignored.
- UTF-8 with optional BOM is preferred; latin-1 used as a fallback.
- Hard cap: **100 rows per upload**. Excess rows are dropped and the
  response sets `truncated: true`.

### Per-row behaviour

- Empty rows → silently skipped.
- Row with empty `description` → returned with `error="Empty description column."`
  so the frontend can render it inline; processing continues.
- Row that explodes during matching → returned with
  `error="Row failed during matching: …"`; the rest of the batch still runs.

### Example

```bash
curl -X POST http://127.0.0.1:8000/api/match/batch-csv \
  -F "file=@jobs.csv"
```

Response shape:

```json
{
  "rows": [
    {
      "row_index": 2,
      "job_title": "Senior AI Engineer",
      "company": "Cortex Labs",
      "location": "Berlin",
      "url": "https://example.com/jobs/123",
      "best_cv_id": 1,
      "best_cv_name": "Jane Doe",
      "best_cv_filename": "jane.pdf",
      "best_score": 82.7,
      "skill_score": 100.0,
      "semantic_score": 57.5,
      "matched_skills": ["Python", "FastAPI", "AWS"],
      "missing_skills": ["Docker"],
      "strongest_points": ["Built RAG pipelines on AWS using FastAPI."],
      "error": ""
    }
  ],
  "truncated": false,
  "rows_processed": 1,
  "rows_skipped": 0
}
```

`POST /api/jobs/import-csv` returns the same parsed rows **without**
running the matcher — useful for previewing what the system extracted
before kicking off the (slower) batch match.

## Optional LLM extraction layer

The system runs end-to-end with **only** the rule-based parsers. When an
OpenAI-compatible API is available you can opt in to an LLM-based
extraction layer that produces cleaner structured JSON for messy CVs and
JDs. The LLM never replaces matching/scoring — it only feeds better
inputs into the existing pipeline.

### Enable it

```bash
export USE_LLM_EXTRACTION=true
export OPENAI_API_KEY=sk-...                   # required
# Optional — point at any OpenAI-compatible endpoint:
export OPENAI_BASE_URL=https://api.openai.com/v1
export LLM_MODEL_NAME=gpt-4o-mini
export LLM_TIMEOUT_SECONDS=30
```

When `USE_LLM_EXTRACTION` is unset / falsey OR `OPENAI_API_KEY` is missing,
the system silently keeps using the rule-based parsers. The matcher logs
which path was used at INFO level, e.g.:

```
INFO ai_job_cv_matcher.extraction — CV extraction: LLM (model=gpt-4o-mini)
INFO ai_job_cv_matcher.extraction — JD extraction: LLM failed → falling back to rule-based parser.
```

### Behaviour

- All LLM calls happen on the backend. The API key is **never** exposed to
  the frontend or to logs.
- Strict JSON-only prompt with `response_format={"type":"json_object"}`.
- Output is validated by Pydantic (`_LLMCv` / `_LLMJob`). Any of these
  conditions falls back to the rule-based parser:
  - API key missing
  - Network / HTTP error
  - Timeout (`LLM_TIMEOUT_SECONDS`)
  - Non-JSON response (code fences are stripped first)
  - Schema validation failure
- Compatible endpoints: official OpenAI API, Azure OpenAI (with
  `OPENAI_BASE_URL`), local servers exposing the OpenAI Chat Completions
  shape (e.g. vLLM, Ollama with the `openai`-compatible adapter, LM Studio,
  Together, Groq).

### Prompt summary

- **CV → JSON**: name, summary, skills, education, experience, projects,
  certifications, languages, contact { email, phone, linkedin, github,
  website }.
- **JD → JSON**: job_title, company, location, salary, employment_type,
  remote_type, required_skills, preferred_skills, responsibilities,
  qualifications, experience_level, education_requirements, technologies,
  soft_skills.

Both prompts forbid prose outside the JSON, forbid markdown wrapping, and
explicitly require canonical skill names.

### Run the LLM tests

```bash
python -m tests.test_llm_extraction   # 12 tests, no real network calls
```

### End-to-end QA walkthrough

`tests/test_e2e.py` exercises the full user journey on a single live
FastAPI instance: upload → profile build → smart queries → JD parse →
text/url/file matching → CSV batch → discover → rank → tailor → generate
cover letter & LinkedIn message → agent run (with override) → delete +
state-after-delete sanity. External HTTP (URL scraper, discovery
sources) is monkeypatched so the suite is fully offline and deterministic.

```bash
python -m tests.test_e2e
```

## Profile aggregator

The profile aggregator is an **aggregation layer on top of CVs**, not a
replacement for them. Individual CVs stay in the `cvs` table and are
never modified. The aggregator combines:

- every uploaded CV, AND
- supplementary `Document`s — PDF / DOCX / TXT files uploaded via
  `POST /api/profile/build` (project notes, portfolio descriptions,
  certificates, transcripts, etc.)

…into a single explainable `UserProfile` row. The aggregation re-runs
the parser over each source so the profile reflects the *current*
parser behaviour.

### Endpoints

| Method | Path                | Body                                              | Notes                                          |
|--------|---------------------|---------------------------------------------------|------------------------------------------------|
| POST   | `/api/profile/build`| `multipart/form-data` field `files[]` (optional)  | Files are persisted as Documents, then aggregation runs across (CVs + Documents). |
| GET    | `/api/profile`      | —                                                 | Returns the unified profile (404 if not built).|
| DELETE | `/api/profile`      | —                                                 | Removes the profile **and** Documents. CVs preserved. |

Allowed document extensions: `.pdf`, `.docx`, `.txt`. Same 5 MB cap as
CV uploads.

### What it produces

```json
{
  "id": 1,
  "name": "Jane Doe",
  "summary": "...",
  "skills": [
    {"name": "Python", "weight": 2.7, "count": 2,
     "sources": ["cv:1", "cv:2"], "in_projects": true},
    {"name": "FAISS",  "weight": 1.5, "count": 1,
     "sources": ["cv:1"], "in_projects": true}
  ],
  "tools_and_technologies": [...],          // skills ∩ technical dictionary
  "work_experience": [
    {"text": "Senior AI Engineer — Acme (2019 - Present)",
     "start_year": 2019, "end_year": 2026,
     "recency_score": 1.0, "sources": ["cv:1"]}
  ],
  "education": [...],
  "projects": [...],
  "certifications": [...],
  "domains": ["AI/ML", "Backend", "DevOps / Cloud"],
  "languages": ["English (Native)", "Spanish (Conversational)"],
  "portfolio_links": {
    "linkedin": "https://linkedin.com/in/janedoe",
    "github":   "https://github.com/janedoe",
    "portfolio":"https://janedoe.dev",
    "websites": []
  },
  "source_cv_ids": [1, 2],
  "source_document_ids": [1],
  "updated_at": "2026-04-29T12:00:00"
}
```

### Skill weighting

```
weight = 1.0 · count + 0.5 · (in_projects ? 1 : 0) + 0.3 · max_recency
```

- **count** — number of distinct sources (CVs + Documents) that mention
  the skill, after canonicalisation through `synonyms.canonical()` (so
  `JS` and `JavaScript` collapse).
- **in_projects** — flag set when the skill appears in any project line.
- **max_recency** — recency of the most-recent experience entry that
  mentions the skill (1.0 = current year, 0.5 = ~7 years ago).

Skills are canonical-name aware AND scanned out of free-text bodies via
the project's technical-skill dictionary, so portfolio prose without a
"Skills" header still surfaces real tooling mentions.

### Domain inference

Domains are auto-derived from the skill set: AI/ML, Web/E-commerce,
Robotics, Backend, DevOps/Cloud, Frontend.

### Privacy

Documents and the unified profile contain personal data. They live in
the same `data/` volume as the CVs and are git-ignored. Calling
`DELETE /api/profile` removes the profile **and every Document**; CVs
are intentionally preserved so you can rebuild the profile later.

## Job discovery

`POST /api/jobs/discover` pulls candidate jobs from **public, no-login
JSON endpoints only** and ranks them against the user's tags.

### Sources (all free, no auth)

| Source            | Endpoint                                       | Notes                                              |
|-------------------|------------------------------------------------|----------------------------------------------------|
| **RemoteOK**      | `https://remoteok.com/api`                     | Whole-list dump; first item is metadata, skipped.  |
| **Remotive**      | `https://remotive.com/api/remote-jobs`         | Supports `?search=` query.                         |
| **HN "Who is hiring"** | `https://hn.algolia.com/api/v1/search`    | Comments matching the query, filtered to lines with `hiring` / `remote` / `engineer`. |

### Constraints (enforced)

- **No HTML scraping** of job boards. Only documented JSON APIs.
- **No login**, **no anti-bot bypass**, **no headless browser**.
- Single descriptive `User-Agent` so upstream services can identify
  and block the project if they ever want to.
- Per-host throttle: 3 s minimum between calls to the same domain.
- Hard cap: 100 results per request regardless of `max_total`.
- Each source is isolated — one failing surfaces in `errors[]`; the
  rest still return.

### Behaviour

- If `queries` / `tags` are omitted, both are derived from the unified
  `UserProfile` via the smart-query builder. Typical flow:
  `POST /api/profile/build` → `POST /api/jobs/discover` (empty body).
- Results are deduped by URL across sources.
- Each result carries a `relevance_score` (0..1) and `matched_terms`
  showing which skills/roles/domains hit. Score is `0.6·skill_overlap
  + 0.3·role_overlap + 0.1·domain_overlap`.

### Example

```bash
curl -X POST http://127.0.0.1:8000/api/jobs/discover \
  -H 'Content-Type: application/json' \
  -d '{
    "sources": ["remoteok", "remotive"],
    "max_total": 25
  }'
```

Sample result row:

```json
{
  "title": "Senior AI Engineer",
  "company": "Cortex Labs",
  "location": "Remote",
  "url": "https://remoteok.com/jobs/...",
  "snippet": "Build RAG pipelines with Python and FastAPI on AWS.",
  "tags": ["python", "rag", "llm"],
  "source": "remoteok",
  "posted_at": "2025-01-01",
  "relevance_score": 0.74,
  "matched_terms": ["Python", "RAG", "LLM"]
}
```

## CV tailoring (`POST /api/tailor`)

One JD in, the best-matching CV out, plus **structured** tailoring
advice the UI can render in distinct panels:

| Field | Meaning |
|---|---|
| `skills_to_add` | Required JD skills missing from the CV |
| `skills_to_emphasize` | Matched skills that are listed in Skills but never appear in the summary / experience prose (the kind a recruiter's keyword scan misses) |
| `keywords_for_ats` | Canonical, deduped JD skills + technologies in priority order |
| `sections_to_add` | Summary / Projects / Certifications when the JD signals they matter and the CV lacks them |
| `bullets_to_rewrite` | Up to 3 experience bullets that mention zero matched skills, paired with the target skills they should weave in and a rationale |
| `summary_hint` | One-sentence template the user can adapt for a tailored summary |
| `generic_tips` | Falls through to the matcher's existing rule-based improvement_suggestions |

Pool selection mirrors `/api/jobs/rank`:
- explicit `cv_ids` → only those CVs,
- else every uploaded CV,
- else (when `use_profile_fallback=true`) a synthetic CV built from the
  unified `UserProfile` (so users with documents but no formal CV still
  get tailoring advice).

Output is fully deterministic — same input, same suggestions. An LLM
rewrite step can layer on top later through the same return type
(replace `bullets_to_rewrite[i].original` with a generated rewrite,
populate `summary_hint` with a model-written summary, etc.).

### Example

```bash
curl -X POST http://127.0.0.1:8000/api/tailor \
  -H 'Content-Type: application/json' \
  -d '{"job_text": "Senior AI Engineer. Required: Python, ML, RAG, FAISS, NLP, Docker, AWS."}'
```

Selected fields from the response:

```json
{
  "best_cv_id": 1,
  "best_cv_name": "Alice Strong",
  "best_cv_filename": "alice.pdf",
  "match": { "overall_score": 78.4, "...": "..." },
  "suggestions": {
    "skills_to_add": ["Natural Language Processing"],
    "skills_to_emphasize": ["Machine Learning", "RAG"],
    "keywords_for_ats": ["Python", "Machine Learning", "RAG", "FAISS", "NLP", "Docker", "AWS"],
    "sections_to_add": [],
    "bullets_to_rewrite": [
      {
        "original": "Senior AI Engineer — Acme. Led a small team and shipped production services.",
        "target_skills": ["Machine Learning", "RAG", "AWS"],
        "rationale": "This bullet doesn't reference any of the JD's required skills. Rewrite it to highlight: Machine Learning, RAG, AWS."
      }
    ],
    "summary_hint": "Senior-level engineer focused on Machine Learning, RAG, FAISS, with a track record of shipping production work relevant to Senior AI Engineer.",
    "generic_tips": ["..."]
  },
  "used_profile_fallback": false
}
```

## Agent orchestrator (`POST /api/agent/run`)

Chains every layer in one call:

```
Profile → Queries/Tags → Discovery → Ranking → Tailoring
```

Behaviour:

- **Profile** — uses the existing unified profile if present; otherwise
  auto-builds it from any CVs already in the DB so a freshly-uploaded
  CV is enough to run the agent.
- **Queries / Tags** — derived via `query_builder` (same payload as
  `GET /api/profile/queries`).
- **Discovery** — runs `discover_jobs` against the same public no-login
  sources (`remoteok`, `remotive`, `hn`); honours per-host throttling
  and the 100-result hard cap.
- **Ranking** — for each discovered job (up to `max_rank`), runs
  `extract_job` + `match_cv_to_job` against the chosen CV pool and
  records the best CV.
- **Tailoring** — for the top `max_tailor` ranked jobs, generates the
  same structured suggestions as `POST /api/tailor`. State is reused
  from the ranking pass — no duplicate parses or matches.

Each step writes an `AgentStep("ok"|"skipped"|"error", detail)` entry
to `steps[]` so the UI can render a progress trace and surface partial
results when something downstream fails. Fatal errors (no profile and
no CVs to build one from, empty CV pool) set `error` and short-circuit;
the partial trace is still returned.

### Defaults

| Field              | Default | Purpose                                  |
|--------------------|--------:|------------------------------------------|
| `max_discover`     | 30      | Cap on jobs pulled per source            |
| `max_rank`         | 15      | Cap on jobs scored against the CV pool   |
| `max_tailor`       | 5       | Cap on full tailoring bundles produced   |
| `use_profile_fallback` | true | Allow synthetic CV from profile when no CVs |

### Example

```bash
curl -X POST http://127.0.0.1:8000/api/agent/run \
  -H 'Content-Type: application/json' \
  -d '{"max_tailor": 3}'
```

The response shape:

```json
{
  "steps": [
    {"name": "profile",   "status": "ok",      "detail": "existing profile"},
    {"name": "queries",   "status": "ok",      "detail": "8 queries, 5 roles"},
    {"name": "discovery", "status": "ok",      "detail": "27 jobs"},
    {"name": "ranking",   "status": "ok",      "detail": "15 ranked"},
    {"name": "tailoring", "status": "ok",      "detail": "3 tailoring bundles"}
  ],
  "profile":   { "id": 1, "name": "...", "...": "..." },
  "queries":   ["Senior AI Engineer (Python, FastAPI, RAG)", "..."],
  "tags":      { "roles": [...], "skills": [...], "...": "..." },
  "discovered":[ /* ranked DiscoveredJob objects */ ],
  "ranked":    [ /* RankedJobResult objects */ ],
  "tailored":  [ /* TailorResponse objects */ ],
  "used_profile_fallback": false,
  "error": ""
}
```

## Application generators (`POST /api/generate`)

One JD in, the best-matching CV out, plus three application artefacts:

| Output             | What it is                                                                              |
|--------------------|-----------------------------------------------------------------------------------------|
| `cv_suggestions`   | Prose version of the structured tailoring panel — what to add, emphasize, rewrite, ATS keywords |
| `cover_letter`     | Short, calibrated cover letter using the matched skills + the strongest CV bullet       |
| `linkedin_message` | 3-sentence outreach message you can paste straight into LinkedIn                        |

Pass `kinds` to pick any subset (default: all three).

### Behaviour

- **Deterministic by default.** Templates are in
  `app/services/generation_service.py`. Same CV + JD → same output.
- **Optional LLM polish.** When `USE_LLM_EXTRACTION=true` and
  `OPENAI_API_KEY` is set, each draft is sent to the LLM with a strict
  "polish without inventing claims" system prompt. Any failure (network,
  empty response, exception) falls back silently to the deterministic
  draft. Set `polish_with_llm: false` in the request body to force the
  rule-based path.
- **Same pool selection** as `/api/tailor` and `/api/jobs/rank` —
  explicit `cv_ids` → all CVs → unified profile fallback.
- **Privacy** — only the chosen CV + the JD text are sent to the LLM.
  The API key never leaves the backend.

### Example

```bash
curl -X POST http://127.0.0.1:8000/api/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "job_text": "Senior AI Engineer at Cortex Labs. Required: Python, ML, RAG, FAISS, Docker, AWS.",
    "kinds": ["cover_letter", "linkedin_message"]
  }'
```

Selected fields from the response:

```json
{
  "best_cv_filename": "alice.pdf",
  "match": { "overall_score": 82.5, "...": "..." },
  "cover_letter": "Dear hiring team at Cortex Labs,\n\nI'm writing to apply for the Senior AI Engineer role at Cortex Labs. With 6+ years of professional experience across Python, Machine Learning, and RAG, I'm a strong match for what you've described.\n\nSpecifically, what I bring that maps to your requirements:\n- Python: documented in my CV and applied directly in my recent work.\n- ...",
  "linkedin_message": "Hi — I came across the Senior AI Engineer opening at Cortex Labs and 6+ years of Python makes me think it could be a strong fit.\nMy background covers Python, Machine Learning, and RAG — happy to share concrete examples.\nWould you be open to a 15-minute chat next week?\n\nThanks, Alice Strong",
  "cv_suggestions": "...",
  "used_llm": false,
  "used_profile_fallback": false
}
```

## Tailored LaTeX CV renderer

Three endpoints that together let you maintain a single source of truth
for your CV and produce a JD-tailored `.tex` (and optionally `.pdf`) per
job application.

### Library shape

```jsonc
{
  "header":   { "name": "...", "location": "...", "email": "...",
                "phone": "...", "website": "...", "linkedin": "...", "github": "..." },
  "summary":  "Applied AI engineer with…",
  "skills_groups": [
    { "label": "Languages",        "items": ["Python", "SQL", "..."] },
    { "label": "LLM / Applied AI", "items": ["RAG", "prompt engineering", "..."] }
  ],
  "education":          [ { "institution": "...", "degree": "...", "period": "...", "highlights": ["..."] } ],
  "selected_projects":  [ { "title": "...", "period": "...", "tags": ["Python","RAG"], "highlights": ["..."] } ],
  "additional_projects":[ { ...same shape... } ],
  "experience":         [ { "title": "...", "company": "...", "period": "...", "tags": [], "highlights": [] } ],
  "publications":       [ { "title": "...", "status": "Under Submission", "venue": "", "tags": [] } ],
  "certifications":     [ { "issuer": "Microsoft", "name": "Azure AI-900", "tags": ["Azure"] } ],
  "languages":          [ "English: Professional", "Farsi: Native" ]
}
```

The `tags` array on every entry drives JD-fit ranking. Add more
projects, publications, etc. than you'd ever fit on one CV — the
renderer picks the best subset per job.

### Rendering pipeline

For each `POST /api/cv/render`:

1. Parse the JD via the existing rule-based parser → canonical skills.
2. Rank `selected_projects`, `additional_projects`, `experience`,
   `publications`, `certifications` by `tag` overlap with the JD.
3. Truncate each section to the configurable cap (`max_selected_projects`
   default 4, `max_additional_projects` 3, `max_experience` 4).
4. Re-order `skills_groups` so categories with JD-matched items come first.
5. Bold every JD-matched skill (and its synonyms) inside bullet text
   using `\textbf{…}` — boundary-aware, never wraps inside an existing
   bold, ignores ultra-short aliases (≥ 3 chars only).
6. Fill the LaTeX template (charter font, 0.5 cm margins, hrulefill
   section rules — identical to the user's original layout).
7. If `compile_pdf=true` and a compiler is available, also run
   `tectonic` (preferred — single binary) or `pdflatex` and return the
   PDF as base64.

### Setup

```bash
# Seed the bundled sample (Danial's CV) once after first boot.
make seed-cv

# Then render a tailored CV.
curl -X POST http://localhost:8000/api/cv/render \
  -H 'Content-Type: application/json' \
  -d '{
    "job_text": "Senior AI Engineer at Cortex Labs. Required: Python, FastAPI, RAG, FAISS.",
    "max_selected_projects": 3,
    "compile_pdf": true
  }' | jq -r '.latex' > tailored.tex
```

### PDF compilation

The Docker image bakes in `tectonic` (single-binary LaTeX, no TeX Live
install needed). On first compile inside the container, tectonic
downloads the required packages and caches them — subsequent renders
are fast.

For local dev (without Docker):

```bash
brew install tectonic            # macOS
# or:
sudo apt-get install texlive-latex-extra texlive-fonts-extra
```

If neither compiler is on PATH, the endpoint still returns the LaTeX
string and a `compile_error` explaining what to install — never errors
out.

## Future work

- Replace BoW similarity with sentence-transformers + FAISS.
- LLM-backed extractor as a fallback when heuristic parsing is sparse.
- Auth + multi-user CV libraries.
- Alembic migrations once the schema stabilises.
