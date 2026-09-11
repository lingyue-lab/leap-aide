import { useRef, useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useChatStore } from '@/stores/useChatStore';
import { useSessionStore } from '@/stores/useSessionStore';
import { useModeStore } from '@/stores/useModeStore';
import { useChatStream } from '@/hooks/useChatStream';
import { useModeSync } from '@/hooks/useModeSync';
import * as StreamManager from '@/stores/streamManager';
import { chatService } from '@/services/api';
import ChatMessages from './ChatMessages';
import ChatComposer from './ChatComposer';
import QueueStatusBar from './QueueStatusBar';
import GoalStatusBar from './GoalStatusBar';
import ApprovalCard, { setApprovalCardCurrentSessionId } from '@/components/dialogs/ApprovalCard';
import ClarifyCard, { setClarifyCardCurrentSessionId } from '@/components/dialogs/ClarifyCard';
import BackgroundTaskCard, { setBgTaskCardCurrentSessionId } from '@/components/dialogs/BackgroundTaskCard';
import InfoCard, { setInfoCardCurrentSessionId, onInfoCardVisibleChange } from '@/components/dialogs/InfoCard';
import { onApprovalCardVisibleChange } from '@/components/dialogs/ApprovalCard';
import { onClarifyCardVisibleChange } from '@/components/dialogs/ClarifyCard';
import { onBackgroundTaskCardVisibleChange } from '@/components/dialogs/BackgroundTaskCard';
import { markSessionSeen } from '@/utils/sessionSeen';

interface ChatInterfaceProps {
  sessionId?: string | null;
}

export default function ChatInterface({ sessionId: propSessionId }: ChatInterfaceProps) {
  const { sessionId: routeSessionId } = useParams<{ sessionId: string }>();
  const sessionId = propSessionId || routeSessionId || null;

  // Track card visibility so composer hides when a card is shown
  const [infoVisible, setInfoVisible] = useState(false);
  const [bgVisible, setBgVisible] = useState(false);
  const [approvalVisible, setApprovalVisible] = useState(false);
  const [clarifyVisible, setClarifyVisible] = useState(false);
  const cardVisible = infoVisible || bgVisible || approvalVisible || clarifyVisible;
  useEffect(() => {
    const unsubInfo = onInfoCardVisibleChange(setInfoVisible);
    const unsubBg = onBackgroundTaskCardVisibleChange(setBgVisible);
    const unsubApproval = onApprovalCardVisibleChange(setApprovalVisible);
    const unsubClarify = onClarifyCardVisibleChange(setClarifyVisible);
    return () => { unsubInfo(); unsubBg(); unsubApproval(); unsubClarify(); };
  }, []);

  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const prevSessionIdRef = useRef<string | null>(null);
  const { currentSession, loadSession, selectSession, loading: sessionsLoading } = useSessionStore();
  const [sessionLoading, setSessionLoading] = useState(false);
  // Listen for clear-history operation from sidebar
  useEffect(() => {
    const onClear = () => { setSessionLoading(true); window.dispatchEvent(new CustomEvent('hermes:session-loading', { detail: true })); };
    const onClearDone = () => { setSessionLoading(false); window.dispatchEvent(new CustomEvent('hermes:session-loading', { detail: false })); };
    window.addEventListener('hermes:clear-history', onClear);
    window.addEventListener('hermes:clear-history-done', onClearDone);
    return () => {
      window.removeEventListener('hermes:clear-history', onClear);
      window.removeEventListener('hermes:clear-history-done', onClearDone);
    };
  }, []);
  const { messages, activeStreamId, canceling, setMessages, setToolCalls } = useChatStore();
  const { startStream, cancelStream } = useChatStream();
  // Use chatStore.activeStreamId for current session (set immediately by StreamManager),
  // not sessionStore.active_stream_id (set via sessions_changed SSE, may lag).
  const streaming = !!activeStreamId;
  const busy = streaming;
  useModeSync(sessionId ?? null);

  // ── Queue consistency cleanup ─────────────────────────────────────────────
  // When the agent goes idle, check that queuedMessages is in sync with
  // actual ghost messages.  Runs twice: once synchronously (handles most
  // cases) and once deferred via setTimeout(0) to catch race conditions
  // where a pending loadSession callback re-adds ghosts after the sync check.
  const prevBusyRef = useRef(busy);
  useEffect(() => {
    const wasBusy = prevBusyRef.current;
    prevBusyRef.current = busy;
    // Only act on busy→idle transition
    if (!wasBusy || busy) return;

    const runCleanup = () => {
      const state = useChatStore.getState();
      const ghosts = state.messages.filter(m => m._queued);
      if (state.queuedMessages.length !== ghosts.length) {
        if (ghosts.length === 0) {
          state.clearQueuedMessages();
        } else {
          state.setMessages(state.messages.filter(m => !m._queued));
          state.clearQueuedMessages();
        }
      }
    };

    // Synchronous pass
    runCleanup();

    // Deferred pass — runs after all microtasks (loadSession callbacks) settle
    const timer = setTimeout(() => runCleanup(), 0);
    return () => clearTimeout(timer);
  }, [busy]);  // eslint-disable-line react-hooks/exhaustive-deps

  // ── Session switch logic ────────────────────────────────────────────────────
  useEffect(() => {
    const prevId = prevSessionIdRef.current;
    prevSessionIdRef.current = sessionId;

    if (!sessionId) return;
    if (prevId === sessionId) return;

    console.log(`[ChatInterface] session switch`, { from: prevId, to: sessionId });

    // Ensure WS is alive so stream reconnection and session notifications work.
    import('@/stores/wsManager').then(({ wsManager }) => wsManager.ensureConnected());

    // Notify MainContent FIRST (synchronous DOM manipulation) to show overlay
    // before React state updates cause messages to clear.
    window.dispatchEvent(new CustomEvent('hermes:session-loading', { detail: true }));
    setSessionLoading(true);

    // Ensure session store's currentSessionId is in sync. This is critical when
    // sessionId comes from route params (/session/:id) — the store starts with
    // currentSessionId=null and loadSession's stale-guard would never set it.
    if (useSessionStore.getState().currentSessionId !== sessionId) {
      selectSession(sessionId);
    }

    // Tell the store which session is "current" so updateSessionCache knows
    // whether to update live state or just the cache.
    useChatStore.getState().setCurrentSessionId(sessionId);

    // Notify card components of session change so they freeze/restore per-session state.
    setInfoCardCurrentSessionId(sessionId ?? null);
    setBgTaskCardCurrentSessionId(sessionId ?? null);
    setApprovalCardCurrentSessionId(sessionId ?? null);
    setClarifyCardCurrentSessionId(sessionId ?? null);

    // ── Switching AWAY from prevId ──────────────────────────────────────────
    if (prevId && prevId !== sessionId) {
      // Save whatever is currently displayed into the cache for prevId.
      // If prevId has a live background stream its cache will keep being
      // updated by useChatStream's StreamManager subscriber — so when we
      // come back we'll see the latest tokens automatically.
      useChatStore.getState().saveToCache(prevId);

      // If the previous session was in the middle of a cancel, reset the
      // flag so the new session doesn't show the spinner overlay.
      useChatStore.getState().setCanceling(false);

      // NOTE: we do NOT cancel the stream here. The backend stream for prevId
      // keeps running in the background. StreamManager continues to accumulate
      // tokens into the cache slot for prevId.
    }

    // ── Helper: refresh session data from backend and reconnect to any active stream ──
    const refreshFromBackend = async (sid: string) => {
      console.log(`[ChatInterface] refreshFromBackend called`, { sid });

      // Guard against stale responses: if the user switched sessions while this
      // loadSession was in flight, its late response must not overwrite the
      // current session's messages / usage / stream reconnection.
      if (useSessionStore.getState().currentSessionId !== sid) {
        console.log(`[ChatInterface] refreshFromBackend: session changed, skipping`);
        return;
      }
      const session = useSessionStore.getState().currentSession;
      if (!session) {
        console.log(`[ChatInterface] refreshFromBackend: no session`);
        setSessionLoading(false);
        window.dispatchEvent(new CustomEvent('hermes:session-loading', { detail: false }));
        return;
      }

      console.log(`[ChatInterface] refreshFromBackend: session loaded`, {
        sessionId: session.session_id,
        messagesCount: session.messages?.length,
        activeStreamId: session.active_stream_id,
        hasPendingMsg: !!session.pending_user_message,
      });

      // Merge pending_user_message into messages before display.
      // During streaming, run_conversation has not yet added the user message
      // to session.messages — it lives only in pending_user_message.
      // Without this merge, a force-refresh would briefly show stale messages
      // without the current user bubble.
      const msgs = session.messages ? [...session.messages] : [];
      const pendingMsg = session.pending_user_message;
      const pendingOriginalMsg = session.pending_original_message ?? undefined;
      const pendingUserMsgTs = session.pending_user_message_ts ?? undefined;
      if (pendingMsg) {
        const last = msgs[msgs.length - 1];
        if (!(last?.role === 'user' && typeof last.content === 'string' && last.content === pendingMsg)) {
          msgs.push({
            role: 'user',
            content: pendingMsg,
            timestamp__webui: pendingUserMsgTs ?? (Date.now() / 1000),
            ...(pendingOriginalMsg ? { original_content__webui: pendingOriginalMsg } : {}),
          });
        }
      }
      if (msgs.length) setMessages(msgs);
      if (session.tool_calls) setToolCalls(session.tool_calls);
      setSessionLoading(false);
      window.dispatchEvent(new CustomEvent('hermes:session-loading', { detail: false }));

      // Restore context usage from persisted compressor data.
      if (session.context_length || session.last_prompt_tokens) {
        useChatStore.getState().setLastUsage({
          context_length: session.context_length || 0,
          last_prompt_tokens: session.last_prompt_tokens || 0,
          threshold_tokens: session.threshold_tokens || 0,
          input_tokens: session.input_tokens,
          output_tokens: session.output_tokens,
          estimated_cost: session.estimated_cost,
        });
      }

      // If there's an active backend stream for this session (e.g. started by a
      // CLI script or subagent), re-attach to it so the browser sees tokens in real time.
      const activeStreamId = session.active_stream_id;

      console.log(`[ChatInterface] refreshFromBackend: checking stream`, {
        activeStreamId,
        hasPendingMsg: !!pendingMsg,
      });

      // Reconnect if there's an active stream — even without pendingMsg.
      // Subagent sessions have active streams but no pendingMsg (the parent
      // agent drives the conversation, not a user message).
      if (activeStreamId) {
        try {
          const status = await chatService.checkStreamStatus(activeStreamId);
          console.log(`[ChatInterface] refreshFromBackend: stream status`, { active: status.active });

          if (status.active) {
            console.log(`[ChatInterface] refreshFromBackend: calling reconnectStream`, {
              sid,
              activeStreamId,
              messagesCount: (session.messages ?? []).length,
            });
            StreamManager.reconnectStream(sid, activeStreamId, pendingMsg || '', session.messages ?? [], pendingOriginalMsg, pendingUserMsgTs);
            // StreamManager.reconnectStream sets busy=true internally.
            // useIsBusy() will pick it up via the subscriber.
          }
        } catch (err) {
          console.error(`[ChatInterface] refreshFromBackend: stream check failed`, err);
          /* network error — ignore, already showing persisted messages */
        }
      }
    };

    // ── Switching TO sessionId ──────────────────────────────────────────────

    // 1. Check if there is a live background stream for this session.
    const bgState = StreamManager.getSessionStreamState(sessionId);
    if (bgState) {
      // Restore from the most up-to-date StreamManager state (not the older cache).
      // Merge ghost messages (queued/steered) from the cache so they survive
      // session switches — StreamManager state lacks frontend-only markers.
      let messages = bgState.messages;
      let queuedMessages = undefined;
      const cached = (useChatStore.getState() as any)._sessionCache?.get(sessionId);
      if (cached) {
        const cachedGhosts = (cached.messages || []).filter((m: any) => m._queued || m._steered);
        if (cachedGhosts.length > 0) {
          messages = [...messages, ...cachedGhosts];
          queuedMessages = cached.queuedMessages;
        }
      }
      useChatStore.setState({
        messages,
        toolCalls: bgState.toolCalls,
        // busy is NOT set here — useIsBusy() reads directly from
        // StreamManager, so the paper plane / pulse dot always reflect
        // the authoritative streaming state.
        activeStreamId: bgState.streaming ? bgState.streamId : null,
        ...(queuedMessages !== undefined ? { queuedMessages } : {}),
      });
      setSessionLoading(false);
      window.dispatchEvent(new CustomEvent('hermes:session-loading', { detail: false }));
      // Load session from backend. When the stream is no longer actively
      // streaming (done/completed), also refresh messages from state.db —
      // the SSE replay may not include the final assistant message (e.g.
      // subagent.complete closes the stream before the last message is sent).
      if (bgState.streaming) {
        loadSession(sessionId);
      } else {
        loadSession(sessionId).then(() => refreshFromBackend(sessionId));
      }
      return;
    }

    // 2. Try restoring from cache for instant display, then refresh from backend.
    const cached = useChatStore.getState().restoreFromCache(sessionId);
    if (cached) {
      setSessionLoading(false);
      window.dispatchEvent(new CustomEvent('hermes:session-loading', { detail: false }));
      loadSession(sessionId).then(() => refreshFromBackend(sessionId));
      return;
    }

    // 3. No cache at all — clean load from backend.
    useChatStore.getState().reset();
    loadSession(sessionId).then(() => refreshFromBackend(sessionId));
  }, [sessionId, loadSession, setMessages, setToolCalls]);

  // ── Mode loading ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!sessionId || sessionId.includes('/')) return;
    useModeStore.getState().loadMode(sessionId);
  }, [sessionId]);

  // ── Auto-scroll (only when user is at the bottom) ────────────────────────
  const isAtBottomRef = useRef(true);

  const scrollToBottom = useCallback(() => {
    const el = scrollContainerRef.current;
    if (el) {
      requestAnimationFrame(() => {
        el.scrollTop = el.scrollHeight;
      });
      isAtBottomRef.current = true;
    }
  }, []);

  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    // Consider "at bottom" if within 40px of the true bottom
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 40;
    isAtBottomRef.current = atBottom;
    // User scrolled to bottom → mark session as seen
    if (atBottom && sessionId) {
      markSessionSeen(sessionId);
    }
  }, [sessionId]);

  useEffect(() => {
    if (isAtBottomRef.current) {
      scrollToBottom();
    }
  }, [messages.length, scrollToBottom]);

  // ── Auto-read: mark session as seen after 5 seconds of viewing ──────────
  useEffect(() => {
    if (!sessionId) return;
    const timer = setTimeout(() => {
      markSessionSeen(sessionId);
    }, 5000);
    return () => clearTimeout(timer);
  }, [sessionId]);

  // ── Send / Cancel ────────────────────────────────────────────────────────────
  const handleSend = useCallback(async (text: string, attachments?: string[], mode?: string) => {
    if (!sessionId || !text.trim()) return;
    const model = currentSession?.model || 'anthropic/claude-sonnet-4-5';
    await startStream(sessionId, text, model, currentSession?.workspace, attachments, mode);
    scrollToBottom();
  }, [sessionId, currentSession, startStream, scrollToBottom]);

  const handleCancel = useCallback(() => {
    if (activeStreamId) {
      cancelStream(activeStreamId);
    }
  }, [activeStreamId, cancelStream]);

  // Notify MainContent about initial session-list loading state
  useEffect(() => {
    if (!sessionId) {
      window.dispatchEvent(new CustomEvent('hermes:session-loading', { detail: sessionsLoading }));
    }
  }, [sessionId, sessionsLoading]);

  if (!sessionId) {
    // No active session: MainContent owns the empty presentation via
    // <WelcomeState/>.  Returning null here keeps ChatInterface from
    // drawing a duplicate empty icon — welcome state replaces the old
    // chat-panel EmptyState entirely.
    return null;
  }

  return (
    <div className="chat-interface" onClick={() => sessionId && markSessionSeen(sessionId)}>
      <ChatMessages
        ref={scrollContainerRef}
        messages={messages}
        streaming={streaming}
        thinkingActive={streaming && !cardVisible}
        onScroll={handleScroll}
      />

      {/* Suppress approval/clarify/background-task cards during cancel.
          InfoCard is intentionally NOT suppressed — it's used for the
          cancel spinner ("取消中..."). */}
      {!canceling && <ApprovalCard />}
      {!canceling && <ClarifyCard />}
      {!canceling && <BackgroundTaskCard />}
      <InfoCard />

      {!sessionLoading && !currentSession?._readonly && !currentSession?.is_subagent_session && (
        <>
          <QueueStatusBar sessionId={sessionId} />
          <GoalStatusBar sessionId={sessionId} />
          <ChatComposer
            onSend={handleSend}
            onCancel={handleCancel}
            cardVisible={cardVisible}
          />
        </>
      )}

    </div>
  );
}

