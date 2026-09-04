export const AGENT_BASE = 'http://127.0.0.1:8500';

export type RunStatus =
  | 'queued'
  | 'running'
  | 'needs_input'
  | 'needs_user_action'
  | 'ready_for_review'
  | 'failed'
  | 'cancelled';

export interface RunEvent {
  id: number;
  run_id: string;
  stage: string;
  state: 'waiting' | 'running' | 'done' | 'error';
  message: string;
  detail: string;
  created_at: string;
}

export interface RunQuestion {
  id: string;
  run_id: string;
  memory_key: string;
  question: string;
  input_type: 'text' | 'select' | 'boolean';
  options: { value: string; label: string }[];
  answer: string;
  status: 'pending' | 'answered';
}

export interface ApplicationRun {
  id: string;
  url: string;
  status: RunStatus;
  stage: string;
  progress: number;
  company: string;
  role: string;
  cv_filename: string;
  application_id: number;
  browser_url: string;
  error: string;
  has_jd: boolean;
  has_cv: boolean;
  created_at: string;
  updated_at: string;
  events?: RunEvent[];
  questions?: RunQuestion[];
}

export interface AgentSettings {
  cv_api_base: string;
  llm_mode: 'claude_subscription' | 'anthropic_api';
  llm_model: string;
  cv_length: 'auto' | 'one_page' | 'one_half_page' | 'two_page';
  browser_channel: string;
  stop_before_submit: string;
  auto_continue: string;
}

export interface MemoryFact {
  memory_key: string;
  question: string;
  answer: string;
  source: string;
  updated_at: string;
}

export interface AgentHealth {
  ok: boolean;
  agent: boolean;
  cv_service: boolean;
  llm: { mode: string; configured: boolean; model: string; fallback?: string };
}

async function handle<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => ({})) as Record<string, unknown>;
  if (!response.ok) {
    const message = body.error || body.detail;
    throw new Error(typeof message === 'string' ? message : `${response.status} ${response.statusText}`);
  }
  return body as T;
}

export async function fetchHealth(): Promise<AgentHealth> {
  return handle(await fetch(`${AGENT_BASE}/api/health`, { cache: 'no-store' }));
}

export async function fetchRuns(): Promise<ApplicationRun[]> {
  return handle(await fetch(`${AGENT_BASE}/api/runs`, { cache: 'no-store' }));
}

export async function fetchRun(id: string): Promise<ApplicationRun> {
  return handle(await fetch(`${AGENT_BASE}/api/runs/${id}`, { cache: 'no-store' }));
}

export async function createRun(url: string): Promise<ApplicationRun> {
  return handle(await fetch(`${AGENT_BASE}/api/runs`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ url }),
  }));
}

export async function answerQuestions(runId: string, answers: { id: string; answer: string }[]): Promise<ApplicationRun> {
  return handle(await fetch(`${AGENT_BASE}/api/runs/${runId}/answers`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ answers }),
  }));
}

export async function runAction(runId: string, action: 'resume' | 'cancel' | 'focus'): Promise<unknown> {
  return handle(await fetch(`${AGENT_BASE}/api/runs/${runId}/${action}`, { method: 'POST' }));
}

export async function fetchSettings(): Promise<AgentSettings> {
  return handle(await fetch(`${AGENT_BASE}/api/settings`, { cache: 'no-store' }));
}

export async function putSettings(settings: Partial<AgentSettings>): Promise<AgentSettings> {
  return handle(await fetch(`${AGENT_BASE}/api/settings`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(settings),
  }));
}

export async function fetchMemory(): Promise<MemoryFact[]> {
  return handle(await fetch(`${AGENT_BASE}/api/memory`, { cache: 'no-store' }));
}

export async function forgetMemory(key: string): Promise<void> {
  await handle(await fetch(`${AGENT_BASE}/api/memory/${encodeURIComponent(key)}`, { method: 'DELETE' }));
}

export function cvUrl(runId: string): string {
  return `${AGENT_BASE}/api/runs/${runId}/cv`;
}
