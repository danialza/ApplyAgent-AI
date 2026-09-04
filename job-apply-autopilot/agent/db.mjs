import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { fileURLToPath } from 'node:url';

const projectRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const dataDir = process.env.APPLYPILOT_DATA_DIR || join(projectRoot, '.data');
mkdirSync(dataDir, { recursive: true });

export const databasePath = join(dataDir, 'applypilot.sqlite');
const sqlite = new DatabaseSync(databasePath);
sqlite.exec('PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON; PRAGMA busy_timeout = 5000;');
sqlite.exec(`
  CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT NOT NULL DEFAULT 'queued',
    progress INTEGER NOT NULL DEFAULT 0,
    company TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    jd_text TEXT NOT NULL DEFAULT '',
    cv_filename TEXT NOT NULL DEFAULT '',
    cv_path TEXT NOT NULL DEFAULT '',
    application_id INTEGER NOT NULL DEFAULT 0,
    browser_url TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
  );

  CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    state TEXT NOT NULL,
    message TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
  );

  CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    memory_key TEXT NOT NULL,
    question TEXT NOT NULL,
    input_type TEXT NOT NULL DEFAULT 'text',
    options_json TEXT NOT NULL DEFAULT '[]',
    answer TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
  );

  CREATE TABLE IF NOT EXISTS memory (
    memory_key TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user',
    updated_at TEXT NOT NULL
  );

  CREATE TABLE IF NOT EXISTS settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
  );

  CREATE INDEX IF NOT EXISTS idx_events_run_id ON events(run_id, id);
  CREATE INDEX IF NOT EXISTS idx_questions_run_status ON questions(run_id, status);
  CREATE INDEX IF NOT EXISTS idx_runs_updated_at ON runs(updated_at DESC);
  PRAGMA optimize;
`);

const now = () => new Date().toISOString();
const parseOptions = (value) => {
  try {
    const parsed = JSON.parse(value || '[]');
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
};

const runColumns = [
  'id', 'url', 'status', 'stage', 'progress', 'company', 'role', 'jd_text',
  'cv_filename', 'cv_path', 'application_id', 'browser_url', 'error',
  'created_at', 'updated_at',
];

export function createRun(id, url) {
  const timestamp = now();
  sqlite.prepare(`
    INSERT INTO runs (id, url, created_at, updated_at)
    VALUES (?, ?, ?, ?)
  `).run(id, url, timestamp, timestamp);
  return getRun(id);
}

export function getRun(id) {
  return sqlite.prepare(`SELECT ${runColumns.join(', ')} FROM runs WHERE id = ?`).get(id) || null;
}

export function listRuns(limit = 30) {
  return sqlite.prepare(`
    SELECT ${runColumns.join(', ')} FROM runs
    ORDER BY updated_at DESC LIMIT ?
  `).all(Math.max(1, Math.min(Number(limit) || 30, 100)));
}

export function updateRun(id, patch) {
  const allowed = new Set(runColumns.filter((key) => !['id', 'created_at', 'updated_at'].includes(key)));
  const entries = Object.entries(patch).filter(([key]) => allowed.has(key));
  if (!entries.length) return getRun(id);
  const assignments = entries.map(([key]) => `${key} = ?`);
  sqlite.prepare(`UPDATE runs SET ${assignments.join(', ')}, updated_at = ? WHERE id = ?`)
    .run(...entries.map(([, value]) => value ?? ''), now(), id);
  return getRun(id);
}

export function addEvent(runId, stage, state, message, detail = '') {
  const result = sqlite.prepare(`
    INSERT INTO events (run_id, stage, state, message, detail, created_at)
    VALUES (?, ?, ?, ?, ?, ?)
  `).run(runId, stage, state, message, detail, now());
  return sqlite.prepare('SELECT * FROM events WHERE id = ?').get(result.lastInsertRowid);
}

export function listEvents(runId) {
  return sqlite.prepare('SELECT * FROM events WHERE run_id = ? ORDER BY id').all(runId);
}

export function replacePendingQuestions(runId, questions) {
  sqlite.prepare("DELETE FROM questions WHERE run_id = ? AND status = 'pending'").run(runId);
  const insert = sqlite.prepare(`
    INSERT INTO questions
      (id, run_id, memory_key, question, input_type, options_json, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
  `);
  for (const question of questions) {
    insert.run(
      question.id,
      runId,
      question.memoryKey,
      question.question,
      question.inputType || 'text',
      JSON.stringify(question.options || []),
      now(),
    );
  }
}

export function listQuestions(runId, status) {
  const rows = status
    ? sqlite.prepare('SELECT * FROM questions WHERE run_id = ? AND status = ? ORDER BY created_at').all(runId, status)
    : sqlite.prepare('SELECT * FROM questions WHERE run_id = ? ORDER BY created_at').all(runId);
  return rows.map((row) => ({ ...row, options: parseOptions(row.options_json) }));
}

export function answerQuestions(runId, answers) {
  const update = sqlite.prepare(`
    UPDATE questions SET answer = ?, status = 'answered'
    WHERE id = ? AND run_id = ?
  `);
  for (const item of answers) update.run(String(item.answer || '').trim(), item.id, runId);
}

export function remember(memoryKey, question, answer, source = 'user') {
  sqlite.prepare(`
    INSERT INTO memory (memory_key, question, answer, source, updated_at)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(memory_key) DO UPDATE SET
      question = excluded.question,
      answer = excluded.answer,
      source = excluded.source,
      updated_at = excluded.updated_at
  `).run(memoryKey, question, answer, source, now());
}

export function forgetMemory(memoryKey) {
  return sqlite.prepare('DELETE FROM memory WHERE memory_key = ?').run(memoryKey).changes > 0;
}

export function listMemory() {
  return sqlite.prepare('SELECT * FROM memory ORDER BY updated_at DESC').all();
}

export function memoryMap() {
  return Object.fromEntries(listMemory().filter((row) => row.answer).map((row) => [row.memory_key, row.answer]));
}

const defaultSettings = {
  cv_api_base: process.env.CV_API_BASE || 'http://127.0.0.1:8400',
  llm_mode: process.env.LLM_MODE || 'claude_subscription',
  llm_model: process.env.LLM_MODEL || 'sonnet',
  cv_length: process.env.CV_LENGTH || 'auto',
  cv_compile_pdf: process.env.CV_COMPILE_PDF || 'true',
  cv_use_llm: process.env.CV_USE_LLM || 'true',
  cv_enhance_tailor: process.env.CV_ENHANCE_TAILOR || 'true',
  cv_coverage_target: process.env.CV_COVERAGE_TARGET || '0.95',
  cv_llm_provider: process.env.CV_LLM_PROVIDER || 'anthropic',
  cv_llm_model: process.env.CV_LLM_MODEL || 'claude-sonnet-5',
  cv_pinned_projects: process.env.CV_PINNED_PROJECTS || '[]',
  cv_pinned_rank: process.env.CV_PINNED_RANK || 'true',
  browser_channel: 'chrome',
  stop_before_submit: 'true',
  auto_continue: 'true',
};

export function getSettings() {
  const stored = Object.fromEntries(
    sqlite.prepare('SELECT setting_key, setting_value FROM settings').all()
      .map((row) => [row.setting_key, row.setting_value]),
  );
  return { ...defaultSettings, ...stored, stop_before_submit: 'true' };
}

export function saveSettings(patch) {
  const allowed = new Set([
    'cv_api_base', 'llm_mode', 'llm_model', 'cv_length', 'cv_compile_pdf',
    'cv_use_llm', 'cv_enhance_tailor', 'cv_coverage_target',
    'cv_llm_provider', 'cv_llm_model', 'cv_pinned_projects', 'cv_pinned_rank',
    'browser_channel', 'auto_continue',
  ]);
  const upsert = sqlite.prepare(`
    INSERT INTO settings (setting_key, setting_value, updated_at)
    VALUES (?, ?, ?)
    ON CONFLICT(setting_key) DO UPDATE SET setting_value = excluded.setting_value, updated_at = excluded.updated_at
  `);
  for (const [key, value] of Object.entries(patch || {})) {
    if (allowed.has(key)) upsert.run(key, String(value), now());
  }
  return getSettings();
}

export function runDetails(id) {
  const run = getRun(id);
  if (!run) return null;
  return { ...run, events: listEvents(id), questions: listQuestions(id) };
}

export function closeDatabase() {
  sqlite.close();
}
