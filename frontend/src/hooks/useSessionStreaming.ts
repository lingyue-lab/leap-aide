import { useSyncExternalStore } from 'react';
import { subscribe, getSessionStreamState } from '@/stores/streamManager';

/**
 * Hook to subscribe to a session's streaming state from StreamManager.
 * Returns true if the session is currently streaming.
 *
 * Uses useSyncExternalStore for efficient React integration with
 * the StreamManager's subscription system.
 */
export function useSessionStreaming(sessionId: string): boolean {
  return useSyncExternalStore(
    (callback) => {
      // Subscribe to all StreamManager changes
      const unsub = subscribe((changedSessionId) => {
        // Only re-render if this specific session changed
        if (changedSessionId === sessionId) {
          callback();
        }
      });
      return unsub;
    },
    () => {
      const state = getSessionStreamState(sessionId);
      return !!(state && state.streaming);
    },
    () => false // Server snapshot (SSR)
  );
}
