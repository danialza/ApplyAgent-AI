import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';

import {
  adoptRunPage,
  applyControl,
  challengeReason,
  clickAction,
  collectActions,
  collectControls,
  extractPageText,
  focusRun,
  openRun,
} from './browser.mjs';
import {
  addEvent,
  answerQuestions as saveQuestionAnswers,
  createRun,
  forgetMemory,
  getRun,
  getSettings,
  listMemory,
  listQuestions,
  listRuns,
  memoryMap,
  remember,
  replacePendingQuestions,
  runDetails,
  saveSettings,
  updateRun,
} from './db.mjs';
import { checkLlm, estimateMarketSalary, mapUnknownFields } from './llm.mjs';
import {
  applySalaryPolicy,
  displaySalary,
  fallbackMarketSalary,
  isExpectedAnnualSalaryControl,
  postedSalaryHigh,
  salaryValueForControl,
} from './salary.mjs';

const projectRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const outputsRoot = join(projectRoot, 'outputs');
mkdirSync(outputsRoot, { recursive: true });

const subscribers = new Map();
const processing = new Set();
const salaryEstimates = new Map();
let queue = Promise.resolve();
let cvOptionsCache = null;

const stageProgress = {
  queued: 2,
  opening: 8,
  reading_job: 18,
  tailoring_cv: 34,
  entering_application: 58,
  filling_form: 72,
  needs_input: 76,
  needs_user_action: 76,
  ready_for_review: 100,
  failed: 100,
  cancelled: 100,
};

function broadcast(runId, payload) {
  for (const response of subscribers.get(runId) || []) {
    response.write(`data: ${JSON.stringify(payload)}\n\n`);
  }
}

function emit(runId, stage, state, message, detail = '', patch = {}) {
  const progress = patch.progress ?? stageProgress[stage] ?? getRun(runId)?.progress ?? 0;
  const run = updateRun(runId, { stage, progress, ...patch });
  const event = addEvent(runId, stage, state, message, detail);
  broadcast(runId, { type: 'event', event, run });
  return run;
}

async function requestJson(url, options = {}, timeoutMs = 120_000) {
  const response = await fetch(url, { ...options, signal: AbortSignal.timeout(timeoutMs) });
  const text = await response.text();
  let body = null;
  try { body = text ? JSON.parse(text) : {}; } catch { body = { detail: text.slice(0, 800) }; }
  if (!response.ok) throw new Error(body?.detail || `${response.status} ${response.statusText}`);
  return body;
}

function parseBoolean(value, fallback = true) {
  if (value === undefined || value === null || value === '') return fallback;
  return ['1', 'true', 'yes', 'on'].includes(String(value).trim().toLowerCase());
}

function coverageTarget(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return 0.95;
  return Math.max(0, Math.min(parsed, 1));
}

const normalise = (value) => String(value || '')
  .toLowerCase()
  .replace(/[^a-z0-9]+/g, '_')
  .replace(/^_+|_+$/g, '')
  .slice(0, 120);

function splitName(name) {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
  return {
    first_name: parts[0] || '',
    last_name: parts.slice(1).join(' '),
  };
}

async function loadCandidate(settings) {
  const base = settings.cv_api_base.replace(/\/$/, '');
  const [library, sharedFacts] = await Promise.all([
    requestJson(`${base}/api/cv/library`, {}, 20_000),
    requestJson(`${base}/api/facts`, {}, 20_000).catch(() => []),
  ]);
  const header = library.header || {};
  const candidate = {
    full_name: header.name || '',
    ...splitName(header.name),
    email: header.email || '',
    phone: header.phone || '',
    location: header.location || '',
    linkedin: header.linkedin || '',
    github: header.github || '',
    website: header.website || '',
  };
  const remembered = memoryMap();
  for (const fact of sharedFacts || []) {
    if (fact.key && fact.answer && !(fact.key in remembered)) {
      remember(normalise(fact.key), fact.question || fact.key, fact.answer, 'shared_cv_service');
      remembered[normalise(fact.key)] = fact.answer;
    }
  }
  return { library, candidate, known: { ...candidate, ...remembered } };
}

function keyForControl(control) {
  const source = control.name || control.label || control.id;
  return `field_${normalise(source)}`;
}

function candidateValue(control, known, cvPath) {
  const label = `${control.label} ${control.name}`.toLowerCase();
  if (control.value && !['checkbox', 'radio', 'file'].includes(control.type)) return { action: 'skip', value: '' };
  if (control.type === 'file' && /resume|résumé|curriculum|\bcv\b/.test(label)) return { action: 'upload', value: cvPath };
  if (/first.?name|given.?name/.test(label) && known.first_name) return { action: 'fill', value: known.first_name };
  if (/last.?name|family.?name|surname/.test(label) && known.last_name) return { action: 'fill', value: known.last_name };
  if (/full.?name|your.?name|candidate.?name/.test(label) && known.full_name) return { action: 'fill', value: known.full_name };
  if (/e.?mail/.test(label) && known.email) return { action: 'fill', value: known.email };
  if (/phone|mobile|telephone/.test(label) && known.phone) return { action: 'fill', value: known.phone };
  if (/linkedin/.test(label) && known.linkedin) return { action: 'fill', value: known.linkedin };
  if (/github/.test(label) && known.github) return { action: 'fill', value: known.github };
  if (/portfolio|personal.?website|website|homepage/.test(label) && known.website) return { action: 'fill', value: known.website };
  if (/current.?location|city.?country|location/.test(label) && known.location) return { action: 'fill', value: known.location };

  const exactKeys = [keyForControl(control), normalise(control.name), normalise(control.label)];
  for (const key of exactKeys) if (known[key]) return { action: 'fill', value: known[key], sourceKey: key };
  return null;
}

const optionalSkip = (control) => !control.required && /gender|race|ethnicity|veteran|disability|pronoun|marketing|talent community/.test(
  `${control.label} ${control.name}`.toLowerCase(),
);

function publicRun(run) {
  if (!run) return null;
  const { jd_text: _jdText, cv_path: _cvPath, ...safe } = run;
  return { ...safe, has_jd: Boolean(run.jd_text), has_cv: Boolean(run.cv_path) };
}

function findApplyAction(actions) {
  return actions.find((action) => !action.disabled && /^(apply|apply now|start application|continue to application)$/i.test(action.text.trim()));
}

function findFinalAction(actions) {
  return actions.find((action) => !action.disabled && /submit( application)?|send application|complete application|finish application/i.test(action.text));
}

function findNextAction(actions) {
  return actions.find((action) => !action.disabled && /^(next|continue|save and continue|review|review application)$/i.test(action.text.trim()));
}

async function streamCvProgress(base, progressId, runId) {
  try {
    const response = await fetch(`${base}/api/cv/render/progress/${progressId}`);
    if (!response.ok || !response.body) return;
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        if (!line.startsWith('data:')) continue;
        try {
          const data = JSON.parse(line.slice(5).trim());
          if (data.label) emit(runId, 'tailoring_cv', 'running', data.label, '', { progress: Math.min(54, 34 + Math.round((data.progress || 0) * 0.2)) });
        } catch {
          // Ignore malformed progress frames; the render response remains authoritative.
        }
      }
    }
  } catch {
    // Progress is advisory; render failure is handled by the main request.
  }
}

async function renderCv(runId, jdText, settings) {
  const base = settings.cv_api_base.replace(/\/$/, '');
  const useLlm = parseBoolean(settings.cv_use_llm);
  const compilePdf = parseBoolean(settings.cv_compile_pdf);
  const enhanceTailor = useLlm && parseBoolean(settings.cv_enhance_tailor);

  if (useLlm) {
    await requestJson(`${base}/api/cv/llm-config`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        provider: settings.cv_llm_provider || 'anthropic',
        model: settings.cv_llm_model || 'claude-sonnet-5',
      }),
    }, 20_000);
  }

  const progressId = `applypilot-${runId}`;
  const progressTask = streamCvProgress(base, progressId, runId);
  const result = await requestJson(`${base}/api/cv/render`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      job_text: jdText,
      compile_pdf: compilePdf,
      use_llm: useLlm,
      enhance_tailor: enhanceTailor,
      target_length: settings.cv_length || 'auto',
      target_keyword_coverage: coverageTarget(settings.cv_coverage_target),
      progress_id: progressId,
    }),
  }, 1_200_000);
  await Promise.race([progressTask, new Promise((resolve) => setTimeout(resolve, 1000))]);
  if (!compilePdf) {
    throw new Error('The CV source was generated, but an application needs a PDF upload. Turn on Compile PDF and retry.');
  }
  if (!result.compiled || !result.pdf_b64) throw new Error(result.compile_error || 'CV PDF compilation failed.');
  const runDir = join(outputsRoot, runId);
  mkdirSync(runDir, { recursive: true });
  const filename = `${String(result.suggested_filename || 'tailored-cv').replace(/\.pdf$/i, '')}.pdf`;
  const cvPath = join(runDir, filename);
  writeFileSync(cvPath, Buffer.from(result.pdf_b64, 'base64'));
  return { result, filename, cvPath };
}

async function trackApplication(run, result, settings) {
  if (run.url.includes('/fixture/')) return 0;
  const base = settings.cv_api_base.replace(/\/$/, '');
  const tracked = await requestJson(`${base}/api/applications`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      company: run.company || result.job_company || '',
      role: run.role || result.job_title || '',
      status: 'To-Apply',
      how: 'ApplyPilot',
      url: run.url,
      notes: 'Application form prepared by ApplyPilot; awaiting human review and submission.',
      jd_text: run.jd_text,
      cv_latex: result.latex || '',
      cv_pdf_b64: result.pdf_b64 || '',
      cv_filename: String(result.suggested_filename || 'tailored-cv').replace(/\.pdf$/i, ''),
      keyword_coverage: result.keyword_coverage ?? -1,
    }),
  }, 30_000);
  return Number(tracked.id || 0);
}

async function pauseForUserAction(runId, page, reason) {
  emit(runId, 'needs_user_action', 'waiting', reason, '', {
    status: 'needs_user_action',
    browser_url: page.url(),
  });
}

async function expectedSalaryForRun(run, settings) {
  const cached = salaryEstimates.get(run.id);
  if (cached) return cached;

  const posted = postedSalaryHigh(run.jd_text);
  if (posted) {
    const result = applySalaryPolicy(posted, 'posted');
    salaryEstimates.set(run.id, result);
    return result;
  }

  try {
    const estimated = await estimateMarketSalary({
      jobTitle: run.role,
      jobText: run.jd_text,
      settings,
    });
    if (estimated.postedHighSalary >= 10_000 && estimated.postedHighSalary <= 2_000_000) {
      const result = applySalaryPolicy({ amount: estimated.postedHighSalary, currency: estimated.currency }, 'posted');
      salaryEstimates.set(run.id, result);
      return result;
    }
    if (estimated.marketAnnualSalary >= 10_000 && estimated.marketAnnualSalary <= 2_000_000) {
      const result = applySalaryPolicy({ amount: estimated.marketAnnualSalary, currency: estimated.currency }, 'market');
      salaryEstimates.set(run.id, result);
      return result;
    }
  } catch {
    // The deterministic role/location fallback below keeps salary fields automatic.
  }

  const result = applySalaryPolicy(fallbackMarketSalary({ jobTitle: run.role, jobText: run.jd_text }), 'market');
  salaryEstimates.set(run.id, result);
  return result;
}

async function fillCurrentPage(runId, page, cvPath, known, settings, run) {
  const controls = await collectControls(page);
  const editable = controls.filter((control) => !['hidden', 'submit', 'button', 'reset'].includes(control.type));
  const unresolved = [];
  const needsSalary = editable.some((control) => !control.value && isExpectedAnnualSalaryControl(control));
  const salary = needsSalary ? await expectedSalaryForRun(run, settings) : null;
  let salaryAnnounced = false;

  for (const control of editable) {
    if (salary && !control.value && isExpectedAnnualSalaryControl(control)) {
      try {
        await applyControl(page, control, 'fill', salaryValueForControl(control, salary.amount));
        if (!salaryAnnounced) {
          const basis = salary.source === 'posted' ? '5% below the posted maximum' : '6% below the role estimate';
          emit(runId, 'filling_form', 'running', `Expected salary: ${displaySalary(salary)}`, basis);
          salaryAnnounced = true;
        }
        continue;
      } catch {
        unresolved.push(control);
        continue;
      }
    }
    const decision = candidateValue(control, known, cvPath);
    if (decision?.value || decision?.action === 'skip') {
      if (decision.action !== 'skip') {
        try { await applyControl(page, control, decision.action, decision.value); }
        catch { unresolved.push(control); }
      }
    } else if (optionalSkip(control)) {
      continue;
    } else {
      unresolved.push(control);
    }
  }

  if (!unresolved.length) return { questions: [], controls: editable };

  let llmDecisions = [];
  try {
    llmDecisions = await mapUnknownFields({ controls: unresolved, knownKeys: Object.keys(known), settings });
  } catch {
    // Safe fallback: ask rather than guess.
  }
  const decisionById = new Map(llmDecisions.map((item) => [item.control_id, item]));
  const questions = [];
  const seen = new Set();

  for (const control of unresolved) {
    const decision = decisionById.get(control.id);
    const sourceKey = normalise(decision?.source_key || '');
    if (decision?.action === 'known' && sourceKey && known[sourceKey]) {
      try {
        await applyControl(page, control, 'fill', known[sourceKey]);
        continue;
      } catch {
        // Fall through to a user question.
      }
    }
    if (decision?.action === 'skip' && !control.required) continue;
    const memoryKey = keyForControl(control);
    if (seen.has(memoryKey)) continue;
    seen.add(memoryKey);
    questions.push({
      id: randomUUID(),
      memoryKey,
      question: decision?.question || control.label || `What should I enter for ${control.name || 'this required field'}?`,
      inputType: control.tag === 'select' ? 'select' : ['checkbox', 'radio'].includes(control.type) ? 'boolean' : 'text',
      options: control.options || [],
    });
  }
  return { questions, controls: editable };
}

async function processRun(runId) {
  if (processing.has(runId)) return;
  processing.add(runId);
  try {
    let run = getRun(runId);
    if (!run || ['cancelled', 'ready_for_review'].includes(run.status)) return;
    const settings = getSettings();
    emit(runId, 'opening', 'running', 'Opening the application in visible Chrome', '', { status: 'running', error: '' });
    let page = await openRun(runId, run.browser_url || run.url);
    updateRun(runId, { browser_url: page.url() });

    const challenge = await challengeReason(page);
    if (challenge) return pauseForUserAction(runId, page, challenge);

    run = getRun(runId);
    if (!run.jd_text) {
      emit(runId, 'reading_job', 'running', 'Reading the job description');
      const jdText = await extractPageText(page);
      if (jdText.trim().length < 250) {
        return pauseForUserAction(runId, page, 'The job description is not visible yet. Open it or sign in, then resume.');
      }
      const parsed = await requestJson(`${settings.cv_api_base.replace(/\/$/, '')}/api/jobs/parse`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ text: jdText }),
      }, 180_000);
      run = updateRun(runId, { jd_text: jdText, company: parsed.company || '', role: parsed.job_title || '' });
      emit(runId, 'reading_job', 'done', `Found ${run.role || 'the role'}${run.company ? ` at ${run.company}` : ''}`);
    }

    run = getRun(runId);
    if (!run.cv_path) {
      emit(runId, 'tailoring_cv', 'running', 'Tailoring the CV to this role');
      const { result, filename, cvPath } = await renderCv(runId, run.jd_text, settings);
      run = updateRun(runId, {
        cv_filename: filename,
        cv_path: cvPath,
        company: run.company || result.job_company || '',
        role: run.role || result.job_title || '',
      });
      const applicationId = await trackApplication(run, result, settings);
      updateRun(runId, { application_id: applicationId });
      emit(runId, 'tailoring_cv', 'done', `Tailored CV ready${result.keyword_coverage >= 0 ? ` · ${Math.round(result.keyword_coverage * 100)}% keyword coverage` : ''}`);
    }

    emit(runId, 'entering_application', 'running', 'Finding the application form');
    for (let step = 0; step < 8; step += 1) {
      const latest = getRun(runId);
      if (!latest || latest.status === 'cancelled') return;
      const currentChallenge = await challengeReason(page);
      if (currentChallenge) return pauseForUserAction(runId, page, currentChallenge);

      const controls = await collectControls(page);
      const formControls = controls.filter((control) => !['hidden', 'search'].includes(control.type));
      if (!formControls.length) {
        const actions = await collectActions(page);
        const finalAction = findFinalAction(actions);
        if (finalAction) {
          emit(runId, 'ready_for_review', 'done', 'Application is ready for your review', `Final action: ${finalAction.text}`, {
            status: 'ready_for_review',
            browser_url: page.url(),
          });
          await page.bringToFront();
          return;
        }
        const applyAction = findApplyAction(actions);
        if (applyAction) {
          emit(runId, 'entering_application', 'running', `Opening “${applyAction.text}”`);
          page = await clickAction(page, applyAction.id);
          adoptRunPage(runId, page);
          updateRun(runId, { browser_url: page.url() });
          continue;
        }
        return pauseForUserAction(runId, page, 'I cannot find the application form. Navigate to the form in Chrome, then resume.');
      }

      emit(runId, 'filling_form', 'running', `Filling application page ${step + 1}`, '', { progress: Math.min(92, 72 + step * 3) });
      const { known } = await loadCandidate(settings);
      const filled = await fillCurrentPage(runId, page, getRun(runId).cv_path, known, settings, latest);
      if (filled.questions.length) {
        replacePendingQuestions(runId, filled.questions);
        emit(runId, 'needs_input', 'waiting', `I need ${filled.questions.length} answer${filled.questions.length === 1 ? '' : 's'} before continuing`, '', {
          status: 'needs_input',
          browser_url: page.url(),
        });
        broadcast(runId, { type: 'questions', questions: listQuestions(runId, 'pending') });
        return;
      }
      // Any previously pending question is now satisfied from memory.
      replacePendingQuestions(runId, []);

      const actions = await collectActions(page);
      const finalAction = findFinalAction(actions);
      if (finalAction) {
        emit(runId, 'ready_for_review', 'done', 'Application is filled and ready for your review', `Final action: ${finalAction.text}`, {
          status: 'ready_for_review',
          browser_url: page.url(),
        });
        await page.bringToFront();
        return;
      }
      const nextAction = findNextAction(actions);
      if (nextAction) {
        emit(runId, 'filling_form', 'running', `Continuing with “${nextAction.text}”`);
        page = await clickAction(page, nextAction.id);
        adoptRunPage(runId, page);
        updateRun(runId, { browser_url: page.url() });
        continue;
      }

      emit(runId, 'ready_for_review', 'done', 'Form fields are filled; review the visible browser before submitting', '', {
        status: 'ready_for_review',
        browser_url: page.url(),
      });
      await page.bringToFront();
      return;
    }
    return pauseForUserAction(runId, page, 'The application has more steps than expected. Review the current page, then resume.');
  } catch (error) {
    emit(runId, 'failed', 'error', 'The application run stopped', String(error?.message || error), { status: 'failed', error: String(error?.message || error).slice(0, 1000) });
  } finally {
    processing.delete(runId);
  }
}

function enqueue(runId) {
  queue = queue.then(() => processRun(runId)).catch(() => {});
  return queue;
}

export function startRun(url) {
  let parsed;
  try { parsed = new URL(url); } catch { throw new Error('Enter a valid application URL.'); }
  if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('Application URL must use http or https.');
  const id = randomUUID();
  const run = createRun(id, parsed.toString());
  emit(id, 'queued', 'waiting', 'Application queued');
  void enqueue(id);
  return publicRun(run);
}

export async function answerAndResume(runId, answers) {
  const run = getRun(runId);
  if (!run) throw new Error('Application run not found.');
  const questions = listQuestions(runId, 'pending');
  const byId = new Map(questions.map((question) => [question.id, question]));
  const clean = (answers || []).filter((item) => byId.has(item.id) && String(item.answer || '').trim());
  const answeredIds = new Set(clean.map((item) => item.id));
  if (!questions.length || questions.some((question) => !answeredIds.has(question.id))) {
    throw new Error('Answer every required question before continuing.');
  }
  for (const item of clean) {
    const question = byId.get(item.id);
    const answer = String(item.answer).trim();
    remember(question.memory_key, question.question, answer, 'user');
    await requestJson(`${getSettings().cv_api_base.replace(/\/$/, '')}/api/facts`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ key: question.memory_key, question: question.question, answer }),
    }, 20_000).catch(() => null);
  }
  saveQuestionAnswers(runId, clean);
  emit(runId, 'filling_form', 'running', 'Answers saved to memory; resuming the form', '', { status: 'running' });
  void enqueue(runId);
  return publicRun(getRun(runId));
}

export function resumeRun(runId) {
  const run = getRun(runId);
  if (!run) throw new Error('Application run not found.');
  emit(runId, run.stage || 'opening', 'running', 'Resuming in the visible browser', '', { status: 'running', error: '' });
  void enqueue(runId);
  return publicRun(getRun(runId));
}

export function cancelRun(runId) {
  if (!getRun(runId)) throw new Error('Application run not found.');
  emit(runId, 'cancelled', 'done', 'Application run cancelled', '', { status: 'cancelled' });
  return publicRun(getRun(runId));
}

export async function focusBrowser(runId) {
  const run = getRun(runId);
  if (!run) throw new Error('Application run not found.');
  return focusRun(runId, run.browser_url || run.url);
}

export function subscribe(runId, response) {
  if (!subscribers.has(runId)) subscribers.set(runId, new Set());
  subscribers.get(runId).add(response);
  response.write(`data: ${JSON.stringify({ type: 'snapshot', run: publicRun(getRun(runId)), events: runDetails(runId)?.events || [] })}\n\n`);
  return () => {
    subscribers.get(runId)?.delete(response);
    if (!subscribers.get(runId)?.size) subscribers.delete(runId);
  };
}

export function getPublicRun(id) {
  const detail = runDetails(id);
  if (!detail) return null;
  const { jd_text: _jdText, cv_path: _cvPath, ...safe } = detail;
  return { ...safe, has_jd: Boolean(detail.jd_text), has_cv: Boolean(detail.cv_path) };
}

export function getPublicRuns() {
  return listRuns().map(publicRun);
}

export function getMemory() { return listMemory(); }
export async function deleteMemory(key) {
  const deleted = forgetMemory(key);
  const base = getSettings().cv_api_base.replace(/\/$/, '');
  const response = await fetch(`${base}/api/facts/${encodeURIComponent(key)}`, {
    method: 'DELETE',
    signal: AbortSignal.timeout(20_000),
  }).catch(() => null);
  return deleted || response?.ok || false;
}
export function settings() { return getSettings(); }
export function updateSettings(patch) {
  cvOptionsCache = null;
  return saveSettings(patch);
}
export function llmStatus() { return checkLlm(getSettings()); }
export async function cvOptions() {
  const now = Date.now();
  if (cvOptionsCache && now - cvOptionsCache.at < 60_000) return cvOptionsCache.value;
  const base = getSettings().cv_api_base.replace(/\/$/, '');
  const [models, status] = await Promise.all([
    requestJson(`${base}/api/cv/llm-models`, {}, 20_000),
    requestJson(`${base}/api/cv/llm-status`, {}, 30_000),
  ]);
  const value = { models, status };
  cvOptionsCache = { at: now, value };
  return value;
}
export function readCv(runId) {
  const run = getRun(runId);
  if (!run?.cv_path) return null;
  return { filename: run.cv_filename, bytes: readFileSync(run.cv_path) };
}
