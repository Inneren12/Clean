import assert from 'node:assert';
import { formatBooleanLabel, normalizeBooleanValue } from '../components/booleanUtils';

const cases = [
  { value: undefined, expectedChecked: false, expectedLabel: '—' },
  { value: null, expectedChecked: false, expectedLabel: '—' },
  { value: false, expectedChecked: false, expectedLabel: 'No' },
  { value: true, expectedChecked: true, expectedLabel: 'Yes' },
  { value: 'true', expectedChecked: false, expectedLabel: '—' },
];

cases.forEach(({ value, expectedChecked, expectedLabel }) => {
  assert.strictEqual(
    normalizeBooleanValue(value),
    expectedChecked,
    `normalizeBooleanValue(${String(value)}) should be ${expectedChecked}`
  );
  assert.strictEqual(
    formatBooleanLabel(value),
    expectedLabel,
    `formatBooleanLabel(${String(value)}) should be ${expectedLabel}`
  );
});

console.log('All boolean normalization tests passed.');
