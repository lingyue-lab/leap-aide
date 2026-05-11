/**
 * Global StreamManager
 *
 * Manages multiple background SSE (EventSource) connections so that switching
 * sessions does NOT cancel an in-progress stream.  The agent keeps running on
 * the backend; this module accumulates incoming events into a per-session
 * message buffer and notifies subscribers when the buffer changes.
 *
 * Key design:
 *  - One EventSource per active stream (keyed by stream_id).
 *  - All SSE events are written into a per-session SessionStreamState.
 *  - Components subscribe to changes via the `subscribe` / `unsubscribe` API.
 *  - ChatInterface restores from the buffer when switching back to a session.
 *  - reconnectStream() allows re-attaching to an existing backend stream after
 *    a page refresh, using the stream_id returned by GET /api/session.
 */

import type { Message, ToolCall, UsageInfo, ContentBlock } from '@/types';
import { chatService } from '@/services/api';

// ── Types ────────────────────────────────────────────────────────────────────

export interface SessionStreamState {
  sessionId: string;
  streamId: string;
  messages: Message[];
  toolCalls: ToolCall[];
  busy: boolean;
  lastUsage: UsageInfo | null;
  /** True while the SSE connection is open */
  streaming: boolean;
  /** Non-null when streaming finished (completed/error/cancel) */
  completedAt: number | null;
  /** Error message if stream ended with an error */
  error: string | null;
}

type ChangeListener = (sessionId: string, state: SessionStreamState) => void;

// ── Module-level state (singleton) ───────────────────────────────────────────

/** Active SSE connections: stream_id → EventSource */
const _eventSources = new Map<string, EventSource>();

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

function _updateToolMessage(messages: Message[], toolCallId: string, updates: Partial<Message>): Message[] {
  return messages.map((m) =>
    m.role === 'tool' && m.tool_call_id === toolCallId ? { ...m, ...updates } : m
  );
}

/**
 * Attach all SSE event listeners to the given EventSource.
 * Shared by startBackgroundStream() and reconnectStream() to avoid duplication.
 */
function _attachEventListeners(es: EventSource, sessionId: string, streamId: string): void {
  es.addEventListener('message_start', () => {
    const s = _sessionStates.get(sessionId);
    if (!s) return;
    const newMsg: Message = { role: 'assistant', content: '', _ts: Date.now() / 1000, _live: true };
    _sessionStates.set(sessionId, { ...s, messages: [...s.messages, newMsg] });
    _notifyImmediate(sessionId);
  });

  es.addEventListener('token', (e: MessageEvent) => {
    const s = _sessionStates.get(sessionId);
    if (!s) return;
    const { text } = JSON.parse(e.data);
    _sessionStates.set(sessionId, { ...s, messages: _appendToLastAssistant(s.messages, text) });
    _notifyThrottled(sessionId);
  });

  es.addEventListener('reasoning', (e: MessageEvent) => {
    const s = _sessionStates.get(sessionId);
    if (!s) return;
    const { text } = JSON.parse(e.data);
    _sessionStates.set(sessionId, { ...s, messages: _appendReasoningToLast(s.messages, text) });
    _notifyThrottled(sessionId);
  });

  es.addEventListener('tool', (e: MessageEvent) => {
    const s = _sessionStates.get(sessionId);
    if (!s) return;
    const data = JSON.parse(e.data) as { name: string; tid?: string; preview?: string; args?: Record<string, unknown> };
    if (data.name === 'clarify') return;

    const tid = data.tid || `live-${data.name}-${Date.now()}`;

    // Ensure assistant message exists
    let msgs = s.messages;
    if (!msgs.some((m) => m.role === 'assistant')) {
      msgs = [...msgs, { role: 'assistant', content: '', _ts: Date.now() / 1000, _live: true }];
    }

    const toolUseBlock: ContentBlock = {
      type: 'tool_use', id: tid, name: data.name, input: data.args || {},
    };
    msgs = _appendContentBlock(msgs, toolUseBlock);
    msgs = [...msgs, {
      role: 'tool', content: '', tool_call_id: tid, tool_name: data.name,
      _ts: Date.now() / 1000, _live: true,
    }];

    const newToolCall: ToolCall = {
      name: data.name, tid, args: data.args || {}, snippet: '', done: false, preview: data.preview || '',
    };
    _sessionStates.set(sessionId, { ...s, messages: msgs, toolCalls: [...s.toolCalls, newToolCall] });
    _notifyImmediate(sessionId);
  });

  es.addEventListener('tool_complete', (e: MessageEvent) => {
    const s = _sessionStates.get(sessionId);
    if (!s) return;
    const data = JSON.parse(e.data) as { name: string; tid?: string; output?: string; preview?: string; duration?: number; is_error?: boolean };
    if (data.name === 'clarify') return;
    const tid = data.tid;
    if (!tid) return;
    const output = data.output || data.preview || '';
    const msgs = _updateToolMessage(s.messages, tid, { content: output, _live: false });
    const toolCalls = s.toolCalls.map((tc) =>
      tc.tid === tid ? { ...tc, snippet: output.substring(0, 200), done: true, duration: data.duration, is_error: data.is_error } : tc
    );
    _sessionStates.set(sessionId, { ...s, messages: msgs, toolCalls });
    _notifyImmediate(sessionId);
  });

  es.addEventListener('compressed', (e: MessageEvent) => {
    const s = _sessionStates.get(sessionId);
    if (!s) return;
    const data = JSON.parse(e.data);
    const msg: Message = {
      role: 'assistant',
      content: `*[${data.message || 'Context compressed'}]*`,
      _ts: Date.now() / 1000,
    };
    _sessionStates.set(sessionId, { ...s, messages: [...s.messages, msg] });
    _notifyImmediate(sessionId);
  });

  es.addEventListener('title', (e: MessageEvent) => {
    // Title updates are dispatched as a custom event so SessionStore can handle it
    const data = JSON.parse(e.data);
    window.dispatchEvent(new CustomEvent('bg-stream-title', { detail: { sessionId, title: data.title } }));
  });

  es.addEventListener('done', (e: MessageEvent) => {
    const s = _sessionStates.get(sessionId);
    if (!s) return;
    const data = JSON.parse(e.data);
    es.close();
    _eventSources.delete(streamId);
    _sessionStates.set(sessionId, {
      ...s,
      busy: false,
      streaming: false,
      completedAt: Date.now(),
      lastUsage: data.usage || null,
    });
    _notifyImmediate(sessionId);
    // Notify UI that this session's stream is done (for sound / badge)
    window.dispatchEvent(new CustomEvent('bg-stream-done', { detail: { sessionId, streamId } }));
  });

  es.addEventListener('stream_end', () => {
    es.close();
    _eventSources.delete(streamId);
    const s = _sessionStates.get(sessionId);
    if (s) {
      _sessionStates.set(sessionId, { ...s, streaming: false, busy: false });
      _notifyImmediate(sessionId);
    }
  });

  es.addEventListener('apperror', (e: MessageEvent) => {
    const s = _sessionStates.get(sessionId);
    if (!s) return;
    const data = JSON.parse(e.data);
    es.close();
    _eventSources.delete(streamId);
    let errorMsg = data.message || 'Unknown error';
    if (data.type === 'rate_limit') errorMsg = `**Rate limit reached:** ${data.message}`;
    else if (data.type === 'no_response') errorMsg = '**No response received.** Check your API key and model selection.';
    else { errorMsg = `**Error:** ${data.message}`; if (data.hint) errorMsg += `\n\n*${data.hint}*`; }
    const errMsgObj: Message = { role: 'assistant', content: errorMsg, _ts: Date.now() / 1000 };
    _sessionStates.set(sessionId, {
      ...s, busy: false, streaming: false, completedAt: Date.now(), error: errorMsg,
      messages: [...s.messages, errMsgObj],
    });
    _notifyImmediate(sessionId);
  });

  es.addEventListener('error', () => {
    const s = _sessionStates.get(sessionId);
    if (!s) return;
    es.close();
    _eventSources.delete(streamId);
    const errMsg = '**Error:** Connection lost';
    _sessionStates.set(sessionId, {
      ...s, busy: false, streaming: false, completedAt: Date.now(), error: errMsg,
      messages: [...s.messages, { role: 'assistant', content: errMsg, _ts: Date.now() / 1000 }],
    });
    _notifyImmediate(sessionId);
  });

  es.addEventListener('cancel', () => {
    const s = _sessionStates.get(sessionId);
    if (!s) return;
    es.close();
    _eventSources.delete(streamId);
    _sessionStates.set(sessionId, {
      ...s, busy: false, streaming: false, completedAt: Date.now(),
      messages: [...s.messages, { role: 'assistant', content: '*Task cancelled.*', _ts: Date.now() / 1000 }],
    });
    _notifyImmediate(sessionId);
    window.dispatchEvent(new CustomEvent('bg-stream-done', { detail: { sessionId, streamId } }));
  });

  es.addEventListener('approval', (e: MessageEvent) => {
    const data = JSON.parse(e.data);
    data._session_id = sessionId;
    window.dispatchEvent(new CustomEvent('approval-required', { detail: data }));
  });

  es.addEventListener('clarify', (e: MessageEvent) => {
    const data = JSON.parse(e.data);
    data._session_id = sessionId;
    window.dispatchEvent(new CustomEvent('clarify-required', { detail: data }));
  });

  es.addEventListener('warning', (e: MessageEvent) => {
    const data = JSON.parse(e.data);
    window.dispatchEvent(new CustomEvent('show-toast', { detail: { message: data.message, duration: 4000 } }));
  });
}

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Start a background stream for sessionId.
 * initialMessages should be the current chat history BEFORE the new user message is added.
 */
export function startBackgroundStream(
  sessionId: string,
  streamId: string,
  userMessage: Message,
  initialMessages: Message[]
): void {
  // Close any previous stream for this session
  stopBackgroundStream(sessionId);

  // Seed the state with existing history + the new user message
  const seedMessages = [...initialMessages, userMessage];
  const state = _getOrCreateState(sessionId, streamId, seedMessages);
  _sessionStates.set(sessionId, { ...state, streamId, busy: true, streaming: true, completedAt: null, error: null, messages: seedMessages, toolCalls: [] });

  // Open SSE connection
  const es = chatService.createSSEConnection(streamId);
  _eventSources.set(streamId, es);

  // Attach all event handlers (shared logic)
  _attachEventListeners(es, sessionId, streamId);

  _notify(sessionId);
}

/**
 * Reconnect to an existing backend stream after a page refresh.
 *
 * Unlike startBackgroundStream(), this does NOT start a new backend run —
 * it uses the /api/chat/stream/replay endpoint which:
 *   1. Sends a 'replay_start' event with the count of historical events
 *   2. Replays all past events (token, tool, tool_complete, etc.) in order
 *   3. Sends a 'replay_end' event to mark the boundary
 *   4. Continues with any new live events from here on
 *
 * This means the user sees all tokens produced before the page refresh,
 * seamlessly followed by any new tokens still arriving.
 *
 * Call this when GET /api/session returns a non-null active_stream_id AND
 * GET /api/chat/stream/status confirms that stream is still alive.
 *
 * @param sessionId         The session being reconnected.
 * @param streamId          The stream_id from session.active_stream_id.
 * @param pendingUserMsg    The user message text that triggered the stream.
 * @param existingMessages  Messages already persisted in the DB (history before the pending turn).
 */
export function reconnectStream(
  sessionId: string,
  streamId: string,
  pendingUserMsg: string,
  existingMessages: Message[]
): void {
  // Close any pre-existing stream/state for this session
  stopBackgroundStream(sessionId);

  // Build the seed: persisted history + the pending user message (optimistic UI).
  // replay_start will reset this to the same baseline before replaying history events.
  const userMsg: Message = {
    role: 'user',
    content: pendingUserMsg,
    _ts: Date.now() / 1000,
  };
  const seedMessages = [...existingMessages, userMsg];

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
  });

  // Use the replay endpoint so we get all past events before live ones
  const es = chatService.createReplaySSEConnection(streamId);
  _eventSources.set(streamId, es);

  // replay_start: reset messages back to baseline so replayed events rebuild them cleanly
  es.addEventListener('replay_start', () => {
    const s = _sessionStates.get(sessionId);
    if (!s) return;
    _sessionStates.set(sessionId, {
      ...s,
      messages: [...existingMessages, userMsg],
      toolCalls: [],
    });
    _notifyImmediate(sessionId);
  });

  // replay_end: boundary marker between replayed and live events — no state change needed
  es.addEventListener('replay_end', () => {
    // Could surface a visual indicator here if desired
  });

  // All regular SSE events (message_start, token, tool, done, error, etc.) are handled
  // identically whether they are replayed or live — the frontend just renders them.
  _attachEventListeners(es, sessionId, streamId);

  _notify(sessionId);
}

/** Stop (cancel) the background stream for a session. Does NOT call backend cancel. */
export function stopBackgroundStream(sessionId: string): void {
  const s = _sessionStates.get(sessionId);
  if (!s) return;
  const es = _eventSources.get(s.streamId);
  if (es) {
    es.close();
    _eventSources.delete(s.streamId);
  }
}

/** Remove all state for a session (call after done event is handled by UI) */
export function clearSessionStream(sessionId: string): void {
  stopBackgroundStream(sessionId);
  _sessionStates.delete(sessionId);
  // Cancel any pending throttled notification before immediate notify
  const pending = _pendingNotifies.get(sessionId);
  if (pending) {
    clearTimeout(pending);
    _pendingNotifies.delete(sessionId);
  }
  _notify(sessionId);
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

/** Subscribe to state changes for any session */
export function subscribe(listener: ChangeListener): () => void {
  _listeners.add(listener);
  return () => _listeners.delete(listener);
}

/** Get all session IDs that currently have a stream state (active or recently completed) */
export function getActiveSessionIds(): string[] {
  return Array.from(_sessionStates.keys());
}
