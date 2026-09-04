import assert from 'node:assert/strict';
import test from 'node:test';

import {
  applySalaryPolicy,
  fallbackMarketSalary,
  isExpectedAnnualSalaryControl,
  postedSalaryHigh,
  salaryValueForControl,
} from './salary.mjs';

test('uses five percent below the top of a posted annual range', () => {
  const posted = postedSalaryHigh('Salary: £80,000 - £100,000 per year');
  assert.deepEqual(applySalaryPolicy(posted, 'posted'), {
    amount: 95_000,
    currency: 'GBP',
    source: 'posted',
    reduction: 5,
  });
});

test('annualises the high end of an hourly range before applying policy', () => {
  const posted = postedSalaryHigh('Base pay range: $50 to $60 per hour');
  assert.equal(applySalaryPolicy(posted, 'posted').amount, 118_560);
});

test('uses the base-pay range rather than a higher OTE figure', () => {
  const posted = postedSalaryHigh('Compensation: $150k OTE; base salary range $100k-$120k');
  assert.equal(applySalaryPolicy(posted, 'posted').amount, 114_000);
});

test('recognises expected salary without confusing current salary', () => {
  assert.equal(isExpectedAnnualSalaryControl({ label: 'Expected annual salary *', name: '' }), true);
  assert.equal(isExpectedAnnualSalaryControl({ label: 'Current salary', name: '' }), false);
});

test('picks the select band containing the estimate', () => {
  const control = {
    tag: 'select',
    options: [
      { value: 'low', label: '£50k–£70k' },
      { value: 'high', label: '£70k–£100k' },
    ],
  };
  assert.equal(salaryValueForControl(control, 92_000), 'high');
});

test('fallback produces a role-aware market estimate', () => {
  const estimate = fallbackMarketSalary({ jobTitle: 'Principal Site Reliability Engineer', jobText: 'London, UK' });
  assert.equal(estimate.currency, 'GBP');
  assert.ok(estimate.amount >= 120_000);
});
