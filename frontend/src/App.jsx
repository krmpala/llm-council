import { useState, useEffect, useCallback } from 'react';
import Sidebar from './components/Sidebar';
import ChatInterface from './components/ChatInterface';
import { api } from './api';
import { upsertModelStatus } from './councilUi';
import './App.css';

function App() {
  const [conversations, setConversations] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState(null);
  const [currentConversation, setCurrentConversation] = useState(null);
  const [isLoading, setIsLoading] = useState(false);

  const loadConversations = useCallback(async () => {
    try {
      const convs = await api.listConversations();
      setConversations(convs);
    } catch (error) {
      console.error('Failed to load conversations:', error);
    }
  }, []);

  // Load conversations on mount
  useEffect(() => {
    let cancelled = false;

    api.listConversations()
      .then((convs) => {
        if (!cancelled) setConversations(convs);
      })
      .catch((error) => {
        console.error('Failed to load conversations:', error);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  // Load conversation details when selected
  useEffect(() => {
    if (!currentConversationId) return undefined;

    let cancelled = false;
    api.getConversation(currentConversationId)
      .then((conv) => {
        if (!cancelled) setCurrentConversation(conv);
      })
      .catch((error) => {
        console.error('Failed to load conversation:', error);
      });

    return () => {
      cancelled = true;
    };
  }, [currentConversationId]);

  const handleNewConversation = async () => {
    try {
      const newConv = await api.createConversation();
      setConversations([
        { id: newConv.id, created_at: newConv.created_at, message_count: 0 },
        ...conversations,
      ]);
      setCurrentConversationId(newConv.id);
    } catch (error) {
      console.error('Failed to create conversation:', error);
    }
  };

  const handleSelectConversation = (id) => {
    setCurrentConversationId(id);
  };

  const updateAssistantMessage = (messageIndex, updater) => {
    setCurrentConversation((prev) => {
      const messages = [...prev.messages];
      const current = messages[messageIndex];
      if (!current || current.role !== 'assistant') return prev;

      messages[messageIndex] = updater({
        ...current,
        loading: { ...(current.loading || {}) },
        metadata: { ...(current.metadata || {}) },
      });
      return { ...prev, messages };
    });
  };

  const handleCouncilEvent = (eventType, event, assistantIndex) => {
    switch (eventType) {
      case 'stage1_start':
        updateAssistantMessage(assistantIndex, (msg) => ({
          ...msg,
          metadata: {
            ...msg.metadata,
            ...(event.metadata || {}),
          },
          loading: { ...msg.loading, stage1: true },
        }));
        break;

      case 'model_status':
        updateAssistantMessage(assistantIndex, (msg) => ({
          ...msg,
          metadata: {
            ...msg.metadata,
            stage1_statuses: upsertModelStatus(
              msg.metadata?.stage1_statuses,
              event.data
            ),
          },
        }));
        break;

      case 'stage1_complete':
        updateAssistantMessage(assistantIndex, (msg) => ({
          ...msg,
          stage1: event.data,
          metadata: {
            ...msg.metadata,
            ...(event.metadata || {}),
          },
          loading: { ...msg.loading, stage1: false },
        }));
        break;

      case 'continue_required':
        updateAssistantMessage(assistantIndex, (msg) => ({
          ...msg,
          needsContinue: true,
          continueMessage: event.message,
          metadata: {
            ...msg.metadata,
            ...(event.metadata || {}),
            requires_continue: true,
          },
          loading: { ...msg.loading, stage1: false, stage2: false, stage3: false },
        }));
        setIsLoading(false);
        break;

      case 'council_blocked':
        updateAssistantMessage(assistantIndex, (msg) => ({
          ...msg,
          blockedMessage: event.message,
          stage3: null,
          metadata: {
            ...msg.metadata,
            ...(event.metadata || {}),
          },
          loading: { ...msg.loading, stage1: false, stage2: false, stage3: false },
        }));
        break;

      case 'stage2_start':
        updateAssistantMessage(assistantIndex, (msg) => ({
          ...msg,
          needsContinue: false,
          loading: { ...msg.loading, stage2: true },
        }));
        break;

      case 'peer_evaluator_started':
      case 'peer_evaluator_retrying':
      case 'peer_evaluator_completed':
      case 'peer_evaluator_failed':
      case 'peer_evaluator_invalid':
        updateAssistantMessage(assistantIndex, (msg) => ({
          ...msg,
          metadata: {
            ...msg.metadata,
            peer_evaluator_statuses: upsertModelStatus(
              msg.metadata?.peer_evaluator_statuses,
              event.data
            ),
          },
        }));
        break;

      case 'peer_stage_completed':
      case 'peer_stage_blocked':
      case 'peer_stage_failed':
        updateAssistantMessage(assistantIndex, (msg) => ({
          ...msg,
          metadata: {
            ...msg.metadata,
            peer_stage_summary: event.data?.summary || event.data || {},
            peer_evaluator_statuses:
              event.data?.statuses || msg.metadata?.peer_evaluator_statuses || [],
          },
          loading: { ...msg.loading, stage2: false },
        }));
        break;

      case 'stage2_complete':
        updateAssistantMessage(assistantIndex, (msg) => ({
          ...msg,
          stage2: event.data,
          metadata: {
            ...msg.metadata,
            ...(event.metadata || {}),
          },
          loading: { ...msg.loading, stage2: false },
        }));
        break;

      case 'stage3_start':
        updateAssistantMessage(assistantIndex, (msg) => ({
          ...msg,
          loading: { ...msg.loading, stage3: true },
        }));
        break;

      case 'stage3_complete':
        updateAssistantMessage(assistantIndex, (msg) => ({
          ...msg,
          stage3: event.data,
          metadata: {
            ...msg.metadata,
            ...(event.metadata || {}),
          },
          loading: { ...msg.loading, stage3: false },
        }));
        break;

      case 'title_complete':
        loadConversations();
        break;

      case 'complete':
        loadConversations();
        setIsLoading(false);
        break;

      case 'error':
        console.error('Stream error:', event.message);
        setIsLoading(false);
        break;

      default:
        console.log('Unknown event type:', eventType);
    }
  };

  const handleSendMessage = async (content) => {
    if (!currentConversationId) return;

    setIsLoading(true);
    try {
      const assistantIndex = currentConversation.messages.length + 1;

      // Optimistically add user message to UI
      const userMessage = { role: 'user', content };
      setCurrentConversation((prev) => ({
        ...prev,
        messages: [...prev.messages, userMessage],
      }));

      // Create a partial assistant message that will be updated progressively
      const assistantMessage = {
        role: 'assistant',
        userQuery: content,
        stage1: null,
        stage2: null,
        stage3: null,
        metadata: null,
        needsContinue: false,
        continueMessage: null,
        loading: {
          stage1: false,
          stage2: false,
          stage3: false,
        },
      };

      // Add the partial assistant message
      setCurrentConversation((prev) => ({
        ...prev,
        messages: [...prev.messages, assistantMessage],
      }));

      // Send message with streaming
      await api.sendMessageStream(currentConversationId, content, (eventType, event) => {
        handleCouncilEvent(eventType, event, assistantIndex);
      });
    } catch (error) {
      console.error('Failed to send message:', error);
      // Remove optimistic messages on error
      setCurrentConversation((prev) => ({
        ...prev,
        messages: prev.messages.slice(0, -2),
      }));
      setIsLoading(false);
    }
  };

  const handleContinueCouncil = async (messageIndex) => {
    if (!currentConversationId || isLoading) return;

    const message = currentConversation.messages[messageIndex];
    const previousUserMessage = currentConversation.messages
      .slice(0, messageIndex)
      .findLast((msg) => msg.role === 'user');
    const content = message.userQuery || previousUserMessage?.content;

    if (!content || !message.stage1) return;

    setIsLoading(true);
    try {
      updateAssistantMessage(messageIndex, (msg) => ({
        ...msg,
        needsContinue: false,
        loading: { ...msg.loading, stage2: true },
      }));

      await api.continueMessageStream(
        currentConversationId,
        content,
        message.stage1,
        message.metadata?.stage1_statuses || [],
        message.metadata?.constraints || {},
        (eventType, event) => handleCouncilEvent(eventType, event, messageIndex)
      );
    } catch (error) {
      console.error('Failed to continue council:', error);
      updateAssistantMessage(messageIndex, (msg) => ({
        ...msg,
        needsContinue: true,
        loading: { ...msg.loading, stage2: false, stage3: false },
      }));
      setIsLoading(false);
    }
  };

  const handleRetryChairman = async (messageIndex) => {
    if (!currentConversationId || isLoading) return;

    const message = currentConversation.messages[messageIndex];
    const previousUserMessage = currentConversation.messages
      .slice(0, messageIndex)
      .findLast((msg) => msg.role === 'user');
    const content = message.userQuery || previousUserMessage?.content;
    if (!content || !message.stage1 || !message.stage2) return;

    setIsLoading(true);
    try {
      updateAssistantMessage(messageIndex, (msg) => ({
        ...msg,
        loading: { ...msg.loading, stage3: true },
      }));
      await api.retryChairmanStream(
        currentConversationId,
        content,
        message.stage1,
        message.stage2,
        message.metadata || {},
        (eventType, event) => handleCouncilEvent(eventType, event, messageIndex)
      );
    } catch (error) {
      console.error('Failed to retry chairman:', error);
      updateAssistantMessage(messageIndex, (msg) => ({
        ...msg,
        loading: { ...msg.loading, stage3: false },
      }));
      setIsLoading(false);
    }
  };

  const handleRetryPeerEvaluations = async (messageIndex) => {
    if (!currentConversationId || isLoading) return;

    const message = currentConversation.messages[messageIndex];
    const previousUserMessage = currentConversation.messages
      .slice(0, messageIndex)
      .findLast((msg) => msg.role === 'user');
    const content = message.userQuery || previousUserMessage?.content;
    if (!content || !message.stage1) return;

    setIsLoading(true);
    try {
      updateAssistantMessage(messageIndex, (msg) => ({
        ...msg,
        loading: { ...msg.loading, stage2: true },
      }));
      await api.retryPeerEvaluationsStream(
        currentConversationId,
        content,
        message.stage1,
        message.stage2 || [],
        message.metadata || {},
        (eventType, event) => handleCouncilEvent(eventType, event, messageIndex)
      );
    } catch (error) {
      console.error('Failed to retry peer evaluations:', error);
      updateAssistantMessage(messageIndex, (msg) => ({
        ...msg,
        loading: { ...msg.loading, stage2: false },
      }));
      setIsLoading(false);
    }
  };

  return (
    <div className="app">
      <Sidebar
        conversations={conversations}
        currentConversationId={currentConversationId}
        onSelectConversation={handleSelectConversation}
        onNewConversation={handleNewConversation}
      />
      <ChatInterface
        conversation={currentConversation}
        onSendMessage={handleSendMessage}
        onContinueCouncil={handleContinueCouncil}
        onRetryChairman={handleRetryChairman}
        onRetryPeerEvaluations={handleRetryPeerEvaluations}
        isLoading={isLoading}
      />
    </div>
  );
}

export default App;
