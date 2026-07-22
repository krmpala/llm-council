import test from 'node:test';
import assert from 'node:assert/strict';

import {
  displayModelName,
  statusLabel,
  upsertModelStatus,
} from './councilUi.js';

test('displayModelName shows provider and model for raw ids', () => {
  assert.equal(
    displayModelName('openai/gpt-4o'),
    'openai / gpt-4o'
  );
});

test('displayModelName prefers explicit provider metadata', () => {
  assert.equal(
    displayModelName({
      model: 'openai/gpt-4o',
      provider: 'OpenAI',
      model_name: 'GPT 4o',
    }),
    'OpenAI / GPT 4o'
  );
});

test('statusLabel maps api statuses to Turkish labels', () => {
  assert.equal(statusLabel('pending'), 'Bekliyor');
  assert.equal(statusLabel('answering'), 'Cevaplıyor');
  assert.equal(statusLabel('completed'), 'Tamamlandı');
  assert.equal(statusLabel('retrying'), 'Yeniden deneniyor');
  assert.equal(statusLabel('failed'), 'Başarısız');
});

test('upsertModelStatus replaces by model without mutating the original list', () => {
  const original = [
    { model: 'openai/a', status: 'answering' },
    { model: 'google/b', status: 'pending' },
  ];

  const updated = upsertModelStatus(original, {
    model: 'openai/a',
    status: 'completed',
  });

  assert.equal(original[0].status, 'answering');
  assert.equal(updated[0].status, 'completed');
  assert.equal(updated[1].status, 'pending');
});
