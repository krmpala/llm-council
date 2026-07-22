import test from 'node:test';
import assert from 'node:assert/strict';

import { api } from './api.js';

test('retryChairmanStream calls the chairman retry endpoint', async () => {
  const calls = [];
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode('data: {"type":"complete"}\n\n'));
      controller.close();
    },
  });

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return new Response(stream, { status: 200 });
  };

  try {
    const events = [];
    await api.retryChairmanStream(
      'conversation-1',
      'Kısa cevap',
      [{ member_id: 'member-1' }],
      [{ member_id: 'member-1' }],
      { constraints: { requested_length: 'short' } },
      (type) => events.push(type)
    );

    assert.equal(
      calls[0].url,
      'http://localhost:8001/api/conversations/conversation-1/chairman/retry/stream'
    );
    assert.equal(JSON.parse(calls[0].options.body).stage1[0].member_id, 'member-1');
    assert.deepEqual(events, ['complete']);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('retryPeerEvaluationsStream calls the peer retry endpoint', async () => {
  const calls = [];
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode('data: {"type":"complete"}\n\n'));
      controller.close();
    },
  });

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return new Response(stream, { status: 200 });
  };

  try {
    const events = [];
    await api.retryPeerEvaluationsStream(
      'conversation-1',
      'Kısa cevap',
      [{ member_id: 'member-1' }],
      [],
      { constraints: { requested_length: 'short' } },
      (type) => events.push(type)
    );

    assert.equal(
      calls[0].url,
      'http://localhost:8001/api/conversations/conversation-1/peer/retry/stream'
    );
    assert.equal(JSON.parse(calls[0].options.body).stage1[0].member_id, 'member-1');
    assert.deepEqual(events, ['complete']);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
