import cors from 'cors';
import express from 'express';

import { closeBrowser } from './browser.mjs';
import { closeDatabase } from './db.mjs';
import {
  answerAndResume,
  cancelRun,
  cvOptions,
  deleteMemory,
  focusBrowser,
  getMemory,
  getPublicRun,
  getPublicRuns,
  llmStatus,
  readCv,
  resumeRun,
  settings,
  startRun,
  subscribe,
  updateSettings,
} from './service.mjs';

const app = express();
const port = Number(process.env.AGENT_PORT || 8500);
const host = '127.0.0.1';

app.use(cors({ origin: [/^http:\/\/(?:localhost|127\.0\.0\.1):3500$/] }));
app.use(express.json({ limit: '1mb' }));

const asyncRoute = (handler) => async (request, response, next) => {
  try { await handler(request, response); } catch (error) { next(error); }
};

app.get('/api/health', asyncRoute(async (_request, response) => {
  const current = settings();
  let cvService = false;
  try {
    const check = await fetch(`${current.cv_api_base.replace(/\/$/, '')}/api/health`, { signal: AbortSignal.timeout(5000) });
    cvService = check.ok;
  } catch {
    cvService = false;
  }
  const llm = await llmStatus();
  response.json({ ok: cvService && llm.configured, agent: true, cv_service: cvService, llm });
}));

app.get('/api/settings', (_request, response) => response.json(settings()));
app.put('/api/settings', (request, response) => response.json(updateSettings(request.body || {})));
app.get('/api/cv-options', asyncRoute(async (_request, response) => response.json(await cvOptions())));

app.get('/api/runs', (_request, response) => response.json(getPublicRuns()));
app.post('/api/runs', (request, response) => {
  const run = startRun(String(request.body?.url || '').trim());
  response.status(201).json(run);
});
app.get('/api/runs/:id', (request, response) => {
  const run = getPublicRun(request.params.id);
  if (!run) return response.status(404).json({ error: 'Application run not found.' });
  response.json(run);
});
app.get('/api/runs/:id/events', (request, response) => {
  response.setHeader('Content-Type', 'text/event-stream');
  response.setHeader('Cache-Control', 'no-cache, no-transform');
  response.setHeader('Connection', 'keep-alive');
  response.flushHeaders();
  const unsubscribe = subscribe(request.params.id, response);
  const heartbeat = setInterval(() => response.write(': keepalive\n\n'), 15_000);
  request.on('close', () => { clearInterval(heartbeat); unsubscribe(); });
});
app.post('/api/runs/:id/answers', asyncRoute(async (request, response) => {
  response.json(await answerAndResume(request.params.id, request.body?.answers || []));
}));
app.post('/api/runs/:id/resume', (request, response) => response.json(resumeRun(request.params.id)));
app.post('/api/runs/:id/cancel', (request, response) => response.json(cancelRun(request.params.id)));
app.post('/api/runs/:id/focus', asyncRoute(async (request, response) => {
  response.json({ url: await focusBrowser(request.params.id) });
}));
app.get('/api/runs/:id/cv', (request, response) => {
  const cv = readCv(request.params.id);
  if (!cv) return response.status(404).json({ error: 'CV is not ready.' });
  response.setHeader('Content-Type', 'application/pdf');
  response.setHeader('Content-Disposition', `inline; filename="${cv.filename.replace(/["\r\n]/g, '')}"`);
  response.send(cv.bytes);
});

app.get('/api/memory', (_request, response) => response.json(getMemory()));
app.delete('/api/memory/:key', asyncRoute(async (request, response) => {
  response.json({ deleted: await deleteMemory(request.params.key) });
}));

// A local-only application fixture used by the smoke test. It never creates
// an Applications-tracker record, but exercises the real visible browser,
// CV upload, question memory, multi-step navigation, and submit lock.
app.get('/fixture/application', (_request, response) => {
  response.type('html').send(`<!doctype html>
  <html><head><meta charset="utf-8"><title>ApplyPilot test application</title>
  <style>body{font:16px system-ui;max-width:780px;margin:40px auto;padding:0 24px;color:#172033}label{display:block;margin:16px 0 6px}input,select{box-sizing:border-box;width:100%;padding:10px;border:1px solid #aab3c2;border-radius:6px}button{margin-top:20px;padding:11px 18px}.hidden{display:none}</style></head>
  <body><article><h1>Senior Platform Engineer</h1><p>Example Systems · London</p><p>We are looking for a platform engineer to improve reliability, observability, Terraform automation and incident response across cloud services.</p></article>
  <form id="application">
    <section id="fields">
      <label for="first">First name *</label><input id="first" name="first_name" required>
      <label for="last">Last name *</label><input id="last" name="last_name" required>
      <label for="email">Email *</label><input id="email" name="email" type="email" required>
      <label for="linkedin">LinkedIn profile</label><input id="linkedin" name="linkedin">
      <label for="resume">Resume / CV *</label><input id="resume" name="resume" type="file" required>
      <label for="salary">Expected annual salary *</label><input id="salary" name="expected_salary" required>
      <label for="sponsor">Will you require visa sponsorship? *</label><select id="sponsor" name="visa_sponsorship" required><option value="">Choose</option><option>No</option><option>Yes</option></select>
      <button type="button" id="next">Next</button>
    </section>
    <section id="review" class="hidden"><h2>Review application</h2><p>Your form is ready.</p><button type="submit">Submit application</button></section>
  </form>
  <script>document.querySelector('#next').addEventListener('click',()=>{document.querySelector('#fields').className='hidden';document.querySelector('#review').className='';});document.querySelector('form').addEventListener('submit',event=>event.preventDefault());</script>
  </body></html>`);
});

app.use((error, _request, response, _next) => {
  console.error(error);
  response.status(400).json({ error: String(error?.message || error) });
});

const server = app.listen(port, host, () => {
  console.log(`ApplyPilot agent listening at http://${host}:${port}`);
  console.log(`CV service: ${settings().cv_api_base}`);
  console.log(`LLM mode: ${settings().llm_mode} (${settings().llm_model})`);
});

async function shutdown() {
  server.close();
  await closeBrowser();
  closeDatabase();
  process.exit(0);
}

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
