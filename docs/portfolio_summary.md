# Portfolio summary

Use one of these blurbs verbatim — or pick the bits that fit — on your
CV, personal site, or in a recruiter cold-email.

## One-liner (CV bullet)

Built an end-to-end AI Job-CV Matching Agent (FastAPI + Next.js +
sentence-transformers + FAISS) that ranks CVs against job descriptions
with a transparent five-factor score, an optional LLM extraction layer,
job-URL scraping, CSV batch matching, and a fine-tuning pipeline for
small open models (Qwen / Gemma / Llama via Unsloth + LoRA).

## Short paragraph (portfolio site)

**AI Job-CV Matching Agent** — a full-stack, explainable matcher that
combines structured parsing, hard skill constraints, and semantic search
over sentence-transformer embeddings stored in a FAISS index. Users
upload PDF/DOCX CVs and paste a job description, paste a posting URL,
or upload a CSV of jobs; the system returns a ranked list of candidates
with a score breakdown, matched and missing skills, evidence chunks,
and concrete improvement suggestions. An optional OpenAI-compatible LLM
layer can replace the heuristic JSON extraction with graceful fallback.
A dedicated training module turns parsed records into instruction-tuning
JSONL and ships a Colab-T4-friendly Unsloth + LoRA script for
fine-tuning Qwen 2.5 / Gemma-2 / Llama-3.2 on the same task. Docker
Compose deploys it in one command; an evaluation suite measures
field-level F1, top-1 / top-3 accuracy, MRR, and constraint satisfaction
on a versioned synthetic gold set.

**Stack:** Python · FastAPI · SQLAlchemy · Pydantic · sentence-transformers
· FAISS · Next.js 14 (App Router) · TypeScript · Tailwind ·
Unsloth + LoRA · Docker.

## Longer paragraph (cover letter / about page)

I built **AI Job-CV Matching Agent** to demonstrate the full lifecycle
of a small but real AI system: from raw text ingestion (PDF / DOCX /
HTML / CSV) through structured parsing, vector indexing, multi-factor
ranking, evaluation, and optional fine-tuning. The matcher uses five
weighted sub-scores — skill match, semantic similarity to the JD via
top-3 sentence-transformer cosine similarity over typed CV chunks,
experience years + keyword overlap, education degree-rank + field of
study, and project / certification overlap — every one of which is
shown to the user with the exact CV bullet that supports it. A
synonym-aware skill dictionary (`JS`≡`JavaScript`, `ML`≡`Machine
Learning`, `WP`≡`WordPress`, …) keeps the rule-based scoring honest;
sentence-transformers + FAISS catch the paraphrases the rules miss.
Both the embedding service and an OpenAI-compatible LLM extraction
layer are gracefully degradable: the system works with neither, with
either, or with both. A separate training module exports the project's
parsed records into instruction-tuning JSONL, validates the format, and
fine-tunes a 0.5B – 3B open model with Unsloth + LoRA in a Colab T4
notebook — the resulting adapter slots back into the backend through
the same OpenAI-compatible interface (e.g. via vLLM or Ollama). The
project ships with Docker Compose, persistent volumes, health checks,
and an evaluation suite that surfaces honest precision / recall / F1
numbers on a versioned gold set so improvements are measured, not
asserted.
