import test from 'node:test';
import assert from 'node:assert/strict';

import {
  chairmanErrorDetails,
  continueButtonLabel,
  displayModelName,
  isChairmanFailed,
  isCouncilBlockedMessage,
  normalizePeerStatuses,
  peerProgress,
  shouldRenderCouncilStages,
  statusLabel,
  upsertModelStatus,
  uniqueByMemberId,
} from './councilUi.js';

test('displayModelName shows provider and model for raw ids', () => {
  assert.equal(displayModelName('openai/gpt-4o'), 'openai / gpt-4o');
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

test('displayModelName shows actual fallback model when present', () => {
  assert.equal(
    displayModelName({
      primary_model: 'google/gemma',
      actual_model: 'openai/fallback-model',
      provider: 'openai',
      model_name: 'fallback-model',
    }),
    'openai / fallback-model'
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

test('upsertModelStatus prefers member_id over model order', () => {
  const original = [
    { member_id: 'member-1', model: 'openai/a', status: 'completed' },
    { member_id: 'member-3', model: 'google/gemma', status: 'retrying' },
  ];

  const updated = upsertModelStatus(original, {
    member_id: 'member-3',
    model: 'google/gemma',
    status: 'failed',
  });

  assert.equal(updated.length, 2);
  assert.equal(updated[0].status, 'completed');
  assert.equal(updated[1].member_id, 'member-3');
  assert.equal(updated[1].status, 'failed');
});

test('blocked council message hides Stage 2 and Stage 3 surfaces', () => {
  const message = {
    role: 'assistant',
    stage1: [],
    stage2: [],
    stage3: null,
    metadata: {
      stage1_summary: {
        blocked: true,
        successful: 2,
        override_available: true,
      },
    },
  };

  assert.equal(isCouncilBlockedMessage(message), true);
  assert.equal(shouldRenderCouncilStages(message), false);
});

test('two successful responses show continue button but one does not', () => {
  assert.equal(
    continueButtonLabel({ override_available: true, successful: 2 }),
    'Kalan 2 üyeyle devam et'
  );
  assert.equal(
    continueButtonLabel({ override_available: false, successful: 1 }),
    null
  );
});

test('reopened conversation preserves continue-required state from metadata', () => {
  const savedMessage = {
    metadata: {
      requires_continue: true,
      stage1_summary: {
        blocked: true,
        successful: 2,
        override_available: true,
      },
    },
    stage3: null,
  };

  assert.equal(isCouncilBlockedMessage(savedMessage), true);
  assert.equal(continueButtonLabel(savedMessage.metadata.stage1_summary), 'Kalan 2 üyeyle devam et');
});

test('duplicate payload is safely deduplicated by member_id', () => {
  const unique = uniqueByMemberId([
    { member_id: 'member-1', response: 'a' },
    { member_id: 'member-1', response: 'copied' },
    { member_id: 'member-2', response: 'b' },
  ], 'test payload');

  assert.deepEqual(
    unique.map((item) => item.response),
    ['a', 'b']
  );
});

test('chairman failure details preserve stage and http code', () => {
  const failure = {
    status: 'failed',
    stage: 'synthesis',
    http_status: 429,
    attempts: 3,
    error_message: 'rate limited',
  };

  assert.equal(isChairmanFailed(failure), true);
  assert.deepEqual(chairmanErrorDetails(failure), {
    stage: 'synthesis',
    httpStatus: 429,
    attempts: 3,
    message: 'rate limited',
  });
});

test('quality warning is stored on final response when quality review fails', () => {
  const response = {
    status: 'completed',
    response: 'first synthesis',
    quality_review_status: 'failed',
    quality_warning: 'Dil ve tutarlılık kontrolü tamamlanamadı; ilk sentez gösteriliyor.',
  };

  assert.equal(isChairmanFailed(response), false);
  assert.match(response.quality_warning, /ilk sentez/);
});

test('old running peer metadata is shown as interrupted', () => {
  const normalized = normalizePeerStatuses([
    { member_id: 'member-1', status: 'running' },
    { member_id: 'member-2', status: 'completed' },
  ]);

  assert.equal(normalized[0].status, 'interrupted');
  assert.equal(normalized[1].status, 'completed');
});

test('peer progress counts terminal evaluator statuses', () => {
  const progress = peerProgress([
    { member_id: 'member-1', status: 'completed' },
    { member_id: 'member-2', status: 'failed' },
    { member_id: 'member-3', status: 'invalid_output' },
  ]);

  assert.deepEqual(progress, {
    completed: 3,
    total: 3,
    terminal: true,
  });
});
