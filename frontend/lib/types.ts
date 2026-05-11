// Mirrors backend pydantic schemas. Keep in sync with backend/app/models/schemas.py.

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
}

export interface ProjectEntry {
  title: string;
  period: string;
  highlights: string[];
  tags: string[];
}

export interface ExperienceEntryLib {
  title: string;
  company: string;
  period: string;
  highlights: string[];
  tags: string[];
}

export interface PublicationEntry {
  title: string;
  status: string;
  venue: string;
  tags: string[];
}

export interface CertificationEntry {
  issuer: string;
  name: string;
  tags: string[];
}

export interface CVLibrary {
  id: number;
  header: CVHeader;
  summary: string;
  skills_groups: SkillGroup[];
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
}

export interface LLMStatus {
  enabled: boolean;
  configured: boolean;
  reachable: boolean;
  model: string;
  base_url: string;
  error: string;
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
