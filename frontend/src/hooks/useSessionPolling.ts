import { useEffect, useRef } from 'react';
import { useChatStore } from '@/stores/useChatStore';
import { useSettingsStore } from '@/stores/useSettingsStore';
import { useSessionStore } from '@/stores/useSessionStore';
import * as StreamManager from '@/stores/streamManager';
import { chatService } from '@/services/api';

/** Track which stream we've already reconnected to (avoid duplicate connections). */
const _reconnectedStreams = new Set<string>();

/**
 * Poll the current session for externally-added messages (e.g., via curl,
 * or from another browser tab that started a stream).
 *
 * - Reads session_poll_interval_sec from settings; 0 = disabled.
 * - Skips polling when busy (active streaming in THIS tab).
 * - When a new active_stream_id is detected, reconnects to the SSE stream
 *   so this tab can follow live token updates in real time.
 * - Compares message_count before triggering a full reload to avoid
 *   unnecessary re-renders.
 */
export function useSessionPolling(sessionId: string | null): void {
  const busy = useChatStore((s) => s.busy);
  const setMessages = useChatStore((s) => s.setMessages);
  const settings = useSettingsStore((s) => s.settings);
  const loadSession = useSessionStore((s) => s.loadSession);
  const currentSession = useSessionStore((s) => s.currentSession);
  const updateSessionListTitle = useSessionStore((s) => s.updateSessionListTitle);

  const lastMessageCountRef = useRef<number>(0);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    // Cleanup previous interval
    if (intervalRef.current !== null) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }

    if (!sessionId) return;

    const intervalSec = parseInt(settings?.session_poll_interval_sec ?? '10', 10);
    if (isNaN(intervalSec) || intervalSec === 0) return;

    // Clamp to minimum 5s
    const effectiveSec = Math.max(5, intervalSec);

    // Track current message count
    lastMessageCountRef.current = currentSession?.message_count ?? 0;

    intervalRef.current = setInterval(async () => {
      // Skip when busy in THIS tab (don't interfere with local streaming)
      if (useChatStore.getState().busy) {
        return;
      }

      try {
        const prevCount = lastMessageCountRef.current;
        await useSessionStore.getState().loadSession(sessionId);
        const session = useSessionStore.getState().currentSession;
        if (!session) return;

        // ── Reconnect to remote stream (check BEFORE message count) ──────
        // A stream started by another client (CLI script, other tab) sets
        // active_stream_id + pending_user_message on the session. The user
        // message hasn't been appended to s.messages yet (agent thread is
        // still starting), so message_count hasn't changed. We must detect
        // the stream FIRST, before the message count early-return below.
        const remoteStreamId = session.active_stream_id;
        const pendingMsg = session.pending_user_message;
        if (
          remoteStreamId &&
          pendingMsg &&
          !_reconnectedStreams.has(remoteStreamId) &&
          !useChatStore.getState().busy  // don't interfere with local stream
        ) {
          try {
            const status = await chatService.checkStreamStatus(remoteStreamId);
            if (status.active) {
              _reconnectedStreams.add(remoteStreamId);
              StreamManager.reconnectStream(
                sessionId,
                remoteStreamId,
                pendingMsg,
                session.messages ?? [],
              );
              useChatStore.setState({ busy: true, activeStreamId: remoteStreamId });
            }
          } catch {
            // Stream may have finished — next poll will try again
          }
        }

        const newCount = session.message_count ?? 0;
        if (newCount === prevCount) return; // No new messages
        lastMessageCountRef.current = newCount;

        // New messages detected — merge into ChatStore
        const storeState = useChatStore.getState();
        if (storeState.activeStreamId === null || storeState.activeStreamId === undefined) {
          storeState.setMessages(session.messages ?? []);
        }

        // Reload sidebar list to update message_count → triggers unread badge
        useSessionStore.getState().loadSessions().catch(() => {});
      } catch {
        // Silently ignore poll errors
      }
    }, effectiveSec * 1000);

    return () => {
      if (intervalRef.current !== null) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [sessionId, busy, settings?.session_poll_interval_sec, currentSession?.message_count,
      setMessages, loadSession, updateSessionListTitle]);
}
