# LinkedIn announcement post

Three options — pick the tone that fits how you usually post. Replace
`<repo URL>` with the GitHub link before publishing.

---

## Option 1 — concise, technical

🚀 New project: **AI Job-CV Matching Agent**

An end-to-end, explainable system that ranks CVs against job
descriptions and shows *why* — combining structured parsing, hard skill
constraints, and semantic search over sentence-transformer embeddings.

🔧 Stack
• FastAPI + SQLAlchemy + Pydantic
• Next.js 14 + TypeScript + Tailwind
• sentence-transformers (`all-MiniLM-L6-v2`) + FAISS
• Optional OpenAI-compatible LLM layer with rule-based fallback
• Docker Compose deployment

🧠 What's inside
✅ PDF / DOCX parsing
✅ Synonym-aware skill matching (JS≡JavaScript, ML≡Machine Learning, …)
✅ Five-factor score: skills 40%, semantic 25%, experience 20%,
   education 10%, projects 5%
✅ Job-URL scraping (robots.txt-aware)
✅ CSV batch matching
✅ Evaluation suite (P/R/F1, top-1/top-3, MRR, constraint satisfaction)
✅ Fine-tuning prep with Unsloth + LoRA for Qwen / Gemma / Llama

Built to demonstrate the full lifecycle of a small but real AI system,
not a single prompt-call demo. Code, screenshots, and the architecture
diagram: <repo URL>

#AI #NLP #RAG #FastAPI #NextJS #LLM #LoRA #Unsloth #PortfolioProject

---

## Option 2 — narrative, hiring-friendly

I just shipped a portfolio project that I'm proud of: **AI Job-CV
Matching Agent**.

Most "AI matchers" do one of two things — fuzzy keyword overlap with no
explainability, or a single LLM call that hallucinates skills the
candidate never had. Neither is something a real recruiter (or a sharp
NLP engineer reviewing a portfolio) can trust.

So I built the alternative. The system parses CVs and job descriptions
into typed structures, runs **synonym-aware** skill matching with hard
required-skill constraints, layers **semantic search** over sentence-
transformer embeddings stored in a FAISS index, and aggregates
everything into a five-factor score that's broken down per CV: skills,
semantic similarity, experience relevance, education fit,
project/certification overlap. Every recommendation comes with the
exact CV bullets that support it.

It also ships with the parts most demos skip:

→ Optional OpenAI-compatible LLM extraction (with graceful fallback to
  the heuristic parser on every failure mode I could think of)
→ Job-URL scraping that respects robots.txt and refuses local addresses
→ CSV batch matching for triaging a saved-jobs export
→ A real evaluation suite on a versioned synthetic gold set
  (precision / recall / F1, top-1 / top-3 accuracy, MRR, constraint
  satisfaction)
→ A fine-tuning module that exports the project's data into instruction-
  tuning JSONL and trains a Qwen / Gemma / Llama adapter via Unsloth +
  LoRA in a Colab T4 notebook
→ One-command Docker deployment

If you're hiring for AI / NLP / RAG / full-stack-AI roles and want to
see the full lifecycle of a small AI system handled end-to-end —
parsing, retrieval, scoring, evaluation, fine-tuning prep, deployment —
the repo is here: <repo URL>

Open to feedback, PRs, and conversations.

#AIEngineering #NLP #RAG #FineTuning #LoRA #FastAPI #NextJS

---

## Option 3 — short and punchy

🛠️  Shipped: **AI Job-CV Matching Agent** — an explainable system that
ranks CVs against job descriptions using *parsing + hard constraints +
semantic search + scoring*, not just a prompt.

▸ FastAPI + Next.js + sentence-transformers + FAISS
▸ Optional LLM extraction with rule-based fallback
▸ Job-URL scraping + CSV batch matching
▸ Evaluation suite on a versioned gold set
▸ Fine-tuning prep with Unsloth + LoRA

Code, diagrams, sample input/output: <repo URL>

If you're hiring for AI / NLP / RAG roles, take a look 🙂

#AI #NLP #RAG #PortfolioProject

---

## Tips before publishing

- Replace `<repo URL>` with the actual link.
- Pin a screenshot — LinkedIn rewards posts with images.
- Tag any tools or maintainers you genuinely use (Unsloth, FAISS, etc.)
  rather than spraying mentions.
- Posting hours that work for technical content: Tue–Thu, 09:00–11:00
  in your audience's timezone.
- Engage with comments for the first hour — LinkedIn's algorithm
  rewards early replies.
