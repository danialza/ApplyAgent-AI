// Mirrors backend pydantic schemas. Keep in sync with backend/app/models/schemas.py.

export interface Application {
  id: number;
  apply_date: string;   // ISO "YYYY-MM-DD" or ""
  deadline: string;
  company: string;
  role: string;
  status: string;       // "To-Apply" | "Applied" | "Interview" | "Offer" | "Rejected" | "Skipped" | custom
  how: string;          // "form" | "indeed" | "linkedin" | "referral" | custom
  url: string;
  notes: string;
  jd_hash: string;
  /** Keyword coverage of the tailored CV at track time (0..1). -1 = not recorded. */
  keyword_coverage: number;
  has_cv_latex: boolean;
  has_cv_pdf: boolean;
  has_jd: boolean;
  cv_filename: string;
  created_at: string;
  updated_at: string;
}

export type ApplicationCreate = Omit<
  Application,
  "id" | "created_at" | "updated_at" | "jd_hash" | "has_cv_latex" | "has_cv_pdf" | "has_jd"
> & {
  jd_text?: string;
  cv_latex?: string;
  cv_pdf_b64?: string;
};

export type ApplicationUpdate = Partial<
  Omit<Application, "id" | "created_at" | "updated_at" | "jd_hash">
>;

// ---------- Unified sources ----------

export interface UnifiedSource {
  id: number;
  kind: "cv" | "document" | "web" | "github_user" | "github_repo";
  title: string;
  detail: string;
  status: "pending" | "done" | "failed";
  error?: string;
  created_at: string;
}

export interface WebSourceOut {
  id: number;
  url: string;
  kind: string;
  title: string;
  status: string;
  error: string;
  has_extracted: boolean;
  created_at: string;
  updated_at: string;
}

export interface DuplicateCheck {
  matched: boolean;
  match_kind: string;   // "" | "url" | "jd_hash" | "company_role"
  application: Application | null;
}


export interface CV {
  id: number;
  filename: string;
  name: string;
  summary: string;
  skills: string[];
  education: string[];
  experience: string[];
  projects: string[];
  certifications: string[];
  languages: string[];
  email: string;
  phone: string;
  linkedin: string;
  github: string;
  portfolio: string;
  raw_text: string;
  created_at: string;
}

export interface JobParsed {
  job_title: string;
  company: string;
  location: string;
  salary: string;
  employment_type: string;
  remote_type: string;
  required_skills: string[];
  preferred_skills: string[];
  responsibilities: string[];
  qualifications: string[];
  experience_level: string;
  education_requirements: string[];
  technologies: string[];
  soft_skills: string[];
  raw_text: string;
}

export interface SemanticMatch {
  cv_id: number;
  cv_name: string;
  filename: string;
  kind: string;
  idx: number;
  text: string;
  score: number;
}

export interface MatchResult {
  cv_id: number;
  cv_name: string | null;
  filename: string;
  overall_score: number;
  skill_score: number;
  semantic_score: number;
  experience_score: number;
  education_score: number;
  project_score: number;
  matched_skills: string[];
  missing_skills: string[];
  strongest_points: string[];
  improvement_suggestions: string[];
  explanation: string;
  top_semantic_matches: SemanticMatch[];
  semantic_evidence: string[];
}

export interface BatchMatchRow {
  row_index: number;
  job_title: string;
  company: string;
  location: string;
  url: string;
  salary: string;
  employment_type: string;
  best_cv_id: number | null;
  best_cv_name: string;
  best_cv_filename: string;
  best_score: number;
  skill_score: number;
  semantic_score: number;
  matched_skills: string[];
  missing_skills: string[];
  strongest_points: string[];
  error: string;
}

export interface BatchMatchResponse {
  rows: BatchMatchRow[];
  truncated: boolean;
  rows_processed: number;
  rows_skipped: number;
  error: string;
}

export interface JobUrlResponse {
  url: string;
  success: boolean;
  extracted_text: string;
  parsed_job: JobParsed | null;
  error: string;
  notes: string[];
}

export interface JobFileResponse {
  filename: string;
  success: boolean;
  extracted_text: string;
  parsed_job: JobParsed | null;
  error: string;
}

export interface RankedMatchResponse {
  job: JobParsed;
  results: MatchResult[];
  recommended_cv_id: number | null;
}

// ---------- Agent orchestrator ----------

export interface AgentStep {
  name: string;
  status: "ok" | "skipped" | "error";
  detail: string;
}

export interface QueryTagSet {
  roles: string[];
  skills: string[];
  tools: string[];
  domains: string[];
  platform_tags: {
    linkedin: string[];
    indeed: string[];
    general: string[];
  };
}

export interface DiscoveredJob {
  title: string;
  company: string;
  location: string;
  url: string;
  snippet: string;
  tags: string[];
  source: string;
  posted_at: string;
  relevance_score: number;
  matched_terms: string[];
}

export interface RankJobInput {
  job_text: string;
  title: string;
  company: string;
  location: string;
  url: string;
  source: string;
}

export interface RankedJobResult {
  job: RankJobInput;
  best_cv_id: number | null;
  best_cv_name: string;
  best_cv_filename: string;
  overall_score: number;
  skill_score: number;
  semantic_score: number;
  experience_score: number;
  education_score: number;
  project_score: number;
  matched_skills: string[];
  missing_skills: string[];
  strongest_points: string[];
  explanation: string;
}

export interface BulletRewriteSuggestion {
  original: string;
  target_skills: string[];
  rationale: string;
}

export interface TailoringSuggestion {
  skills_to_add: string[];
  skills_to_emphasize: string[];
  keywords_for_ats: string[];
  sections_to_add: string[];
  bullets_to_rewrite: BulletRewriteSuggestion[];
  summary_hint: string;
  generic_tips: string[];
}

export interface TailorResponse {
  best_cv_id: number | null;
  best_cv_name: string;
  best_cv_filename: string;
  job: JobParsed;
  match: MatchResult;
  suggestions: TailoringSuggestion;
  used_profile_fallback: boolean;
}

export interface UserProfileOut {
  id: number;
  name: string;
  summary: string;
  // The shape mirrors WeightedSkill / WorkExperienceEntry but the dashboard
  // only renders top names — keep them loose for forward compatibility.
  skills: { name: string; weight: number }[];
  tools_and_technologies: { name: string; weight: number }[];
  domains: string[];
  languages: string[];
  source_cv_ids: number[];
  source_document_ids: number[];
  updated_at: string;
}

export interface AgentRunRequest {
  sources?: string[];
  max_discover?: number;
  max_rank?: number;
  max_tailor?: number;
  cv_ids?: number[];
  use_profile_fallback?: boolean;
  /** User-supplied search queries — overrides the profile-derived ones. */
  queries?: string[];
  /** User-supplied tag overrides for ranking + platform-tag fan-out. */
  tags?: QueryTagSet;
}

export interface QueryBuilderResponse {
  queries: string[];
  tags: QueryTagSet;
}

// ---------- Tailored LaTeX CV renderer ----------

export interface CVHeader {
  name: string;
  location: string;
  email: string;
  phone: string;
  website: string;
  linkedin: string;
  github: string;
}

export interface SkillGroup {
  label: string;
  items: string[];
}

export interface EducationEntryLib {
  institution: string;
  degree: string;
  period: string;
  highlights: string[];
  sources?: string[];
}

export interface ProjectEntry {
  title: string;
  period: string;
  highlights: string[];
  tags: string[];
  sources?: string[];
  /** Optional public URL — portfolio page, GitHub repo, demo.
   *  Tailored renders auto-append UTM tracking when set. */
  url?: string;
}

export interface ExperienceEntryLib {
  title: string;
  company: string;
  period: string;
  highlights: string[];
  tags: string[];
  sources?: string[];
}

export interface PublicationEntry {
  title: string;
  status: string;
  venue: string;
  tags: string[];
  sources?: string[];
}

export interface CertificationEntry {
  issuer: string;
  name: string;
  tags: string[];
  sources?: string[];
}

export interface SourceBreakdown {
  by_source: Record<string, {
    education: number;
    selected_projects: number;
    additional_projects: number;
    experience: number;
    publications: number;
    certifications: number;
  }>;
}

export interface CompetencyEntry {
  name: string;
  rating: number; // 1..5
  rationale: string;
}

export interface CVLibrary {
  id: number;
  manually_edited_at?: string | null;
  header: CVHeader;
  summary: string;
  skills_groups: SkillGroup[];
  core_competencies: CompetencyEntry[];
  education: EducationEntryLib[];
  selected_projects: ProjectEntry[];
  additional_projects: ProjectEntry[];
  experience: ExperienceEntryLib[];
  publications: PublicationEntry[];
  certifications: CertificationEntry[];
  languages: string[];
  updated_at: string;
}

export interface RenderCVRequest {
  job_text?: string;
  compile_pdf?: boolean;
  max_selected_projects?: number;
  max_additional_projects?: number;
  max_experience?: number;
  use_llm?: boolean;
  /** 1..5 — items in `core_competencies` below this rating stay hidden. */
  min_competency_rating?: number;
  /** Page-target picker. "auto" asks the LLM to choose caps. */
  target_length?: "auto" | "one_page" | "one_half_page" | "two_page";
  /** 0..1. If coverage < target, LLM rewrites bullets to weave in missing keywords. */
  target_keyword_coverage?: number;
  /** Max boost rounds. 0 disables. */
  max_boost_iterations?: number;
  /** Enhance mode — allow LLM to add JD-relevant skills, expand
   *  project bullets, and rewrite experience bullets more freely. */
  enhance_tailor?: boolean;
  /** Manual project pick. When non-empty, restricts the projects
   *  section to these titles. */
  pinned_project_titles?: string[];
  /** How the pick is used. true (default) = LLM-rank + cap within the
   *  pick; false = force all picks in tick order. */
  pinned_rank?: boolean;
  /** When false, skips the 2-page shrink loop so the PDF can run long.
   *  Used by the master-CV export. Default true. */
  enforce_page_cap?: boolean;
  /** Client-generated id to receive live stage events over SSE
   *  (GET /api/cv/render/progress/{id}). */
  progress_id?: string;
}

export interface SectionPlanOut {
  max_selected_projects: number;
  max_additional_projects: number;
  max_experience: number;
  source: string; // "user_override" | "one_page" | "two_page" | "llm" | "llm_fallback"
  rationale: string;
}

export interface LLMStatus {
  enabled: boolean;
  configured: boolean;
  reachable: boolean;
  provider: string;
  model: string;
  base_url: string;
  error: string;
  /** Providers with a usable API key — UI offers only these. */
  available_providers?: string[];
}

export interface RenderCVResponse {
  latex: string;
  pdf_b64: string;
  compiled: boolean;
  compile_error: string;
  sections_chosen: Record<string, string[]>;
  matched_skills: string[];
  used_llm: boolean;
  llm_skip_reason: string;
  keyword_coverage: number;
  keywords_covered: string[];
  keywords_missing: string[];
  suggested_filename: string;
  section_plan?: SectionPlanOut;
  job_title?: string;
  job_company?: string;
  core_competencies?: string[];
  coverage_iterations?: number;
  coverage_history?: number[];
  coverage_boost_log?: string[];
}

export interface AgentRunResponse {
  steps: AgentStep[];
  profile: UserProfileOut | null;
  queries: string[];
  tags: QueryTagSet;
  discovered: DiscoveredJob[];
  ranked: RankedJobResult[];
  tailored: TailorResponse[];
  used_profile_fallback: boolean;
  error: string;
}
