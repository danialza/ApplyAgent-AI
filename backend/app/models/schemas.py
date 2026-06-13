"""Pydantic v2 DTOs used by the API layer."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------- CV ----------

class CVBase(BaseModel):
    filename: str
    name: str = ""
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    experience: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)

    # Contact info (empty string when not detected).
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""

    raw_text: str = ""


class CVOut(CVBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------- Job ----------

class JobParseRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Raw JD text pasted by the user.")


class JobParsed(BaseModel):
    job_title: str = ""
    company: str = ""
    location: str = ""
    salary: str = ""
    employment_type: str = ""
    remote_type: str = ""

    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    qualifications: list[str] = Field(default_factory=list)

    experience_level: str = ""
    education_requirements: list[str] = Field(default_factory=list)

    technologies: list[str] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)

    raw_text: str = ""


class JobUrlRequest(BaseModel):
    url: str = Field(..., min_length=1, description="Public job-posting URL (http/https).")


class JobUrlResponse(BaseModel):
    url: str
    success: bool
    extracted_text: str = ""
    parsed_job: Optional[JobParsed] = None
    error: str = ""
    notes: list[str] = Field(default_factory=list)


class JobFileResponse(BaseModel):
    """Response shape for `POST /api/jobs/from-file`."""
    filename: str
    success: bool
    extracted_text: str = ""
    parsed_job: Optional[JobParsed] = None
    error: str = ""


# ---------- Semantic search ----------

class SemanticMatch(BaseModel):
    """A single CV chunk that scored highly against a query vector."""
    cv_id: int
    cv_name: str = ""
    filename: str = ""
    kind: str = ""
    idx: int = 0
    text: str = ""
    score: float = 0.0


class SemanticSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)


class SemanticSearchResponse(BaseModel):
    query: str
    results: list[SemanticMatch] = Field(default_factory=list)


class IndexRebuildResponse(BaseModel):
    status: str
    cvs_indexed: int = 0
    chunks_indexed: int = 0
    detail: str = ""


# ---------- Match ----------

class MatchRequest(BaseModel):
    """Match a JD against every uploaded CV."""
    job_text: str = Field(..., min_length=1)


class SingleMatchRequest(BaseModel):
    """Match a JD against one specific CV."""
    cv_id: int
    job_text: str = Field(..., min_length=1)


class MatchResult(BaseModel):
    cv_id: int
    cv_name: Optional[str] = None
    filename: str

    overall_score: float
    skill_score: float
    semantic_score: float
    experience_score: float
    education_score: float
    project_score: float

    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    strongest_points: list[str] = Field(default_factory=list)
    improvement_suggestions: list[str] = Field(default_factory=list)
    explanation: str = ""

    # Embedding-driven evidence (empty when neural model isn't available).
    top_semantic_matches: list[SemanticMatch] = Field(default_factory=list)
    semantic_evidence: list[str] = Field(default_factory=list)


class RankedMatchResponse(BaseModel):
    job: JobParsed
    results: list[MatchResult]
    recommended_cv_id: Optional[int] = None


# ---------- Unified user profile ----------

class WeightedSkill(BaseModel):
    """A skill plus the metadata the aggregator computes.

    `weight` blends per-source frequency, a project boost, and a recency
    factor pulled from the user's most recent experience. `count` /
    `sources` / `in_projects` are exposed so the UI can explain why a
    skill ranks where it does.
    """
    name: str
    weight: float = 0.0
    count: int = 1
    sources: list[str] = Field(default_factory=list)
    in_projects: bool = False


class WorkExperienceEntry(BaseModel):
    """One role from the unified profile (deduped, with parsed years)."""
    text: str
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    recency_score: float = 0.0
    sources: list[str] = Field(default_factory=list)


class PortfolioLinks(BaseModel):
    """Mirrors the JSON dict held by `UserProfile.portfolio_links`."""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""
    websites: list[str] = Field(default_factory=list)


class UserProfileBase(BaseModel):
    name: str = ""
    summary: str = ""
    skills: list[WeightedSkill] = Field(default_factory=list)
    tools_and_technologies: list[WeightedSkill] = Field(default_factory=list)
    work_experience: list[WorkExperienceEntry] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    portfolio_links: PortfolioLinks = Field(default_factory=PortfolioLinks)


class UserProfileOut(UserProfileBase):
    id: int
    source_cv_ids: list[int] = Field(default_factory=list)
    source_document_ids: list[int] = Field(default_factory=list)
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BuildProfileRequest(BaseModel):
    """Optional knobs for `POST /api/profile/build` when no files are uploaded."""
    include_cvs: bool = True


class BuildProfileResponse(BaseModel):
    profile: UserProfileOut
    used_cv_ids: list[int] = Field(default_factory=list)
    used_document_ids: list[int] = Field(default_factory=list)
    skipped_uploads: list[dict] = Field(default_factory=list)


# ---------- Smart query / tag intelligence ----------

class PlatformTags(BaseModel):
    linkedin: list[str] = Field(default_factory=list)
    indeed: list[str] = Field(default_factory=list)
    general: list[str] = Field(default_factory=list)


class QueryTagSet(BaseModel):
    roles: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    platform_tags: PlatformTags = Field(default_factory=PlatformTags)


class QueryBuilderResponse(BaseModel):
    queries: list[str] = Field(default_factory=list)
    tags: QueryTagSet = Field(default_factory=QueryTagSet)


# ---------- Job discovery ----------

class JobDiscoveryRequest(BaseModel):
    """Optional overrides for `POST /api/jobs/discover`.

    When `queries` / `tags` are omitted the endpoint derives them from
    the unified profile via `query_builder`.
    """
    queries: Optional[list[str]] = None
    tags: Optional[QueryTagSet] = None
    sources: Optional[list[str]] = None
    max_per_source: int = Field(default=25, ge=1, le=100)
    max_total: int = Field(default=50, ge=1, le=100)


class DiscoveredJob(BaseModel):
    title: str = ""
    company: str = ""
    location: str = ""
    url: str = ""
    snippet: str = ""
    tags: list[str] = Field(default_factory=list)
    source: str = ""
    posted_at: str = ""
    relevance_score: float = 0.0
    matched_terms: list[str] = Field(default_factory=list)


class JobDiscoveryResponse(BaseModel):
    queries_used: list[str] = Field(default_factory=list)
    results: list[DiscoveredJob] = Field(default_factory=list)
    skipped_sources: list[str] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)


# ---------- Multi-job ranking ----------

class RankJobInput(BaseModel):
    """One job in a ranking request.

    `job_text` is the only required field — everything else is metadata
    that's echoed back in the response so the UI can render the ranked
    list without a second lookup.
    """
    job_text: str = Field(..., min_length=1)
    title: str = ""
    company: str = ""
    location: str = ""
    url: str = ""
    source: str = ""


class RankJobsRequest(BaseModel):
    jobs: list[RankJobInput] = Field(..., min_length=1, max_length=200)
    # Optional CV pool filter. None = every CV in the DB.
    cv_ids: Optional[list[int]] = None
    # If True and the CV pool is empty, fall back to a synthetic CV built
    # from the unified UserProfile so ranking still works for users who
    # haven't uploaded individual CVs.
    use_profile_fallback: bool = True
    max_results: int = Field(default=50, ge=1, le=200)


class RankedJobResult(BaseModel):
    """One ranked job with the score breakdown of the **best** matching CV."""
    job: RankJobInput
    best_cv_id: Optional[int] = None
    best_cv_name: str = ""
    best_cv_filename: str = ""
    overall_score: float = 0.0
    skill_score: float = 0.0
    semantic_score: float = 0.0
    experience_score: float = 0.0
    education_score: float = 0.0
    project_score: float = 0.0
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    strongest_points: list[str] = Field(default_factory=list)
    explanation: str = ""


class RankJobsResponse(BaseModel):
    results: list[RankedJobResult] = Field(default_factory=list)
    cv_pool_ids: list[int] = Field(default_factory=list)
    used_profile_fallback: bool = False


# ---------- Tailoring ----------

class BulletRewriteSuggestion(BaseModel):
    original: str
    target_skills: list[str] = Field(default_factory=list)
    rationale: str = ""


class TailoringSuggestion(BaseModel):
    """Categorised tailoring advice for one (CV, JD) pair."""
    skills_to_add: list[str] = Field(default_factory=list)
    skills_to_emphasize: list[str] = Field(default_factory=list)
    keywords_for_ats: list[str] = Field(default_factory=list)
    sections_to_add: list[str] = Field(default_factory=list)
    bullets_to_rewrite: list[BulletRewriteSuggestion] = Field(default_factory=list)
    summary_hint: str = ""
    generic_tips: list[str] = Field(default_factory=list)


class TailorRequest(BaseModel):
    job_text: str = Field(..., min_length=1)
    # Optional CV pool. None = every CV in the DB.
    cv_ids: Optional[list[int]] = None
    use_profile_fallback: bool = True


class TailorResponse(BaseModel):
    best_cv_id: Optional[int] = None
    best_cv_name: str = ""
    best_cv_filename: str = ""
    job: JobParsed
    match: MatchResult
    suggestions: TailoringSuggestion
    used_profile_fallback: bool = False


# ---------- Generation (cover letter / LinkedIn / CV suggestions) ----------

class GenerateRequest(BaseModel):
    """Request body for `POST /api/generate`.

    `kinds` selects which artefacts to produce — any subset of the three
    is allowed, and unknown values are ignored. Pool selection mirrors
    `/api/tailor`: explicit `cv_ids` → all CVs → unified profile.
    """
    job_text: str = Field(..., min_length=1)
    kinds: list[str] = Field(
        default_factory=lambda: ["cv_suggestions", "cover_letter", "linkedin_message"],
    )
    cv_ids: Optional[list[int]] = None
    use_profile_fallback: bool = True
    polish_with_llm: bool = True


## ---------- CV Library + LaTeX renderer ----------

class CVHeader(BaseModel):
    """Personal header line — name + contact bits shown at the top of the CV."""
    name: str = ""
    location: str = ""
    email: str = ""
    phone: str = ""
    website: str = ""
    linkedin: str = ""
    github: str = ""


class SkillGroup(BaseModel):
    """One labelled bucket in the Skills section, e.g. 'Languages: Python, SQL'."""
    label: str
    items: list[str] = Field(default_factory=list)


class EducationEntry(BaseModel):
    institution: str
    degree: str
    period: str = ""
    highlights: list[str] = Field(default_factory=list)
    # Provenance — list of source keys ("cv:1", "document:3", "web:5")
    # that contributed to this entry. Multi-element when several
    # sources mention the same institution+degree and the builder
    # merged them.
    sources: list[str] = Field(default_factory=list)


class ProjectEntry(BaseModel):
    """Reusable project entry. Tags drive JD-fit ranking during tailoring."""
    title: str
    period: str = ""
    highlights: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    # Optional public URL (portfolio page, GitHub repo, demo). When
    # set, the renderer wraps the title in \href and appends UTM
    # tracking params derived from the JD's company so the candidate
    # can see in analytics which application drove the click.
    url: str = ""


class ExperienceEntryLib(BaseModel):
    """Renamed to avoid clashing with the unified-profile `WorkExperienceEntry`."""
    title: str
    company: str = ""
    period: str = ""
    highlights: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class PublicationEntry(BaseModel):
    title: str
    status: str = ""  # e.g. "Under Submission", "Accepted", "Published"
    venue: str = ""
    tags: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class CertificationEntry(BaseModel):
    issuer: str = ""
    name: str
    tags: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class CompetencyEntry(BaseModel):
    """User-curated stretch skill the candidate is willing to claim.

    `rating` is a 1..5 self-assessment:
      5 = expert, ship daily
      4 = strong working knowledge, used in real projects
      3 = comfortable, used in side projects / coursework
      2 = familiar with concepts, light hands-on
      1 = aspirational, learning now

    The tailored renderer injects competencies whose rating is at or
    above the request's threshold AND whose name appears in JD vocab.
    Items rated below the threshold stay hidden so nothing claims
    something the candidate can't defend in an interview.
    """
    name: str
    rating: int = 3
    rationale: str = ""


class CVLibraryBase(BaseModel):
    header: CVHeader = Field(default_factory=CVHeader)
    summary: str = ""
    skills_groups: list[SkillGroup] = Field(default_factory=list)
    core_competencies: list[CompetencyEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    selected_projects: list[ProjectEntry] = Field(default_factory=list)
    additional_projects: list[ProjectEntry] = Field(default_factory=list)
    experience: list[ExperienceEntryLib] = Field(default_factory=list)
    publications: list[PublicationEntry] = Field(default_factory=list)
    certifications: list[CertificationEntry] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    # Project title → personal blog/portfolio URL. Populated from
    # the `## Project Links` section of cv.md. Renderer uses this
    # instead of any GitHub URL the project might carry — user
    # preference is blog-first.
    project_links: dict[str, str] = Field(default_factory=dict)


class CVLibraryOut(CVLibraryBase):
    id: int = 1
    updated_at: datetime
    # When set, library is hand-locked. Auto-rebuilds skip; UI shows
    # a lock badge. force=true on /library/rebuild clears it.
    manually_edited_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class RenderCVRequest(BaseModel):
    """Render a tailored CV.

    `job_text` drives the section selection + bolding. When omitted the
    renderer produces an unfiltered "master" CV using everything in the
    library. `compile_pdf` triggers tectonic if available.
    """
    job_text: str = ""
    compile_pdf: bool = False
    # Page-target picker drives section caps so users don't have to
    # guess raw numbers. "one_page" = junior caps, "two_page" = senior
    # caps, "auto" asks the LLM to pick based on JD seniority + CV
    # density and falls back to "two_page" when LLM is off. The raw
    # max_* fields below still work and OVERRIDE the picker — leave
    # them at their defaults to follow the picker.
    target_length: Literal["auto", "one_page", "one_half_page", "two_page"] = "auto"
    # Caps applied per section after JD-relevance ranking. -1 = "use
    # whatever the planner picks". Explicit non-negative values override.
    max_selected_projects: int = Field(default=-1, ge=-1, le=20)
    max_additional_projects: int = Field(default=-1, ge=-1, le=20)
    max_experience: int = Field(default=-1, ge=-1, le=20)
    # Career-ops style LLM polish: rewrite Summary + reformulate bullets
    # using JD vocabulary, then plug the polished content through the
    # same template. Falls back to the rule-based path on any failure.
    # Requires USE_LLM_EXTRACTION=true + OPENAI_API_KEY.
    use_llm: bool = False
    # Minimum self-rating (1..5) for items in `core_competencies`.
    # Default 1 = treat every stretch skill as fair game (user
    # decision: "assume best on all"). The dropdown was removed from
    # the UI; field kept for API compat.
    min_competency_rating: int = Field(default=1, ge=1, le=5)
    # Auto-boost loop: when keyword coverage < this fraction AND
    # use_llm is on, the renderer asks the LLM to weave missing JD
    # keywords into existing bullets and re-renders. Loop runs up to
    # `max_boost_iterations` times. Set target=0 to disable.
    target_keyword_coverage: float = Field(default=0.80, ge=0.0, le=1.0)
    max_boost_iterations: int = Field(default=3, ge=0, le=5)
    # Enhance mode — when on, polish layer is allowed to stretch:
    # add JD-relevant skills not already in the library, expand
    # project descriptions with plausible details, and lightly rewrite
    # Professional Experience bullets even when the JD wording isn't
    # in the original library. Default OFF to preserve career-ops
    # "never invent" guarantee.
    enhance_tailor: bool = False
    # Manual project pick. When non-empty, the renderer ignores its
    # automatic project ranking/capping and renders exactly these
    # project titles, in this order, as the Selected Projects section.
    # Empty list = automatic selection (default behaviour).
    pinned_project_titles: list[str] = Field(default_factory=list)


class RenderCVResponse(BaseModel):
    latex: str
    pdf_b64: str = ""
    compiled: bool = False
    compile_error: str = ""
    sections_chosen: dict[str, list[str]] = Field(default_factory=dict)
    matched_skills: list[str] = Field(default_factory=list)
    used_llm: bool = False
    llm_skip_reason: str = ""
    # Career-ops parity: report which JD canonical terms made it into the
    # rendered LaTeX and which didn't, plus a 0-1 coverage ratio. Lets
    # the UI show "covered 9/12 keywords" instead of guessing.
    keyword_coverage: float = 0.0
    keywords_covered: list[str] = Field(default_factory=list)
    keywords_missing: list[str] = Field(default_factory=list)
    # Filename composed from candidate + company + date (kebab-case),
    # matching career-ops's `cv-{candidate}-{company}-{YYYY-MM-DD}.pdf`.
    suggested_filename: str = ""
    # What the section planner actually chose, so the UI can show
    # "Auto picked 3 / 2 / 3 — mid-level JD, fits two pages".
    section_plan: dict = Field(default_factory=dict)
    # Parsed JD fields the tracker UI uses to prefill a new row.
    job_title: str = ""
    job_company: str = ""
    # Career-ops Core Competencies row that landed in the LaTeX.
    # Empty when no JD or LLM off (the heuristic intersection is used
    # instead, and you can read what was bolded from `matched_skills`).
    core_competencies: list[str] = Field(default_factory=list)
    # Coverage-boost loop telemetry: how many extra LLM passes were
    # needed to hit `target_keyword_coverage`, and what the booster
    # did each round. Surfaces in the UI as "Boosted coverage from
    # 27% → 83% in 2 rounds".
    coverage_iterations: int = 0
    coverage_history: list[float] = Field(default_factory=list)
    coverage_boost_log: list[str] = Field(default_factory=list)


class GenerateResponse(BaseModel):
    cv_suggestions: str = ""
    cover_letter: str = ""
    linkedin_message: str = ""
    best_cv_id: Optional[int] = None
    best_cv_name: str = ""
    best_cv_filename: str = ""
    job: JobParsed
    match: MatchResult
    used_llm: bool = False
    used_profile_fallback: bool = False


# ---------- Agent orchestrator ----------

class AgentRunRequest(BaseModel):
    """Knobs for the end-to-end pipeline.

    Defaults are tuned for a portfolio demo: small numbers, fast response.

    `queries` / `tags` override the auto-derived values from the profile.
    Either, both, or neither may be provided. Useful for the dashboard's
    "edit before running" flow.
    """
    sources: Optional[list[str]] = None
    max_discover: int = Field(default=30, ge=1, le=100)
    max_rank: int = Field(default=15, ge=1, le=100)
    max_tailor: int = Field(default=5, ge=1, le=20)
    cv_ids: Optional[list[int]] = None
    use_profile_fallback: bool = True
    queries: Optional[list[str]] = None
    tags: Optional[QueryTagSet] = None


class AgentStep(BaseModel):
    """One step in the orchestrator's progress trace."""
    name: str
    status: str  # "ok" | "skipped" | "error"
    detail: str = ""


class AgentRunResponse(BaseModel):
    steps: list[AgentStep] = Field(default_factory=list)
    profile: Optional[UserProfileOut] = None
    queries: list[str] = Field(default_factory=list)
    tags: QueryTagSet = Field(default_factory=QueryTagSet)
    discovered: list[DiscoveredJob] = Field(default_factory=list)
    ranked: list[RankedJobResult] = Field(default_factory=list)
    tailored: list[TailorResponse] = Field(default_factory=list)
    used_profile_fallback: bool = False
    error: str = ""


# ---------- CSV batch ----------

class JobCsvRowSchema(BaseModel):
    """One CSV row after parsing; mirrors `services.job_csv_importer.JobCsvRow`."""
    row_index: int
    job_title: str = ""
    company: str = ""
    location: str = ""
    url: str = ""
    salary: str = ""
    employment_type: str = ""
    description: str = ""
    error: str = ""


class JobCsvImportResponse(BaseModel):
    rows: list[JobCsvRowSchema] = Field(default_factory=list)
    headers: list[str] = Field(default_factory=list)
    truncated: bool = False
    error: str = ""


class BatchMatchRow(BaseModel):
    """One row in the batch-match result table."""
    row_index: int
    job_title: str = ""
    company: str = ""
    location: str = ""
    url: str = ""
    salary: str = ""
    employment_type: str = ""

    best_cv_id: Optional[int] = None
    best_cv_name: str = ""
    best_cv_filename: str = ""
    best_score: float = 0.0
    skill_score: float = 0.0
    semantic_score: float = 0.0

    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    strongest_points: list[str] = Field(default_factory=list)

    error: str = ""


class BatchMatchResponse(BaseModel):
    rows: list[BatchMatchRow] = Field(default_factory=list)
    truncated: bool = False
    rows_processed: int = 0
    rows_skipped: int = 0
    error: str = ""


# ---------- Applications tracker ----------

class ApplicationBase(BaseModel):
    apply_date: str = ""
    deadline: str = ""
    company: str = ""
    role: str = ""
    status: str = "To-Apply"
    how: str = ""
    url: str = ""
    notes: str = ""


class ApplicationCreate(ApplicationBase):
    """Body for POST /api/applications. jd_text optional; backend
    hashes it for dedupe and stores. cv_* fields attach the tailored
    CV snapshot so the row can re-download .tex/.pdf later."""
    jd_text: str = ""
    cv_latex: str = ""
    cv_pdf_b64: str = ""
    cv_filename: str = ""


class ApplicationUpdate(BaseModel):
    """PATCH body — every field optional."""
    apply_date: Optional[str] = None
    deadline: Optional[str] = None
    company: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None
    how: Optional[str] = None
    url: Optional[str] = None
    notes: Optional[str] = None


class ApplicationOut(ApplicationBase):
    id: int
    jd_hash: str = ""
    # Booleans the UI can read without pulling the (potentially large)
    # tailored CV payload over the wire on every list refresh. The
    # actual content is served via /api/applications/{id}/cv.tex|cv.pdf.
    has_cv_latex: bool = False
    has_cv_pdf: bool = False
    has_jd: bool = False
    cv_filename: str = ""
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ApplicationDuplicateMatch(BaseModel):
    """Returned by GET /api/applications/check when a prior application
    fingerprint matches the inbound JD/URL."""
    matched: bool = False
    match_kind: str = ""   # "url" | "jd_hash" | "company_role"
    application: Optional[ApplicationOut] = None


# ---------- Web sources (portfolio URLs, GitHub) ----------

class WebSourceBase(BaseModel):
    url: str
    kind: str = "web"
    title: str = ""
    status: str = "pending"
    error: str = ""


class WebSourceCreate(BaseModel):
    url: str


class WebSourceOut(WebSourceBase):
    id: int
    has_extracted: bool = False
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ---------- Unified source list (CV + Document + WebSource) ----------

class UnifiedSource(BaseModel):
    """Flat row the unified-uploader UI consumes — every CV, Document,
    or WebSource the user has added, normalised to the same shape."""
    id: int
    kind: str          # "cv" | "document" | "web" | "github_user" | "github_repo"
    title: str         # filename for CV/Document, host+path for WebSource
    detail: str        # extra info (e.g. URL, file size hint)
    status: str        # "done" | "pending" | "failed"
    error: str = ""
    created_at: datetime
