import { useEffect } from 'react';
import { useModeStore, type ApprovalMode } from '@/stores/useModeStore';

/**
 * Listen for mode_changed SSE events from other tabs and sync the
 * current mode display.  The event carries `{ mode, session_id }` —
 * we only update when session_id matches the active session.
 */
export function useModeSync(sessionId: string | null) {
  useEffect(() => {
    if (!sessionId) return;

    const handler = (e: CustomEvent) => {
      const { mode, session_id } = e.detail;
      if (session_id !== sessionId) return;
      if (mode === 'manual' || mode === 'smart' || mode === 'yolo') {
        useModeStore.getState().syncFromEvent(mode as ApprovalMode);
      }
    };

    window.addEventListener('mode-changed', handler as EventListener);
    return () => {
      window.removeEventListener('mode-changed', handler as EventListener);
    };
  }, [sessionId]);
}
