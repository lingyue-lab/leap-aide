import { useRef, useCallback, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { useChatStore } from '@/stores/useChatStore';
import { useSessionStore } from '@/stores/useSessionStore';
import { useModeStore } from '@/stores/useModeStore';
import { useChatStream } from '@/hooks/useChatStream';
import { useModeSync } from '@/hooks/useModeSync';
import { useSessionPolling } from '@/hooks/useSessionPolling';
import * as StreamManager from '@/stores/streamManager';
import { chatService } from '@/services/api';
import ChatMessages from './ChatMessages';
import ChatComposer from './ChatComposer';
import QueueStatusBar from './QueueStatusBar';
import ApprovalCard from '@/components/dialogs/ApprovalCard';
import ClarifyCard from '@/components/dialogs/ClarifyCard';
import { useTranslation } from 'react-i18next';

interface ChatInterfaceProps {
  sessionId?: string | null;
}

export default function ChatInterface({ sessionId: propSessionId }: ChatInterfaceProps) {
  const { sessionId: routeSessionId } = useParams<{ sessionId: string }>();
  const sessionId = propSessionId || routeSessionId || null;
  
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const prevSessionIdRef = useRef<string | null>(null);
  const { currentSession, loadSession } = useSessionStore();
  const { messages, busy, activeStreamId, setMessages, setToolCalls } = useChatStore();
  const { startStream, cancelStream, streaming } = useChatStream();
  useModeSync(sessionId ?? null);
  useSessionPolling(sessionId ?? null);

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

    // Tell the store which session is "current" so updateSessionCache knows
    // whether to update live state or just the cache.
    useChatStore.getState().setCurrentSessionId(sessionId);

    // ── Switching AWAY from prevId ──────────────────────────────────────────
    if (prevId && prevId !== sessionId) {
      // Save whatever is currently displayed into the cache for prevId.
      // If prevId has a live background stream its cache will keep being
      // updated by useChatStream's StreamManager subscriber — so when we
      // come back we'll see the latest tokens automatically.
      useChatStore.getState().saveToCache(prevId);

      // NOTE: we do NOT cancel the stream here. The backend stream for prevId
      // keeps running in the background. StreamManager continues to accumulate
      // tokens into the cache slot for prevId.
    }

    // ── Helper: refresh session data from backend and reconnect to any active stream ──
    const refreshFromBackend = async (sid: string) => {
      const session = useSessionStore.getState().currentSession;
      if (!session) return;
      if (session.messages) setMessages(session.messages);
      if (session.tool_calls) setToolCalls(session.tool_calls);

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
      // CLI script), re-attach to it so the browser sees tokens in real time.
      const activeStreamId = session.active_stream_id;
      const pendingMsg = session.pending_user_message;
      if (activeStreamId && pendingMsg) {
        try {
          const status = await chatService.checkStreamStatus(activeStreamId);
          if (status.active) {
            StreamManager.reconnectStream(sid, activeStreamId, pendingMsg, session.messages ?? []);
            useChatStore.setState({ busy: true, activeStreamId });
          }
        } catch { /* network error — ignore, already showing persisted messages */ }
      }
    };

    // ── Switching TO sessionId ──────────────────────────────────────────────

    // 1. Check if there is a live background stream for this session.
    const bgState = StreamManager.getSessionStreamState(sessionId);
    if (bgState) {
      // Restore from the most up-to-date StreamManager state (not the older cache).
      useChatStore.setState({
        messages: bgState.messages,
        toolCalls: bgState.toolCalls,
        busy: bgState.busy,
        activeStreamId: bgState.streaming ? bgState.streamId : null,
      });
      // Still load session metadata (model, workspace, etc.) in background.
      loadSession(sessionId);
      return;
    }

    // 2. Try restoring from cache for instant display, then refresh from backend.
    const cached = useChatStore.getState().restoreFromCache(sessionId);
    if (cached) {
      loadSession(sessionId).then(() => refreshFromBackend(sessionId));
      return;
    }

    // 3. No cache at all — clean load from backend.
    useChatStore.getState().reset();
    loadSession(sessionId).then(() => refreshFromBackend(sessionId));
  }, [sessionId, loadSession, setMessages, setToolCalls]);

  // ── Mode loading ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!sessionId) return;
    useModeStore.getState().loadMode(sessionId);
  }, [sessionId]);

  // ── Auto-scroll ─────────────────────────────────────────────────────────────
  const scrollToBottom = useCallback(() => {
    if (scrollContainerRef.current) {
      scrollContainerRef.current.scrollTop = scrollContainerRef.current.scrollHeight;
    }
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages.length, scrollToBottom]);

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

  if (!sessionId) {
    return <EmptyState />;
  }

  return (
    <div className="chat-interface">
      <ChatMessages
        ref={scrollContainerRef}
        messages={messages}
        streaming={streaming}
      />

      {!currentSession?._readonly && (
        <>
          <QueueStatusBar sessionId={sessionId} />
          <ChatComposer
            onSend={handleSend}
            onCancel={handleCancel}
            busy={busy}
            streaming={streaming}
          />
        </>
      )}
      
      <ApprovalCard />
      <ClarifyCard />
    </div>
  );
}

function EmptyState() {
  const { createSession } = useSessionStore();
  const { t } = useTranslation('chat');

  const handleSuggestion = async (_msg: string) => {
    const session = await createSession();
    if (session) {
      // TODO: Navigate and auto-send _msg
    }
  };

  return (
    <div className="empty-state">
      <div className="empty-logo">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="80" height="80" aria-label="Hermes caduceus">
          <defs>
            <linearGradient id="hermes-gold-empty" x1="0%" y1="0%" x2="0%" y2="100%">
              <stop offset="0%" style={{ stopColor: '#F5C542', stopOpacity: 1 }} />
              <stop offset="100%" style={{ stopColor: '#D4961C', stopOpacity: 1 }} />
            </linearGradient>
          </defs>
          <rect x="30" y="10" width="4" height="46" rx="2" fill="url(#hermes-gold-empty)" />
          <path d="M30 18 C24 14, 14 14, 10 18 C14 16, 22 16, 28 20" fill="#F5C542" opacity="0.9" />
          <path d="M30 22 C26 19, 18 19, 14 22 C18 20, 24 20, 28 24" fill="#D4961C" opacity="0.8" />
          <path d="M34 18 C40 14, 50 14, 54 18 C50 16, 42 16, 36 20" fill="#F5C542" opacity="0.9" />
          <path d="M34 22 C38 19, 46 19, 50 22 C46 20, 40 20, 36 24" fill="#D4961C" opacity="0.8" />
          <path d="M32 48 C22 44, 20 38, 26 34 C20 36, 18 42, 24 46 C18 40, 22 30, 30 28 C24 32, 22 38, 28 42" fill="none" stroke="#F5C542" strokeWidth="2.5" strokeLinecap="round" />
          <path d="M32 48 C42 44, 44 38, 38 34 C44 36, 46 42, 40 46 C46 40, 42 30, 34 28 C40 32, 42 38, 36 42" fill="none" stroke="#D4961C" strokeWidth="2.5" strokeLinecap="round" />
          <circle cx="32" cy="10" r="4" fill="#F5C542" />
          <circle cx="32" cy="10" r="2" fill="#FFF8E1" opacity="0.7" />
        </svg>
      </div>
      <h2>{t('empty.title')}</h2>
      <p>{t('empty.subtitle')}</p>
      <div className="suggestion-grid">
        <button className="suggestion" data-msg={t('suggestions.files_msg')} onClick={() => handleSuggestion(t('suggestions.files_msg'))}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
          </svg>
          <span>{t('suggestions.files')}</span>
        </button>
        <button className="suggestion" data-msg={t('suggestions.schedule_msg')} onClick={() => handleSuggestion(t('suggestions.schedule_msg'))}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
            <rect x="8" y="2" width="8" height="4" rx="1" ry="1" />
            <line x1="9" y1="12" x2="15" y2="12" />
            <line x1="9" y1="16" x2="12" y2="16" />
          </svg>
          <span>{t('suggestions.schedule')}</span>
        </button>
        <button className="suggestion" data-msg={t('suggestions.plan_msg')} onClick={() => handleSuggestion(t('suggestions.plan_msg'))}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6" />
            <line x1="8" y1="2" x2="8" y2="18" />
            <line x1="16" y1="6" x2="16" y2="22" />
          </svg>
          <span>{t('suggestions.plan')}</span>
        </button>
      </div>
    </div>
  );
}
