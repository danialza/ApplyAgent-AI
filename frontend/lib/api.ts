// Thin fetch wrappers around the FastAPI backend.
// Base URL comes from NEXT_PUBLIC_API_URL (see .env.local.example).

import type {
  AgentRunRequest,
  AgentRunResponse,
  BatchMatchResponse,
  CV,
  CVLibrary,
  JobFileResponse,
  JobUrlResponse,
  LLMStatus,
  QueryBuilderResponse,
  RankedMatchResponse,
  RenderCVRequest,
  RenderCVResponse,
} from "./types";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:8000";

class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // Non-JSON error body — keep the default message.
    }
    throw new ApiError(detail, res.status);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export async function uploadCVs(files: File[]): Promise<CV[]> {
  const formData = new FormData();
  for (const f of files) formData.append("files", f);
  const res = await fetch(`${API_BASE}/api/cvs/upload`, {
    method: "POST",
    body: formData,
  });
  return handle<CV[]>(res);
}

export async function listCVs(): Promise<CV[]> {
  const res = await fetch(`${API_BASE}/api/cvs`, { cache: "no-store" });
  return handle<CV[]>(res);
}

export async function deleteCV(cvId: number): Promise<void> {
  const res = await fetch(`${API_BASE}/api/cvs/${cvId}`, { method: "DELETE" });
  return handle<void>(res);
}

export async function matchAll(jobText: string): Promise<RankedMatchResponse> {
  const res = await fetch(`${API_BASE}/api/match`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_text: jobText }),
  });
  return handle<RankedMatchResponse>(res);
}

export async function parseJobFromUrl(url: string): Promise<JobUrlResponse> {
  const res = await fetch(`${API_BASE}/api/jobs/from-url`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  return handle<JobUrlResponse>(res);
}

export async function matchFromUrl(url: string): Promise<RankedMatchResponse> {
  const res = await fetch(`${API_BASE}/api/match/from-url`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  return handle<RankedMatchResponse>(res);
}

export async function parseJobFromFile(file: File): Promise<JobFileResponse> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${API_BASE}/api/jobs/from-file`, {
    method: "POST",
    body: formData,
  });
  return handle<JobFileResponse>(res);
}

export async function matchFromFile(file: File): Promise<RankedMatchResponse> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${API_BASE}/api/match/from-file`, {
    method: "POST",
    body: formData,
  });
  return handle<RankedMatchResponse>(res);
}

export async function batchMatchCsv(file: File): Promise<BatchMatchResponse> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${API_BASE}/api/match/batch-csv`, {
    method: "POST",
    body: formData,
  });
  return handle<BatchMatchResponse>(res);
}

export async function fetchProfileQueries(): Promise<QueryBuilderResponse> {
  const res = await fetch(`${API_BASE}/api/profile/queries`, { cache: "no-store" });
  return handle<QueryBuilderResponse>(res);
}

export async function runAgent(body: AgentRunRequest = {}): Promise<AgentRunResponse> {
  const res = await fetch(`${API_BASE}/api/agent/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handle<AgentRunResponse>(res);
}

// ---------- CV library + tailored renderer ----------

export async function fetchCVLibrary(): Promise<CVLibrary | null> {
  const res = await fetch(`${API_BASE}/api/cv/library`, { cache: "no-store" });
  if (res.status === 404) return null;
  return handle<CVLibrary>(res);
}

export async function buildLibraryFromCV(cvId: number): Promise<CVLibrary> {
  const res = await fetch(`${API_BASE}/api/cv/library/from-cv/${cvId}`, {
    method: "POST",
  });
  return handle<CVLibrary>(res);
}

/** Aggregate every uploaded CV + Document into one merged library. */
export async function rebuildLibraryFromAll(): Promise<CVLibrary> {
  const res = await fetch(`${API_BASE}/api/cv/library/rebuild`, {
    method: "POST",
  });
  return handle<CVLibrary>(res);
}

/** Fetch the canonical CV markdown template for one-click download. */
export async function fetchCVTemplate(): Promise<{ filename: string; content: string }> {
  const res = await fetch(`${API_BASE}/api/cv/template`, { cache: "no-store" });
  return handle<{ filename: string; content: string }>(res);
}

/** Drop the singleton library row (e.g. after a bad PDF auto-seed). */
export async function deleteCVLibrary(): Promise<void> {
  const res = await fetch(`${API_BASE}/api/cv/library`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) {
    throw new ApiError(`Delete library failed (HTTP ${res.status})`, res.status);
  }
}

/** Upload a markdown CV (career-ops style cv.md) → replace library. */
export async function uploadMarkdownCV(file: File): Promise<CVLibrary> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${API_BASE}/api/cv/library/from-markdown`, {
    method: "POST",
    body: formData,
  });
  return handle<CVLibrary>(res);
}

/** Convert raw CV text → cv.md via the connected LLM. */
export async function convertCVTextToMarkdown(
  text: string
): Promise<{ markdown: string; filename: string }> {
  const res = await fetch(`${API_BASE}/api/cv/convert-to-markdown`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  return handle<{ markdown: string; filename: string }>(res);
}

/** Quick LLM connectivity check. */
export async function fetchLLMStatus(): Promise<LLMStatus> {
  const res = await fetch(`${API_BASE}/api/cv/llm-status`, { cache: "no-store" });
  return handle<LLMStatus>(res);
}

/** Switch the live LLM provider without restarting the backend.
 *  Persists only for the lifetime of this backend process. */
export async function setLLMProvider(
  provider: "claude_code" | "anthropic" | "openai"
): Promise<LLMStatus> {
  const res = await fetch(`${API_BASE}/api/cv/llm-provider`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider }),
  });
  return handle<LLMStatus>(res);
}

export async function setLLMModel(model: string): Promise<LLMStatus> {
  const res = await fetch(`${API_BASE}/api/cv/llm-model`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  });
  return handle<LLMStatus>(res);
}

export async function fetchKnownModels(): Promise<Record<string, string[]>> {
  const res = await fetch(`${API_BASE}/api/cv/llm-models`, { cache: "no-store" });
  return handle<Record<string, string[]>>(res);
}

export async function putCVLibrary(payload: Omit<CVLibrary, "id" | "updated_at">): Promise<CVLibrary> {
  const res = await fetch(`${API_BASE}/api/cv/library`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return handle<CVLibrary>(res);
}

export async function renderCV(body: RenderCVRequest): Promise<RenderCVResponse> {
  const res = await fetch(`${API_BASE}/api/cv/render`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handle<RenderCVResponse>(res);
}

// ---------- Applications tracker ----------

import type { Application, ApplicationCreate, ApplicationUpdate, DuplicateCheck } from "./types";

export async function listApplications(): Promise<Application[]> {
  const res = await fetch(`${API_BASE}/api/applications`, { cache: "no-store" });
  return handle<Application[]>(res);
}

export async function createApplication(body: ApplicationCreate): Promise<Application> {
  const res = await fetch(`${API_BASE}/api/applications`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handle<Application>(res);
}

export async function updateApplication(
  id: number,
  patch: ApplicationUpdate
): Promise<Application> {
  const res = await fetch(`${API_BASE}/api/applications/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  return handle<Application>(res);
}

export async function deleteApplication(id: number): Promise<void> {
  const res = await fetch(`${API_BASE}/api/applications/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) {
    throw new ApiError(`Delete failed (HTTP ${res.status})`, res.status);
  }
}

export async function checkDuplicateApplication(args: {
  url?: string;
  jd_text?: string;
  company?: string;
  role?: string;
}): Promise<DuplicateCheck> {
  const qs = new URLSearchParams();
  if (args.url) qs.set("url", args.url);
  if (args.jd_text) qs.set("jd_text", args.jd_text);
  if (args.company) qs.set("company", args.company);
  if (args.role) qs.set("role", args.role);
  const res = await fetch(`${API_BASE}/api/applications/check?${qs.toString()}`, {
    cache: "no-store",
  });
  return handle<DuplicateCheck>(res);
}

export function applicationsCsvUrl(): string {
  return `${API_BASE}/api/applications/export.csv`;
}

export function applicationCvTexUrl(id: number): string {
  return `${API_BASE}/api/applications/${id}/cv.tex`;
}

export function applicationCvPdfUrl(id: number): string {
  return `${API_BASE}/api/applications/${id}/cv.pdf`;
}

export function applicationJdTxtUrl(id: number): string {
  return `${API_BASE}/api/applications/${id}/jd.txt`;
}

export interface LibraryIssue {
  severity: "error" | "warning" | "info";
  scope: string;
  title: string;
  detail: string;
  fix_hint: string;
  fix_action?: {
    kind: string;
    payload: Record<string, any>;
    preview: string;
  } | null;
  fingerprint: string;
}

export async function fetchLibraryIssues(): Promise<{
  issues: LibraryIssue[];
  counts: { error?: number; warning?: number; info?: number; total: number };
  llm_used: boolean;
}> {
  const res = await fetch(`${API_BASE}/api/cv/library/issues`, { cache: "no-store" });
  return handle(res);
}

export async function applyLibraryFix(action: {
  kind: string;
  payload: Record<string, any>;
}): Promise<any> {
  const res = await fetch(`${API_BASE}/api/cv/library/apply-fix`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(action),
  });
  return handle(res);
}

export async function unlockLibrary(): Promise<any> {
  const res = await fetch(`${API_BASE}/api/cv/library/unlock`, { method: "POST" });
  return handle(res);
}

export async function rebuildLibraryForce(): Promise<any> {
  const res = await fetch(`${API_BASE}/api/cv/library/rebuild?force=true`, { method: "POST" });
  return handle(res);
}

export async function ignoreIssue(fp: string): Promise<any> {
  const res = await fetch(`${API_BASE}/api/cv/library/issues/ignore`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fingerprint: fp }),
  });
  return handle(res);
}

export async function unignoreAllIssues(): Promise<any> {
  const res = await fetch(`${API_BASE}/api/cv/library/issues/ignore`, {
    method: "DELETE",
  });
  return handle(res);
}

// ---------- Unified sources ----------

import type { UnifiedSource, WebSourceOut } from "./types";

export async function listAllSources(): Promise<UnifiedSource[]> {
  const res = await fetch(`${API_BASE}/api/sources`, { cache: "no-store" });
  return handle<UnifiedSource[]>(res);
}

export async function addUrlSource(url: string): Promise<WebSourceOut> {
  const res = await fetch(`${API_BASE}/api/sources/url`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  return handle<WebSourceOut>(res);
}

export async function refreshUrlSource(id: number): Promise<WebSourceOut> {
  const res = await fetch(`${API_BASE}/api/sources/url/${id}/refresh`, { method: "POST" });
  return handle<WebSourceOut>(res);
}

export async function fetchSourceBreakdown(): Promise<import("./types").SourceBreakdown> {
  const res = await fetch(`${API_BASE}/api/sources/breakdown`, { cache: "no-store" });
  return handle<import("./types").SourceBreakdown>(res);
}

export async function deleteUrlSource(id: number): Promise<void> {
  const res = await fetch(`${API_BASE}/api/sources/url/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) {
    throw new ApiError(`Delete failed (HTTP ${res.status})`, res.status);
  }
}

export async function addNotesSource(text: string, title?: string): Promise<UnifiedSource> {
  const res = await fetch(`${API_BASE}/api/sources/notes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, title: title || undefined }),
  });
  return handle<UnifiedSource>(res);
}

export async function deleteNotesSource(id: number): Promise<void> {
  const res = await fetch(`${API_BASE}/api/sources/notes/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) {
    throw new ApiError(`Delete failed (HTTP ${res.status})`, res.status);
  }
}

export { ApiError, API_BASE };
