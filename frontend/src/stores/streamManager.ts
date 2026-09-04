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

/** Safety timeouts to auto-clear stuck busy state if done is never received */
const _safetyTimeouts = new Map<string, ReturnType<typeof setTimeout>>();

/** Sessions with a pending loadSession (interrupt mode) — startSessionStream
 *  waits for the load to complete before reading from useChatStore. */
const _pendingLoads = new Map<string, { promise: Promise<void>; resolve: () => void }>();

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

// ── Pending load tracking (interrupt mode) ─────────────────────────────────

/**
 * Signal that a loadSession is in progress for interrupt mode.
 * startSessionStream will wait for it to complete before reading from useChatStore.
 */
export function beginPendingLoad(sessionId: string): Promise<void> {
  let entry = _pendingLoads.get(sessionId);
  if (!entry) {
    let resolve: () => void;
    const promise = new Promise<void>((r) => { resolve = r; });
    entry = { promise, resolve: resolve! };
    _pendingLoads.set(sessionId, entry);
  }
  return entry.promise;
}

/** Signal that loadSession has completed — useChatStore is now fresh. */
export function endPendingLoad(sessionId: string): void {
  const entry = _pendingLoads.get(sessionId);
  if (entry) {
    _pendingLoads.delete(sessionId);
    entry.resolve();
  }
}

// ── submitStreamRequest ─────────────────────────────────────────────────────

/**
 * Pure HTTP request to start a chat stream. No frontend state changes.
 * All state initialization is handled by startSessionStream() via begin_stream notification.
 */
export async function submitStreamRequest(
  sessionId: string,
  message: string,
  model: string,
  workspace?: string,
  attachments?: string[],
  mode?: string,
  abortSignal?: AbortSignal,
  originalMsg?: string,
): Promise<any> {
  const { chatService } = await import('@/services/api');
  return chatService.startChat(sessionId, message, model, workspace, attachments, mode, abortSignal, originalMsg);
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
 * Reconnect to an existing backend stream after a page refresh.
 *
 * Unlike startSessionStream(), this does NOT start a new backend run —
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
  streamId: string,
  pendingUserMsg: string,
  existingMessages: Message[],
  pendingOriginalMsg?: string,
  pendingUserMsgTs?: number,
): Promise<void> {
  console.log(`[StreamManager] reconnectStream: session=${sessionId}`);

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

  console.log(`[StreamManager] reconnectStream setting seedMessages`, {
    seedMessagesCount: seedMessages.length,
    roles: seedMessages.map(m => m.role),
  });

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
export async function startSessionStream(sessionId: string, streamId?: string): Promise<void> {
  // If already streaming this session, skip
  const existing = _sessionStates.get(sessionId);
  if (existing?.streaming) {
    console.log(`[StreamManager] startSessionStream: already streaming session=${sessionId}`);
    return;
  }
  console.log(`[StreamManager] startSessionStream: session=${sessionId}`);

  // Wait for any pending loadSession (interrupt mode) to complete so
  // useChatStore has the authoritative baseline before we read it.
  const pendingLoad = _pendingLoads.get(sessionId);
  if (pendingLoad) {
    console.log(`[StreamManager] startSessionStream: waiting for pending loadSession session=${sessionId}`);
    await pendingLoad.promise;
  }

  // Use real backend stream ID if provided
  const effectiveStreamId = streamId || `session-${sessionId}`;
  // For the active session, always read from useChatStore — it has the latest
  // messages including the optimistic user message added by send().
  // For background sessions with existing state, preserve existing messages.
  // For background sessions without state, load from backend.
  let seedMessages: Message[];
  const { useSessionStore } = await import('@/stores/useSessionStore');
  const currentSid = useSessionStore.getState().currentSessionId;

  if (currentSid === sessionId) {
    // Active session: always read from useChatStore (has optimistic user message)
    seedMessages = useChatStore.getState().messages || [];
  } else if (existing?.messages?.length) {
    // Background session with existing state: preserve existing messages
    seedMessages = existing.messages;
  } else {
    // Background session, no existing state: load from backend
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

  _sessionStates.set(sessionId, {
    sessionId,
    streamId: effectiveStreamId,
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

  // Diagnostic timeout: log a warning if done is never received.
  // Does NOT auto-clear state — subagent sessions can legitimately run for minutes.
  // The clearSubscription fix (done/no_stream handlers) is the real fix.
  const _safetyTimeoutId = setTimeout(() => {
    const currentState = _sessionStates.get(sessionId);
    if (currentState?.busy && currentState?.streaming) {
      console.error(
        `[StreamManager] STILL BUSY after 2min: session=${sessionId} — done event may be lost`,
        { busy: currentState.busy, streaming: currentState.streaming, messagesCount: currentState.messages.length },
      );
    }
  }, 2 * 60 * 1000);
  // Store timeout ID so stopBackgroundStream can cancel it
  _safetyTimeouts.set(sessionId, _safetyTimeoutId);
}

/** Stop (cancel) the background stream for a session. Does NOT call backend cancel. */
export function stopBackgroundStream(sessionId: string): void {
  const s = _sessionStates.get(sessionId);
  if (!s) return;
  console.log(`[StreamManager] stopBackgroundStream: session=${sessionId}`);
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
  // Cancel safety timeout
  const safetyTimeout = _safetyTimeouts.get(sessionId);
  if (safetyTimeout) {
    clearTimeout(safetyTimeout);
    _safetyTimeouts.delete(sessionId);
  }
  // Mark as no longer streaming so the UI (ThinkingIndicator, sidebar pulse)
  // reflects the stopped state even if state persists for a while.
  _sessionStates.set(sessionId, { ...s, streaming: false, busy: false });
  _notify(sessionId);
}

/** Remove all state for a session (call after done event is handled by UI) */
export function clearSessionStream(sessionId: string): void {
  console.log(`[StreamManager] clearSessionStream: session=${sessionId}`);
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

  switch (event) {
    case 'replay_start':
      // Reset messages to baseline before replay
      // Keep the existing seed messages (from reconnectStream or startSessionStream)
      // instead of fetching from useChatStore which may be stale during page refresh
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
      console.warn(`[StreamManager] NO_STREAM for session=${sessionId} — backend stream already gone`);
      // Clear busy state so the UI doesn't stay stuck
      if (s.busy || s.streaming) {
        console.warn(`[StreamManager] no_stream: clearing stale busy state for session=${sessionId}`);
        _sessionStates.set(sessionId, {
          ...s,
          busy: false,
          streaming: false,
          completedAt: Date.now(),
        });
        _notifyImmediate(sessionId);
        // Also clear useChatStore.busy
        import('@/stores/useChatStore').then(({ useChatStore: chatStore }) => {
          if (chatStore.getState().busy) {
            chatStore.getState().setBusy(false);
          }
        });
      }
      // Reset subscription tracking — no stream exists, next begin_stream
      // needs a fresh subscribeReplay.
      import('@/stores/wsManager').then(({ wsManager }) => {
        wsManager.clearSubscription(sessionId);
      });
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
      // Always clear useChatStore.busy — even if StreamManager state was
      // already cleared by subscribe_success's stale-cleaning, the done event
      // means the backend has finished and the UI must not stay stuck.
      import('@/stores/useChatStore').then(({ useChatStore: chatStore }) => {
        if (chatStore.getState().busy) {
          chatStore.getState().setBusy(false);
        }
      });
      // Cancel safety timeout — done received successfully
      const doneSafetyTimeout = _safetyTimeouts.get(sessionId);
      if (doneSafetyTimeout) {
        clearTimeout(doneSafetyTimeout);
        _safetyTimeouts.delete(sessionId);
      }
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
          }).catch((err) => {
            console.error(`[StreamManager] DONE: loadSession FAILED for session=${sessionId}`, err);
          });
        }
      });
      // Reset frontend subscription tracking — backend stream is done,
      // the old subscription is dead (sentinel already unsubscribed the
      // backend side). Next begin_stream needs a fresh subscribeReplay.
      import('@/stores/wsManager').then(({ wsManager }) => {
        wsManager.clearSubscription(sessionId);
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
      // Reset subscription tracking — stream ended with error
      import('@/stores/wsManager').then(({ wsManager }) => {
        wsManager.clearSubscription(sessionId);
      });
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
      // Reset subscription tracking — stream ended with error
      import('@/stores/wsManager').then(({ wsManager }) => {
        wsManager.clearSubscription(sessionId);
      });
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

    case 'subagent':
      // Forwarded subagent events (status, etc.) not mapped to standard types — silently ignore
      break;

    default:
      console.warn(`[StreamManager] Unknown replay event: ${event}`);
  }
}
