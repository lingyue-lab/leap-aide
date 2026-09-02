/**
 * Global StreamManager
 *
 * Manages WebSocket replay connections for streaming agent responses.
 * The agent keeps running on the backend; this module accumulates incoming
 * events into a per-session message buffer and notifies subscribers when
 * the buffer changes.
 *
 * Key design:
 *  - Uses WebSocket replay (via wsManager) for all streaming.
 *  - All events are written into a per-session SessionStreamState.
 *  - Components subscribe to changes via the `subscribe` / `unsubscribe` API.
 *  - ChatInterface restores from the buffer when switching back to a session.
 *  - reconnectStream() allows re-attaching to an existing backend stream after
 *    a page refresh.
 */

import type { Message, ToolCall, UsageInfo, ContentBlock } from '@/types';

// ── Types ────────────────────────────────────────────────────────────────────

export interface SessionStreamState {
  sessionId: string;
  streamId: string;
  messages: Message[];
  toolCalls: ToolCall[];
  busy: boolean;
  lastUsage: UsageInfo | null;
  /** True while the WebSocket connection is active */
  streaming: boolean;
  /** Non-null when streaming finished (completed/error/cancel) */
  completedAt: number | null;
  /** Error message if stream ended with an error */
  error: string | null;
  /** Number of messages before the current stream started (used to identify new messages for timestamp POST) */
  initialMessageCount: number;
}

type ChangeListener = (sessionId: string, state: SessionStreamState) => void;

// ── Module-level state (singleton) ───────────────────────────────────────────

/** Per-session stream state: session_id → SessionStreamState */
const _sessionStates = new Map<string, SessionStreamState>();

/** Change listeners */
const _listeners = new Set<ChangeListener>();

/** Throttle rapid notifications to avoid overwhelming React renders */
const _pendingNotifies = new Map<string, number>(); // sessionId → setTimeout ID
const NOTIFY_INTERVAL_MS = 150; // ~6-7 updates/sec during streaming (balanced: smooth enough for UX, safe for render budget)

// ── Internal helpers ──────────────────────────────────────────────────────────

function _notify(sessionId: string) {
  const state = _sessionStates.get(sessionId);
  if (!state) return;
  _listeners.forEach((fn) => fn(sessionId, state));
}

/** Throttled notification: accumulates state changes and notifies at most every NOTIFY_INTERVAL_MS.
 *  Used for rapid events (token, reasoning) to prevent render storms. */
function _notifyThrottled(sessionId: string) {
  if (_pendingNotifies.has(sessionId)) return; // already scheduled
  const timerId = setTimeout(() => {
    _pendingNotifies.delete(sessionId);
    _notify(sessionId);
  }, NOTIFY_INTERVAL_MS);
  _pendingNotifies.set(sessionId, timerId);
}

/** Immediate notification: cancels any pending throttled notification and fires right away.
 *  Used for important events (message_start, done, error, etc.) */
function _notifyImmediate(sessionId: string) {
  const pending = _pendingNotifies.get(sessionId);
  if (pending) {
    clearTimeout(pending);
    _pendingNotifies.delete(sessionId);
  }
  _notify(sessionId);
}

// ── Compressing spinner tracking ──────────────────────────────────────────────
// Sessions currently showing the transient "Auto-compressing…" InfoCard (loading,
// noDismiss). Tracked so terminal WebSocket events (done/stream_end/error/apperror/cancel)
// can dismiss it as a safety net — the card must never outlive the stream, even if
// the backend `compressed` event is dropped (e.g. compression was a no-op).
const _compressingSessions = new Set<string>();

function _dismissCompressing(sessionId: string) {
  if (!_compressingSessions.has(sessionId)) return;
  _compressingSessions.delete(sessionId);
  import('@/components/dialogs/InfoCard').then(({ dismissInfoCard }) => {
    dismissInfoCard(sessionId);
  });
}

/**
 * StreamManager — Per-session streaming state and replay event handling.
 * Single entry point: startSessionStream (called from begin_stream notify).
 */
import { useChatStore } from '@/stores/useChatStore';

function _getOrCreateState(sessionId: string, streamId: string, initialMessages: Message[]): SessionStreamState {
  let state = _sessionStates.get(sessionId);
  if (!state) {
    state = {
      sessionId,
      streamId,
      messages: initialMessages,
      toolCalls: [],
      busy: true,
      lastUsage: null,
      streaming: true,
      completedAt: null,
      error: null,
      initialMessageCount: initialMessages.length,
    };
    _sessionStates.set(sessionId, state);
  }
  return state;
}

function _appendToLastAssistant(messages: Message[], token: string): Message[] {
  const msgs = [...messages];
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].role === 'assistant') {
      const cur = msgs[i].content;
      if (typeof cur === 'string') {
        msgs[i] = { ...msgs[i], content: cur + token, _live: true };
      } else if (Array.isArray(cur)) {
        const blocks = [...(cur as ContentBlock[])];
        let lastText = -1;
        for (let j = blocks.length - 1; j >= 0; j--) {
          if (blocks[j].type === 'text') { lastText = j; break; }
        }
        if (lastText >= 0) {
          blocks[lastText] = { ...blocks[lastText], text: (blocks[lastText].text || '') + token };
        } else {
          blocks.push({ type: 'text', text: token });
        }
        msgs[i] = { ...msgs[i], content: blocks, _live: true };
      } else {
        msgs[i] = { ...msgs[i], content: token, _live: true };
      }
      break;
    }
  }
  return msgs;
}

function _appendReasoningToLast(messages: Message[], text: string): Message[] {
  const msgs = [...messages];
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].role === 'assistant') {
      msgs[i] = { ...msgs[i], reasoning: (msgs[i].reasoning || '') + text, _live: true };
      break;
    }
  }
  return msgs;
}

function _appendContentBlock(messages: Message[], block: ContentBlock): Message[] {
  const msgs = [...messages];
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].role === 'assistant') {
      const cur = msgs[i].content;
      let newContent: ContentBlock[];
      if (Array.isArray(cur)) {
        newContent = [...(cur as ContentBlock[]), block];
      } else if (typeof cur === 'string' && cur) {
        newContent = [{ type: 'text', text: cur }, block];
      } else {
        newContent = [block];
      }
      msgs[i] = { ...msgs[i], content: newContent, _live: true };
      break;
    }
  }
  return msgs;
}

function _lastAssistant(messages: Message[]): Message | undefined {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === 'assistant') return messages[i];
  }
  return undefined;
}

function _updateToolMessage(messages: Message[], toolCallId: string, updates: Partial<Message>): Message[] {
  return messages.map((m) =>
    m.role === 'tool' && m.tool_call_id === toolCallId ? { ...m, ...updates } : m
  );
}

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Start a background stream for sessionId.
 * initialMessages should be the current chat history BEFORE the new user message is added.
 *
 * Uses WebSocket replay.
 * The streamId parameter is kept for backward compatibility but the actual connection
 * is keyed by sessionId.
 */
export async function startBackgroundStream(
  sessionId: string,
  _streamId: string,  // Kept for backward compatibility, not used
  userMessage: Message,
  initialMessages: Message[]
): Promise<void> {
  // Close any previous stream for this session
  stopBackgroundStream(sessionId);

  // Use session-based stream ID for internal tracking
  const sessionStreamId = `session-${sessionId}`;

  // Seed the state with existing history + the new user message
  const seedMessages = [...initialMessages, userMessage];
  const state = _getOrCreateState(sessionId, sessionStreamId, seedMessages);
  _sessionStates.set(sessionId, {
    ...state,
    streamId: sessionStreamId,
    busy: true,
    streaming: true,
    completedAt: null,
    error: null,
    messages: seedMessages,
    toolCalls: [],
    // Record how many messages the backend already has (before the user message
    // and the streaming response).  Used at stream-end to compute how many new
    // timestamps to POST.
    initialMessageCount: initialMessages.length,
  });

  // Use WebSocket replay
  const { wsManager } = await import('@/stores/wsManager');
  wsManager.connectSession(sessionId, handleReplayEvent);

  _notify(sessionId);
}

/**
 * Reconnect to an existing backend stream after a page refresh.
 *
 * Unlike startBackgroundStream(), this does NOT start a new backend run —
 * it uses WebSocket replay to:
 *   1. Send a 'replay_start' event with the count of historical events
 *   2. Replay all past events (token, tool, tool_complete, etc.) in order
 *   3. Send a 'replay_end' event to mark the boundary
 *   4. Continue with any new live events from here on
 *
 * Call this when GET /api/session returns a non-null active_stream_id AND
 * GET /api/chat/stream/status confirms that stream is still alive.
 *
 * @param sessionId         The session being reconnected.
 * @param streamId          The stream_id from session.active_stream_id (kept for backward compat).
 * @param pendingUserMsg    The user message text that triggered the stream.
 * @param existingMessages  Messages already persisted in the DB (history before the pending turn).
 */
export async function reconnectStream(
  sessionId: string,
  _streamId: string,  // Kept for backward compatibility, not used
  pendingUserMsg: string,
  existingMessages: Message[],
  pendingOriginalMsg?: string,
  pendingUserMsgTs?: number,
): Promise<void> {
  console.log(`[StreamManager] reconnectStream called`, {
    sessionId,
    existingMessagesCount: existingMessages.length,
    pendingUserMsg: pendingUserMsg?.substring(0, 50),
  });

  // Close any pre-existing stream/state for this session
  stopBackgroundStream(sessionId);

  // Use session-based stream ID
  const sessionStreamId = `session-${sessionId}`;

  // Build the seed: persisted history + the pending user message (optimistic UI).
  // replay_start will reset this to the same baseline before replaying history events.
  const userMsg: Message = {
    role: 'user',
    content: pendingUserMsg,
    timestamp__webui: pendingUserMsgTs ?? (Date.now() / 1000),  // fallback for older backends
    ...(pendingOriginalMsg ? { original_content__webui: pendingOriginalMsg } : {}),
  };
  const seedMessages = [...existingMessages, userMsg];

  console.log(`[StreamManager] reconnectStream setting seedMessages`, {
    seedMessagesCount: seedMessages.length,
    roles: seedMessages.map(m => m.role),
  });

  _sessionStates.set(sessionId, {
    sessionId,
    streamId: sessionStreamId,
    messages: seedMessages,
    toolCalls: [],
    busy: true,
    lastUsage: null,
    streaming: true,
    completedAt: null,
    error: null,
    // Reconnect: all existing messages are already in the backend.
    initialMessageCount: existingMessages.length,
  });

  // Use WebSocket replay
  const { wsManager } = await import('@/stores/wsManager');
  wsManager.connectSession(sessionId, handleReplayEvent);

  _notify(sessionId);
}

/**
 * Start an independent session-based stream for a session (main or subagent).
 * Uses WebSocket replay.
 *
 * @param sessionId The session to start streaming for.
 * @param initialMessages Optional initial messages to seed the state with.
 */
export async function startSessionStream(sessionId: string): Promise<void> {
  // If already streaming this session, skip
  const existing = _sessionStates.get(sessionId);
  if (existing?.streaming) {
    return;
  }

  const streamId = `session-${sessionId}`;
  // For the active session, useChatStore has the latest messages including
  // the optimistic user message added by send() before POST /api/chat/start.
  // The backend session.messages won't have it yet (pending_user_message).
  // For background sessions (child agents, page refresh), load from backend.
  let seedMessages: Message[];
  if (existing?.messages?.length) {
    // Preserve existing messages (e.g., from reconnectStream)
    seedMessages = existing.messages;
  } else {
    const { useSessionStore } = await import('@/stores/useSessionStore');
    const currentSid = useSessionStore.getState().currentSessionId;
    if (currentSid === sessionId) {
      seedMessages = useChatStore.getState().messages || [];
    } else {
      try {
        const { sessionService } = await import('@/services/api');
        const resp = await sessionService.getSession(sessionId);
        const session = (resp as any)?.session;
        seedMessages = session?.messages || [];
        // During streaming, the SDK hasn't persisted the user message to
        // session.messages yet — it lives only in pending_user_message.
        // Merge it into seed so the replay baseline includes the user bubble.
        const pendingMsg = session?.pending_user_message;
        if (pendingMsg && typeof pendingMsg === 'string') {
          const last = seedMessages[seedMessages.length - 1];
          if (!(last?.role === 'user' && typeof last.content === 'string' && last.content === pendingMsg)) {
            seedMessages = [...seedMessages, {
              role: 'user' as const,
              content: pendingMsg,
              timestamp__webui: session?.pending_user_message_ts ?? (Date.now() / 1000),
              ...(session?.pending_original_message ? { original_content__webui: session.pending_original_message } : {}),
            }];
          }
        }
      } catch {
        seedMessages = [];
      }
    }
  }

  _sessionStates.set(sessionId, {
    sessionId,
    streamId,
    messages: seedMessages,
    toolCalls: [],
    busy: true,
    lastUsage: null,
    streaming: true,
    completedAt: null,
    error: null,
    initialMessageCount: seedMessages.length,
  });

  // Use WebSocket replay
  const { wsManager } = await import('@/stores/wsManager');
  wsManager.connectSession(sessionId, handleReplayEvent);

  _notify(sessionId);
}

/** Stop (cancel) the background stream for a session. Does NOT call backend cancel. */
export function stopBackgroundStream(sessionId: string): void {
  const s = _sessionStates.get(sessionId);
  if (!s) return;
  // Disconnect WebSocket session if connected
  import('@/stores/wsManager').then(({ wsManager }) => {
    wsManager.disconnectSession(sessionId);
  });
  // Cancel any pending throttled notification so stale tokens from
  // this session are not pushed to useChatStore after the stream is
  // stopped (critical for interrupt mode where a new stream starts
  // immediately after stopping the old one).
  const pending = _pendingNotifies.get(sessionId);
  if (pending) {
    clearTimeout(pending);
    _pendingNotifies.delete(sessionId);
  }
  // Mark as no longer streaming so the UI (ThinkingIndicator, sidebar pulse)
  // reflects the stopped state even if state persists for a while.
  _sessionStates.set(sessionId, { ...s, streaming: false, busy: false });
  _notify(sessionId);
}

/** Remove all state for a session (call after done event is handled by UI) */
export function clearSessionStream(sessionId: string): void {
  // ── Notify subscribers BEFORE deleting state ──────────────────────────
  // stopBackgroundStream already sets busy=false, streaming=false and calls
  // _notify.  But _notify reads from _sessionStates, so we must ensure the
  // state still exists with the terminal flags when subscribers fire.
  // After the delete, _notify returns early (state is null), which was the
  // root cause of stale busy=true surviving clearSessionStream.
  stopBackgroundStream(sessionId);

  // Cancel any pending throttled notification — the final _notify in
  // stopBackgroundStream already delivered the terminal state.
  const pending = _pendingNotifies.get(sessionId);
  if (pending) {
    clearTimeout(pending);
    _pendingNotifies.delete(sessionId);
  }

  // Now safe to delete — subscribers have already seen busy=false
  _sessionStates.delete(sessionId);
}

/** Get the current stream state for a session (null if no stream) */
export function getSessionStreamState(sessionId: string): SessionStreamState | null {
  return _sessionStates.get(sessionId) ?? null;
}

/** True if a session currently has an active background stream */
export function isSessionStreaming(sessionId: string): boolean {
  const s = _sessionStates.get(sessionId);
  return !!(s && s.streaming);
}

/** Replace the message list for a session in the internal buffer.
 *  Used after compression to keep _sessionStates in sync with Zustand
 *  so subsequent WebSocket events don't push stale pre-compression data back. */
export function replaceSessionMessages(sessionId: string, messages: Message[]): void {
  const s = _sessionStates.get(sessionId);
  if (s) {
    _sessionStates.set(sessionId, { ...s, messages });
  }
}

/** Subscribe to state changes for any session */
export function subscribe(listener: ChangeListener): () => void {
  _listeners.add(listener);
  return () => _listeners.delete(listener);
}

/** Get all session IDs that currently have a stream state (active or recently completed) */
export function getActiveSessionIds(): string[] {
  return Array.from(_sessionStates.keys());
}

/**
 * Close all active WebSocket connections. Called on page unload to free up connections.
 */
export function closeAllStreams(): void {
  import('@/stores/wsManager').then(({ wsManager }) => {
    wsManager.disconnect();
  });
}

// Close all WebSocket connections on page unload to free up connections for refresh
if (typeof window !== 'undefined') {
  const cleanup = () => closeAllStreams();
  window.addEventListener('beforeunload', cleanup);
  window.addEventListener('pagehide', cleanup);
}

// ── WebSocket Replay Support ────────────────────────────────────────────────

/**
 * Handle a replay event from WebSocket.
 * Called by wsManager when it receives a replay channel message.
 */
export function handleReplayEvent(sessionId: string, event: string, data: any): void {
  const s = _sessionStates.get(sessionId);
  if (!s) {
    console.warn(`[StreamManager] handleReplayEvent: no state for session ${sessionId}, event=${event}`);
    return;
  }

  // Log key events for debugging
  if (['replay_start', 'replay_end', 'message_start', 'done', 'no_stream', 'error'].includes(event)) {
    console.log(`[StreamManager] handleReplayEvent: ${event}`, {
      sessionId,
      messagesCount: s.messages.length,
      streaming: s.streaming,
      busy: s.busy,
    });
  }

  switch (event) {
    case 'replay_start':
      // Reset messages to baseline before replay
      // Keep the existing seed messages (from reconnectStream or startSessionStream)
      // instead of fetching from useChatStore which may be stale during page refresh
      console.log(`[StreamManager] replay_start: keeping ${s.messages.length} existing messages`);
      _sessionStates.set(sessionId, {
        ...s,
        toolCalls: [],
      });
      _notifyImmediate(sessionId);
      break;

    case 'replay_end':
      // Boundary marker — no state change needed
      break;

    case 'no_stream':
      // No active stream for this session
      console.log(`[StreamManager] No stream for session ${sessionId}`);
      break;

    case 'message_start': {
      const ts = data.ts ?? (Date.now() / 1000);
      const newMsg: Message = { role: 'assistant', content: '', timestamp__webui: ts, _live: true };
      _sessionStates.set(sessionId, { ...s, messages: [...s.messages, newMsg] });
      _notifyImmediate(sessionId);
      break;
    }

    case 'token': {
      const { text } = data;
      const updated = _appendToLastAssistant(s.messages, text);
      _sessionStates.set(sessionId, { ...s, messages: updated });
      _notifyThrottled(sessionId);
      break;
    }

    case 'reasoning': {
      const { text } = data;
      _sessionStates.set(sessionId, { ...s, messages: _appendReasoningToLast(s.messages, text) });
      _notifyThrottled(sessionId);
      break;
    }

    case 'tool': {
      const tid = data.tid || `live-${data.name}-${Date.now()}`;
      let msgs = s.messages;
      const _toolTs = data.ts ?? _lastAssistant(msgs)?.timestamp__webui;

      const toolUseBlock: ContentBlock = {
        type: 'tool_use', id: tid, name: data.name, input: data.args || {},
      };
      msgs = _appendContentBlock(msgs, toolUseBlock);
      msgs = [...msgs, {
        role: 'tool', content: '', tool_call_id: tid, tool_name: data.name,
        timestamp__webui: _toolTs, _live: true,
      }];

      const newToolCall: ToolCall = {
        name: data.name, tid, args: data.args || {}, snippet: '', done: false, preview: data.preview || '',
      };
      _sessionStates.set(sessionId, { ...s, messages: msgs, toolCalls: [...s.toolCalls, newToolCall] });
      _notifyImmediate(sessionId);
      break;
    }

    case 'tool_complete': {
      const tid = data.tid;
      if (!tid) return;
      const output = data.todos
        ? JSON.stringify({ todos: data.todos })
        : (data.output || data.preview || '');
      const msgs = _updateToolMessage(s.messages, tid, { content: output, _live: false });
      const toolCalls = s.toolCalls.map((tc) =>
        tc.tid === tid ? { ...tc, snippet: output.substring(0, 200), done: true, duration: data.duration, is_error: data.is_error } : tc
      );
      _sessionStates.set(sessionId, { ...s, messages: msgs, toolCalls });
      _notifyImmediate(sessionId);
      break;
    }

    case 'done': {
      _dismissCompressing(sessionId);
      _sessionStates.set(sessionId, {
        ...s,
        busy: false,
        streaming: false,
        completedAt: Date.now(),
        lastUsage: data.usage || null,
      });
      _notifyImmediate(sessionId);
      window.dispatchEvent(new CustomEvent('bg-stream-done', { detail: { sessionId, streamId: s.streamId } }));

      // Reload messages from backend for the active session.
      // Previously handled by the 'end_stream' notify event (now removed).
      // The replay buffer may not include the final assistant message;
      // loadSession ensures the UI has the complete persisted history.
      import('@/stores/useSessionStore').then(({ useSessionStore }) => {
        const currentSid = useSessionStore.getState().currentSessionId;
        if (currentSid === sessionId) {
          useSessionStore.getState().loadSession(sessionId).then(async () => {
            if (useSessionStore.getState().currentSessionId !== sessionId) return;
            const session = useSessionStore.getState().currentSession;
            if (session?.messages) {
              const { useChatStore } = await import('@/stores/useChatStore');
              useChatStore.getState().setMessages(session.messages);
              replaceSessionMessages(sessionId, session.messages);
            }
            if (session?.tool_calls) {
              const { useChatStore } = await import('@/stores/useChatStore');
              useChatStore.getState().setToolCalls(session.tool_calls);
            }
          });
        }
      });
      break;
    }

    // stream_end / stream_close removed — 'done' is the sole completion signal.
    // Subagent completion logic was dead code (subagent-complete/subagent-load-session
    // had no listeners).

    case 'apperror': {
      _dismissCompressing(sessionId);
      let errorMsg = data.message || 'Unknown error';
      if (data.type === 'rate_limit') errorMsg = `Rate limit reached: ${data.message}`;
      else if (data.type === 'no_response') errorMsg = 'No response received. Check your API key and model selection.';
      else { errorMsg = data.message; if (data.hint) errorMsg += `\n\n${data.hint}`; }
      window.dispatchEvent(new CustomEvent('app-error-card', { detail: { sessionId, message: errorMsg } }));
      _sessionStates.set(sessionId, {
        ...s, busy: false, streaming: false, completedAt: Date.now(), error: errorMsg,
      });
      _notifyImmediate(sessionId);
      break;
    }

    case 'error': {
      _dismissCompressing(sessionId);
      const errMsg = 'Connection lost';
      window.dispatchEvent(new CustomEvent('app-error-card', { detail: { sessionId, message: errMsg } }));
      _sessionStates.set(sessionId, {
        ...s, busy: false, streaming: false, completedAt: Date.now(), error: errMsg,
      });
      _notifyImmediate(sessionId);
      break;
    }

    case 'cancel': {
      _dismissCompressing(sessionId);
      _sessionStates.set(sessionId, {
        ...s, busy: false, streaming: false, completedAt: Date.now(),
        messages: [...s.messages, { role: 'assistant', content: '*Task cancelled.*', timestamp: Date.now() / 1000 }],
      });
      _notifyImmediate(sessionId);
      window.dispatchEvent(new CustomEvent('bg-stream-done', { detail: { sessionId, streamId: s.streamId } }));
      break;
    }

    case 'approval': {
      data._session_id = sessionId;
      window.dispatchEvent(new CustomEvent('approval-required', { detail: data }));
      break;
    }

    case 'clarify': {
      data._session_id = sessionId;
      window.dispatchEvent(new CustomEvent('clarify-required', { detail: data }));
      break;
    }

    case 'mode_changed': {
      window.dispatchEvent(new CustomEvent('mode-changed', { detail: data }));
      break;
    }

    case 'warning': {
      window.dispatchEvent(new CustomEvent('show-toast', { detail: { message: data.message, duration: 4000 } }));
      break;
    }

    case 'user_message': {
      const text = data.text || '';
      if (!text) return;
      const lastMsg = s.messages[s.messages.length - 1];
      if (lastMsg?.role === 'user' && lastMsg.content === text) return;
      const userMsg: Message = {
        role: 'user',
        content: text,
        timestamp__webui: data.ts || (Date.now() / 1000),
        _live: true,
      };
      _sessionStates.set(sessionId, { ...s, messages: [...s.messages, userMsg] });
      _notifyImmediate(sessionId);
      break;
    }

    case 'compressing': {
      const reason = data.reason ? ` (${data.reason})` : '';
      _compressingSessions.add(sessionId);
      import('@/components/dialogs/InfoCard').then(({ showInfoCard }) => {
        showInfoCard(sessionId, `Auto-compressing${reason}...`, {
          loading: true,
          noDismiss: true,
        });
      });
      break;
    }

    case 'compressed': {
      _dismissCompressing(sessionId);
      window.dispatchEvent(new CustomEvent('compressed-reload', { detail: { sessionId } }));
      break;
    }

    case 'title': {
      window.dispatchEvent(new CustomEvent('bg-stream-title', { detail: { sessionId, title: data.title } }));
      break;
    }

    case 'subagent_start': {
      // User message is now seeded by the backend at placeholder creation.
      // startSessionStream loads it via HTTP when begin_stream fires.
      // This handler is kept for future UI-only needs (e.g. title pulse).
      break;
    }

    case 'metering':
      // Metering events are for token usage tracking — silently ignore
      break;

    case 'phase':
      // Phase events indicate agent execution phases — silently ignore
      break;

    default:
      console.warn(`[StreamManager] Unknown replay event: ${event}`);
  }
}
