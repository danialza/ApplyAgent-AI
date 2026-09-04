import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { chromium } from 'playwright';

const projectRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const profilePath = process.env.APPLYPILOT_BROWSER_PROFILE || join(projectRoot, '.data', 'chrome-profile');
mkdirSync(profilePath, { recursive: true });

let contextPromise = null;
const pagesByRun = new Map();

async function context() {
  if (!contextPromise) {
    contextPromise = chromium.launchPersistentContext(profilePath, {
      channel: 'chrome',
      headless: false,
      viewport: null,
      args: ['--start-maximized', '--disable-blink-features=AutomationControlled'],
      acceptDownloads: true,
    }).catch((error) => {
      contextPromise = null;
      throw error;
    });
  }
  return contextPromise;
}

export async function pageForRun(runId, url = '') {
  const current = pagesByRun.get(runId);
  if (current && !current.isClosed()) return current;
  const browser = await context();
  const page = await browser.newPage();
  pagesByRun.set(runId, page);
  page.on('close', () => pagesByRun.delete(runId));
  if (url) await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90_000 });
  return page;
}

export async function openRun(runId, url) {
  const page = await pageForRun(runId);
  if (page.url() !== url) await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90_000 });
  await page.bringToFront();
  return page;
}

export async function focusRun(runId, fallbackUrl = '') {
  const page = await pageForRun(runId, fallbackUrl);
  await page.bringToFront();
  return page.url();
}

export function adoptRunPage(runId, page) {
  if (!page || page.isClosed()) return;
  pagesByRun.set(runId, page);
  page.on('close', () => {
    if (pagesByRun.get(runId) === page) pagesByRun.delete(runId);
  });
}

export async function extractPageText(page) {
  await page.waitForTimeout(600);
  const text = await page.locator('body').innerText({ timeout: 15_000 }).catch(() => '');
  return String(text || '').split(String.fromCharCode(0)).join('').slice(0, 120_000);
}

export async function challengeReason(page) {
  const text = (await extractPageText(page)).toLowerCase().slice(0, 12_000);
  const captcha = await page.locator(
    'iframe[src*="captcha" i], iframe[title*="captcha" i], [class*="captcha" i], [id*="captcha" i]',
  ).count().catch(() => 0);
  if (captcha || /verify you are human|security check|complete the captcha|checking your browser/.test(text)) {
    return 'Complete the CAPTCHA or security check in Chrome, then resume the agent.';
  }
  const password = await page.locator('input[type="password"]:visible').count().catch(() => 0);
  if (password || /sign in to continue|log in to continue/.test(text)) {
    return 'Sign in in the visible Chrome window, then resume the agent.';
  }
  return '';
}

export async function collectControls(page) {
  const controls = [];
  for (const [frameIndex, frame] of page.frames().entries()) {
    const frameControls = await frame.locator('input, textarea, select').evaluateAll(
      (elements, index) => elements.map((element, controlIndex) => {
        const input = /** @type {HTMLInputElement|HTMLTextAreaElement|HTMLSelectElement} */ (element);
        const style = window.getComputedStyle(input);
        const rect = input.getBoundingClientRect();
        const type = (input.getAttribute('type') || input.tagName.toLowerCase()).toLowerCase();
        const visible = type === 'file' || (
          style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0
        );
        if (!visible || input.disabled) return null;
        const id = `ap-${index}-${controlIndex}`;
        input.setAttribute('data-applypilot-id', id);
        const explicit = input.id ? document.querySelector(`label[for="${CSS.escape(input.id)}"]`) : null;
        const wrapping = input.closest('label');
        const fieldset = input.closest('fieldset');
        const label = [
          explicit?.textContent,
          wrapping?.textContent,
          input.getAttribute('aria-label'),
          input.getAttribute('placeholder'),
          input.getAttribute('name'),
          fieldset?.querySelector('legend')?.textContent,
        ].find((value) => value && value.trim()) || '';
        const options = input instanceof HTMLSelectElement
          ? Array.from(input.options).filter((option) => option.value).map((option) => ({ value: option.value, label: option.textContent?.trim() || option.value }))
          : [];
        return {
          id,
          frameIndex: index,
          tag: input.tagName.toLowerCase(),
          type,
          label: label.replace(/\s+/g, ' ').trim().slice(0, 500),
          name: input.getAttribute('name') || '',
          required: input.hasAttribute('required') || input.getAttribute('aria-required') === 'true',
          value: 'value' in input ? String(input.value || '') : '',
          checked: 'checked' in input ? Boolean(input.checked) : false,
          options,
        };
      }).filter(Boolean),
      frameIndex,
    ).catch(() => []);
    controls.push(...frameControls);
  }
  return controls;
}

export async function collectActions(page) {
  return page.locator('button, input[type="submit"], input[type="button"], a').evaluateAll((elements) =>
    elements.map((element, index) => {
      const html = /** @type {HTMLElement} */ (element);
      const style = window.getComputedStyle(html);
      const rect = html.getBoundingClientRect();
      if (style.display === 'none' || style.visibility === 'hidden' || rect.width <= 0 || rect.height <= 0) return null;
      const id = `ap-action-${index}`;
      html.setAttribute('data-applypilot-action', id);
      return {
        id,
        text: (html.innerText || html.getAttribute('value') || html.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim().slice(0, 250),
        href: html instanceof HTMLAnchorElement ? html.href : '',
        disabled: html.hasAttribute('disabled') || html.getAttribute('aria-disabled') === 'true',
      };
    }).filter(Boolean),
  ).catch(() => []);
}

export async function applyControl(page, control, action, value = '') {
  const frame = page.frames()[control.frameIndex];
  if (!frame) throw new Error(`Frame ${control.frameIndex} is no longer available.`);
  const locator = frame.locator(`[data-applypilot-id="${control.id}"]`).first();
  if (action === 'upload') {
    await locator.setInputFiles(value);
    return;
  }
  if (action === 'check') {
    await locator.check({ force: true });
    return;
  }
  if (control.tag === 'select') {
    const wanted = String(value).toLowerCase();
    const option = (control.options || []).find((item) =>
      item.value.toLowerCase() === wanted || item.label.toLowerCase() === wanted || item.label.toLowerCase().includes(wanted),
    );
    if (!option) throw new Error(`No matching option for ${control.label}.`);
    await locator.selectOption(option.value);
    return;
  }
  if (control.type === 'radio') {
    const wanted = String(value).trim().toLowerCase();
    const ownValue = String(control.value || '').trim().toLowerCase();
    const ownLabel = String(control.label || '').trim().toLowerCase();
    if (wanted && (ownValue === wanted || ownLabel === wanted || ownLabel.startsWith(`${wanted} `))) {
      await locator.check({ force: true });
    }
    return;
  }
  if (control.type === 'checkbox') {
    const yes = /^(true|yes|y|1|agree|accepted)$/i.test(String(value));
    if (yes) await locator.check({ force: true });
    return;
  }
  await locator.fill(String(value));
}

export async function clickAction(page, actionId) {
  const locator = page.locator(`[data-applypilot-action="${actionId}"]`).first();
  const before = page.url();
  const popupPromise = page.context().waitForEvent('page', { timeout: 4000 }).catch(() => null);
  await locator.click();
  const popup = await popupPromise;
  if (popup) {
    await popup.waitForLoadState('domcontentloaded', { timeout: 60_000 }).catch(() => {});
    return popup;
  }
  await page.waitForLoadState('domcontentloaded', { timeout: 20_000 }).catch(() => {});
  if (page.url() === before) await page.waitForTimeout(800);
  return page;
}

export async function closeBrowser() {
  if (!contextPromise) return;
  const browser = await contextPromise.catch(() => null);
  contextPromise = null;
  pagesByRun.clear();
  await browser?.close().catch(() => {});
}
