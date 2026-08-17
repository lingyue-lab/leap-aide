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
  /** Number of messages before the current stream started (used to identify new messages for timestamp POST) */
  initialMessageCount: number;
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

// ── Compressing spinner tracking ──────────────────────────────────────────────
// Sessions currently showing the transient "Auto-compressing…" InfoCard (loading,
// noDismiss). Tracked so terminal SSE events (done/stream_end/error/apperror/cancel)
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

/**
 * Attach all SSE event listeners to the given EventSource.
 * Shared by startBackgroundStream() and reconnectStream() to avoid duplication.
 */
function _attachEventListeners(es: EventSource, sessionId: string, streamId: string): void {
  es.addEventListener('message_start', (e: MessageEvent) => {
    const s = _sessionStates.get(sessionId);
    if (!s) return;
    const data = JSON.parse(e.data);
    // Backend-provided timestamp (Unix seconds) for stable timestamps across replays.
    // Falls back to Date.now() for backward compatibility with older backends.
    const ts = data.ts ?? (Date.now() / 1000);
    const newMsg: Message = { role: 'assistant', content: '', timestamp__webui: ts, _live: true };
    _sessionStates.set(sessionId, { ...s, messages: [...s.messages, newMsg] });
    _notifyImmediate(sessionId);
  });

  es.addEventListener('token', (e: MessageEvent) => {
    const s = _sessionStates.get(sessionId);
    if (!s) return;
    const { text } = JSON.parse(e.data);
    const msgs = s.messages;
    const updated = _appendToLastAssistant(msgs, text);
    _sessionStates.set(sessionId, {
      ...s,
      messages: updated,
    });
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
    const data = JSON.parse(e.data) as { name: string; tid?: string; preview?: string; args?: Record<string, unknown>; ts?: number };
    const tid = data.tid || `live-${data.name}-${Date.now()}`;

    // ── Unified message_start: backend now emits message_start before
    //     every tool, so an assistant row always exists.  We just need
    //     to append the tool_use block and create the tool message.
    let msgs = s.messages;

    // Prefer the tool event's own timestamp (per-message granularity).
    // Fall back to inheriting from the last assistant for backward
    // compatibility with older backends that don't send ts in tool events.
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
  });

  es.addEventListener('phase', () => {
    // Kept as a no-op listener — the backend may still emit phase events,
    // but the frontend no longer tracks a phase state.
  });

  es.addEventListener('tool_complete', (e: MessageEvent) => {
    const s = _sessionStates.get(sessionId);
    if (!s) return;
    const data = JSON.parse(e.data) as {
      name: string;
      tid?: string;
      output?: string;
      preview?: string;
      duration?: number;
      is_error?: boolean;
      todos?: Array<{ id: string; content: string; status: string }>;
    };
    const tid = data.tid;
    if (!tid) return;
    // If the backend attached a full todos list (write_todo tool), use it as the
    // tool message content so the TodosDrawer can parse it immediately during streaming.
    const output = data.todos
      ? JSON.stringify({ todos: data.todos })
      : (data.output || data.preview || '');
    const msgs = _updateToolMessage(s.messages, tid, { content: output, _live: false });
    const toolCalls = s.toolCalls.map((tc) =>
      tc.tid === tid ? { ...tc, snippet: output.substring(0, 200), done: true, duration: data.duration, is_error: data.is_error } : tc
    );
    _sessionStates.set(sessionId, { ...s, messages: msgs, toolCalls });
    _notifyImmediate(sessionId);
  });

  es.addEventListener('compressing', (e: MessageEvent) => {
    const data = JSON.parse(e.data);
    const reason = data.reason ? ` (${data.reason})` : '';
    _compressingSessions.add(sessionId);
    import('@/components/dialogs/InfoCard').then(({ showInfoCard }) => {
      showInfoCard(sessionId, `Auto-compressing${reason}...`, {
        loading: true,
        noDismiss: true,
      });
    });
  });

  es.addEventListener('compressed', (_e: MessageEvent) => {
    // Dismiss the compressing InfoCard
    _dismissCompressing(sessionId);
    // Trigger a session reload so the frontend picks up the compressed
    // message list (includes the context compaction summary as a regular
    // message row).
    window.dispatchEvent(new CustomEvent('compressed-reload', { detail: { sessionId } }));
  });

  es.addEventListener('title', (e: MessageEvent) => {
    // Title updates are dispatched as a custom event so SessionStore can handle it
    const data = JSON.parse(e.data);
    window.dispatchEvent(new CustomEvent('bg-stream-title', { detail: { sessionId, title: data.title } }));
  });

  es.addEventListener('done', (e: MessageEvent) => {
    _dismissCompressing(sessionId);
    const s = _sessionStates.get(sessionId);
    if (!s) return;
    const data = JSON.parse(e.data);
    // Do NOT close the EventSource here — the backend may still send a `title`
    // event after `done` (async title generation). The EventSource will be
    // closed when `stream_end` arrives (which is always emitted after title).
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
    _dismissCompressing(sessionId);
    // Close the EventSource now that all post-done events (e.g. title) have been sent.
    // State (busy/streaming/completedAt) is already set by the 'done' handler;
    // no _notifyImmediate here — that would trigger a duplicate loadSession.
    es.close();
    _eventSources.delete(streamId);
  });

  es.addEventListener('apperror', (e: MessageEvent) => {
    _dismissCompressing(sessionId);
    const s = _sessionStates.get(sessionId);
    if (!s) return;
    const data = JSON.parse(e.data);
    es.close();
    _eventSources.delete(streamId);
    let errorMsg = data.message || 'Unknown error';
    if (data.type === 'rate_limit') errorMsg = `Rate limit reached: ${data.message}`;
    else if (data.type === 'no_response') errorMsg = 'No response received. Check your API key and model selection.';
    else { errorMsg = data.message; if (data.hint) errorMsg += `\n\n${data.hint}`; }
    // Show via InfoCard rather than appending an error message to conversation
    // history, keeping the chat clean.
    window.dispatchEvent(new CustomEvent('app-error-card', { detail: { sessionId, message: errorMsg } }));
    _sessionStates.set(sessionId, {
      ...s, busy: false, streaming: false, completedAt: Date.now(), error: errorMsg,
    });
    _notifyImmediate(sessionId);
  });

  es.addEventListener('error', () => {
    _dismissCompressing(sessionId);
    const s = _sessionStates.get(sessionId);
    if (!s) return;
    es.close();
    _eventSources.delete(streamId);
    const errMsg = 'Connection lost';
    window.dispatchEvent(new CustomEvent('app-error-card', { detail: { sessionId, message: errMsg } }));
    _sessionStates.set(sessionId, {
      ...s, busy: false, streaming: false, completedAt: Date.now(), error: errMsg,
    });
    _notifyImmediate(sessionId);
  });

  es.addEventListener('cancel', () => {
    _dismissCompressing(sessionId);
    const s = _sessionStates.get(sessionId);
    if (!s) return;
    es.close();
    _eventSources.delete(streamId);
    _sessionStates.set(sessionId, {
      ...s, busy: false, streaming: false, completedAt: Date.now(),
      messages: [...s.messages, { role: 'assistant', content: '*Task cancelled.*', timestamp: Date.now() / 1000 }],
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

  es.addEventListener('mode_changed', (e: MessageEvent) => {
    const data = JSON.parse(e.data);
    window.dispatchEvent(new CustomEvent('mode-changed', { detail: data }));
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
  _sessionStates.set(sessionId, {
    ...state,
    streamId,
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
  existingMessages: Message[],
  pendingOriginalMsg?: string,
  pendingUserMsgTs?: number,
): void {
  // Close any pre-existing stream/state for this session
  stopBackgroundStream(sessionId);

  // Build the seed: persisted history + the pending user message (optimistic UI).
  // replay_start will reset this to the same baseline before replaying history events.
  const userMsg: Message = {
    role: 'user',
    content: pendingUserMsg,
    timestamp__webui: pendingUserMsgTs ?? (Date.now() / 1000),  // fallback for older backends
    ...(pendingOriginalMsg ? { original_content__webui: pendingOriginalMsg } : {}),
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
    // Reconnect: all existing messages are already in the backend.
    initialMessageCount: existingMessages.length,
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
 *  so subsequent SSE events don't push stale pre-compression data back. */
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
