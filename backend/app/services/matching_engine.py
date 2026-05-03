"""Glue layer: parse a JD and score every CV against it.

Outputs:
    - `MatchResult` per CV with sub-scores, matched/missing skills,
      strongest points, improvement suggestions, and a human-readable
      explanation string.
    - `RankedMatchResponse` with the JD echoed back and CVs sorted desc by
      `overall_score`.

Determinism: no randomness, no external LLM calls. Same inputs → same outputs.
"""
from __future__ import annotations

from app.models.db_models import CV
from app.models.schemas import JobParsed, MatchResult, RankedMatchResponse, SemanticMatch
from app.services.embedding_service import EmbeddingService, get_embedding_service
from app.services.extraction import extract_job
from app.services.scoring_service import (
    aggregate,
    education_score,
    experience_score,
    project_score,
    semantic_score,
    skill_score,
)
from app.services.vector_store import VectorStore, get_vector_store


# ---------- Suggestion templates ----------

# Per-skill phrasings for the most common required skills. Anything not in
# the map falls back to `_DEFAULT_SUGGESTION_TEMPLATE`.
_SUGGESTION_TEMPLATES: dict[str, str] = {
    "Python": "Add a bullet showing your experience with Python if you have used it.",
    "FastAPI": "Mention FastAPI or backend API experience if relevant.",
    "Machine Learning": "Highlight your AI/ML coursework or project experience.",
    "Deep Learning": "Add deep-learning projects or model-training experience to your CV.",
    "Natural Language Processing": "Showcase any NLP work — text classification, embeddings, or LLM apps.",
    "Large Language Models": "Mention any work with LLMs, prompt engineering, or RAG pipelines.",
    "Retrieval Augmented Generation": "Add a project or bullet about RAG / retrieval pipelines.",
    "LangChain": "Mention LangChain in your AI/LLM project bullets.",
    "FAISS": "Reference FAISS (or another vector DB) in your retrieval projects.",
    "Chroma": "Reference Chroma (or another vector DB) in your retrieval projects.",
    "PyTorch": "Add PyTorch model training experience or coursework.",
    "TensorFlow": "Mention TensorFlow in any deep-learning work you have done.",
    "Docker": "Add Docker / containerization experience to a deployment bullet.",
    "Kubernetes": "Highlight any Kubernetes / orchestration experience.",
    "AWS": "List AWS services you have used (e.g. EC2, S3, Lambda, ECS).",
    "Azure": "Mention Azure services you have worked with.",
    "GCP": "Mention GCP services you have worked with.",
    "SQL": "Add a bullet showcasing SQL / relational database experience.",
    "React": "Highlight any React UI work, even from side projects.",
    "Next.js": "Add Next.js experience if you have shipped any front-end work.",
    "TypeScript": "Mention TypeScript in your front-end / Node experience.",
    "JavaScript": "Highlight your JavaScript work explicitly in skills and bullets.",
    "WordPress": "Add a bullet for WordPress site development if you have any.",
    "WooCommerce": "Mention WooCommerce stores or plugins you have built.",
    "PHP": "Add PHP project experience to your CV.",
    "SEO": "Reference SEO best practices or measurable improvements you delivered.",
    "Google Analytics": "Mention Google Analytics setup or reporting experience.",
    "ROS": "Add a bullet for any ROS-based robotics project.",
    "ROS2": "Highlight ROS2 nodes or simulations you have built.",
    "PID": "Mention PID controller tuning experience if you have any.",
    "Gazebo": "Reference Gazebo simulations in your robotics projects.",
    "MATLAB": "Highlight MATLAB / Simulink modelling work.",
    "Simulink": "Highlight MATLAB / Simulink modelling work.",
    "Git": "Add Git / version control to your skills section.",
}

_DEFAULT_SUGGESTION_TEMPLATE = (
    "Add a bullet referencing {skill} if you have related experience or coursework."
)


def _suggestion_for(skill: str) -> str:
    return _SUGGESTION_TEMPLATES.get(skill, _DEFAULT_SUGGESTION_TEMPLATE.format(skill=skill))


# ---------- Strongest points ----------

def _strongest_points(cv: CV, matched_skills: list[str]) -> list[str]:
    """Pick up to three CV bullets that mention a matched skill.

    Falls back to the first experience/project entries if nothing scores.
    """
    if not matched_skills:
        return (cv.experience[:3] or cv.projects[:3] or [])

    matched_low = [s.lower() for s in matched_skills]
    scored: list[tuple[int, str]] = []
    pool = (cv.experience or []) + (cv.projects or [])
    for entry in pool:
        low = entry.lower()
        hits = sum(1 for s in matched_low if s in low)
        if hits:
            scored.append((hits, entry))

    # Sort by descending hits, preserving original order for ties (stable).
    scored.sort(key=lambda t: t[0], reverse=True)
    seen: set[str] = set()
    out: list[str] = []
    for _, entry in scored:
        if entry not in seen:
            out.append(entry)
            seen.add(entry)
        if len(out) >= 3:
            break
    if not out:
        return (cv.experience[:3] or cv.projects[:3] or [])
    return out


# ---------- Improvement suggestions ----------

def _build_suggestions(
    missing_skills: list[str],
    cv: CV,
    job: JobParsed,
) -> list[str]:
    """Per-skill templates first, then structural tips."""
    tips: list[str] = []

    for skill in missing_skills[:6]:  # cap to keep the panel compact
        tips.append(_suggestion_for(skill))

    if not cv.summary:
        tips.append("Add a 2-3 line professional summary tailored to this role.")

    if not cv.projects and (job.technologies or job.required_skills):
        tips.append("Add 1-2 portfolio projects that demonstrate the required skills.")

    if not cv.certifications and job.education_requirements:
        tips.append("List relevant certifications or courses matching the JD requirements.")

    if job.experience_level in {"senior", "lead", "principal"} and len(cv.experience or []) < 3:
        tips.append("Expand work experience with quantified achievements for senior-level roles.")

    if job.soft_skills and not (cv.summary or cv.experience):
        tips.append("Demonstrate soft skills (e.g. communication, mentoring) in your bullets.")

    if not tips:
        tips.append("Strong alignment overall — consider quantifying impact in bullet points.")
    return tips


# ---------- Explanation ----------

def _build_explanation(
    overall: float,
    matched: list[str],
    missing: list[str],
    job: JobParsed,
) -> str:
    """Human-readable single-sentence summary of the match."""
    role = job.job_title or "this role"
    tier = "strong" if overall >= 75 else "moderate" if overall >= 50 else "weak"

    matched_part = (
        f"it includes {_join_human(matched[:3])}"
        if matched else "few of the required skills are present"
    )
    if missing:
        missing_part = f", but it is missing {_join_human(missing[:3])}"
    else:
        missing_part = ", and there are no major skill gaps"

    return f"This CV is a {tier} match for {role} because {matched_part}{missing_part}."


def _join_human(items: list[str]) -> str:
    """Oxford-comma join for nicer prose ('A, B, and C')."""
    items = [i for i in items if i]
    if not items:
        return "—"
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


# ---------- Public API ----------

def _semantic_for_cv(
    cv: CV,
    job: JobParsed,
    embedder: EmbeddingService,
    store: VectorStore | None,
) -> tuple[float, list[SemanticMatch]]:
    """Compute the semantic score and top-3 evidence chunks for one CV.

    Two paths:
      - Neural + vector store available → mean of top-k(=3) cosine similarities
        between the JD vector and this CV's chunk vectors. Evidence chunks
        are returned for UI display.
      - Otherwise → BoW cosine on the full texts; evidence list is empty.
    """
    jd_text = " ".join(filter(None, [
        job.raw_text or "",
        " ".join(job.responsibilities or []),
        " ".join(job.required_skills or []),
    ]))

    if embedder.is_ready() and store is not None and store.has_cv(cv.id):
        cv_vecs, metas = store.get_for_cv(cv.id)
        if cv_vecs.shape[0] > 0:
            jd_vec = embedder.encode([jd_text])[0]
            sims = cv_vecs @ jd_vec
            top_n = min(3, sims.shape[0])
            order = sims.argsort()[::-1][:top_n]
            avg = float(sims[order].mean())
            top_chunks = [
                SemanticMatch(
                    cv_id=int(metas[int(i)]["cv_id"]),
                    cv_name=metas[int(i)].get("cv_name", ""),
                    filename=metas[int(i)].get("filename", ""),
                    kind=metas[int(i)].get("kind", ""),
                    idx=int(metas[int(i)].get("idx", 0)),
                    text=metas[int(i)].get("text", ""),
                    score=round(float(sims[int(i)]), 4),
                )
                for i in order
            ]
            return round(max(0.0, avg) * 100, 2), top_chunks

    # Fallback: lexical similarity on full JD ↔ CV raw text.
    return semantic_score(cv.raw_text or "", jd_text, embedder), []


def match_cv_to_job(
    cv: CV,
    job: JobParsed,
    *,
    embedder: EmbeddingService | None = None,
    store: VectorStore | None = None,
) -> MatchResult:
    """Compute every sub-score and assemble a `MatchResult`."""
    embedder = embedder or get_embedding_service()
    if store is None:
        store = get_vector_store()

    s_skill, matched, missing, _matched_pref = skill_score(
        cv.skills or [], job.required_skills, job.preferred_skills
    )
    s_sem, top_semantic = _semantic_for_cv(cv, job, embedder, store)

    # Experience: years + domain-keyword overlap.
    # Pool from skills only (de-duplicated). Responsibility *sentences* are
    # too long to overlap reliably; their skills already live in technologies.
    keyword_pool = list(dict.fromkeys(
        [s for s in (job.required_skills or []) + (job.technologies or []) if s]
    ))
    s_exp = experience_score(
        cv.experience or [], job.raw_text or "", job.experience_level, keyword_pool
    )

    s_edu = education_score(cv.education or [], job.education_requirements)
    s_proj = project_score(
        cv.projects or [], cv.certifications or [], job.technologies or job.required_skills
    )

    overall = aggregate(s_skill, s_sem, s_exp, s_edu, s_proj)

    return MatchResult(
        cv_id=cv.id,
        cv_name=cv.name,
        filename=cv.filename,
        overall_score=overall,
        skill_score=s_skill,
        semantic_score=s_sem,
        experience_score=s_exp,
        education_score=s_edu,
        project_score=s_proj,
        matched_skills=matched,
        missing_skills=missing,
        strongest_points=_strongest_points(cv, matched),
        improvement_suggestions=_build_suggestions(missing, cv, job),
        explanation=_build_explanation(overall, matched, missing, job),
        top_semantic_matches=top_semantic,
        semantic_evidence=[m.text for m in top_semantic],
    )


def rank_cvs(cvs: list[CV], job_text: str) -> tuple[JobParsed, list[MatchResult]]:
    """Parse a JD and rank every CV against it (highest score first)."""
    parsed = extract_job(job_text)
    job = JobParsed(**parsed.to_dict())

    # Resolve the embedder/store once so we don't reload the model per CV.
    embedder = get_embedding_service()
    store = get_vector_store()

    results = [match_cv_to_job(cv, job, embedder=embedder, store=store) for cv in cvs]
    # Stable sort by overall_score desc, then skill_score desc, then cv_id asc.
    results.sort(key=lambda r: (-r.overall_score, -r.skill_score, r.cv_id))
    return job, results


def build_ranked_response(cvs: list[CV], job_text: str) -> RankedMatchResponse:
    job, results = rank_cvs(cvs, job_text)
    return RankedMatchResponse(
        job=job,
        results=results,
        recommended_cv_id=results[0].cv_id if results else None,
    )
