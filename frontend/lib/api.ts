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

export { ApiError, API_BASE };
