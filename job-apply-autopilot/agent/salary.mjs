const SALARY_WORDS = /\b(?:salary|compensation|pay range|base pay|base salary|remuneration)\b/i;
const EXPECTATION_WORDS = /\b(?:expected|desired|expectation|expectations|requirement|requirements)\b/i;
const CURRENT_WORDS = /\b(?:current|present|previous|last)\s+(?:salary|compensation|pay)\b/i;

const CURRENCY_SYMBOLS = {
  GBP: '£',
  USD: '$',
  EUR: '€',
  CAD: 'CA$',
  AUD: 'A$',
  CHF: 'CHF',
};

const MARKET_BASES = {
  GBP: { junior: 36_000, mid: 55_000, senior: 78_000, lead: 92_000, principal: 110_000, director: 130_000 },
  USD: { junior: 80_000, mid: 115_000, senior: 145_000, lead: 170_000, principal: 195_000, director: 220_000 },
  EUR: { junior: 45_000, mid: 62_000, senior: 82_000, lead: 98_000, principal: 115_000, director: 135_000 },
  CAD: { junior: 68_000, mid: 95_000, senior: 125_000, lead: 145_000, principal: 165_000, director: 190_000 },
  AUD: { junior: 75_000, mid: 105_000, senior: 140_000, lead: 160_000, principal: 185_000, director: 210_000 },
  CHF: { junior: 75_000, mid: 100_000, senior: 125_000, lead: 145_000, principal: 165_000, director: 190_000 },
};

function numericValues(text) {
  const values = [];
  const pattern = /\d+(?:[,.\s]\d{3})*(?:\.\d+)?\s*[kKmM]?/g;
  for (const match of String(text || '').matchAll(pattern)) {
    const raw = match[0].trim();
    const suffix = raw.slice(-1).toLowerCase();
    const multiplier = suffix === 'k' ? 1_000 : suffix === 'm' ? 1_000_000 : 1;
    const number = Number(raw.replace(/[kKmM\s,]/g, '')) * multiplier;
    if (Number.isFinite(number) && number > 0) values.push(number);
  }
  return values;
}

function currencyFromText(text) {
  const source = String(text || '');
  if (/£|\bGBP\b/i.test(source)) return 'GBP';
  if (/€|\bEUR\b/i.test(source)) return 'EUR';
  if (/\bCHF\b/i.test(source)) return 'CHF';
  if (/\bCAD\b|CA\$/i.test(source)) return 'CAD';
  if (/\bAUD\b|A\$/i.test(source)) return 'AUD';
  if (/\bUSD\b|\$/i.test(source)) return 'USD';
  if (/\b(?:united states|usa|u\.s\.|new york|california|san francisco|seattle|austin)\b/i.test(source)) return 'USD';
  if (/\b(?:canada|toronto|vancouver|montreal)\b/i.test(source)) return 'CAD';
  if (/\b(?:australia|sydney|melbourne|brisbane)\b/i.test(source)) return 'AUD';
  if (/\b(?:switzerland|zurich|geneva)\b/i.test(source)) return 'CHF';
  if (/\b(?:europe|germany|france|netherlands|spain|italy|ireland|belgium|austria)\b/i.test(source)) return 'EUR';
  return 'GBP';
}

function annualise(amount, context) {
  const source = String(context || '').toLowerCase();
  if (/\b(?:per\s+)?hour(?:ly)?\b|\/\s*(?:h|hr)\b/.test(source) && amount < 1_000) return amount * 2_080;
  if (/\b(?:per\s+)?day|daily|day rate\b|\/\s*day\b/.test(source) && amount < 5_000) return amount * 260;
  if (/\b(?:per\s+)?week|weekly\b|\/\s*(?:wk|week)\b/.test(source) && amount < 20_000) return amount * 52;
  if (/\b(?:per\s+)?month|monthly\b|\/\s*(?:mo|month)\b/.test(source) && amount < 100_000) return amount * 12;
  return amount;
}

function statedSalaryAmounts(context) {
  const highs = [];
  const ranges = String(context || '').matchAll(
    /(\d+(?:[,.\s]\d{3})*(?:\.\d+)?\s*[kKmM]?)\s*(?:-|–|—|\bto\b)\s*(?:£|€|\$|\b(?:GBP|EUR|USD|CAD|AUD|CHF)\b)?\s*(\d+(?:[,.\s]\d{3})*(?:\.\d+)?\s*[kKmM]?)/gi,
  );
  for (const range of ranges) {
    const values = numericValues(`${range[1]} ${range[2]}`);
    if (values.length === 2) highs.push(Math.max(...values));
  }
  return highs.length ? highs : numericValues(context);
}

function salaryContexts(jobText) {
  const source = String(jobText || '');
  const lines = source.split(/\r?\n/);
  const explicitRange = /(?:£|€|\$|\b(?:GBP|EUR|USD|CAD|AUD|CHF)\b)\s*\d[^\n]{0,45}(?:-|–|—|\bto\b)[^\n]{0,15}\d/i;
  const contexts = [];
  for (const [index, line] of lines.entries()) {
    if (SALARY_WORDS.test(line)) {
      contexts.push(numericValues(line).length ? line : `${line} ${lines[index + 1] || ''}`);
    } else if (explicitRange.test(line)) {
      contexts.push(line);
    }
  }
  return contexts;
}

export function postedSalaryHigh(jobText) {
  const contexts = salaryContexts(jobText);
  let best = null;
  for (const context of contexts) {
    const annual = statedSalaryAmounts(context)
      .map((amount) => annualise(amount, context))
      .filter((amount) => amount >= 10_000 && amount <= 2_000_000);
    if (!annual.length) continue;
    const high = Math.max(...annual);
    if (!best || high > best.amount) best = { amount: high, currency: currencyFromText(context) };
  }
  return best;
}

function seniority(jobTitle, jobText) {
  const source = `${jobTitle || ''} ${jobText || ''}`.toLowerCase();
  if (/\b(?:director|head of|vice president|vp)\b/.test(source)) return 'director';
  if (/\b(?:principal|staff|distinguished)\b/.test(source)) return 'principal';
  if (/\b(?:lead|manager|architect)\b/.test(source)) return 'lead';
  if (/\b(?:senior|sr\.?|7\+? years|8\+? years|10\+? years)\b/.test(source)) return 'senior';
  if (/\b(?:junior|graduate|entry.level|intern)\b/.test(source)) return 'junior';
  return 'mid';
}

export function fallbackMarketSalary({ jobTitle = '', jobText = '' } = {}) {
  const currency = currencyFromText(`${jobTitle}\n${jobText}`);
  const level = seniority(jobTitle, jobText);
  let amount = MARKET_BASES[currency][level];
  const source = `${jobTitle} ${jobText}`.toLowerCase();
  if (/\b(?:site reliability|sre|platform engineer|machine learning|artificial intelligence|cyber.?security)\b/.test(source)) amount *= 1.08;
  if (currency === 'GBP' && /\blondon\b/.test(source)) amount *= 1.1;
  return { amount: Math.round(amount / 500) * 500, currency };
}

export function applySalaryPolicy(estimate, source) {
  const reduction = source === 'posted' ? 0.95 : 0.94;
  return {
    amount: Math.round(Number(estimate.amount) * reduction),
    currency: estimate.currency || 'GBP',
    source,
    reduction: source === 'posted' ? 5 : 6,
  };
}

function rangeForOption(text) {
  const source = String(text || '');
  const values = numericValues(source).map((amount) => annualise(amount, source));
  if (!values.length) return null;
  if (/\b(?:under|below|up to|less than)\b/i.test(source)) return { low: 0, high: values[0] };
  if (/\b(?:over|above|more than|or more|plus)\b|\+\s*$/i.test(source)) return { low: values[0], high: Number.POSITIVE_INFINITY };
  return { low: Math.min(...values), high: Math.max(...values) };
}

export function salaryValueForControl(control, amount) {
  if (control.tag !== 'select') return String(Math.round(amount));
  const option = (control.options || []).find((item) => {
    const range = rangeForOption(`${item.label} ${item.value}`);
    return range && amount >= range.low && amount <= range.high;
  });
  return option?.value || String(Math.round(amount));
}

export function isExpectedAnnualSalaryControl(control) {
  const label = `${control.label || ''} ${control.name || ''}`;
  if (CURRENT_WORDS.test(label)) return false;
  return SALARY_WORDS.test(label) && (EXPECTATION_WORDS.test(label) || /\bannual\b/i.test(label));
}

export function displaySalary(estimate) {
  const symbol = CURRENCY_SYMBOLS[estimate.currency] || `${estimate.currency} `;
  return `${symbol}${Math.round(estimate.amount).toLocaleString('en-GB')}`;
}
