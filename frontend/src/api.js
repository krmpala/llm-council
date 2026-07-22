/**
 * API client for the LLM Council backend.
 */

const API_BASE = 'http://localhost:8001';

export const api = {
  /**
   * List all conversations.
   */
  async listConversations() {
    const response = await fetch(`${API_BASE}/api/conversations`);
    if (!response.ok) {
      throw new Error('Failed to list conversations');
    }
    return response.json();
  },

  /**
   * Create a new conversation.
   */
  async createConversation() {
    const response = await fetch(`${API_BASE}/api/conversations`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({}),
    });
    if (!response.ok) {
      throw new Error('Failed to create conversation');
    }
    return response.json();
  },

  /**
   * Get a specific conversation.
   */
  async getConversation(conversationId) {
    const response = await fetch(
      `${API_BASE}/api/conversations/${conversationId}`
    );
    if (!response.ok) {
      throw new Error('Failed to get conversation');
    }
    return response.json();
  },

  /**
   * Send a message in a conversation.
   */
  async sendMessage(conversationId, content) {
    const response = await fetch(
      `${API_BASE}/api/conversations/${conversationId}/message`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ content }),
      }
    );
    if (!response.ok) {
      throw new Error('Failed to send message');
    }
    return response.json();
  },

  /**
   * Send a message and receive streaming updates.
   * @param {string} conversationId - The conversation ID
   * @param {string} content - The message content
   * @param {function} onEvent - Callback function for each event: (eventType, data) => void
   * @returns {Promise<void>}
   */
  async sendMessageStream(conversationId, content, onEvent) {
    const response = await fetch(
      `${API_BASE}/api/conversations/${conversationId}/message/stream`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ content }),
      }
    );

    if (!response.ok) {
      throw new Error('Failed to send message');
    }

    await readSseStream(response, onEvent);
  },

  /**
   * Continue a paused council run after failed members are acknowledged.
   */
  async continueMessageStream(
    conversationId,
    content,
    stage1,
    stage1Statuses,
    constraints,
    onEvent
  ) {
    const response = await fetch(
      `${API_BASE}/api/conversations/${conversationId}/message/continue/stream`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          content,
          stage1,
          stage1_statuses: stage1Statuses || [],
          constraints: constraints || {},
        }),
      }
    );

    if (!response.ok) {
      throw new Error('Failed to continue council');
    }

    await readSseStream(response, onEvent);
  },

  async retryChairmanStream(conversationId, content, stage1, stage2, metadata, onEvent) {
    const response = await fetch(
      `${API_BASE}/api/conversations/${conversationId}/chairman/retry/stream`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          content,
          stage1,
          stage2,
          metadata: metadata || {},
        }),
      }
    );

    if (!response.ok) {
      throw new Error('Failed to retry chairman');
    }

    await readSseStream(response, onEvent);
  },

  async retryPeerEvaluationsStream(conversationId, content, stage1, stage2, metadata, onEvent) {
    const response = await fetch(
      `${API_BASE}/api/conversations/${conversationId}/peer/retry/stream`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          content,
          stage1,
          stage2: stage2 || [],
          metadata: metadata || {},
        }),
      }
    );

    if (!response.ok) {
      throw new Error('Failed to retry peer evaluations');
    }

    await readSseStream(response, onEvent);
  },
};

async function readSseStream(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split('\n\n');
    buffer = events.pop() || '';

    for (const rawEvent of events) {
      const line = rawEvent
        .split('\n')
        .find((eventLine) => eventLine.startsWith('data: '));
      if (!line) continue;

      const data = line.slice(6);
      try {
        const event = JSON.parse(data);
        onEvent(event.type, event);
      } catch (e) {
        console.error('Failed to parse SSE event:', e);
      }
    }
  }
}
