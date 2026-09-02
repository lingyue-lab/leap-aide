import { useEffect } from 'react';
import { ts } from '@/utils/logTs';
import { addNotifyListener } from '@/stores/notifyManager';
import { useSessionStore } from '@/stores/useSessionStore';

/** Whether the chat panel is currently active (for bg-stream-done overlay). */
let _chatPanelActive = false;
export function setChatPanelActive(active: boolean) { _chatPanelActive = active; }
export function isChatPanelActive() { return _chatPanelActive; }

/**
 * Subscribe to session-level stream notifications via notifyManager (WebSocket).
 *
 * Flow:
 *   - begin_stream → update session's active_stream_id in store
 *
 * Stream completion is handled by the 'done' event in the replay channel
 * (streamManager.handleReplayEvent). Sidebar breathing indicator is driven
 * by active_stream_id in upsert_session.
 */
export function useStreamNotify(sessionId: string | null) {
  useEffect(() => {
    if (!sessionId) return;

    const unsub = addNotifyListener((event, data) => {
      if (data.session_id !== sessionId) return;

      if (event === 'begin_stream' && data.stream_id) {
        console.log(`[${ts()}] [NOTIFY] begin_stream for session=${data.session_id}, stream=${data.stream_id}`);
        // Update session's active_stream_id directly.
        // refreshFromBackend will check this after loadSession completes.
        useSessionStore.getState().updateCurrentSession({
          active_stream_id: data.stream_id,
        });
      }
    });

    return unsub;
  }, [sessionId]);
}
