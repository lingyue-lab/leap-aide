import { useEffect } from 'react';
import { ts } from '@/utils/logTs';
import * as StreamManager from '@/stores/streamManager';
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
 *   - end_stream → clear session's active_stream_id and stream state
 *
 * The actual reconnection is handled by ChatInterface.refreshFromBackend()
 * after loadSession completes, which checks session.active_stream_id.
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
      } else if (event === 'end_stream') {
        console.log(`[${ts()}] [NOTIFY] end_stream for session=${data.session_id}`);
        // Clear active_stream_id
        useSessionStore.getState().updateCurrentSession({
          active_stream_id: null,
        });
        StreamManager.clearSessionStream(data.session_id);
      }
    });

    return unsub;
  }, [sessionId]);
}
